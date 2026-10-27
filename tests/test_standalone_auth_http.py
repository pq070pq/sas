"""HTTP integration tests for the unmounted standalone auth router."""
import re
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, SASEmailOutbox, get_session
from app.standalone_outbox import decrypt_message
from app.standalone_routes import router


class StandaloneAuthHTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.app = FastAPI()
        self.app.include_router(router)

        async def override_session():
            async with self.sessions() as session:
                yield session

        self.app.dependency_overrides[get_session] = override_session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://testserver",
        )
        self.origin_patch = patch.dict("os.environ", {
            "STANDALONE_PUBLIC_ORIGIN": "https://sas.example.com",
            "STANDALONE_EMAIL_FERNET_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        })
        self.origin_patch.start()

    async def asyncTearDown(self):
        self.origin_patch.stop()
        await self.client.aclose()
        await self.engine.dispose()

    async def queued_email_body(self, expected_path: str) -> str:
        async with self.sessions() as session:
            rows = list((await session.scalars(select(SASEmailOutbox).order_by(SASEmailOutbox.id))).all())
            for row in reversed(rows):
                _, body = decrypt_message(row.body)
                if expected_path in body:
                    return body
        self.fail(f"No queued email contained {expected_path!r}")

    async def test_register_verify_login_me_logout_http_flow(self):
        response = await self.client.post("/api/standalone/auth/register", json={
            "email": " Trader@Example.com ", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 202, response.text)
        body = await self.queued_email_body("/standalone/verify-email?token=")
        match = re.search(r"/standalone/verify-email\?token=([^\s]+)", body)
        self.assertIsNotNone(match, body)
        token = match.group(1)

        response = await self.client.post("/api/standalone/auth/login", headers={"origin": "https://sas.example.com"}, json={
            "email": "trader@example.com", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 401)

        response = await self.client.post("/api/standalone/auth/verify-email", json={"token": token})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post("/api/standalone/auth/login", headers={"origin": "https://sas.example.com"}, json={
            "email": "TRADER@example.com", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("sas_standalone_session", response.cookies)

        response = await self.client.get("/api/standalone/auth/me")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["email"], "trader@example.com")

        response = await self.client.post("/api/standalone/auth/logout", headers={"origin": "https://sas.example.com"})
        self.assertEqual(response.status_code, 204, response.text)
        response = await self.client.get("/api/standalone/auth/me")
        self.assertEqual(response.status_code, 401)


    async def test_login_rejects_cross_origin_requests(self):
        response = await self.client.post(
            "/api/standalone/auth/login",
            headers={"origin": "https://attacker.example"},
            json={"email": "trader@example.com", "password": "strong-password-123"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    async def test_login_requires_origin_header(self):
        response = await self.client.post(
            "/api/standalone/auth/login",
            json={"email": "trader@example.com", "password": "strong-password-123"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    async def test_logout_rejects_cross_origin_requests(self):
        response = await self.client.post(
            "/api/standalone/auth/logout",
            headers={"origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    async def test_logout_requires_origin_header(self):
        response = await self.client.post("/api/standalone/auth/logout")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_password_reset_http_flow_and_account_enumeration(self):
        unknown = await self.client.post(
            "/api/standalone/auth/request-password-reset",
            json={"email": "unknown@example.com"},
        )
        self.assertEqual(unknown.status_code, 202, unknown.text)
        unknown_message = unknown.json()["message"]
        async with self.sessions() as session:
            self.assertEqual(await session.scalar(select(SASEmailOutbox.id)), None)

        response = await self.client.post("/api/standalone/auth/register", json={
            "email": "reset@example.com", "password": "original-password-123"
        })
        self.assertEqual(response.status_code, 202, response.text)
        verification_body = await self.queued_email_body("/standalone/verify-email?token=")
        verification_match = re.search(r"/standalone/verify-email\?token=([^\s]+)", verification_body)
        self.assertIsNotNone(verification_match, verification_body)
        response = await self.client.post(
            "/api/standalone/auth/verify-email", json={"token": verification_match.group(1)}
        )
        self.assertEqual(response.status_code, 200, response.text)

        response = await self.client.post(
            "/api/standalone/auth/login",
            headers={"origin": "https://sas.example.com"},
            json={"email": "reset@example.com", "password": "original-password-123"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        old_session_cookie = response.cookies.get("sas_standalone_session")
        self.assertTrue(old_session_cookie)

        response = await self.client.post(
            "/api/standalone/auth/request-password-reset",
            json={"email": "RESET@example.com"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["message"], unknown_message)
        reset_body = await self.queued_email_body("/standalone/reset-password?token=")
        reset_match = re.search(r"/standalone/reset-password\?token=([^\s]+)", reset_body)
        self.assertIsNotNone(reset_match, reset_body)

        response = await self.client.post(
            "/api/standalone/auth/reset-password",
            json={"token": reset_match.group(1), "new_password": "replacement-password-456"},
        )
        self.assertEqual(response.status_code, 204, response.text)
        response = await self.client.get(
            "/api/standalone/auth/me",
            cookies={"sas_standalone_session": old_session_cookie},
        )
        self.assertEqual(response.status_code, 401)

        response = await self.client.post(
            "/api/standalone/auth/login",
            headers={"origin": "https://sas.example.com"},
            json={"email": "reset@example.com", "password": "replacement-password-456"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(
            "/api/standalone/auth/reset-password",
            json={"token": reset_match.group(1), "new_password": "another-password-789"},
        )
        self.assertEqual(response.status_code, 400, response.text)

    async def test_registration_queues_encrypted_email_atomically(self):
        response = await self.client.post("/api/standalone/auth/register", json={
            "email": "fail@example.com", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 202, response.text)
        async with self.sessions() as session:
            from sqlalchemy import select
            from app.db import SASAccount
            account = await session.scalar(select(SASAccount).where(SASAccount.email == "fail@example.com"))
            self.assertIsNotNone(account)
            queued = await session.scalar(select(SASEmailOutbox).where(SASEmailOutbox.recipient == "fail@example.com"))
            self.assertIsNotNone(queued)
            self.assertEqual(queued.status, "pending")
            self.assertEqual(queued.subject, "[encrypted]")
            self.assertIn("/standalone/verify-email?token=", decrypt_message(queued.body)[1])

    async def test_invalid_email_is_rejected_before_handler(self):
        response = await self.client.post("/api/standalone/auth/register", json={
            "email": "not-an-email", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
