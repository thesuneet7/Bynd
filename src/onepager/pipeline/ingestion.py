"""Stage 3 — Ingestion & Indexing.

Fetches candidates (PDFs via LlamaParse, web pages via Firecrawl), registers a
Source for each, and indexes content into the per-company EvidenceStore with full
provenance (source_id + locator) so every retrieved chunk knows where it came from.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import SETTINGS
from ..schemas import Source, SourceType
from ..tools.pdf import ParsedPage, PdfParser
from ..tools.retrieval import relevance_score
from ..tools.scrape import Scraper
from .context import RunContext
from .discovery import Candidate

_PDF_TYPES = {SourceType.annual_report, SourceType.investor_presentation}
_PAYWALL_DOMAINS = {"tofler.in", "zaubacorp.com", "thecompanycheck.com"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _select_relevant_pages(pages: list[ParsedPage], max_keep: int = 35) -> list[ParsedPage]:
    """Per-page relevance triage (cheap, free) so we only embed the
    information-rich pages of a large filing — the financial statements, MD&A,
    business/products/clients sections — and skip notices, governance, and legal
    boilerplate. This keeps embedding fast and retrieval precise.
    """
    if len(pages) <= max_keep:
        return pages
    scored = [(relevance_score(p.markdown), p) for p in pages]
    kept = [p for s, p in sorted(scored, key=lambda x: -x[0])[:max_keep] if s > 0]
    kept.sort(key=lambda p: p.page)  # restore reading order
    return kept or [p for _, p in sorted(scored, key=lambda x: -x[0])[:max_keep]]


def ingest(ctx: RunContext, candidates: list[Candidate], *, max_pdfs: int = 1) -> dict[str, int]:
    ctx.note("Ingesting sources...")
    scraper = Scraper()
    pdf_parser = PdfParser()
    pdfs_done = 0
    firecrawl_done = 0
    web_done = 0
    skipped_existing = 0
    known_urls = {s.url for s in ctx.sources.values()}

    for cand in candidates:
        if cand.url in known_urls:
            skipped_existing += 1
            continue
        is_pdf = cand.source_type in _PDF_TYPES or cand.url.lower().endswith(".pdf")

        if is_pdf:
            if pdfs_done >= max_pdfs:
                continue
            pages = pdf_parser.parse_url(cand.url)
            if not pages:
                ctx.note(f"  (pdf parse empty/failed) {cand.url}")
                continue
            pdfs_done += 1
            sid = ctx.new_source_id()
            src = Source(
                id=sid, url=cand.url, title=cand.title or "Filing/Report",
                source_type=cand.source_type if cand.source_type in _PDF_TYPES else SourceType.annual_report,
                retrieved_at=_now(), access="public",
            )
            ctx.register_source(src)
            known_urls.add(cand.url)
            kept = _select_relevant_pages(pages)
            chunks = 0
            for pg in kept:
                chunks += ctx.store.add(pg.markdown, source_id=sid, locator={"page": pg.page, "doc": src.title})
            ctx.note(f"  [PDF {src.id}] parsed {len(pages)}p, kept {len(kept)} relevant, "
                     f"{chunks} chunks <- {cand.url}")
        else:
            # Local httpx / disk cache scrapes are always allowed. Only paid
            # Firecrawl calls count against MAX_FIRECRAWL_SCRAPES.
            res = scraper.scrape(cand.url)
            if not res.ok or not res.markdown.strip():
                continue
            if res.via == "firecrawl":
                if firecrawl_done >= SETTINGS.max_firecrawl_scrapes:
                    continue
                firecrawl_done += 1
            web_done += 1
            from urllib.parse import urlparse

            dom = urlparse(cand.url).netloc.lower().replace("www.", "")
            access = "paywalled" if dom in _PAYWALL_DOMAINS else "public"
            sid = ctx.new_source_id()
            src = Source(
                id=sid, url=cand.url, title=res.title or cand.title or dom,
                publisher=dom, source_type=cand.source_type,
                publication_date=res.published, retrieved_at=_now(), access=access,
            )
            ctx.register_source(src)
            known_urls.add(cand.url)
            n = ctx.store.add(res.markdown, source_id=sid, locator={"url": cand.url})
            ctx.note(f"  [WEB {src.id}] {n} chunks ({res.via}) <- {cand.url}")

    ctx.note(f"Indexed {len(ctx.store.chunks)} chunks from {len(ctx.sources)} sources "
             f"({pdfs_done} PDFs, {web_done} web pages, {firecrawl_done} via Firecrawl)")
    return {
        "pdfs": pdfs_done,
        "web": web_done,
        "firecrawl": firecrawl_done,
        "skipped_existing": skipped_existing,
    }
