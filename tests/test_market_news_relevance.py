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

    def test_us_equity_financial_headline_with_whitespace_is_normalized(self):
        row = {
            "headline": "U.S. stock market company reports\nquarterly earnings",
        }
        self.assertTrue(_is_relevant_stock_market_news(row))

    def test_generic_global_finance_without_us_equity_context_is_rejected(self):
        row = {
            "headline": "Global investors react to inflation data",
        }
        self.assertFalse(_is_relevant_stock_market_news(row))


if __name__ == "__main__":
    unittest.main()
