"""Safe link and message builders for standalone account emails.

This module does not send mail or expose HTTP routes. Public URL configuration is
validated so emailed links cannot accidentally target an insecure or malformed host.
"""
from urllib.parse import urlencode, urlsplit


class InvalidStandalonePublicURL(ValueError):
    """The configured public origin is not a safe HTTPS origin."""


def validate_public_origin(value: str) -> str:
    origin = (value or "").strip().rstrip("/")
    parts = urlsplit(origin)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise InvalidStandalonePublicURL("Standalone public origin must be an HTTPS origin without a path.")
    return origin


def _link(origin: str, path: str, token: str) -> str:
    safe_origin = validate_public_origin(origin)
    if path not in ("/standalone/verify-email", "/standalone/reset-password"):
        raise ValueError("Unsupported standalone email link path.")
    if not isinstance(token, str) or not 20 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise ValueError("Invalid one-time token.")
    return f"{safe_origin}{path}?{urlencode({'token': token})}"


def verification_email(origin: str, token: str) -> tuple[str, str]:
    url = _link(origin, "/standalone/verify-email", token)
    return "Verify your SAS PRO email", (
        "Welcome to SAS PRO. To verify your email address, open this one-time link:\n\n"
        f"{url}\n\nThis link expires after 30 minutes. If you did not create this account, "
        "you can ignore this email."
    )


def password_reset_email(origin: str, token: str) -> tuple[str, str]:
    url = _link(origin, "/standalone/reset-password", token)
    return "Reset your SAS PRO password", (
        "A password reset was requested for your SAS PRO account. Use this one-time link:\n\n"
        f"{url}\n\nThis link expires after 20 minutes. If you did not request a reset, "
        "ignore this email and your password will remain unchanged."
    )
