"""HTTP integration tests for the unmounted standalone auth router."""
import re
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_session
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
        self.mail = AsyncMock()
        self.mail_patch = patch("app.standalone_routes.send_standalone_email", self.mail)
        self.mail_patch.start()
        self.origin_patch = patch.dict("os.environ", {"STANDALONE_PUBLIC_ORIGIN": "https://sas.example.com"})
        self.origin_patch.start()

    async def asyncTearDown(self):
        self.origin_patch.stop()
        self.mail_patch.stop()
        await self.client.aclose()
        await self.engine.dispose()

    async def test_register_verify_login_me_logout_http_flow(self):
        response = await self.client.post("/api/standalone/auth/register", json={
            "email": " Trader@Example.com ", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 202, response.text)
        self.mail.assert_awaited_once()
        body = self.mail.await_args.args[2]
        match = re.search(r"/standalone/verify-email\?token=([^\s]+)", body)
        self.assertIsNotNone(match, body)
        token = match.group(1)

        response = await self.client.post("/api/standalone/auth/login", json={
            "email": "trader@example.com", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 401)

        response = await self.client.post("/api/standalone/auth/verify-email", json={"token": token})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post("/api/standalone/auth/login", json={
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


    async def test_logout_rejects_cross_origin_requests(self):
        response = await self.client.post(
            "/api/standalone/auth/logout",
            headers={"origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    async def test_logout_requires_origin_header(self):
        response = await self.client.post("/api/standalone/auth/logout")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_registration_email_failure_does_not_create_account(self):
        self.mail.side_effect = OSError("mail server unavailable")
        response = await self.client.post("/api/standalone/auth/register", json={
            "email": "fail@example.com", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 503, response.text)
        async with self.sessions() as session:
            from sqlalchemy import select
            from app.db import SASAccount
            account = await session.scalar(select(SASAccount).where(SASAccount.email == "fail@example.com"))
            self.assertIsNone(account)

    async def test_invalid_email_is_rejected_before_handler(self):
        response = await self.client.post("/api/standalone/auth/register", json={
            "email": "not-an-email", "password": "strong-password-123"
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
