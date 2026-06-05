"""Cross-document merge with deterministic confidence from verified citations."""
from __future__ import annotations

import re
from typing import Any

from .models import DocumentExtraction, KnowledgeGraph, SOURCE_CONFIDENCE

_WEAK_SOURCES = {"news", "company_website"}


def _norm(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"\s+", " ", s)


def _source_type_from_label(label: str) -> str:
    low = label.lower()
    if "annual report" in low:
        return "annual_report"
    if "presentation" in low or "earnings" in low:
        return "investor_presentation"
    if "website" in low:
        return "company_website"
    return "news"


def _deterministic_confidence(source_types: set[str], *, doc_count: int) -> float:
    if not source_types:
        return 0.5
    base = max(SOURCE_CONFIDENCE.get(t, 0.5) for t in source_types)
    if doc_count >= 2:
        return round(min(0.99, base + 0.08 * (doc_count - 1)), 3)
    if any(t in _WEAK_SOURCES for t in source_types):
        return round(min(base, 0.55), 3)
    return round(base, 3)


def _merge_items(
    docs: list[DocumentExtraction],
    attr: str,
    *,
    bucket_key: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for doc in docs:
        for item in getattr(doc, attr, []) or []:
            if not getattr(item, "verified", False):
                continue
            key = _norm(item.name)
            if not key:
                continue
            src_type = _source_type_from_label(item.source)
            citation = {
                "document_id": item.document_id,
                "source": item.source,
                "local_path": item.local_path,
                "page": item.page,
                "quote": item.evidence,
            }
            row = grouped.get(key)
            if row is None:
                grouped[key] = {
                    bucket_key: item.name,
                    "citations": [citation],
                    "sources": [item.source],
                    "source_types": [src_type],
                    "pages": [item.page] if item.page else [],
                    "documents": [item.document_id],
                }
                continue
            # Dedupe citations by doc+page+quote prefix
            seen = {
                (c["document_id"], c.get("page"), (c.get("quote") or "")[:80])
                for c in row["citations"]
            }
            sig = (citation["document_id"], citation.get("page"), (citation.get("quote") or "")[:80])
            if sig not in seen:
                row["citations"].append(citation)
            if item.source not in row["sources"]:
                row["sources"].append(item.source)
                row["source_types"].append(src_type)
            if item.page and item.page not in row["pages"]:
                row["pages"].append(item.page)
            if item.document_id not in row["documents"]:
                row["documents"].append(item.document_id)

    out: list[dict[str, Any]] = []
    for row in grouped.values():
        types = set(row["source_types"])
        doc_count = len(set(row["documents"]))
        row["confidence"] = _deterministic_confidence(types, doc_count=doc_count)
        row["verified"] = True
        row["cross_checked"] = doc_count >= 2
        row["verification"] = "substring_match"
        row["pages"] = sorted(p for p in row["pages"] if p)
        out.append(row)

    out.sort(key=lambda r: (-int(r.get("cross_checked", False)), -r["confidence"], r[bucket_key]))
    return out


def build_knowledge_graph(
    *,
    company: str,
    ticker: str,
    doc_results: list[DocumentExtraction],
    notes: list[str],
) -> KnowledgeGraph:
    return KnowledgeGraph(
        company=company,
        ticker=ticker,
        products=_merge_items(doc_results, "products", bucket_key="product"),
        customers=_merge_items(doc_results, "customers", bucket_key="customer"),
        documents_processed=len(doc_results),
        extraction_notes=notes,
    )
