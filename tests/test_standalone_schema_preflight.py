import unittest

from sqlalchemy import create_engine, text

from app.standalone_schema_preflight import inspect_schema


class StandaloneSchemaPreflightTests(unittest.TestCase):
    def test_reports_legacy_schema_and_additive_tables_without_mutating_database(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id INTEGER, status TEXT)"))
            connection.execute(text("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, telegram_id INTEGER, plan TEXT, starts_at TEXT, expires_at TEXT, active BOOLEAN)"))
            connection.execute(text("CREATE TABLE payments (id INTEGER PRIMARY KEY, telegram_id INTEGER, plan TEXT, stars INTEGER, telegram_charge_id TEXT)"))
            before = set(__import__("sqlalchemy").inspect(connection).get_table_names())
            result = inspect_schema(connection)
            after = set(__import__("sqlalchemy").inspect(connection).get_table_names())

        self.assertTrue(result["read_only"])
        self.assertTrue(result["legacy_tables_ok"])
        self.assertFalse(result["standalone_tables_present"])
        self.assertFalse(result["safe_to_enable_routes"])
        self.assertEqual(before, after)
        engine.dispose()

    def test_detects_missing_legacy_columns(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, telegram_id INTEGER)"))
            connection.execute(text("CREATE TABLE payments (id INTEGER PRIMARY KEY, telegram_id INTEGER, telegram_charge_id TEXT)"))
            result = inspect_schema(connection)

        self.assertFalse(result["legacy_tables_ok"])
        self.assertEqual(result["legacy_tables"]["users"]["missing_columns"], ["status", "telegram_id"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
