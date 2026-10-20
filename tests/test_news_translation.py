import unittest
from unittest.mock import patch
from app.news_translation import translate_stock_news, _has_arabic, _normalize_news_url


class StockNewsTranslationTests(unittest.IsolatedAsyncioTestCase):
    def test_arabic_output_validation(self):
        self.assertTrue(_has_arabic("ارتفعت الأسهم الأمريكية"))
        self.assertFalse(_has_arabic("US stocks rose 4%"))

    def test_news_url_normalization(self):
        self.assertEqual(_normalize_news_url("HTTPS://Example.com/story/?utm_source=x"), "https://example.com/story")

    async def test_empty_news_returns_empty(self):
        self.assertEqual(await translate_stock_news("ABC", []), [])

    async def test_items_without_headline_are_ignored(self):
        self.assertEqual(await translate_stock_news("ABC", [{"url": "https://example.com"}]), [])

    async def test_without_provider_key_uses_arabic_fallback(self):
        rows = [{"headline": "Headline", "url": "https://example.com/1"}]
        expected = [{
            "url": "https://example.com/1",
            "headline": "Headline",
            "translated_headline": "العنوان",
            "summary": "",
            "basis": "headline_only",
            "source": "",
            "translation_provider": "MyMemory fallback",
        }]
        with patch("app.news_translation.settings") as cfg, patch(
            "app.news_translation._fallback_translate_items", return_value=expected
        ) as fallback:
            for key, value in {
                "gemini_api_key": "", "gemini_base_url": "https://example.com/v1",
                "gemini_model": "test", "groq_api_key": "", "groq_base_url": "https://example.com/v1",
                "groq_model": "test", "openrouter_api_key": "", "openrouter_base_url": "https://example.com/v1",
                "openrouter_model": "test",
            }.items():
                setattr(cfg, key, value)
            self.assertEqual(await translate_stock_news("ABC", rows), expected)
            fallback.assert_awaited_once_with(rows)


if __name__ == "__main__":
    unittest.main()
