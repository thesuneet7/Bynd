"""BSE annual reports and major investor presentation filings."""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from ..models import DocCategory, DocSource, DocumentRef
from ..relevance import is_major_investor_presentation
from ..years import YearWindow, bse_from_date, bse_to_date

_BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
}


def _client() -> httpx.Client:
    c = httpx.Client(headers=_BSE_HEADERS, follow_redirects=True, timeout=45)
    c.get("https://www.bseindia.com/")
    return c


def _clean_bse_filename(raw: str) -> str:
    s = (raw or "").strip().replace("\\", "")
    return re.sub(r"\.pdf\.pdf$", ".pdf", s, flags=re.I)


def _bse_annual_url(scrip: str, file_name: str) -> str:
    clean = _clean_bse_filename(file_name)
    if re.fullmatch(r"\d+\.pdf", clean, re.I):
        return f"https://www.bseindia.com/bseplus/AnnualReport/{scrip}/{clean}"
    uuid = clean.replace(".pdf", "")
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{uuid}.pdf"


def _bse_attach_url(file_name: str) -> str:
    clean = _clean_bse_filename(file_name)
    if clean.startswith("http"):
        return clean
    base = clean.replace(".pdf", "")
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{base}.pdf"


def _presentation_attachments(row: dict) -> list[str]:
    """BSE may put the actual deck in Investor_Presentation (space-separated PDFs)."""
    names: list[str] = []
    inv = str(row.get("Investor_Presentation") or "").strip()
    for part in inv.split():
        if part.lower().endswith(".pdf"):
            names.append(part)
    primary = (row.get("ATTACHMENTNAME") or "").strip()
    subj = (row.get("NEWSSUB") or "").lower()
    if primary and ("investor presentation" in subj or "earnings update" in subj):
        if primary not in names:
            names.insert(0, primary)
    return names


def fetch_bse_documents(scrip: str, window: YearWindow) -> list[DocumentRef]:
    c = _client()
    try:
        refs: list[DocumentRef] = []
        refs.extend(_annual_reports(c, scrip, window))
        refs.extend(_presentations(c, scrip, window))
        return refs
    finally:
        c.close()


def _annual_reports(c: httpx.Client, scrip: str, window: YearWindow) -> list[DocumentRef]:
    out: list[DocumentRef] = []
    r = c.get(f"https://api.bseindia.com/BseIndiaAPI/api/AnnualReport/w?scripcode={scrip}&flag=0")
    r.raise_for_status()
    rows = r.json().get("Table", [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        year = row.get("year")
        if not window.contains_report_year(year):
            continue
        file_name = row.get("file_name") or ""
        url = _bse_annual_url(scrip, file_name)
        published = None
        raw_dt = row.get("dt_tm")
        if raw_dt:
            try:
                published = datetime.fromisoformat(str(raw_dt).split(".")[0]).date().isoformat()
            except ValueError:
                published = None
        out.append(
            DocumentRef(
                title=f"BSE Annual Report {year}",
                url=url,
                category=DocCategory.annual_report,
                source=DocSource.bse,
                report_year=int(year) if year else None,
                fy_label=f"FY{int(year) % 100:02d}" if year else None,
                published=published,
                meta={"file_name": file_name},
            )
        )
    return out


def _presentations(c: httpx.Client, scrip: str, window: YearWindow) -> list[DocumentRef]:
    out: list[DocumentRef] = []
    seen: set[str] = set()
    for page in range(1, 8):
        r = c.get(
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
            params={
                "pageno": page,
                "strCat": -1,
                "strPrevDate": bse_from_date(window),
                "strScrip": scrip,
                "strSearch": "P",
                "strToDate": bse_to_date(window),
                "strType": "C",
            },
        )
        if r.status_code != 200:
            break
        rows = r.json().get("Table", [])
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = (row.get("NEWSSUB") or row.get("HEADLINE") or "BSE announcement").strip()
            subcat = str(row.get("SUBCATNAME") or "")
            published = None
            raw_dt = row.get("NEWS_DT") or row.get("DT_TM")
            if raw_dt:
                try:
                    published = datetime.fromisoformat(str(raw_dt).split(".")[0]).date().isoformat()
                except ValueError:
                    published = None
            for attach in _presentation_attachments(row):
                url = _bse_attach_url(attach)
                if url in seen:
                    continue
                ref = DocumentRef(
                    title=title[:200],
                    url=url,
                    category=DocCategory.investor_presentation,
                    source=DocSource.bse,
                    published=published,
                    meta={
                        "attachment": attach,
                        "investor_presentation": row.get("Investor_Presentation"),
                        "subcat": subcat,
                        "headline": row.get("HEADLINE"),
                    },
                )
                if is_major_investor_presentation(ref):
                    seen.add(url)
                    out.append(ref)
    return out


def bse_download_headers() -> dict[str, str]:
    return dict(_BSE_HEADERS)
