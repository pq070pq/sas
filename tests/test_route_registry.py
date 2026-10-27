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

    def test_standalone_auth_routes_remain_unmounted_in_production_app(self):
        paths = {route.path for route in app.routes}
        self.assertFalse(
            any(path.startswith("/api/standalone/auth") for path in paths),
            "Standalone auth API must remain disabled until rollout approval.",
        )
        self.assertNotIn("/standalone/verify-email", paths)
        self.assertNotIn("/standalone/reset-password", paths)

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

    def test_market_pulse_ribbon_and_index_rail_are_present(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "assets" / "app-ticker-fix-20261015.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "assets" / "app-ticker-fix-20261015.css").read_text(encoding="utf-8")
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('id="sasMarketNewsTrack"', html)
        self.assertIn('id="sasMarketIndicesTrack"', html)
        self.assertIn("sasMarketNewsScroll", css)
        self.assertIn("/api/market/news", js)
        self.assertIn("async def market_news_endpoint", main)
        self.assertIn("translate_stock_news", main)

    def test_production_deploy_is_gated_to_main_branch(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        self.assertIn("branches:", workflow)
        self.assertIn("- main", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)

    def test_deploy_smoke_test_accepts_new_market_pulse_ui(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        self.assertIn('id="sasMarketNewsTrack"', workflow)
        self.assertIn('id="sasMarketIndicesTrack"', workflow)
        self.assertIn("new market-news or market-indices ribbon", workflow)
        self.assertNotIn("! grep -q \"sasMarketNews\" /app/web/index.html", workflow)

    def test_shariah_search_does_not_reference_an_out_of_scope_controller(self):
        for relative in ("web/assets/app.js", "web/assets/app-ticker-fix-20261015.js"):
            js = (ROOT / relative).read_text(encoding="utf-8")
            start = js.index("async function searchShariah()")
            end = js.index("let stockAnalysisLoaderTimer", start)
            handler = js[start:end]
            self.assertNotIn("signal:searchController.signal", handler)
            self.assertIn('class="shariah-retry-btn"', handler)
            self.assertIn('onclick="searchShariah()"', handler)


if __name__ == "__main__":
    unittest.main()
