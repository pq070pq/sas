"""Read-only schema preflight for the standalone SAS account rollout.

This command never creates, alters, or drops tables. Run it against staging first:
    python -m app.standalone_schema_preflight
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from .config import settings

# Core Telegram/payment tables must exist before the rollout is considered safe.
LEGACY_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "users": {"id", "telegram_id", "status"},
    "subscriptions": {"id", "telegram_id", "plan", "starts_at", "expires_at", "active"},
    "payments": {"id", "telegram_id", "plan", "stars", "telegram_charge_id"},
}

# Additive standalone tables. Missing tables are reported, never created here.
STANDALONE_TABLES = {
    "sas_accounts",
    "sas_identity_links",
    "sas_auth_tokens",
    "sas_login_sessions",
    "sas_email_outbox",
}


def inspect_schema(connection: Connection) -> dict[str, Any]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    legacy: dict[str, Any] = {}
    for table, required in LEGACY_REQUIRED_COLUMNS.items():
        if table not in tables:
            legacy[table] = {"exists": False, "missing_columns": sorted(required)}
            continue
        actual = {column["name"] for column in inspector.get_columns(table)}
        legacy[table] = {
            "exists": True,
            "missing_columns": sorted(required - actual),
        }

    standalone = {
        table: {"exists": table in tables}
        for table in sorted(STANDALONE_TABLES)
    }
    legacy_ok = all(item["exists"] and not item["missing_columns"] for item in legacy.values())
    return {
        "database_backend": connection.dialect.name,
        "read_only": True,
        "legacy_tables_ok": legacy_ok,
        "legacy_tables": legacy,
        "standalone_tables": standalone,
        "standalone_tables_present": all(item["exists"] for item in standalone.values()),
        "safe_to_enable_routes": False,
        "note": (
            "This is a schema inventory only. It does not prove backup/restore, data integrity, "
            "SMTP delivery, or end-to-end readiness; standalone routes must remain disabled."
        ),
    }


async def main() -> int:
    from .db import engine

    async with engine.connect() as connection:
        result = await connection.run_sync(inspect_schema)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Exit nonzero if core legacy schema is missing/incomplete. Missing additive
    # tables are reported but are not created or treated as a migration request.
    return 0 if result["legacy_tables_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
