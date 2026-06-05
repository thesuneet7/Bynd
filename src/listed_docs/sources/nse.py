"""NSE annual reports and major investor presentation filings."""
from __future__ import annotations

from datetime import datetime

import httpx

from ..models import DocCategory, DocSource, DocumentRef
from ..relevance import is_major_investor_presentation
from ..years import YearWindow, nse_from_date, nse_to_date

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}


def _client() -> httpx.Client:
    c = httpx.Client(headers=_NSE_HEADERS, follow_redirects=True, timeout=45)
    c.get("https://www.nseindia.com/")
    return c


def _fy_label(from_yr: str, to_yr: str) -> str:
    try:
        return f"FY{int(to_yr) % 100:02d}"
    except ValueError:
        return f"{from_yr}-{to_yr}"


def _parse_nse_date(raw: str) -> str | None:
    if not raw or raw == "-":
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def fetch_nse_documents(symbol: str, window: YearWindow) -> list[DocumentRef]:
    c = _client()
    try:
        refs: list[DocumentRef] = []
        refs.extend(_annual_reports(c, symbol, window))
        refs.extend(_presentations(c, symbol, window))
        return refs
    finally:
        c.close()


def _annual_reports(c: httpx.Client, symbol: str, window: YearWindow) -> list[DocumentRef]:
    out: list[DocumentRef] = []
    r = c.get(f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={symbol}")
    r.raise_for_status()
    rows = r.json().get("data", [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        from_yr = str(row.get("fromYr", ""))
        to_yr = str(row.get("toYr", ""))
        if not window.contains_nse_span(from_yr, to_yr):
            continue
        url = (row.get("fileName") or "").strip()
        if not url:
            continue
        try:
            report_year = int(to_yr)
        except ValueError:
            report_year = None
        out.append(
            DocumentRef(
                title=f"NSE Annual Report {from_yr}-{to_yr}",
                url=url,
                category=DocCategory.annual_report,
                source=DocSource.nse,
                report_year=report_year,
                fy_label=_fy_label(from_yr, to_yr),
                published=_parse_nse_date(str(row.get("broadcast_dttm") or "")),
                meta={"fromYr": from_yr, "toYr": to_yr},
            )
        )
    return out


def _presentations(c: httpx.Client, symbol: str, window: YearWindow) -> list[DocumentRef]:
    out: list[DocumentRef] = []
    url = (
        "https://www.nseindia.com/api/corporate-announcements"
        f"?index=equities&symbol={symbol}&from_date={nse_from_date(window)}&to_date={nse_to_date(window)}"
    )
    r = c.get(url)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        attach = (row.get("attchmntFile") or "").strip()
        if not attach or not attach.lower().endswith(".pdf"):
            continue
        title = (row.get("desc") or row.get("attchmntText") or "NSE filing").strip()
        body = str(row.get("attchmntText") or "")
        ref = DocumentRef(
            title=title[:200],
            url=attach,
            category=DocCategory.investor_presentation,
            source=DocSource.nse,
            published=_parse_nse_date(str(row.get("an_dt") or row.get("sort_date") or "")),
            meta={"desc": row.get("desc"), "attchmntText": body},
        )
        if is_major_investor_presentation(ref):
            out.append(ref)
    return out


def nse_download_headers() -> dict[str, str]:
    return dict(_NSE_HEADERS)
