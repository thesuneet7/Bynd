"""Section detection and canonical bucket classification.

Default: keyword heuristic over page text (high recall).
Optional: Claude Sonnet on page outlines (--section-mode claude).
"""
from __future__ import annotations

import re
from typing import Literal

from onepager.config import SETTINGS
from onepager.llm import claude

from .models import EXTRACTION_BUCKETS, EXTRACTION_TARGETS, CanonicalBucket, ParsedPageRecord, SectionRange

SectionMode = Literal["heuristic", "claude"]

_BUCKET_ALIASES: dict[CanonicalBucket, tuple[str, ...]] = {
    "Products": (
        "products",
        "product portfolio",
        "offerings",
        "solutions",
        "product range",
        "what we make",
        "manufacturing capabilities",
    ),
    "Customers": (
        "customers",
        "key accounts",
        "oem relationships",
        "client base",
        "clients",
        "key customers",
        "customer relationships",
    ),
    "Operations": (
        "business overview",
        "operations",
        "business segments",
        "segments",
        "about the company",
        "company overview",
        "our business",
        "industry overview",
    ),
    "Competitors": (
        "competition",
        "competitive landscape",
        "competitors",
        "peer comparison",
        "market share",
    ),
    "Risks": (
        "risk factors",
        "risks",
        "risk management",
        "principal risks",
        "risk and concerns",
    ),
}

_DETECT_SYS = """You analyze Indian corporate filings (annual reports, investor presentations).
Identify ALL major document sections, their page ranges, and a canonical bucket.
Return JSON array only:
[{"section": "Business Overview", "start_page": 10, "end_page": 18, "bucket": "Operations"}, ...]

Buckets (use exactly one per section):
- Products — product portfolio, offerings, solutions, manufacturing capabilities, what the company makes
- Customers — named customers, OEMs, key accounts, client relationships
- Operations — business overview, segments, industry/market review, geography/revenue breakdown, strategy
- Competitors — competition, competitive landscape, peers
- Risks — risk factors, principal risks
- Other — governance, financial statements, AGM notices, ESG boilerplate, legal, director reports

Rules:
- Use exact section titles when visible; otherwise infer a short descriptive title.
- start_page and end_page are 1-based inclusive integers.
- Cover the full document; sections may overlap slightly at boundaries.
- Investor deck slides reviewing India/export business, segments, or product lines → Operations (or Products if product-specific).
- Ignore page headers/footers as standalone sections."""

_VALID_BUCKETS = frozenset({*EXTRACTION_BUCKETS, "Other"})


def _page_outline(pages: list[ParsedPageRecord], *, chars_per_page: int = 350) -> str:
    lines: list[str] = []
    for p in pages:
        snippet = re.sub(r"\s+", " ", p.content[:chars_per_page]).strip()
        lines.append(f"Page {p.page}: {snippet}")
    return "\n".join(lines)


def _classify_local(section: str) -> CanonicalBucket:
    low = section.lower()
    best: CanonicalBucket = "Other"
    best_score = 0
    for bucket, aliases in _BUCKET_ALIASES.items():
        score = sum(1 for a in aliases if a in low)
        if score > best_score:
            best_score = score
            best = bucket
    return best


def classify_section(section: str) -> CanonicalBucket:
    return _classify_local(section)


def _parse_section_json(raw: object, *, max_page: int) -> list[SectionRange]:
    if not isinstance(raw, list):
        return []
    ranges: list[SectionRange] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("section") or "").strip()
        if not title:
            continue
        try:
            start = int(item.get("start_page", 1))
            end = int(item.get("end_page", start))
        except (TypeError, ValueError):
            continue
        start = max(1, start)
        end = min(max_page, max(start, end))
        bucket_raw = str(item.get("bucket") or "").strip()
        bucket: CanonicalBucket = (
            bucket_raw if bucket_raw in _VALID_BUCKETS else classify_section(title)
        )
        ranges.append(
            SectionRange(
                section=title,
                start_page=start,
                end_page=end,
                bucket=bucket,
            )
        )
    return ranges


def _detect_sections_heuristic(pages: list[ParsedPageRecord]) -> list[SectionRange]:
    """Keyword/page-score fallback when Claude section detection fails."""
    page_hits: dict[int, set[CanonicalBucket]] = {}
    for p in pages:
        low = p.content.lower()
        hits: set[CanonicalBucket] = set()
        for bucket, aliases in _BUCKET_ALIASES.items():
            if any(a in low for a in aliases):
                hits.add(bucket)
        if hits:
            page_hits[p.page] = hits

    if not page_hits:
        return [
            SectionRange(
                section="Full document",
                start_page=pages[0].page,
                end_page=pages[-1].page,
                bucket="Operations",
            )
        ]

    ranges: list[SectionRange] = []
    for bucket in EXTRACTION_BUCKETS:
        pages_for_bucket = sorted(pg for pg, hits in page_hits.items() if bucket in hits)
        if not pages_for_bucket:
            continue
        start = pages_for_bucket[0]
        end = pages_for_bucket[0]
        for pg in pages_for_bucket[1:]:
            if pg <= end + 2:
                end = pg
            else:
                ranges.append(SectionRange(section=bucket, start_page=start, end_page=end, bucket=bucket))
                start = end = pg
        ranges.append(SectionRange(section=bucket, start_page=start, end_page=end, bucket=bucket))
    return ranges


def detect_sections(
    pages: list[ParsedPageRecord],
    *,
    doc_title: str,
    mode: SectionMode = "heuristic",
) -> list[SectionRange]:
    if not pages:
        return []

    if mode == "heuristic":
        return _detect_sections_heuristic(pages)

    max_page = max(p.page for p in pages)
    outline = _page_outline(pages)
    user = (
        f"Document: {doc_title}\n"
        f"Total pages: {max_page}\n\n"
        f"Page outline (first ~350 chars per page):\n{outline}"
    )

    if SETTINGS.claude_api_key:
        try:
            raw = claude().complete_json(_DETECT_SYS, user, max_tokens=8000)
            ranges = _parse_section_json(raw, max_page=max_page)
            if ranges:
                return ranges
        except Exception:
            pass

    return _detect_sections_heuristic(pages)


def relevant_sections(sections: list[SectionRange]) -> list[SectionRange]:
    """Keep only Products and Customers sections for extraction."""
    return [s for s in sections if s.bucket in EXTRACTION_TARGETS]


def content_for_sections(pages: list[ParsedPageRecord], sections: list[SectionRange]) -> dict[CanonicalBucket, str]:
    by_page = {p.page: p.content for p in pages}
    grouped: dict[CanonicalBucket, list[str]] = {b: [] for b in EXTRACTION_BUCKETS}

    for sec in sections:
        if sec.bucket not in grouped:
            continue
        parts: list[str] = []
        for pg in range(sec.start_page, sec.end_page + 1):
            text = by_page.get(pg, "").strip()
            if text:
                parts.append(f"--- Page {pg} ({sec.section}) ---\n{text}")
        if parts:
            grouped[sec.bucket].append("\n\n".join(parts))

    return {k: "\n\n".join(v) for k, v in grouped.items() if v}
