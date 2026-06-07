"""Orchestrate interactive website exploration and write knowledge graph artifacts."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from listed_docs.extraction.models import KnowledgeGraph, SOURCE_CONFIDENCE

from .agent import AgentState, run_agent
from .scrape import normalize_website


def _norm(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"\s+", " ", s)


def _merge_web_items(items: list, *, key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        if not getattr(item, "verified", False):
            continue
        k = _norm(item.name)
        if not k:
            continue
        local = item.local_path or ""
        is_image = local.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"))
        citation = {
            "document_id": item.document_id,
            "source": item.source,
            "url": local if local.startswith("http") else None,
            "local_path": local,
            "page": item.page,
            "quote": item.evidence,
            "type": "image" if is_image else "page",
        }
        row = grouped.get(k)
        if row is None:
            grouped[k] = {
                key: item.name,
                "citations": [citation],
                "sources": [item.source],
                "source_types": ["company_website"],
                "pages": [],
                "documents": [item.document_id],
                "confidence": SOURCE_CONFIDENCE["company_website"],
                "verified": True,
                "cross_checked": False,
                "verification": "substring_match" if not is_image else "vision",
            }
            continue
        seen = {(c["document_id"], (c.get("quote") or "")[:80]) for c in row["citations"]}
        sig = (citation["document_id"], (citation.get("quote") or "")[:80])
        if sig not in seen:
            row["citations"].append(citation)
        if item.source not in row["sources"]:
            row["sources"].append(item.source)
        if item.document_id not in row["documents"]:
            row["documents"].append(item.document_id)
            if len(row["documents"]) >= 2:
                row["cross_checked"] = True
                row["confidence"] = round(min(0.85, SOURCE_CONFIDENCE["company_website"] + 0.05), 3)

    out = list(grouped.values())
    out.sort(key=lambda r: (-int(r.get("cross_checked", False)), -r["confidence"], r[key]))
    return out


def _write_section_cache(output_dir: Path, state: AgentState) -> None:
    sections_dir = output_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    manifest_sections = []
    for sec in state.sections:
        slug = re.sub(r"[^a-z0-9]+", "_", f"{sec.bucket}_{sec.heading}".lower())[:90].strip("_")
        path = sections_dir / f"{slug}.json"
        payload = {
            "url": sec.url,
            "bucket": sec.bucket,
            "heading": sec.heading,
            "interaction": sec.interaction,
            "chars": len(sec.text),
            "text": sec.text,
            "images": [
                {
                    "src_url": img.src_url,
                    "alt": img.alt,
                    "local_path": str(Path(img.local_path).relative_to(output_dir)) if img.local_path else "",
                }
                for img in sec.images
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_sections.append(
            {
                "url": sec.url,
                "bucket": sec.bucket,
                "heading": sec.heading,
                "local_path": str(path.relative_to(output_dir)),
                "images": len(sec.images),
            }
        )

    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source": "company_website",
                "strategy": "keyword_guided_sections",
                "website": state.website,
                "company": state.company,
                "sections": manifest_sections,
                "urls_visited": state.rounds,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_extraction_summary(output_dir: Path, kg: KnowledgeGraph, *, website: str) -> None:
    lines = [
        f"# Website extraction — {kg.company}",
        "",
        f"**Website:** {website}",
        f"**Keyword sections harvested:** {kg.documents_processed}",
        f"**Extracted at:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "_Keyword-guided: only sections under products/customers headings. Text verified by substring match; images by vision._",
        "",
        f"- Products: **{len(kg.products)}**",
        f"- Customers: **{len(kg.customers)}**",
        "",
    ]

    def _section(title: str, items: list[dict], key: str) -> None:
        if not items:
            return
        lines.append(f"## {title}")
        lines.append("")
        for row in items:
            lines.append(f"- **{row[key]}** — {', '.join(row.get('sources', []))}")
            for cite in row.get("citations") or []:
                if cite.get("type") == "image":
                    lines.append(f"  - image `{cite.get('local_path')}`")
                elif cite.get("url"):
                    lines.append(f"  - [{cite.get('url')}]({cite.get('url')})")
                else:
                    lines.append(f"  - {cite.get('source')}")
                quote = (cite.get("quote") or "").strip()
                if quote:
                    lines.append(f"    > {quote}")
        lines.append("")

    _section("Products", kg.products, "product")
    _section("Customers", kg.customers, "customer")

    lines.append("## Pipeline log")
    lines.append("")
    for note in kg.extraction_notes:
        lines.append(f"- `{note}`")
    lines.append("")

    (output_dir / "extraction_summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_website_extraction(
    *,
    company_name: str,
    website: str,
    output_dir: Path,
    registry_id: str | None = None,
    return_sections: bool = False,
) -> KnowledgeGraph | tuple[KnowledgeGraph, list]:
    """Interactively explore company website; extract products/customers from pages + images."""
    output_dir.mkdir(parents=True, exist_ok=True)
    root = normalize_website(website)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    state = run_agent(company=company_name, website=root, images_dir=images_dir)
    _write_section_cache(output_dir, state)

    extraction_payload = {
        "company": company_name,
        "website": root,
        "registry_id": registry_id,
        "urls_visited": state.rounds,
        "products": [asdict(i) for i in state.products],
        "customers": [asdict(i) for i in state.customers],
        "images_parsed": len(state.image_parses),
        "images_saved": len(state.images),
        "sections_harvested": len(state.sections),
        "notes": state.notes,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    extraction_dir = output_dir / "extraction"
    extraction_dir.mkdir(parents=True, exist_ok=True)
    extraction_dir.joinpath("website.json").write_text(
        json.dumps(extraction_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    notes = list(state.notes)
    notes.insert(0, f"Keyword-guided: {state.rounds} URLs visited, {len(state.sections)} sections, {len(state.images)} images")
    notes.append(f"Verified: {len(state.products)} products, {len(state.customers)} customers")

    kg = KnowledgeGraph(
        company=company_name,
        ticker=registry_id or root,
        products=_merge_web_items(state.products, key="product"),
        customers=_merge_web_items(state.customers, key="customer"),
        documents_processed=len(state.sections),
        extraction_notes=notes,
    )

    kg_path = output_dir / "knowledge_graph.json"
    kg_path.write_text(
        json.dumps(
            {
                "company": kg.company,
                "ticker": kg.ticker,
                "website": root,
                "source": "company_website",
                "products": kg.products,
                "customers": kg.customers,
                "documents_processed": kg.documents_processed,
                "images_saved": len(state.images),
                "extraction_notes": kg.extraction_notes,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_extraction_summary(output_dir, kg, website=root)
    if return_sections:
        return kg, state.sections
    return kg
