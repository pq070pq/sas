"""Security primitives for standalone SAS authentication.

These helpers deliberately contain no web routes, email delivery, or billing logic.
Use only token digests in the database; never store raw verification/reset/session tokens.
"""

import hashlib
import hmac
import secrets
import re


_PASSWORD_ITERATIONS = 600_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
        raise ValueError("A valid email address is required.")
    return value


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("Password must be between 12 and 1024 characters.")


def hash_password(password: str, *, iterations: int = _PASSWORD_ITERATIONS) -> str:
    validate_password(password)
    if iterations < 100_000:
        raise ValueError("Password hashing work factor is too low.")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$" + str(iterations) + "$" + salt.hex() + "$" + derived.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iteration_text, salt_hex, digest_hex = encoded.split("$", 3)
        iterations = int(iteration_text)
        if algorithm != "pbkdf2_sha256" or iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) != _SALT_BYTES or len(expected) != 32:
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(candidate, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def new_bearer_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def digest_token(token: str) -> str:
    if not isinstance(token, str) or len(token) < 20 or len(token) > 512:
        raise ValueError("Invalid token.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
