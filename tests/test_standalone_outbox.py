import os
import unittest
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, SASEmailOutbox
from app.standalone_outbox import (
    OutboxEncryptionNotConfigured,
    decrypt_message,
    dedupe_key,
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
