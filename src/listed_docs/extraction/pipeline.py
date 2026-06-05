"""Iterate manifest PDFs: parse → detect sections → extract → merge."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..context import ListedDocsContext
from .extract import extract_from_bucket
from .merge import build_knowledge_graph
from .verify import verify_items
from .models import EXTRACTION_TARGETS, DocumentExtraction, KnowledgeGraph
from .parse import parse_document
from .sections import SectionMode, content_for_sections, detect_sections, relevant_sections


def _load_manifest_docs(ctx: ListedDocsContext) -> list[dict]:
    if not ctx.manifest_path.exists():
        raise FileNotFoundError(f"No manifest at {ctx.manifest_path}. Run fetch first.")
    data = json.loads(ctx.manifest_path.read_text())
    docs = data.get("documents", []) if isinstance(data, dict) else []
    seen_paths: set[str] = set()
    out: list[dict] = []
    for row in docs:
        if not isinstance(row, dict):
            continue
        if row.get("status") not in ("saved", "skipped_duplicate"):
            continue
        path = row.get("local_path") or ""
        if not path or path in seen_paths:
            continue
        ext = Path(path).suffix.lower()
        if ext not in (".pdf", ".zip"):
            continue
        seen_paths.add(path)
        out.append(row)
    return _prioritize_docs(out)


def _prioritize_docs(docs: list[dict]) -> list[dict]:
    order = {"annual_report": 0, "investor_presentation": 1}

    def key(row: dict) -> tuple:
        cat = str(row.get("category") or "other")
        yr = int(row.get("report_year") or 0)
        return (order.get(cat, 9), -yr, row.get("title") or "")

    return sorted(docs, key=key)


def _source_label(row: dict) -> str:
    cat = str(row.get("category") or "document").replace("_", " ").title()
    fy = row.get("fy_label") or row.get("report_year") or ""
    title = row.get("title") or cat
    return f"{title} {fy}".strip()


def _doc_id(row: dict) -> str:
    path = row.get("local_path") or row.get("url") or "doc"
    return Path(path).stem


def _serialize_doc_result(result: DocumentExtraction) -> dict:
    return {
        "document_id": result.document_id,
        "title": result.title,
        "category": result.category,
        "fy_label": result.fy_label,
        "local_path": result.local_path,
        "pages_parsed": result.pages_parsed,
        "sections": [asdict(s) for s in result.sections],
        "products": [asdict(i) for i in result.products],
        "customers": [asdict(i) for i in result.customers],
        "errors": result.errors,
    }


def _write_extraction_summary(ctx: ListedDocsContext, kg: KnowledgeGraph, doc_results: list[DocumentExtraction]) -> None:
    lines = [
        f"# Products & clients extraction — {kg.company}",
        "",
        f"**Ticker:** `{kg.ticker}` · **Documents processed:** {kg.documents_processed}",
        f"**Extracted at:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "_All items passed deterministic verification: entity name + evidence quote found verbatim in parsed PDF text. "
        "Each citation includes document path and page number._",
        "",
        "## Knowledge graph summary",
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
            cross = "✓✓" if row.get("cross_checked") else "✓"
            pages = row.get("pages") or []
            page_hint = f" p.{','.join(str(p) for p in pages)}" if pages else ""
            lines.append(
                f"- {cross} **{row[key]}**{page_hint} — "
                f"{', '.join(row.get('sources', []))}"
            )
        lines.append("")

    _section("Products", kg.products, "product")
    _section("Customers", kg.customers, "customer")

    lines.append("## Per-document")
    lines.append("")
    for d in doc_results:
        lines.append(
            f"- `{d.document_id}`: {d.pages_parsed} pages, "
            f"{len(d.products)} products, {len(d.customers)} customers"
            + (f" — errors: {', '.join(d.errors)}" if d.errors else "")
        )
    lines.append("")
    lines.append("## Pipeline log")
    lines.append("")
    for note in kg.extraction_notes:
        lines.append(f"- `{note}`")
    lines.append("")

    ctx.extraction_summary_path.write_text("\n".join(lines))


def _items_from_json(rows: list, bucket: str) -> list:
    from .models import ExtractedItem

    out: list[ExtractedItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            ExtractedItem(
                name=str(row.get("name") or ""),
                evidence=str(row.get("evidence") or ""),
                source=str(row.get("source") or ""),
                confidence=float(row.get("confidence") or 0),
                bucket=bucket,
                page=row.get("page"),
                document_id=str(row.get("document_id") or ""),
                local_path=str(row.get("local_path") or ""),
                verified=bool(row.get("verified")),
            )
        )
    return out


def _load_cached_result(doc_id: str, row: dict, path: Path) -> DocumentExtraction | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if data.get("errors"):
        return None
    for key in ("products", "customers", "segments", "competitors", "risks"):
        for row in data.get(key) or []:
            if isinstance(row, dict) and not row.get("verified"):
                return None
    return DocumentExtraction(
        document_id=doc_id,
        title=str(data.get("title") or row.get("title") or doc_id),
        category=str(data.get("category") or row.get("category") or ""),
        fy_label=str(data.get("fy_label") or row.get("fy_label") or ""),
        local_path=str(data.get("local_path") or row.get("local_path") or ""),
        pages_parsed=int(data.get("pages_parsed") or 0),
        products=_items_from_json(data.get("products") or [], "products"),
        customers=_items_from_json(data.get("customers") or [], "customers"),
        segments=_items_from_json(data.get("segments") or [], "segments"),
        competitors=_items_from_json(data.get("competitors") or [], "competitors"),
        risks=_items_from_json(data.get("risks") or [], "risks"),
        errors=list(data.get("errors") or []),
    )


def process_document(
    ctx: ListedDocsContext,
    row: dict,
    *,
    force: bool = False,
    section_mode: SectionMode = "heuristic",
) -> DocumentExtraction:
    doc_id = _doc_id(row)
    per_doc_path = ctx.extraction_dir / f"{doc_id}.json"
    if not force:
        cached = _load_cached_result(doc_id, row, per_doc_path)
        if cached is not None:
            return cached

    local = ctx.output_dir / str(row.get("local_path"))
    category = str(row.get("category") or "other")
    title = str(row.get("title") or doc_id)
    fy = str(row.get("fy_label") or row.get("report_year") or "")
    source_label = _source_label(row)

    result = DocumentExtraction(
        document_id=doc_id,
        title=title,
        category=category,
        fy_label=fy,
        local_path=str(row.get("local_path") or ""),
        pages_parsed=0,
    )

    try:
        pages = parse_document(local, document_type=category)
        result.pages_parsed = len(pages)
        if not pages:
            result.errors.append("parse returned no pages")
            return result

        ctx.parsed_dir.mkdir(parents=True, exist_ok=True)
        parsed_out = ctx.parsed_dir / f"{doc_id}.json"
        parsed_out.write_text(
            json.dumps([{"page": p.page, "content": p.content, "document_type": p.document_type} for p in pages], ensure_ascii=False),
            encoding="utf-8",
        )

        sections = detect_sections(pages, doc_title=title, mode=section_mode)
        result.sections = sections
        rel = relevant_sections(sections)
        if not rel:
            result.errors.append("no relevant sections detected")
            return result

        grouped = content_for_sections(pages, rel)
        local_path = str(row.get("local_path") or "")
        rejected_total = 0
        for bucket, content in grouped.items():
            if bucket not in EXTRACTION_TARGETS:
                continue
            proposed = extract_from_bucket(
                bucket,
                content,
                company=ctx.company_name,
                source_label=source_label,
                category=category,
                document_id=doc_id,
            )
            verified, rejected = verify_items(proposed, pages, local_path=local_path)
            rejected_total += rejected
            if bucket == "Products":
                result.products.extend(verified)
            elif bucket == "Customers":
                result.customers.extend(verified)
        if rejected_total:
            result.errors.append(f"verification rejected {rejected_total} ungrounded LLM proposals")

    except Exception as e:  # noqa: BLE001
        result.errors.append(str(e))

    ctx.extraction_dir.mkdir(parents=True, exist_ok=True)
    per_doc_path.write_text(json.dumps(_serialize_doc_result(result), indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def run_extraction(
    ctx: ListedDocsContext,
    *,
    force: bool = False,
    section_mode: SectionMode = "heuristic",
) -> KnowledgeGraph:
    ctx.parsed_dir.mkdir(parents=True, exist_ok=True)
    ctx.extraction_dir.mkdir(parents=True, exist_ok=True)

    docs = _load_manifest_docs(ctx)
    if not docs:
        raise RuntimeError("No PDF documents in manifest to extract.")

    notes: list[str] = []
    notes.append(f"Processing {len(docs)} unique documents (annual reports first)")
    notes.append(f"Section detection mode: {section_mode}")
    notes.append("Verification: deterministic substring match on parsed PDF pages (no LLM judge)")
    doc_results: list[DocumentExtraction] = []

    for i, row in enumerate(docs, start=1):
        title = row.get("title") or row.get("local_path")
        ctx.note(f"[{i}/{len(docs)}] {title}")
        result = process_document(ctx, row, force=force, section_mode=section_mode)
        doc_results.append(result)
        notes.append(
            f"{result.document_id}: {result.pages_parsed}p → "
            f"{len(result.products)} products, {len(result.customers)} customers"
            + (f" ERR: {'; '.join(result.errors)}" if result.errors else "")
        )

    kg = build_knowledge_graph(
        company=ctx.company_name,
        ticker=ctx.ticker,
        doc_results=doc_results,
        notes=notes,
    )

    kg_payload = {
        "company": kg.company,
        "ticker": kg.ticker,
        "products": kg.products,
        "customers": kg.customers,
        "documents_processed": kg.documents_processed,
        "extraction_notes": kg.extraction_notes,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    ctx.knowledge_graph_path.write_text(json.dumps(kg_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_extraction_summary(ctx, kg, doc_results)
    ctx.note(
        f"Knowledge graph: {len(kg.products)} products, {len(kg.customers)} customers "
        f"from {kg.documents_processed} documents"
    )
    return kg
