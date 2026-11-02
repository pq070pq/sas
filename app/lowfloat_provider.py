"""LowFloat.com universe adapter.

This is discovery metadata only: it never supplies prices, volume, or trade
signals. It is intentionally opt-in and is not wired into either publisher.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

import httpx

from .config import settings

logger = logging.getLogger(__name__)
LOWFLOAT_URL = "https://www.lowfloat.com/"
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}
MAX_FLOAT_SHARES = 10_000_000


def _number(value: str) -> float | None:
    text = str(value or "").strip().upper().replace(",", "").replace("$", "").replace("%", "")
    match = re.fullmatch(r"([0-9]*\\.?[0-9]+)\\s*([KMB]?)", text)
    if not match:
        return None
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2)]
    return float(match.group(1)) * scale


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def parse_lowfloat_html(html: str) -> list[dict]:
    """Parse the published ticker/company/exchange/float/short-interest table."""
    parser = _TableParser()
    parser.feed(html or "")
    output = []
    seen = set()
    for cells in parser.rows:
        if len(cells) < 7:
            continue
        symbol, company, exchange, float_text, outstanding_text, short_text, industry = cells[:7]
        symbol = symbol.strip().upper()
        exchange = exchange.strip().upper()
        float_shares = _number(float_text)
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol)
            or symbol in {"TICKER"}
            or symbol in seen
            or exchange not in ALLOWED_EXCHANGES
            or float_shares is None
            or not (0 < float_shares < MAX_FLOAT_SHARES)
        ):
            continue
        seen.add(symbol)
        output.append({
            "symbol": symbol,
            "name": company.strip() or symbol,
            "exchange": exchange,
            "float_shares": float_shares,
            "shares_outstanding": _number(outstanding_text),
            "short_interest_pct": _number(short_text),
            "industry": industry.strip(),
            "source": "LowFloat.com",
            "source_url": LOWFLOAT_URL,
            "data_role": "discovery_metadata_only",
        })
    return output


async def discover_lowfloat_universe(client: httpx.AsyncClient | None = None) -> list[dict]:
    """Fetch LowFloat metadata when explicitly enabled; fail closed on errors."""
    if not bool(getattr(settings, "lowfloat_enabled", False)):
        return []
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=max(3, int(getattr(settings, "lowfloat_timeout_seconds", 12))),
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 SAS-PRO low-float-universe/1.0"},
    )
    try:
        response = await active_client.get(LOWFLOAT_URL)
        response.raise_for_status()
        rows = parse_lowfloat_html(response.text)
        logger.info("LOWFLOAT_DISCOVERY_OK rows=%s", len(rows))
        return rows
    except Exception as exc:
        logger.warning("LOWFLOAT_DISCOVERY_FAILED error=%s", type(exc).__name__)
        return []
    finally:
        if owns_client:
            await active_client.aclose()
