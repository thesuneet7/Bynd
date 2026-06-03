"""Recursive research planner.

The planner turns broad one-pager sections into targeted web-search playbooks,
ingests the best new URLs, embeds the extracted text, then checks whether the
local evidence store has enough coverage to support claim drafting.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ..budget import BUDGET
from ..schemas import Gap, Section, SourceType
from ..tools.search import Searcher
from .context import RunContext
from .discovery import Candidate, _classify
from .ingestion import ingest


@dataclass(frozen=True)
class ResearchNeed:
    section: Section
    description: str
    queries: list[str]
    retrieval_queries: list[str]
    min_chunks: int
    min_sources: int


@dataclass
class Coverage:
    chunks: int
    sources: int
    source_types: set[str]

    @property
    def ok(self) -> bool:
        return self.chunks > 0 and self.sources > 0


def _company_name(ctx: RunContext) -> str:
    return ctx.entity.canonical_name if ctx.entity else ctx.input_name


def _site_domain(ctx: RunContext) -> str:
    website = ctx.entity.website if ctx.entity else None
    if not website:
        return ""
    return urlparse(website).netloc.lower().replace("www.", "")


def _needs(ctx: RunContext) -> list[ResearchNeed]:
    name = _company_name(ctx)
    site = _site_domain(ctx)
    site_prefix = f"site:{site} " if site else ""

    return [
        ResearchNeed(
            section=Section.financials,
            description="recent reported financials, annual reports, and investor presentations",
            queries=[
                f"{name} latest annual report pdf FY25 FY24",
                f"{name} investor presentation pdf financial highlights FY25 FY24",
                f"{name} revenue from operations EBITDA PAT annual report",
                f"{name} stock exchange annual report pdf",
                f"{name} screener financial results revenue profit",
                f"site:bseindia.com {name} annual report pdf",
                f"site:nseindia.com {name} annual report pdf",
            ],
            retrieval_queries=[
                f"{name} revenue from operations total income",
                f"{name} financial highlights FY25 FY24 FY23",
                f"{name} EBITDA operating profit profit after tax",
                f"{name} balance sheet borrowings equity",
            ],
            min_chunks=6,
            min_sources=1,
        ),
        ResearchNeed(
            section=Section.products,
            description="exact product portfolio and named products/components",
            queries=[
                f"{name} official products product portfolio",
                f"{site_prefix}{name} products product portfolio components",
                f"{name} product catalogue pdf",
                f"{name} investor presentation products segments",
                f"{name} annual report product portfolio",
                f"{name} press release product launch",
                f"{name} manufacturing products components",
            ],
            retrieval_queries=[
                f"{name} products manufactured product portfolio",
                f"{name} product catalogue components offerings",
                f"{name} product launch manufacturing",
            ],
            min_chunks=5,
            min_sources=2,
        ),
        ResearchNeed(
            section=Section.clients,
            description="named clients/customers/OEMs with relationship evidence",
            queries=[
                f"{name} customers clients OEMs supplies to",
                f"{name} key customers client list",
                f"{name} customer success stories case studies",
                f"{site_prefix}{name} customers clients case studies",
                f"{name} annual report customers OEM",
                f"{name} investor presentation customers OEM clients",
                f"{name} press release supplies to customer",
                f"{name} marquee customers exports clients",
            ],
            retrieval_queries=[
                f"{name} key customers clients OEMs supplies to",
                f"{name} named customers relationship",
                f"{name} customer success case study",
            ],
            min_chunks=4,
            min_sources=2,
        ),
    ]


def _coverage(ctx: RunContext, need: ResearchNeed) -> Coverage:
    chunks = ctx.store.search_multi(need.retrieval_queries, k_each=5, k_total=12)
    source_ids = {c.source_id for c in chunks}
    source_types = {
        ctx.sources[sid].source_type.value for sid in source_ids if sid in ctx.sources
    }
    return Coverage(chunks=len(chunks), sources=len(source_ids), source_types=source_types)


def _is_satisfied(need: ResearchNeed, cov: Coverage) -> bool:
    if cov.chunks < need.min_chunks or cov.sources < need.min_sources:
        return False
    if need.section == Section.financials:
        return bool(
            {SourceType.annual_report.value, SourceType.investor_presentation.value}
            & cov.source_types
        )
    return True


def _candidate_priority(query: str, candidate: Candidate) -> int:
    low = f"{query} {candidate.title} {candidate.snippet} {candidate.url}".lower()
    priority = candidate.priority
    if any(k in low for k in ("annual report", "integrated report", "investor presentation")):
        priority -= 2
    if any(k in low for k in ("products", "product portfolio", "catalogue", "customers", "clients", "case stud")):
        priority -= 1
    if any(k in low for k in ("linkedin.com", "facebook.com", "instagram.com", "youtube.com")):
        priority += 4
    return priority


def _search_for_need(ctx: RunContext, need: ResearchNeed, *, round_idx: int) -> list[Candidate]:
    entity_site = ctx.entity.website if ctx.entity else None
    searcher = Searcher()
    seen: dict[str, Candidate] = {}
    existing_urls = {s.url for s in ctx.sources.values()}

    # Earlier rounds use the highest-signal queries; later rounds broaden.
    start = round_idx * 2
    queries = need.queries[start : start + 3] or need.queries[-2:]
    for query in queries:
        if query in ctx.searched_queries:
            continue
        ctx.searched_queries.append(query)
        depth = "advanced" if any(k in query.lower() for k in ("annual report", "investor", "customers")) else "basic"
        hits = searcher.search(query, max_results=5, depth=depth)
        ctx.research_trace.append(
            {
                "section": need.section.value,
                "query": query,
                "hits": len(hits),
                "providers": sorted({h.provider for h in hits if h.provider}),
            }
        )
        for hit in hits:
            if not hit.url or hit.url in seen or hit.url in existing_urls:
                continue
            stype, prio = _classify(hit.url, entity_site)
            cand = Candidate(
                url=hit.url,
                title=hit.title,
                snippet=hit.content,
                source_type=stype,
                priority=prio,
            )
            seen[hit.url] = cand

    return sorted(seen.values(), key=lambda c: (_candidate_priority(" ".join(queries), c), -len(c.snippet)))[:8]


def recursive_research(ctx: RunContext, *, max_rounds: int = 3) -> dict:
    """Search, ingest, embed, and repeat until coverage targets are met.

    This is intentionally budget-aware and conservative: searches stop naturally
    when provider caps are exhausted, and claim-generation later still enforces
    exact evidence quotes plus independent verification.
    """
    ctx.note("Starting recursive evidence research loop...")
    needs = _needs(ctx)
    summary: dict[str, dict] = {}

    for round_idx in range(max_rounds):
        unmet: list[tuple[ResearchNeed, Coverage]] = []
        for need in needs:
            cov = _coverage(ctx, need)
            summary[need.section.value] = {
                "chunks": cov.chunks,
                "sources": cov.sources,
                "source_types": sorted(cov.source_types),
                "satisfied": _is_satisfied(need, cov),
            }
            if not _is_satisfied(need, cov):
                unmet.append((need, cov))

        if not unmet:
            ctx.note("Research loop reached coverage targets for financials/products/clients.")
            break
        if BUDGET.remaining("ddg") <= 0 and BUDGET.remaining("exa") <= 0 and BUDGET.remaining("tavily") <= 0:
            ctx.note("Research loop stopped: search budgets exhausted.")
            break

        ctx.note(
            "Research round "
            f"{round_idx + 1}: expanding {', '.join(n.section.value for n, _ in unmet)} evidence."
        )
        round_candidates: list[Candidate] = []
        for need, _ in unmet:
            round_candidates.extend(_search_for_need(ctx, need, round_idx=round_idx))

        deduped = {c.url: c for c in round_candidates}
        candidates = sorted(deduped.values(), key=lambda c: (c.priority, -len(c.snippet)))[:14]
        if not candidates:
            ctx.note("Research round found no new candidate URLs.")
            continue
        ingest(ctx, candidates, max_pdfs=2)
        if ctx.store.chunks:
            ctx.store.warm_index()

    for need in needs:
        cov = _coverage(ctx, need)
        ok = _is_satisfied(need, cov)
        summary[need.section.value] = {
            "chunks": cov.chunks,
            "sources": cov.sources,
            "source_types": sorted(cov.source_types),
            "satisfied": ok,
        }
        if not ok:
            searched = [q for q in ctx.searched_queries if _company_name(ctx) in q]
            ctx.gaps.append(
                Gap(
                    section=need.section,
                    description=f"Insufficient evidence coverage for {need.description}.",
                    searched=searched[-10:],
                    reason=(
                        f"Only {cov.chunks} relevant chunks from {cov.sources} source(s) "
                        f"after recursive search; final claims will omit unsupported facts."
                    ),
                )
            )

    ctx.note(
        "Research coverage: "
        + ", ".join(
            f"{section}={data['chunks']} chunks/{data['sources']} sources"
            for section, data in summary.items()
        )
    )
    return summary
