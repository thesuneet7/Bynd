"""Keyword-guided explore → section-scoped text extract + image vision → verified items."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from onepager.budget import BudgetExceeded
from onepager.llm import claude

from listed_docs.extraction.models import ExtractedItem, SOURCE_CONFIDENCE
from listed_docs.extraction.verify import _best_quote_span, _name_in_text, _norm

from .explore import ExploreResult, RelevantSection, SiteImage, explore_site
from .images import ImageParseResult, parse_section_images
from .scrape import WebPage, normalize_website

_MAX_SECTION_CHARS = 28_000

_STRICT_RULES = """
STRICT RULES (violations cause rejection):
- Return [] if nothing is explicitly stated — never guess.
- Every item MUST include "url" matching the page URL provided.
- "evidence" MUST be a verbatim copy-paste substring from the SECTION TEXT only (<= 300 chars).
- The entity name MUST appear inside the evidence quote.
- Do NOT use knowledge outside the provided section.
"""

_PRODUCT_SYS = f"""Extract explicitly stated products from this website SECTION (under a products/offerings heading).
Return JSON array:
[{{"product": "...", "evidence": "verbatim quote from section", "url": "https://..."}}]
{_STRICT_RULES}
Extract: products, product families, business lines, components, service offerings explicitly named."""

_CUSTOMER_SYS = f"""Extract explicitly named customers/clients/OEMs from this website SECTION (under a customers/clients heading).
Return JSON array:
[{{"customer": "...", "evidence": "verbatim quote from section", "url": "https://..."}}]
{_STRICT_RULES}
Customer name must appear in evidence with relationship context (OEM, supplies, partner, award, etc.)."""


@dataclass
class AgentState:
    company: str
    website: str
    sections: list[RelevantSection] = field(default_factory=list)
    images: list[SiteImage] = field(default_factory=list)
    image_parses: list[ImageParseResult] = field(default_factory=list)
    products: list[ExtractedItem] = field(default_factory=list)
    customers: list[ExtractedItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rounds: int = 0


def _slug(url: str) -> str:
    path = urlparse(url).path.strip("/") or "home"
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_") or "home"


def _truncate(text: str, limit: int = _MAX_SECTION_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... truncated ...]"


def _section_header(company: str, section: RelevantSection) -> str:
    return (
        f"Company: {company}\n"
        f"Source: Company Website\n"
        f"Page URL: {section.url}\n"
        f"Section heading: {section.heading}\n"
        f"Section type: {section.bucket}\n"
        f"Found via: {section.interaction}\n\n"
        f"SECTION TEXT:\n{_truncate(section.text)}"
    )


def _parse_items(
    raw: Any,
    *,
    field: str,
    bucket: str,
    page_url: str,
    doc_id: str,
    source_label: str,
) -> list[ExtractedItem]:
    if not isinstance(raw, list):
        return []
    out: list[ExtractedItem] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get(field) or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        url = str(row.get("url") or page_url).strip().rstrip("/")
        if not name or not evidence:
            continue
        out.append(
            ExtractedItem(
                name=name,
                evidence=evidence,
                source=source_label,
                confidence=0.0,
                bucket=bucket,
                page=None,
                document_id=doc_id,
                local_path=url,
                verified=False,
            )
        )
    return out


def verify_web_item(item: ExtractedItem, section_text: str) -> ExtractedItem | None:
    ok, grounded = _best_quote_span(item.evidence, section_text)
    if not ok:
        return None
    if not _name_in_text(item.name, grounded) and not _name_in_text(item.name, section_text):
        return None
    return ExtractedItem(
        name=item.name.strip(),
        evidence=grounded[:400],
        source=item.source,
        confidence=SOURCE_CONFIDENCE["company_website"],
        bucket=item.bucket,
        page=None,
        document_id=item.document_id,
        local_path=item.local_path,
        verified=True,
    )


def extract_from_section(section: RelevantSection, *, company: str) -> tuple[list[ExtractedItem], list[ExtractedItem], int]:
    doc_id = f"{_slug(section.url)}_{section.bucket}"
    source_label = f"Company Website — {section.heading}"
    header = _section_header(company, section)
    rejected = 0
    products: list[ExtractedItem] = []
    customers: list[ExtractedItem] = []

    if section.bucket == "products":
        pairs = ((_PRODUCT_SYS, "product", "products", products),)
    elif section.bucket == "customers":
        pairs = ((_CUSTOMER_SYS, "customer", "customers", customers),)
    else:
        return products, customers, 0

    for sys, field, bucket, acc in pairs:
        raw = claude().complete_json(sys, header, cheap=True)
        proposed = _parse_items(
            raw,
            field=field,
            bucket=bucket,
            page_url=section.url,
            doc_id=doc_id,
            source_label=source_label,
        )
        for item in proposed:
            verified = verify_web_item(item, section.text)
            if verified is None:
                rejected += 1
            else:
                acc.append(verified)
    return products, customers, rejected


def _known_names(items: list[ExtractedItem]) -> set[str]:
    return {_norm(i.name) for i in items if i.verified and i.name}


def _dedupe_items(existing: list[ExtractedItem], new_items: list[ExtractedItem]) -> list[ExtractedItem]:
    seen = _known_names(existing)
    out = list(existing)
    for item in new_items:
        key = _norm(item.name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def run_agent(
    *,
    company: str,
    website: str,
    images_dir: Path,
    max_rounds: int | None = None,
) -> AgentState:
    """Explore until keyword sections found; extract only from those sections + their images."""
    root = normalize_website(website)
    state = AgentState(company=company, website=root)
    _ = max_rounds

    def log(msg: str) -> None:
        state.notes.append(msg)

    log("Step 1: keyword-guided explore (products/customers headings only)")
    explored: ExploreResult = explore_site(root, images_dir=images_dir, log=log)
    state.notes.extend(explored.notes)
    state.rounds = explored.urls_visited

    if not explored.sections:
        state.notes.append("no product/customer keyword sections found on site")
        return state

    state.sections = explored.sections
    state.images = [img for sec in explored.sections for img in sec.images]

    log(f"Step 2: LLM extraction from {len(explored.sections)} keyword-matched sections")
    for sec in explored.sections:
        try:
            p, c, rej = extract_from_section(sec, company=company)
        except BudgetExceeded as e:
            state.notes.append(f"text extraction budget exhausted: {e}")
            break
        state.products = _dedupe_items(state.products, p)
        state.customers = _dedupe_items(state.customers, c)
        if p or c:
            state.notes.append(
                f"«{sec.heading}» ({sec.bucket}): +{len(p)} products, +{len(c)} customers ({rej} rejected)"
            )

    log(f"Step 3: vision-parse {len(state.images)} images from matched sections only")
    try:
        img_products, img_customers, parses, img_notes = parse_section_images(
            explored.sections, company=company
        )
        state.image_parses = parses
        state.notes.extend(img_notes)
        state.products = _dedupe_items(state.products, img_products)
        state.customers = _dedupe_items(state.customers, img_customers)
        state.notes.append(
            f"section images: +{len(img_products)} products, +{len(img_customers)} customers from vision"
        )
    except BudgetExceeded as e:
        state.notes.append(f"vision budget exhausted: {e}")

    state.notes.append(f"final: {len(state.products)} products, {len(state.customers)} customers")
    return state
