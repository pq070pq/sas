import unittest

import httpx
from fastapi import FastAPI, Request

from app.standalone_security import StandaloneSecurityMiddleware


class StandaloneSecurityMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()

        @app.post("/api/standalone/auth/echo")
        async def echo(request: Request):
            body = await request.body()
            return {"size": len(body)}

        @app.get("/health")
        async def health():
            return {"ok": True}

        app.add_middleware(StandaloneSecurityMiddleware, max_body_bytes=32)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_rejects_content_length_over_limit(self):
        response = await self.client.post(
            "/api/standalone/auth/echo",
            content=b"x" * 33,
            headers={"content-type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 413)

    async def test_rejects_chunked_body_over_limit(self):
        async def chunks():
            yield b"x" * 20
            yield b"y" * 13

        response = await self.client.post(
            "/api/standalone/auth/echo",
            content=chunks(),
            headers={"content-type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 413)

    async def test_allows_body_at_limit_and_sets_security_headers(self):
        response = await self.client.post(
            "/api/standalone/auth/echo",
            content=b"x" * 32,
            headers={"content-type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["size"], 32)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    async def test_does_not_change_unrelated_routes(self):
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("content-security-policy", response.headers)


if __name__ == "__main__":
    unittest.main()
