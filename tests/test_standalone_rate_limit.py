"""Tests for shared, persistent standalone-auth rate limiting."""
from datetime import datetime, timedelta, timezone
import unittest

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.standalone_rate_limit import enforce_rate_limit


class StandaloneRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_limit_is_shared_across_sessions_and_returns_retry_after(self):
        now = datetime.now(timezone.utc)
        async with self.sessions() as first:
            await enforce_rate_limit(
                first, scope="login:ip", identity="127.0.0.1",
                limit=2, window_seconds=60, now=now,
            )
        async with self.sessions() as second:
            await enforce_rate_limit(
                second, scope="login:ip", identity="127.0.0.1",
                limit=2, window_seconds=60, now=now,
            )
        async with self.sessions() as third:
            with self.assertRaises(HTTPException) as caught:
                await enforce_rate_limit(
                    third, scope="login:ip", identity="127.0.0.1",
                    limit=2, window_seconds=60, now=now,
                )
            self.assertEqual(caught.exception.status_code, 429)
            self.assertIn("Retry-After", caught.exception.headers)

    async def test_new_window_starts_a_separate_counter(self):
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            await enforce_rate_limit(
                session, scope="register", identity="person@example.com",
                limit=1, window_seconds=60, now=now,
            )
            with self.assertRaises(HTTPException):
                await enforce_rate_limit(
                    session, scope="register", identity="person@example.com",
                    limit=1, window_seconds=60, now=now,
                )
            await enforce_rate_limit(
                session, scope="register", identity="person@example.com",
                limit=1, window_seconds=60, now=now + timedelta(seconds=61),
            )

    async def test_raw_identity_is_not_stored(self):
        from sqlalchemy import select
        from app.db import SASRateLimitBucket

        async with self.sessions() as session:
            await enforce_rate_limit(
                session, scope="login", identity="sensitive@example.com",
                limit=2, window_seconds=60,
            )
            keys = (await session.scalars(select(SASRateLimitBucket.bucket_key))).all()
            self.assertEqual(len(keys), 1)
            self.assertNotIn("sensitive", keys[0])
            self.assertEqual(len(keys[0]), 64)


if __name__ == "__main__":
    unittest.main()
