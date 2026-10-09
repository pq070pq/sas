import unittest
from unittest.mock import patch

from app.news_translation import translate_stock_news


class StockNewsTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_news_returns_empty(self):
        self.assertEqual(await translate_stock_news("ABC", []), [])

    async def test_items_without_headline_are_ignored(self):
        self.assertEqual(await translate_stock_news("ABC", [{"url": "https://example.com"}]), [])

    async def test_without_provider_key_fails_gracefully(self):
        rows = [{"headline": "Headline", "url": "https://example.com/1"}]
        with patch("app.news_translation.settings") as cfg:
            cfg.gemini_api_key = ""
            cfg.gemini_base_url = "https://example.com/v1beta/openai"
            cfg.gemini_model = "test"
            cfg.groq_api_key = ""
            cfg.groq_base_url = "https://example.com/v1"
            cfg.groq_model = "test"
            cfg.openrouter_api_key = ""
            cfg.openrouter_base_url = "https://example.com/v1"
            cfg.openrouter_model = "test"
            self.assertEqual(await translate_stock_news("ABC", rows), [])


if __name__ == "__main__":
    unittest.main()
