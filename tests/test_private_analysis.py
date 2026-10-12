from app.private_analysis import _format_news


def test_format_news_empty_list_returns_safe_message():
    assert _format_news([]) == "• لا توجد أخبار موثقة حديثة من المصادر المتاحة."


def test_format_news_non_list_returns_safe_message():
    assert _format_news(None) == "• لا توجد أخبار موثقة حديثة من المصادر المتاحة."


def test_format_news_ignores_items_without_headline():
    assert _format_news([{"source": "Example"}]) == "• لا توجد أخبار موثقة حديثة من المصادر المتاحة."


def test_format_news_formats_headline():
    result = _format_news([{"headline": "Company reports results", "source": "Example"}])
    assert "Company reports results" in result
    assert "Example" in result
