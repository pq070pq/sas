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
