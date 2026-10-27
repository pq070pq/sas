import asyncio
import unittest

from app.key_pool import KeyPool, parse_keys


class KeyPoolRotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_keys_accepts_separators_and_deduplicates(self):
        self.assertEqual(
            parse_keys("first, second", "third;first", "fourth\nfifth"),
            ["first", "second", "third", "fourth", "fifth"],
        )

    async def test_failed_key_is_cooled_and_next_key_is_acquired(self):
        pool = KeyPool(["secret-one", "secret-two"], cooldown_seconds=10)
        first = await pool.acquire()
        self.assertEqual(first, "secret-one")
        await pool.mark_failure(first)
        second = await pool.acquire()
        self.assertEqual(second, "secret-two")
        snapshot = await pool.snapshot()
        self.assertEqual(snapshot, {"keys": 2, "available": 1})

    async def test_snapshot_never_exposes_key_values(self):
        pool = KeyPool(["DO_NOT_LOG_THIS_KEY", "another-secret"])
        snapshot = await pool.snapshot()
        self.assertEqual(snapshot, {"keys": 2, "available": 2})
        self.assertNotIn("DO_NOT_LOG_THIS_KEY", repr(snapshot))
        self.assertNotIn("another-secret", repr(snapshot))


if __name__ == "__main__":
    unittest.main()
