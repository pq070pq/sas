"""Security middleware for isolated standalone authentication routes.

This middleware is deliberately not installed in app/main.py until the standalone
routes are explicitly approved for rollout.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_STANDALONE_PREFIXES = ("/api/standalone/auth/", "/standalone/")
_MAX_BODY_BYTES = 16 * 1024


class StandaloneSecurityMiddleware:
    """Apply baseline browser headers and enforce a streaming request-body cap."""

    def __init__(self, app: ASGIApp, max_body_bytes: int = _MAX_BODY_BYTES):
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(_STANDALONE_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._too_large(scope, receive, send)
                    return
            except ValueError:
                await self._too_large(scope, receive, send)
                return

        buffered: list[Message] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_body_bytes:
                await self._too_large(scope, receive, send)
                return
            buffered.append(message)
            more_body = message.get("more_body", False)

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ])
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, replay_receive, secure_send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-frame-options", b"DENY"),
                (b"referrer-policy", b"no-referrer"),
                (b"content-security-policy", b"default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})
