import unittest

from app.news import _is_relevant_stock_market_news


class MarketNewsRelevanceTests(unittest.TestCase):
    def test_related_ticker_allows_company_event_without_generic_finance_term(self):
        row = {
            "headline": "AAPL announces a new product",
            "related": "AAPL",
        }
        self.assertTrue(_is_relevant_stock_market_news(row))

    def test_unrelated_sports_story_is_rejected(self):
        row = {
            "headline": "Football club announces record deal after matchday",
            "summary": "A major transfer window story.",
        }
        self.assertFalse(_is_relevant_stock_market_news(row))

    def test_financial_headline_with_whitespace_is_normalized(self):
        row = {
            "headline": "Company reports\nquarterly earnings",
        }
        self.assertTrue(_is_relevant_stock_market_news(row))


if __name__ == "__main__":
    unittest.main()
