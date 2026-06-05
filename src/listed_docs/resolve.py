"""Resolve NSE ticker to exchange identifiers and company website."""
from __future__ import annotations

import re

import httpx
from selectolax.parser import HTMLParser

from onepager.financials.screener import fetch_screener_html, normalize_ticker
from onepager.financials.screener_session import get_screener_client

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def resolve_listed_company(
    *,
    name: str,
    ticker: str | None = None,
    hint: str | None = None,
) -> tuple[str, str | None, str | None]:
    """Return (nse_symbol, bse_scrip_code, website)."""
    sym = normalize_ticker(ticker, hint=hint, name=name)
    if not sym:
        raise ValueError("Could not parse NSE/BSE ticker — pass --ticker BHARATFORG")

    session = get_screener_client()
    html, _, _ = fetch_screener_html(sym, client=session)
    tree = HTMLParser(html)

    bse_scrip: str | None = None
    website: str | None = None
    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        if "bseindia.com/stock-share-price" in href:
            m = re.search(r"/(\d{5,6})/?", href)
            if m:
                bse_scrip = m.group(1)
        if href.startswith("http") and "screener.in" not in href and "bseindia" not in href and "nseindia" not in href:
            if website is None and any(href.endswith(tld) for tld in (".com", ".in", ".co.in")):
                if "/assets/" not in href and "/investor" not in href.lower():
                    website = href.rstrip("/")

    links = tree.css(".company-links a")
    for a in links:
        href = (a.attributes.get("href") or "").strip()
        text = a.text(strip=True).lower()
        if text == "website" and href.startswith("http"):
            website = href.rstrip("/")

    if not bse_scrip:
        bse_scrip = _bse_scrip_from_api(sym)

    return sym, bse_scrip, website


def _bse_scrip_from_api(nse_symbol: str) -> str | None:
    """Lookup BSE scrip code via BSE equity search API."""
    headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.bseindia.com/"}
    try:
        with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as c:
            c.get("https://www.bseindia.com/")
            r = c.get(
                "https://api.bseindia.com/BseIndiaAPI/api/Suggest/GetData/",
                params={"Type": "EQ", "text": nse_symbol},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            rows = data if isinstance(data, list) else data.get("Table", [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sym = str(row.get("symbol") or row.get("SYMBOL") or "").upper()
                if sym == nse_symbol.upper():
                    code = row.get("scrip_cd") or row.get("SCRIP_CD") or row.get("scripcode")
                    if code:
                        return str(code)
    except Exception:
        return None
    return None
