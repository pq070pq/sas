from pathlib import Path
import unittest

from fastapi.routing import APIRoute
from app.main import app


ROOT = Path(__file__).resolve().parents[1]


class ProductionRouteRegistryTests(unittest.TestCase):
    def test_no_duplicate_top_level_function_definitions(self):
        import ast
        from collections import Counter

        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        self.assertEqual(duplicates, [], f"Duplicate top-level functions: {duplicates}")

    def test_no_duplicate_method_path_registrations(self):
        seen = {}
        duplicates = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods or set():
                key = (method.upper(), route.path)
                if key in seen:
                    duplicates.append((key, seen[key], route.name))
                else:
                    seen[key] = route.name
        self.assertEqual(duplicates, [], f"Duplicate FastAPI routes: {duplicates}")


class MiniAppRollbackContractTests(unittest.TestCase):
    def test_home_page_uses_the_active_versioned_frontend_assets(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/assets/app-ticker-fix-20261015.js", html)
        self.assertIn("/assets/app-ticker-fix-20261015.css", html)

    def test_removed_market_ticker_and_news_panel_stay_absent(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "assets" / "app-ticker-fix-20261015.js").read_text(encoding="utf-8")
        for marker in ('market-ticker-wrap', 'id="marketTicker"', 'sasMarketNews'):
            self.assertNotIn(marker, html)
        for marker in ("marketTicker", "refreshTickerOnly", "sasMarketNews"):
            self.assertNotIn(marker, js)


if __name__ == "__main__":
    unittest.main()
