"""Stage 2 — Source Strategy & Discovery.

Branches on data-richness tier. Listed companies => chase annual reports /
investor decks / financial press. Unlisted => company site, registries
(MCA/Tofler-style), parent-group disclosures, trade press. Records what it
searched for so honest NOT_FOUND gaps can cite the attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ..schemas import SourceType
from ..tools.search import Searcher
from .context import RunContext


@dataclass
class Candidate:
    url: str
    title: str
    snippet: str
    source_type: SourceType
    priority: int  # lower = more important


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


_NEWS_DOMAINS = {
    "economictimes.indiatimes.com", "business-standard.com", "livemint.com",
    "moneycontrol.com", "thehindubusinessline.com", "reuters.com", "bloomberg.com",
    "financialexpress.com", "business-standard.com", "cnbctv18.com",
}
_REGISTRY_DOMAINS = {"tofler.in", "zaubacorp.com", "thecompanycheck.com", "mca.gov.in", "screener.in"}


def _classify(url: str, entity_site: str | None) -> tuple[SourceType, int]:
    d = _domain(url)
    low = url.lower()
    is_pdf = low.endswith(".pdf") or "filetype=pdf" in low
    site_d = _domain(entity_site or "")
    if is_pdf and any(k in low for k in ("annual", "ar-", "annualreport", "integrated-report")):
        # Prefer the latest public annual reports when search returns several years.
        if any(k in low for k in ("24-25", "2025", "fy25", "ar51")):
            return SourceType.annual_report, -2
        if any(k in low for k in ("23-24", "2024", "fy24")):
            return SourceType.annual_report, -1
        return SourceType.annual_report, 0
    if is_pdf and any(k in low for k in ("investor", "presentation", "earnings", "investor-deck")):
        return SourceType.investor_presentation, 1
    if site_d and d == site_d:
        return SourceType.company_website, 2
    if d in _REGISTRY_DOMAINS:
        return SourceType.regulatory_filing, 1
    if d in _NEWS_DOMAINS:
        return SourceType.news, 3
    if is_pdf:
        return SourceType.annual_report, 1
    return SourceType.other, 4


def _matches_entity(ctx: RunContext, url: str, title: str = "", snippet: str = "") -> bool:
    """Avoid polluting a run with similarly named registry pages."""
    e = ctx.entity
    if not e:
        return True
    combined = f"{url} {title} {snippet}".lower()
    if e.registry_id and _domain(url) in _REGISTRY_DOMAINS:
        return e.registry_id.lower() in combined
    return True


def _queries(ctx: RunContext) -> list[tuple[str, int]]:
    e = ctx.entity
    name = e.canonical_name if e else ctx.input_name
    tier = (e.data_richness_tier if e else "unknown") or "unknown"
    if tier == "rich":
        return [
            (f"{name} latest annual report pdf FY25 FY24", 4),
            (f"{name} products customers investor presentation", 3),
        ]
    if tier == "sparse":
        return [
            (f"{name} official products customers", 4),
            (f"{name} annual report financials MCA Tofler", 3),
        ]
    return [
        (f"{name} official website annual report pdf", 4),
        (f"{name} products customers financials", 4),
    ]


def _seed_candidates(ctx: RunContext) -> dict[str, Candidate]:
    """Known high-value sources for the fixed assignment companies.

    These avoid spending search credits on URLs we already know how to reach.
    They are routing hints only; claims still require retrieved evidence.
    """
    e = ctx.entity
    name = (e.canonical_name if e else ctx.input_name).lower()
    seeds: dict[str, Candidate] = {}
    if "bharat forge" in name:
        # Older seeded AR as backup; DDG search should surface newer FY24/25 PDFs first.
        url = "https://www.bharatforge.com/assets/pdf/investors/annualReport/AR51.pdf"
        seeds[url] = Candidate(url=url, title="Bharat Forge Annual Report (archive)", snippet="",
                               source_type=SourceType.annual_report, priority=1)
        for url in (
            "https://www.bharatforge.com",
            "https://www.bharatforge.com/company/about-us",
        ):
            seeds[url] = Candidate(url=url, title="Bharat Forge official website", snippet="",
                                   source_type=SourceType.company_website, priority=1)
    elif "brakes india" in name:
        for url in (
            "https://www.brakesindia.com",
            "https://www.brakesindia.com/products/",
        ):
            seeds[url] = Candidate(url=url, title="Brakes India official website", snippet="",
                                   source_type=SourceType.company_website, priority=1)
    elif e and e.website:
        seeds[e.website] = Candidate(url=e.website, title=f"{e.canonical_name} website", snippet="",
                                     source_type=SourceType.company_website, priority=2)
    return seeds


def discover_sources(ctx: RunContext) -> list[Candidate]:
    ctx.note("Discovering candidate sources...")
    seen: dict[str, Candidate] = _seed_candidates(ctx)
    entity_site = ctx.entity.website if ctx.entity else None

    searcher = Searcher()
    # Always run a small number of free DDG queries (seeds + search) to find newer filings.
    for query, k in _queries(ctx):
        ctx.searched_queries.append(query)
        depth = "advanced" if "annual report" in query else "basic"
        hits = searcher.search(query, max_results=k, depth=depth)
        for h in hits:
            if not h.url or h.url in seen:
                continue
            if not _matches_entity(ctx, h.url, h.title, h.content):
                continue
            stype, prio = _classify(h.url, entity_site)
            seen[h.url] = Candidate(url=h.url, title=h.title, snippet=h.content, source_type=stype, priority=prio)

    # Always include the official site root if known.
    if entity_site and entity_site not in seen:
        seen[entity_site] = Candidate(
            url=entity_site, title=f"{ctx.entity.canonical_name} website", snippet="",
            source_type=SourceType.company_website, priority=2,
        )

    candidates = sorted(seen.values(), key=lambda c: (c.priority, -len(c.snippet)))
    ctx.note(f"Found {len(candidates)} candidate sources "
             f"({sum(1 for c in candidates if c.source_type==SourceType.annual_report)} annual-report PDFs)")
    return candidates
