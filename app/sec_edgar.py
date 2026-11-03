"""Small cached SEC EDGAR submissions client. This is enrichment only, never a radar gate."""
import asyncio
import time
from datetime import datetime, timezone

import httpx

from .config import settings

_TICKERS = {}
_TICKERS_AT = 0.0
_CACHE = {}
_LOCK = asyncio.Lock()


def _headers():
    email = str(getattr(settings, "sec_contact_email", "") or "").strip()
    return {
        "Accept": "application/json",
        "User-Agent": f"SAS PRO research contact {email}",
    }


async def sec_company_intelligence(symbol: str):
    symbol = str(symbol or "").strip().upper()
    email = str(getattr(settings, "sec_contact_email", "") or "").strip()
    if not symbol or not email:
        return {"available": False, "status": "disabled", "filings": []}

    cached = _CACHE.get(symbol)
    if cached and time.monotonic() - cached[0] < 3600:
        return dict(cached[1])

    try:
        global _TICKERS, _TICKERS_AT
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
            if not _TICKERS or time.monotonic() - _TICKERS_AT > 86400:
                async with _LOCK:
                    if not _TICKERS or time.monotonic() - _TICKERS_AT > 86400:
                        response = await client.get(
                            "https://www.sec.gov/files/company_tickers.json",
                            headers=_headers(),
                        )
                        response.raise_for_status()
                        raw = response.json()
                        _TICKERS = {
                            str(item["ticker"]).upper(): int(item["cik_str"])
                            for item in raw.values()
                            if isinstance(item, dict) and item.get("ticker") and item.get("cik_str")
                        }
                        _TICKERS_AT = time.monotonic()

            cik = _TICKERS.get(symbol)
            if not cik:
                return {"available": False, "status": "not_found", "symbol": symbol, "filings": []}

            response = await client.get(
                f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                headers=_headers(),
            )
            response.raise_for_status()
            recent = (response.json().get("filings") or {}).get("recent") or {}

        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        allowed = {"4", "8-K", "10-Q", "10-K", "S-1", "S-3", "424B5", "13D", "13G"}
        filings = []
        for index, form in enumerate(forms):
            if form not in allowed or index >= len(dates):
                continue
            accession = str(accessions[index] if index < len(accessions) else "").replace("-", "")
            document = documents[index] if index < len(documents) else ""
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
                if accession and document else ""
            )
            filings.append({"form": form, "filed": dates[index], "url": url})
            if len(filings) == 10:
                break

        result = {
            "available": True,
            "status": "ok",
            "symbol": symbol,
            "cik": f"{cik:010d}",
            "filings": filings,
            "source": "SEC EDGAR",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interpretation": "Form type alone does not prove insider selling or dilution.",
        }
        _CACHE[symbol] = (time.monotonic(), result)
        return result
    except Exception as exc:
        return {
            "available": False,
            "status": "error",
            "symbol": symbol,
            "filings": [],
            "reason": type(exc).__name__,
        }
