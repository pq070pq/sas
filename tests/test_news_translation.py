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
            for key, value in {
                "gemini_api_key": "", "gemini_base_url": "https://example.com/v1",
                "gemini_model": "test", "groq_api_key": "", "groq_base_url": "https://example.com/v1",
                "groq_model": "test", "openrouter_api_key": "", "openrouter_base_url": "https://example.com/v1",
                "openrouter_model": "test",
            }.items():
                setattr(cfg, key, value)
            self.assertEqual(await translate_stock_news("ABC", rows), [])


if __name__ == "__main__":
    unittest.main()
