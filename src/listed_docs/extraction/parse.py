"""PDF → per-page markdown via LlamaParse (pypdf fallback)."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from onepager.config import CACHE_DIR, SETTINGS

from .models import ParsedPageRecord

_PARSE_CACHE = CACHE_DIR / "listed_docs_parsed"
_PARSE_CACHE.mkdir(parents=True, exist_ok=True)


def _cache_key(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def _resolve_pdf(path: Path) -> tuple[Path, TemporaryDirectory | None]:
    """Return a readable PDF path; extract from .zip when needed."""
    if path.suffix.lower() != ".zip":
        return path, None
    tmp = TemporaryDirectory(prefix="listed_docs_zip_")
    root = Path(tmp.name)
    with zipfile.ZipFile(path) as zf:
        zf.extractall(root)
    pdfs = sorted(root.rglob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    if not pdfs:
        tmp.cleanup()
        raise ValueError(f"No PDF found inside zip: {path}")
    return pdfs[0], tmp


def _pypdf_pages(path: Path) -> list[ParsedPageRecord]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out: list[ParsedPageRecord] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            out.append(ParsedPageRecord(page=i, content=text, document_type=""))
    return out


def _llamaparse_pages(path: Path) -> list[ParsedPageRecord]:
    if not SETTINGS.llamaparse_api_key:
        return []
    try:
        from llama_cloud_services import LlamaParse
    except Exception:
        return []

    parser = LlamaParse(
        api_key=SETTINGS.llamaparse_api_key,
        result_type="markdown",
        split_by_page=True,
    )
    try:
        docs = parser.load_data(str(path))
    except Exception:
        return []

    pages: list[ParsedPageRecord] = []
    for i, doc in enumerate(docs):
        text = getattr(doc, "text", "") or ""
        page_no = i + 1
        meta = getattr(doc, "metadata", {}) or {}
        if isinstance(meta, dict) and meta.get("page_label"):
            try:
                page_no = int(str(meta["page_label"]))
            except (TypeError, ValueError):
                pass
        if text.strip():
            pages.append(ParsedPageRecord(page=page_no, content=text, document_type=""))
    return pages


def parse_document(path: Path, *, document_type: str) -> list[ParsedPageRecord]:
    """Parse a local PDF (or zip containing a PDF) into page records."""
    if not path.exists():
        return []

    cache_file = _PARSE_CACHE / f"{_cache_key(path)}.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
        return [
            ParsedPageRecord(page=r["page"], content=r["content"], document_type=document_type)
            for r in data
        ]

    tmp: TemporaryDirectory | None = None
    try:
        pdf_path, tmp = _resolve_pdf(path)
        pages = _llamaparse_pages(pdf_path)
        via = "llamaparse"
        if not pages:
            pages = _pypdf_pages(pdf_path)
            via = "pypdf"
        if not pages:
            return []

        for p in pages:
            p.document_type = document_type

        cache_file.write_text(
            json.dumps(
                [{"page": p.page, "content": p.content, "document_type": p.document_type, "via": via} for p in pages],
                ensure_ascii=False,
            )
        )
        return pages
    finally:
        if tmp is not None:
            tmp.cleanup()
