import unittest

from app.ai_radar import _safe_http_url, _safe_news, _validate
from app.private_analysis import _format_news


class NewsSummaryValidationTests(unittest.TestCase):
    def setUp(self):
        self.news = [{
            "id": "N1",
            "headline": "الشركة تعلن نتائجها الفصلية",
            "source": "مصدر موثوق",
            "url": "https://news.example.com/story/1",
            "summary": "مقتطف موثق عن النتائج.",
        }]

    def test_summary_uses_trusted_source_metadata(self):
        result = _validate({
            "primary_source_id": "N1",
            "news_summaries": [{
                "source_id": "N1",
                "summary": "أعلنت الشركة نتائجها الفصلية.",
                "headline": "عنوان مزيف",
                "source": "مصدر مزيف",
                "url": "https://attacker.example/",
            }],
        }, self.news, {})
        summary = result["news_summaries"][0]
        self.assertEqual(summary["headline"], self.news[0]["headline"])
        self.assertEqual(summary["source"], self.news[0]["source"])
        self.assertEqual(summary["url"], self.news[0]["url"])
        self.assertEqual(summary["basis"], "source_excerpt")

    def test_invalid_and_duplicate_source_ids_are_dropped(self):
        result = _validate({
            "primary_source_id": "N1",
            "news_summaries": [
                {"source_id": "N9", "summary": "خبر غير موثق"},
                {"source_id": "N1", "summary": "الملخص الأول"},
                {"source_id": "N1", "summary": "ملخص مكرر"},
            ],
        }, self.news, {})
        self.assertEqual([item["summary"] for item in result["news_summaries"]], ["الملخص الأول"])

    def test_price_claim_is_not_published_in_news_summary(self):
        result = _validate({
            "primary_source_id": "N1",
            "news_summaries": [{"source_id": "N1", "summary": "سعر السهم 22 دولار"}],
        }, self.news, {})
        self.assertEqual(result["news_summaries"], [])

    def test_headline_only_summary_is_labeled(self):
        news = [{**self.news[0], "summary": ""}]
        result = _validate({
            "primary_source_id": "N1",
            "news_summaries": [{"source_id": "N1", "summary": "أعلنت الشركة نتائجها."}],
        }, news, {})
        self.assertEqual(result["news_summaries"][0]["basis"], "headline_only")

    def test_only_absolute_http_links_are_accepted(self):
        self.assertEqual(_safe_http_url("https://example.com/news"), "https://example.com/news")
        self.assertEqual(_safe_http_url("http://example.com/news"), "http://example.com/news")
        for url in (
            "javascript:alert(1)",
            "//example.com/news",
            "https://user:pass@example.com/news",
            "https://example.com:invalid/news",
        ):
            self.assertEqual(_safe_http_url(url), "")

    def test_private_report_binds_summary_and_source_to_the_story(self):
        output = _format_news([{
            "headline": "نتائج الشركة",
            "source": "مصدر موثوق",
            "url": "https://news.example.com/story",
            "summary": "مقتطف من المصدر",
        }], {
            "news_summaries": [{
                "headline": "نتائج الشركة",
                "source": "مصدر مزيف",
                "url": "https://news.example.com/story",
                "summary": "ملخص موثق",
                "basis": "source_excerpt",
            }],
        })
        self.assertIn("مختصر AI", output)
        self.assertIn("ملخص موثق", output)
        self.assertIn('href="https://news.example.com/story"', output)
        self.assertNotIn("attacker.example", output)

    def test_private_report_does_not_link_unsafe_news_urls(self):
        output = _format_news([{
            "headline": "نتائج الشركة",
            "source": "مصدر",
            "url": "javascript:alert(1)",
        }])
        self.assertNotIn("href=", output)
        self.assertIn("نتائج الشركة", output)

    def test_unsafe_news_urls_are_excluded_from_ai_evidence(self):
        rows = [
            {"headline": "خبر", "source": "مصدر", "url": "javascript:alert(1)"},
            {"headline": "خبر موثق", "source": "مصدر", "url": "https://example.com/news"},
        ]
        result = _safe_news(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://example.com/news")


if __name__ == "__main__":
    unittest.main()
