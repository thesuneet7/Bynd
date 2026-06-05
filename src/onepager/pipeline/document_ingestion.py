"""Ingest saved research archive artifacts into the evidence store."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..schemas import Source, SourceType
from ..tools.excel import parse_spreadsheet
from ..tools.pdf import PdfParser
from .context import RunContext
from .discovery import _matches_entity
from .ingestion import _select_relevant_pages


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest(ctx: RunContext) -> list[dict]:
    if ctx.research_dir is None:
        return []
    path = ctx.research_dir / "manifest.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _local_path(ctx: RunContext, rec: dict) -> Path | None:
    raw = rec.get("local_path")
    if not raw or ctx.output_dir is None:
        return None
    path = ctx.output_dir / raw
    return path if path.exists() else None


def _source_type(value: str) -> SourceType:
    try:
        return SourceType(value)
    except Exception:
        return SourceType.other


def ingest_archived_documents(ctx: RunContext, *, max_new_sources: int = 3, max_new_chunks: int = 60) -> dict[str, int]:
    """Index archived files not already represented in ctx.sources.

    The original URL ingest remains useful for quick paths, but this pass makes
    the saved archive the durable source for downstream retrieval.
    """
    records = [r for r in _manifest(ctx) if r.get("status") == "saved"]
    if not records:
        return {"sources": 0, "chunks": 0}

    known_urls = {s.url for s in ctx.sources.values()}
    parser = PdfParser()
    sources_added = 0
    chunks_added = 0

    for rec in records:
        if sources_added >= max_new_sources or chunks_added >= max_new_chunks:
            break
        url = rec.get("url") or ""
        if not _matches_entity(ctx, url, rec.get("title", ""), rec.get("snippet", "")):
            continue
        path = _local_path(ctx, rec)
        if not url or not path or url in known_urls:
            continue
        stype = _source_type(rec.get("source_type", "other"))
        sid = ctx.new_source_id()
        dom = urlparse(url).netloc.lower().replace("www.", "")
        src = Source(
            id=sid,
            url=url,
            title=rec.get("title") or path.name,
            publisher=dom,
            source_type=stype,
            retrieved_at=rec.get("retrieved_at") or _now(),
            access="public",
            snapshot_path=rec.get("local_path"),
        )
        ctx.register_source(src)
        known_urls.add(url)
        sources_added += 1

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            pages = parser.parse_file(path)
            for pg in _select_relevant_pages(pages, max_keep=35):
                if chunks_added >= max_new_chunks:
                    break
                chunks_added += ctx.store.add(
                    pg.markdown,
                    source_id=sid,
                    locator={"page": pg.page, "doc": src.title, "snapshot_path": src.snapshot_path},
                )
        elif suffix in {".xlsx", ".xlsm", ".xls", ".csv"}:
            for sheet in parse_spreadsheet(path):
                if chunks_added >= max_new_chunks:
                    break
                chunks_added += ctx.store.add(
                    sheet.markdown,
                    source_id=sid,
                    locator={"sheet": sheet.sheet, "snapshot_path": src.snapshot_path},
                )
        else:
            text = path.read_text(errors="ignore")
            chunks_added += ctx.store.add(
                text,
                source_id=sid,
                locator={"url": url, "snapshot_path": src.snapshot_path},
            )

    if sources_added:
        ctx.note(f"Indexed archived documents: {chunks_added} chunks from {sources_added} saved sources")
    return {"sources": sources_added, "chunks": chunks_added}
