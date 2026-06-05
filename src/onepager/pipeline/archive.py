"""Company-local source archive.

This stage makes discovery durable: every relevant URL gets a local manifest row,
and downloadable documents / extracted page markdown are copied into
outputs/<slug>/research/ before downstream extraction starts.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..schemas import SourceType
from ..tools.pdf import download_pdf
from ..tools.scrape import Scraper
from .context import RunContext
from .discovery import Candidate, _matches_entity


@dataclass
class ArchiveRecord:
    url: str
    title: str
    source_type: str
    status: str
    local_path: str | None = None
    content_sha1: str | None = None
    media_type: str = ""
    retrieved_at: str = ""
    error: str = ""
    snippet: str = ""


_DOWNLOAD_EXTS = {".pdf", ".xls", ".xlsx", ".csv"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str, *, fallback: str = "source") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return (s or fallback)[:80]


def _sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _manifest_path(ctx: RunContext) -> Path | None:
    if ctx.research_dir is None:
        return None
    return ctx.research_dir / "manifest.json"


def _load_manifest(ctx: RunContext) -> list[dict]:
    path = _manifest_path(ctx)
    if path is None or not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_manifest(ctx: RunContext, records: list[ArchiveRecord]) -> None:
    path = _manifest_path(ctx)
    if path is None:
        return
    existing = _load_manifest(ctx)
    by_url = {r.get("url", ""): r for r in existing if isinstance(r, dict)}
    for rec in records:
        by_url[rec.url] = asdict(rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(by_url.values()), indent=2))


def _prune_manifest(ctx: RunContext) -> None:
    path = _manifest_path(ctx)
    if path is None or not path.exists():
        return
    rows = _load_manifest(ctx)
    kept = [
        r for r in rows
        if isinstance(r, dict) and _matches_entity(ctx, r.get("url", ""), r.get("title", ""), r.get("snippet", ""))
    ]
    if len(kept) != len(rows):
        path.write_text(json.dumps(kept, indent=2))


def _is_download(url: str, stype: SourceType) -> bool:
    low = urlparse(url).path.lower()
    ext = Path(low).suffix
    return ext in _DOWNLOAD_EXTS or stype in {SourceType.annual_report, SourceType.investor_presentation}


def _download_generic(url: str, dest: Path) -> tuple[Path | None, str, str]:
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=120,
            headers={"User-Agent": "Mozilla/5.0 onepager-agent"},
        ) as resp:
            resp.raise_for_status()
            media_type = resp.headers.get("content-type", "")
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        if dest.stat().st_size <= 0:
            dest.unlink(missing_ok=True)
            return None, media_type, "empty download"
        return dest, media_type, ""
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return None, "", str(exc)


def _archive_download(ctx: RunContext, cand: Candidate, documents_dir: Path) -> ArchiveRecord:
    title_slug = _slug(cand.title or Path(urlparse(cand.url).path).stem, fallback="document")
    url_hash = hashlib.sha1(cand.url.encode()).hexdigest()[:10]
    suffix = Path(urlparse(cand.url).path).suffix.lower()
    if not suffix or len(suffix) > 6:
        suffix = ".pdf" if cand.source_type in {SourceType.annual_report, SourceType.investor_presentation} else ".bin"
    dest = documents_dir / f"{title_slug}_{url_hash}{suffix}"

    if suffix == ".pdf" or cand.source_type in {SourceType.annual_report, SourceType.investor_presentation}:
        cached = download_pdf(cand.url)
        if not cached:
            return ArchiveRecord(
                url=cand.url, title=cand.title, source_type=cand.source_type.value,
                status="failed", retrieved_at=_now(), error="PDF download failed", snippet=cand.snippet,
            )
        shutil.copyfile(cached, dest)
        data = dest.read_bytes()
        return ArchiveRecord(
            url=cand.url, title=cand.title, source_type=cand.source_type.value,
            status="saved", local_path=_rel(dest, ctx.output_dir or dest.parent),
            content_sha1=_sha1_bytes(data), media_type="application/pdf",
            retrieved_at=_now(), snippet=cand.snippet,
        )

    path, media_type, err = _download_generic(cand.url, dest)
    if not path:
        return ArchiveRecord(
            url=cand.url, title=cand.title, source_type=cand.source_type.value,
            status="failed", retrieved_at=_now(), error=err, snippet=cand.snippet,
        )
    return ArchiveRecord(
        url=cand.url, title=cand.title, source_type=cand.source_type.value,
        status="saved", local_path=_rel(path, ctx.output_dir or path.parent),
        content_sha1=_sha1_bytes(path.read_bytes()), media_type=media_type,
        retrieved_at=_now(), snippet=cand.snippet,
    )


def _archive_page(ctx: RunContext, cand: Candidate, pages_dir: Path, scraper: Scraper) -> ArchiveRecord:
    res = scraper.scrape(cand.url)
    if not res.ok or not res.markdown.strip():
        return ArchiveRecord(
            url=cand.url, title=cand.title, source_type=cand.source_type.value,
            status="failed", retrieved_at=_now(), error="page extraction failed", snippet=cand.snippet,
        )
    title_slug = _slug(res.title or cand.title or urlparse(cand.url).netloc, fallback="page")
    url_hash = hashlib.sha1(cand.url.encode()).hexdigest()[:10]
    dest = pages_dir / f"{title_slug}_{url_hash}.md"
    header = f"# {res.title or cand.title or cand.url}\n\nSource: {cand.url}\nRetrieved: {_now()}\n\n"
    dest.write_text(header + res.markdown)
    return ArchiveRecord(
        url=cand.url, title=res.title or cand.title, source_type=cand.source_type.value,
        status="saved", local_path=_rel(dest, ctx.output_dir or dest.parent),
        content_sha1=_sha1_text(res.markdown), media_type="text/markdown",
        retrieved_at=_now(), snippet=cand.snippet,
    )


def archive_candidates(ctx: RunContext, candidates: list[Candidate]) -> list[ArchiveRecord]:
    """Persist candidate documents/pages into the company output folder."""
    if ctx.research_dir is None or ctx.output_dir is None:
        return []
    research_dir = ctx.research_dir
    documents_dir = research_dir / "documents"
    pages_dir = research_dir / "pages"
    documents_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    _prune_manifest(ctx)

    existing = {r.get("url", ""): r for r in _load_manifest(ctx) if isinstance(r, dict)}
    scraper = Scraper()
    records: list[ArchiveRecord] = []
    for cand in candidates:
        if not cand.url:
            continue
        if not _matches_entity(ctx, cand.url, cand.title, cand.snippet):
            continue
        old = existing.get(cand.url)
        if old and old.get("status") == "saved" and old.get("local_path"):
            ctx.archived_by_url[cand.url] = old
            continue
        if _is_download(cand.url, cand.source_type):
            rec = _archive_download(ctx, cand, documents_dir)
        else:
            rec = _archive_page(ctx, cand, pages_dir, scraper)
        records.append(rec)
        if rec.status == "saved" and rec.local_path:
            ctx.archived_by_url[rec.url] = asdict(rec)

    if records:
        _write_manifest(ctx, records)
    saved = sum(1 for r in records if r.status == "saved")
    failed = sum(1 for r in records if r.status == "failed")
    ctx.note(f"Archived {saved} source snapshots ({failed} failed) into research/")
    return records
