import unittest
from unittest.mock import patch

from app.catalyst_intelligence import (
    _extract_payload, _normalise, _safe_public_news_url, enrich_catalyst_news
)


class CatalystIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_non_public_or_sec_urls(self):
        for url in ("file:///etc/passwd", "http://127.0.0.1/a", "https://localhost/a",
                    "https://data.sec.gov/submissions/CIK0000000000.json"):
            self.assertFalse(_safe_public_news_url(url))
        self.assertTrue(_safe_public_news_url("https://investor.example.com/news"))

    def test_normalises_unverified_evidence_without_inventing_figures(self):
        item = {"url": "https://investor.example.com/news/1", "headline": "New agreement"}
        row = _normalise({
            "event_type": "contract", "summary_ar": "أعلنت الشركة اتفاقًا جديدًا.",
            "event_direction": "positive", "financial_figures": [], "duration": "",
            "evidence_quote": "The company announced an agreement."
        }, item, "ABC")
        self.assertEqual(row["symbol"], "ABC")
        self.assertEqual(row["financial_figures"], [])
        self.assertEqual(row["verification_status"], "extracted_unverified")
        self.assertEqual(row["source_url"], item["url"])

    async def test_disabled_integration_does_not_change_news(self):
        items = [{"headline": "Headline", "url": "https://example.com/news"}]
        with patch("app.catalyst_intelligence.settings") as cfg:
            cfg.scrapegraphai_enabled = False
            cfg.scrapegraphai_api_key = ""
            with patch("app.catalyst_intelligence._client_class", None):
                self.assertEqual(await enrich_catalyst_news("ABC", items), items)

    def test_extract_payload_requires_success(self):
        class Result:
            status = "error"
            data = {"event_type": "contract"}
        self.assertIsNone(_extract_payload(Result()))


if __name__ == "__main__":
    unittest.main()
