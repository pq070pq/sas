import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, SASEmailOutbox
from app.standalone_outbox import (
    OutboxEncryptionNotConfigured,
    decrypt_message,
    dedupe_key,
    deliver_one,
    enqueue_email,
    encrypt_message,
)


class StandaloneOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_key = os.environ.get("STANDALONE_EMAIL_FERNET_KEY")
        os.environ["STANDALONE_EMAIL_FERNET_KEY"] = Fernet.generate_key().decode("ascii")
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        if self.old_key is None:
            os.environ.pop("STANDALONE_EMAIL_FERNET_KEY", None)
        else:
            os.environ["STANDALONE_EMAIL_FERNET_KEY"] = self.old_key
        await self.engine.dispose()

    async def test_payload_is_encrypted_and_round_trips(self):
        subject = "Verify SAS account"
        body = "https://sas.example.com/standalone/verify-email?token=secret-token-value"
        ciphertext = encrypt_message(subject, body)
        self.assertNotIn("secret-token-value", ciphertext)
        self.assertNotIn(subject, ciphertext)
        self.assertEqual(decrypt_message(ciphertext), (subject, body))

    async def test_missing_encryption_key_fails_closed(self):
        os.environ.pop("STANDALONE_EMAIL_FERNET_KEY", None)
        with self.assertRaises(OutboxEncryptionNotConfigured):
            encrypt_message("Subject", "token-bearing body")

    async def test_expired_sending_lease_is_reclaimed_and_delivered(self):
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            row = await enqueue_email(
                session,
                dedupe_key=dedupe_key("verify", "expired-lease-token"),
                recipient="person@example.com",
                subject="Verify account",
                body="https://sas.example.com/standalone/verify-email?token=expired-lease-token",
            )
            row.status = "sending"
            row.locked_until = now - timedelta(seconds=1)
            await session.commit()
            row_id = row.id

        with patch("app.standalone_outbox.send_standalone_email", new_callable=AsyncMock) as send:
            async with self.sessions() as session:
                delivered = await deliver_one(session, now=now)
                self.assertTrue(delivered)
            send.assert_awaited_once()
            self.assertEqual(send.await_args.args[0], "person@example.com")
            self.assertEqual(send.await_args.args[1], "Verify account")
            self.assertIn("expired-lease-token", send.await_args.args[2])

        async with self.sessions() as session:
            row = await session.get(SASEmailOutbox, row_id)
            self.assertEqual(row.status, "sent")
            self.assertIsNone(row.locked_until)
            self.assertEqual(row.attempts, 1)

    async def test_concurrent_workers_do_not_send_same_message_twice(self):
        async with self.sessions() as session:
            await enqueue_email(
                session,
                dedupe_key=dedupe_key("verify", "concurrent-worker-token"),
                recipient="person@example.com",
                subject="Verify account",
                body="https://sas.example.com/standalone/verify-email?token=concurrent-worker-token",
            )
            await session.commit()

        import asyncio

        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def delayed_send(*args):
            send_started.set()
            await release_send.wait()

        async def worker():
            async with self.sessions() as session:
                return await deliver_one(session)

        with patch("app.standalone_outbox.send_standalone_email", new_callable=AsyncMock, side_effect=delayed_send) as send:
            first = asyncio.create_task(worker())
            await asyncio.wait_for(send_started.wait(), timeout=2)
            second = asyncio.create_task(worker())
            # The first worker holds a live lease while SMTP is delayed.
            second_result = await asyncio.wait_for(second, timeout=2)
            release_send.set()
            first_result = await asyncio.wait_for(first, timeout=2)

        self.assertTrue(first_result)
        self.assertFalse(second_result)
        send.assert_awaited_once()

    async def test_failed_delivery_retries_without_storing_sensitive_error_text(self):
        now = datetime.now(timezone.utc)
        secret = "sensitive-one-time-token"
        async with self.sessions() as session:
            row = await enqueue_email(
                session,
                dedupe_key=dedupe_key("verify", secret),
                recipient="person@example.com",
                subject="Verify account",
                body=f"https://sas.example.com/standalone/verify-email?token={secret}",
            )
            # Freeze the queue's due time so this test does not depend on
            # sub-second timing between enqueueing and delivery.
            row.available_at = now - timedelta(seconds=1)
            await session.commit()
            row_id = row.id

        with patch(
            "app.standalone_outbox.send_standalone_email",
            new_callable=AsyncMock,
            side_effect=RuntimeError(f"provider failed while handling {secret}"),
        ):
            async with self.sessions() as session:
                self.assertTrue(await deliver_one(session, now=now))

        async with self.sessions() as session:
            row = await session.get(SASEmailOutbox, row_id)
            self.assertEqual(row.status, "pending")
            self.assertEqual(row.attempts, 1)
            self.assertEqual(row.last_error, "RuntimeError")
            self.assertNotIn(secret, row.last_error)
            self.assertGreater(row.available_at, now)
            self.assertIsNone(row.locked_until)

    async def test_enqueue_stores_ciphertext_and_deduplicates(self):
        async with self.sessions() as session:
            key = dedupe_key("verify", "one-time-token-value")
            row = await enqueue_email(
                session, dedupe_key=key, recipient="person@example.com",
                subject="Verify account",
                body="https://sas.example.com/standalone/verify-email?token=one-time-token-value",
            )
            await session.commit()
            again = await enqueue_email(
                session, dedupe_key=key, recipient="person@example.com",
                subject="Verify account", body="different body should not replace first",
            )
            await session.commit()
            self.assertEqual(row.id, again.id)
            self.assertEqual(row.status, "pending")
            self.assertEqual(row.subject, "[encrypted]")
            self.assertNotIn("one-time-token-value", row.body)
            stored = (await session.scalars(select(SASEmailOutbox))).all()
            self.assertEqual(len(stored), 1)


if __name__ == "__main__":
    unittest.main()
