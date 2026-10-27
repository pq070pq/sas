"""Isolated browser pages for standalone account email links.

Keep this router separate and unmounted until the standalone auth rollout is approved.
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["standalone-auth-pages"])
_PAGE_DIR = Path(__file__).resolve().parent.parent / "web" / "standalone"


@router.get("/standalone/verify-email", include_in_schema=False)
async def standalone_verify_email_page():
    return FileResponse(
        _PAGE_DIR / "verify-email.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.get("/standalone/reset-password", include_in_schema=False)
async def standalone_reset_password_page():
    return FileResponse(
        _PAGE_DIR / "reset-password.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )
