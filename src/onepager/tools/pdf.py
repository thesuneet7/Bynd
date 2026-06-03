"""Annual-report / filing parsing via LlamaParse (layout-aware table extraction).

This is the make-or-break tool for the financial table. Annual reports can be
300-500 pages, and parsing all of them with LlamaParse is slow and wastes credits
on boilerplate. So we do a two-pass strategy:

  1. CHEAP local pass (pypdf): extract rough text per page and score each page for
     relevance (financial statements, MD&A, business/products/clients).
  2. API fallback (disabled by default): LlamaParse may be used later on a tiny
     subset of pages, but the strict default is 0 LlamaParse pages to protect
     credits.

Each page becomes a citable locator; results are cached on disk (re-runs are free).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from ..budget import BUDGET
from ..config import CACHE_DIR, SETTINGS

_PDF_CACHE = CACHE_DIR / "pdf"
_PDF_CACHE.mkdir(parents=True, exist_ok=True)
_PARSE_CACHE = CACHE_DIR / "parsed"
_PARSE_CACHE.mkdir(parents=True, exist_ok=True)


@dataclass
class ParsedPage:
    page: int
    markdown: str


def download_pdf(url: str) -> Optional[Path]:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    dest = _PDF_CACHE / f"{h}.pdf"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        with httpx.stream(
            "GET", url, timeout=120, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 onepager-agent"},
        ) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        # Reject obvious non-PDFs (HTML error pages saved as .pdf)
        head = dest.read_bytes()[:5]
        if head[:4] != b"%PDF" and "pdf" not in ctype.lower():
            dest.unlink(missing_ok=True)
            return None
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        return None


class PdfParser:
    def __init__(self) -> None:
        self._parser = None
        # LlamaParse is intentionally disabled when MAX_LLAMAPARSE_PAGES=0.
        # The default pipeline is local pypdf-only to avoid accidental credit burn.
        if SETTINGS.llamaparse_api_key and SETTINGS.max_llamaparse_pages > 0:
            try:
                from llama_cloud_services import LlamaParse

                self._parser = LlamaParse(
                    api_key=SETTINGS.llamaparse_api_key,
                    result_type="markdown",
                    split_by_page=True,
                )
            except Exception:
                self._parser = None

    def parse_url(self, url: str, *, max_pages: Optional[int] = None) -> list[ParsedPage]:
        cache_key = hashlib.sha1(url.encode()).hexdigest()[:16]
        cache_file = _PARSE_CACHE / f"{cache_key}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            return [ParsedPage(**p) for p in data]

        path = download_pdf(url)
        if not path:
            return []

        pages = _local_extract_pages(path)
        if pages:
            cache_file.write_text(json.dumps([p.__dict__ for p in pages]))
            return pages

        # Optional paid fallback. It is skipped by default because the configured
        # page budget is 0 in strict low-API mode.
        cap = max_pages if max_pages is not None else SETTINGS.max_llamaparse_pages
        if not self._parser or cap <= 0 or BUDGET.remaining("llamaparse_pages") <= 0:
            return []
        cap = min(cap, BUDGET.remaining("llamaparse_pages"))

        try:
            docs = self._parser.load_data(str(path))
        except Exception:
            return []

        pages = _docs_to_pages(docs, cap)
        BUDGET.charge("llamaparse_pages", amount=len(pages))
        cache_file.write_text(json.dumps([p.__dict__ for p in pages]))
        return pages


def _local_extract_pages(path: Path) -> list[ParsedPage]:
    """Free local PDF text extraction. It is less layout-aware than LlamaParse,
    but good enough to locate financial/business pages and often preserves enough
    numeric table text for grounded extraction.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages: list[ParsedPage] = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(ParsedPage(page=i, markdown=text))
        return pages
    except Exception:
        return []


def _docs_to_pages(docs: list, cap: int) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    for i, d in enumerate(docs):
        if i >= cap:
            break
        text = getattr(d, "text", "") or ""
        page_no = i + 1
        meta = getattr(d, "metadata", {}) or {}
        if isinstance(meta, dict) and meta.get("page_label"):
            try:
                page_no = int(str(meta["page_label"]))
            except (TypeError, ValueError):
                pass
        if text.strip():
            pages.append(ParsedPage(page=page_no, markdown=text))
    return pages
