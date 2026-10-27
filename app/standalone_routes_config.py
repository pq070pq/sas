"""Fixed public-origin configuration for standalone email links."""
import os

from .standalone_email_templates import validate_public_origin


def standalone_public_origin() -> str:
    value = os.environ.get("STANDALONE_PUBLIC_ORIGIN", "")
    try:
        return validate_public_origin(value)
    except ValueError as exc:
        raise RuntimeError("STANDALONE_PUBLIC_ORIGIN must be configured as an HTTPS origin.") from exc
