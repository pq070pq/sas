import unittest
from unittest.mock import AsyncMock, patch

from app import scanner


class SessionAwareFinalCandlesTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_session_can_reuse_yahoo_fallback(self):
        candles_lookup = AsyncMock(return_value=([], "unavailable"))
        with patch.object(scanner, "_get_analysis_candles", candles_lookup):
            result = await scanner.classify_sas(
                "TEST",
                quote={"symbol": "TEST"},
                allow_yahoo=True,
            )

        self.assertFalse(result["quality_data_available"])
        self.assertTrue(candles_lookup.await_args.kwargs["allow_yahoo"])

    async def test_yahoo_fallback_remains_disabled_by_default(self):
        candles_lookup = AsyncMock(return_value=([], "unavailable"))
        with patch.object(scanner, "_get_analysis_candles", candles_lookup):
            result = await scanner.classify_sas("TEST", quote={"symbol": "TEST"})

        self.assertFalse(result["quality_data_available"])
        self.assertFalse(candles_lookup.await_args.kwargs["allow_yahoo"])


if __name__ == "__main__":
    unittest.main()
