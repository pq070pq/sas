import unittest

from app.lowfloat_provider import parse_lowfloat_html


class LowFloatProviderTests(unittest.TestCase):
    def test_extracts_float_and_short_interest_without_market_quotes(self):
        html = """
        <table>
          <tr><th>Ticker</th><th>Company</th><th>Exchange</th><th>Float</th><th>Outstd</th><th>ShortInt</th><th>Industry</th></tr>
          <tr><td>ABCD</td><td>Example Corp</td><td>Nasdaq</td><td>2.5M</td><td>8.1M</td><td>12.4%</td><td>Technology</td></tr>
          <tr><td>OTCX</td><td>OTC Example</td><td>OTCBB</td><td>1.2M</td><td>2M</td><td>3%</td><td>Other</td></tr>
          <tr><td>BIGF</td><td>Big Float</td><td>NYSE</td><td>12M</td><td>20M</td><td>1%</td><td>Other</td></tr>
        </table>
        """
        rows = parse_lowfloat_html(html)
        self.assertEqual([row["symbol"] for row in rows], ["ABCD"])
        self.assertEqual(rows[0]["float_shares"], 2_500_000)
        self.assertEqual(rows[0]["short_interest_pct"], 12.4)
        self.assertEqual(rows[0]["data_role"], "discovery_metadata_only")
        self.assertNotIn("price", rows[0])
        self.assertNotIn("volume", rows[0])

    def test_deduplicates_symbols_and_rejects_invalid_tickers(self):
        html = """
        <tr><td>ABCD</td><td>First</td><td>NASDAQ</td><td>900K</td><td>1M</td><td>1%</td><td>Tech</td></tr>
        <tr><td>ABCD</td><td>Duplicate</td><td>NASDAQ</td><td>800K</td><td>1M</td><td>1%</td><td>Tech</td></tr>
        <tr><td>BAD!</td><td>Invalid</td><td>NASDAQ</td><td>900K</td><td>1M</td><td>1%</td><td>Tech</td></tr>
        """
        rows = parse_lowfloat_html(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "First")


if __name__ == "__main__":
    unittest.main()
