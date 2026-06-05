"""Structured extraction from classified sections only (LLM proposes; verify.py gates)."""
from __future__ import annotations

from typing import Any

from onepager.llm import claude

from .models import SOURCE_CONFIDENCE, CanonicalBucket, ExtractedItem

_MAX_SECTION_CHARS = 48_000

_STRICT_RULES = """
STRICT RULES (violations cause rejection):
- Return [] if nothing is explicitly stated — never guess.
- Every item MUST include "page" (integer) copied from the nearest "--- Page N ---" marker in the input.
- "evidence" MUST be a verbatim copy-paste substring from that page only (<= 300 chars).
- The entity name (product/customer/segment/etc.) MUST appear inside the evidence quote.
- Do NOT merge or infer entities not literally named in the text.
- Do NOT use knowledge outside the provided excerpt.
"""

_PRODUCT_SYS = f"""Extract explicitly stated products from the given document sections.
Return JSON array:
[{{"product": "...", "evidence": "verbatim quote from that page", "page": 12}}]
{_STRICT_RULES}
Extract: products, product families, business lines, service offerings explicitly named."""

_CUSTOMER_SYS = f"""Extract explicitly named customers/clients/OEMs/strategic accounts.
Return JSON array:
[{{"customer": "...", "evidence": "verbatim quote from that page", "page": 12}}]
{_STRICT_RULES}
The customer name must appear in the evidence quote with relationship context (supplies, OEM, partner, etc.)."""

_SEGMENT_SYS = f"""Extract business segments / operating divisions explicitly reported.
Return JSON array:
[{{"segment": "...", "evidence": "verbatim quote from that page", "page": 12}}]
{_STRICT_RULES}"""

_COMPETITOR_SYS = f"""Extract named competitors or competitive sets explicitly mentioned.
Return JSON array:
[{{"competitor": "...", "evidence": "verbatim quote from that page", "page": 12}}]
{_STRICT_RULES}"""

_RISK_SYS = f"""Extract principal business/product/market risks explicitly stated.
Return JSON array:
[{{"risk": "...", "evidence": "verbatim quote from that page", "page": 12}}]
{_STRICT_RULES}"""


def _truncate(text: str) -> str:
    if len(text) <= _MAX_SECTION_CHARS:
        return text
    return text[:_MAX_SECTION_CHARS] + "\n\n[... truncated ...]"


def _base_confidence(category: str) -> float:
    return SOURCE_CONFIDENCE.get(category, 0.7)


def _parse_items(
    raw: Any,
    *,
    field: str,
    bucket: str,
    source_label: str,
    document_id: str,
) -> list[ExtractedItem]:
    if not isinstance(raw, list):
        return []
    out: list[ExtractedItem] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get(field) or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        if not name or not evidence:
            continue
        try:
            page = int(row["page"]) if row.get("page") is not None else None
        except (TypeError, ValueError):
            page = None
        if page is None:
            continue
        out.append(
            ExtractedItem(
                name=name,
                evidence=evidence,
                source=source_label,
                confidence=0.0,
                bucket=bucket,
                page=page,
                document_id=document_id,
                verified=False,
            )
        )
    return out


def extract_from_bucket(
    bucket: CanonicalBucket,
    content: str,
    *,
    company: str,
    source_label: str,
    category: str,
    document_id: str,
) -> list[ExtractedItem]:
    if not content.strip():
        return []
    text = _truncate(content)
    header = (
        f"Company: {company}\n"
        f"Source: {source_label}\n"
        f"Pages in this excerpt are marked like: --- Page N (section title) ---\n\n"
        f"{text}"
    )

    if bucket == "Products":
        raw = claude().complete_json(_PRODUCT_SYS, header)
        return _parse_items(raw, field="product", bucket="products", source_label=source_label, document_id=document_id)
    if bucket == "Customers":
        raw = claude().complete_json(_CUSTOMER_SYS, header)
        return _parse_items(raw, field="customer", bucket="customers", source_label=source_label, document_id=document_id)
    if bucket == "Operations":
        raw = claude().complete_json(_SEGMENT_SYS, header)
        return _parse_items(raw, field="segment", bucket="segments", source_label=source_label, document_id=document_id)
    if bucket == "Competitors":
        raw = claude().complete_json(_COMPETITOR_SYS, header)
        return _parse_items(raw, field="competitor", bucket="competitors", source_label=source_label, document_id=document_id)
    if bucket == "Risks":
        raw = claude().complete_json(_RISK_SYS, header)
        return _parse_items(raw, field="risk", bucket="risks", source_label=source_label, document_id=document_id)
    return []
