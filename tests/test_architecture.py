import ast
import importlib
from pathlib import Path
import unittest


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
