import ast
import importlib
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class ArchitectureBoundaryTests(unittest.TestCase):
    def _imports(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_scanner_never_imports_ui_or_main(self):
        imports = self._imports(APP / "scanner.py")
        self.assertNotIn("main", imports)
        self.assertNotIn("web", imports)

    def test_binance_provider_isolated_from_radar_and_ui(self):
        imports = self._imports(APP / "binance_spot.py")
        forbidden = {"main", "jobs", "scanner", "private_analysis"}
        self.assertTrue(forbidden.isdisjoint(imports))

    def test_scheduler_is_separate_from_jobs_and_ui(self):
        scheduler_source = (APP / "scheduler.py").read_text(encoding="utf-8")
        self.assertNotIn("from .main import", scheduler_source)
        self.assertNotIn("from .telegram import", scheduler_source)
        self.assertNotIn("from .scanner import", scheduler_source)
        jobs_source = (APP / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("from .scheduler import scheduler", jobs_source)

    def test_quality_score_is_independent_from_delivery_and_ui(self):
        imports = self._imports(APP / "quality_score.py")
        self.assertTrue({"main", "jobs", "telegram", "scanner"}.isdisjoint(imports))

    def test_frontend_is_not_python_dependency(self):
        source = (ROOT / "web" / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("app.scanner", source)
        self.assertNotIn("app.jobs", source)

    def test_channel_only_publishes_confirmed_candidates_while_app_keeps_active_limit(self):
        from app.config import settings
        self.assertEqual(settings.radar_channel_daily_limit, 0)
        self.assertEqual(settings.radar_app_daily_limit, 15)
        jobs_source = (APP / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("channel_limit=unlimited", jobs_source)
        self.assertIn("Unconfirmed candidates remain in the app/database only", jobs_source)
        self.assertIn('"published": False', jobs_source)
        self.assertNotIn("Radar candidate sent as watch report", jobs_source)

    def test_core_imports(self):
        for module in (
            "app.scanner",
            "app.binance_spot",
            "app.radar_health",
            "app.private_analysis",
        ):
            importlib.import_module(module)


class BinancePureFunctionTests(unittest.TestCase):
    def test_crypto_normalization(self):
        from app.binance_spot import is_crypto_symbol, normalize_symbol
        self.assertEqual(normalize_symbol("BTC"), "BTCUSDT")
        self.assertEqual(normalize_symbol("BTC/USDT"), "BTCUSDT")
        self.assertTrue(is_crypto_symbol("ETH"))
        self.assertFalse(is_crypto_symbol("AAPL"))

    def test_unknown_asset_is_not_reclassified_as_crypto(self):
        from app.binance_spot import is_crypto_symbol
        self.assertFalse(is_crypto_symbol("SPX"))
        self.assertFalse(is_crypto_symbol("GOLD"))


if __name__ == "__main__":
    unittest.main()


class QualityScoreTests(unittest.TestCase):
    def test_quality_score_is_deterministic_and_bounded(self):
        from app.quality_score import score_quality
        row = {
            "change_pct": 8,
            "momentum_rvol_10d": 2.5,
            "intraday_confirmation": True,
            "breakout_confirmed": True,
            "news_items": [{"headline": "خبر"}],
            "risk_reward": 2.5,
        }
        result = score_quality(row)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)
        self.assertEqual(result["score"], score_quality(row)["score"])
        self.assertEqual(set(result["components"]), {"trend", "momentum", "market", "catalyst", "risk"})

    def test_missing_evidence_does_not_create_price_or_signal(self):
        from app.quality_score import score_quality
        result = score_quality({})
        self.assertIn(result["label"], {"ممتاز", "قوي", "متوسط", "مراقبة"})
        self.assertNotIn("price", result)
        self.assertNotIn("entry", result)
        self.assertNotIn("target", result)


class QualityBacktestTests(unittest.TestCase):
    def test_quality_buckets_use_completed_outcomes_only(self):
        from app.quality_backtest import summarize_quality_outcomes
        result = summarize_quality_outcomes([
            {"quality_score": 90, "status": "target1", "achieved_target": 1},
            {"quality_score": 90, "status": "failed", "achieved_target": 0},
            {"quality_score": 90, "status": "active", "achieved_target": 0},
        ])
        self.assertEqual(result["85-100"]["success"], 1)
        self.assertEqual(result["85-100"]["failure"], 1)
        self.assertEqual(result["85-100"]["active"], 1)
        self.assertEqual(result["85-100"]["success_rate"], 50.0)


class RadarOutcomeClassificationTests(unittest.TestCase):
    def test_expired_without_target_is_excluded_from_success_rate(self):
        from app.quality_backtest import summarize_quality_outcomes
        result = summarize_quality_outcomes([
            {"quality_score": 90, "status": "target1", "achieved_target": 1},
            {"quality_score": 90, "status": "failed", "achieved_target": 0},
            {"quality_score": 90, "status": "expired", "achieved_target": 0},
            {"quality_score": 90, "status": "active", "achieved_target": 0},
        ])["85-100"]
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failure"], 1)
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["active"], 1)
        self.assertEqual(result["success_rate"], 50.0)

    def test_expired_after_target_remains_a_target_hit(self):
        from app.quality_backtest import summarize_quality_outcomes
        result = summarize_quality_outcomes([
            {"quality_score": 80, "status": "expired", "achieved_target": 2},
        ])["75-84"]
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["expired"], 0)
        self.assertEqual(result["success_rate"], 100.0)

    def test_unknown_status_is_not_silently_counted(self):
        from app.quality_backtest import summarize_quality_outcomes
        result = summarize_quality_outcomes([
            {"quality_score": 70, "status": "unexpected", "achieved_target": 0},
        ])["65-74"]
        self.assertEqual(result["unclassified"], 1)
        self.assertIsNone(result["success_rate"])



class RadarLiveQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_session_prefers_timestamped_panwatch_minute_quote(self):
        from app.market import quote

        observed = {
            "symbol": "BESS",
            "price": 2.70,
            "change_pct": 1.2,
            "source": "PanWatch Extended 1m",
            "is_extended_hours": False,
            "session_volume": 250000,
            "datetime": "2026-11-04T10:12:00-05:00",
        }
        with patch("app.market._panwatch_extended_quote", new=AsyncMock(return_value=observed)), patch(
            "app.market._fmp_quote", new=AsyncMock(return_value=None)
        ) as fmp_quote:
            result = await quote("BESS", session="regular")

        self.assertEqual(result, observed)
        fmp_quote.assert_not_awaited()

    async def test_regular_session_does_not_promote_missing_minute_quote(self):
        from app.market import quote

        fallback = {
            "symbol": "BESS",
            "price": 2.70,
            "change_pct": 1.2,
            "source": "FMP",
            "datetime": None,
        }
        with patch("app.market._panwatch_extended_quote", new=AsyncMock(return_value=None)), patch(
            "app.market._fmp_quote", new=AsyncMock(return_value=fallback)
        ):
            result = await quote("BESS", session="regular")

        self.assertEqual(result["source"], "FMP")
        self.assertIsNone(result.get("datetime"))
