import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, SASAccount, SASAuthToken, SASLoginSession
from app.standalone_accounts import (
    AccountAlreadyExists,
    InvalidCredentials,
    InvalidOrExpiredToken,
    authenticate_session,
    login_account,
    logout_account,
    register_account,
    verify_account_email,
)


class StandaloneAccountLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_registration_requires_email_verification_before_login(self):
        async with self.sessions() as session:
            account, verification_token = await register_account(
                session, " Trader@Example.com ", "a-strong-test-password"
            )
            self.assertEqual(account.email, "trader@example.com")
            self.assertIsNone(account.email_verified_at)
            stored = await session.scalar(select(SASAuthToken).where(SASAuthToken.account_id == account.id))
            self.assertIsNotNone(stored)
            self.assertNotEqual(stored.token_digest, verification_token)
            await session.commit()

            with self.assertRaises(InvalidCredentials):
                await login_account(session, account.email, "a-strong-test-password")

            verified = await verify_account_email(session, verification_token)
            self.assertIsNotNone(verified.email_verified_at)
            await session.commit()

            logged_in, raw_session = await login_account(session, account.email, "a-strong-test-password")
            self.assertEqual(logged_in.id, account.id)
            stored_session = await session.scalar(select(SASLoginSession).where(SASLoginSession.account_id == account.id))
            self.assertNotEqual(stored_session.token_digest, raw_session)
            await session.commit()

            authenticated = await authenticate_session(session, raw_session)
            self.assertEqual(authenticated.id, account.id)
            await logout_account(session, raw_session)
            await session.commit()
            with self.assertRaises(InvalidOrExpiredToken):
                await authenticate_session(session, raw_session)

    async def test_verification_token_is_one_time_only(self):
        async with self.sessions() as session:
            _, token = await register_account(session, "one@example.com", "a-strong-test-password")
            await session.commit()
            await verify_account_email(session, token)
            await session.commit()
            with self.assertRaises(InvalidOrExpiredToken):
                await verify_account_email(session, token)

    async def test_duplicate_normalized_email_is_rejected(self):
        async with self.sessions() as session:
            await register_account(session, "same@example.com", "a-strong-test-password")
            await session.commit()
            with self.assertRaises(AccountAlreadyExists):
                await register_account(session, " SAME@example.com ", "another-strong-password")

    async def test_expired_verification_token_is_rejected(self):
        async with self.sessions() as session:
            account, token = await register_account(session, "expired@example.com", "a-strong-test-password")
            record = await session.scalar(select(SASAuthToken).where(SASAuthToken.account_id == account.id))
            record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
            with self.assertRaises(InvalidOrExpiredToken):
                await verify_account_email(session, token)


if __name__ == "__main__":
    unittest.main()
