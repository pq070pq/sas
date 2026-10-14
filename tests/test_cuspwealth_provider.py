import unittest

from app.cuspwealth_provider import parse_asset_page


class CuspWealthParserTests(unittest.TestCase):
    def test_extracts_published_price_and_fundamentals_without_inventing_change(self):
        document = """
        <html lang="ar">
          <head><title>سعر السهم والسعر اللحظي لـ Example Inc (XYZ) | CUSP Wealth</title>
          <meta name="description" content="Company profile from CUSP"></head>
          <body>
            <h1>Example Inc</h1>
            <p>XYZ</p>
            <p>مغلق</p>
            <p>12.34 US$</p>
            <p>مغلق</p>
            <p>-(-)</p>
            <p>عند الإغلاق</p>
            <h2>الإحصائيات الرئيسية</h2>
            <p>سعر الافتتاح</p><p>12.10 US$</p>
            <p>حجم التداول</p><p>1.25 مليون</p>
            <p>القيمة السوقية الإجمالية</p><p>540 مليون US$</p>
            <h2>التقييم</h2>
            <p>44</p>
            <p>نتيجة التقييم</p>
            <p>مقوّم بقيمة عادلة</p>
            <p>نسبة السعر إلى الأرباح</p><p>18.5</p>
            <p>متوسط السعر المستهدف</p><p>14.00 US$</p>
          </body>
        </html>
        """
        result = parse_asset_page("XYZ", document, "https://cuspwealth.com/ar/asset/XYZ")
        self.assertTrue(result["available"])
        self.assertEqual(result["price"], 12.34)
        self.assertIsNone(result["change_pct"])
        self.assertEqual(result["metrics"]["market_cap"], "540 مليون US$")
        self.assertEqual(result["metrics"]["valuation_score"], "44")
        self.assertEqual(result["metrics"]["valuation_label"], "مقوّم بقيمة عادلة")
        self.assertEqual(result["metrics"]["analyst_target"], "14.00 US$")

    def test_missing_price_remains_missing(self):
        result = parse_asset_page("ABC", "<html><head><title>ABC</title></head><body><h1>ABC</h1></body></html>", "https://cuspwealth.com/ar/asset/ABC")
        self.assertIsNone(result["price"])


if __name__ == "__main__":
    unittest.main()
