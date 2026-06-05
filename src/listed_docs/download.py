"""Download and deduplicate document files."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .context import ListedDocsContext
from .models import DocSource, DownloadedDocument, DocumentRef
from .sources.bse import bse_download_headers
from .sources.nse import nse_download_headers

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return (s or "doc")[:90]


def _headers_for(ref: DocumentRef) -> dict[str, str]:
    if ref.source == DocSource.nse or "nsearchives.nseindia.com" in ref.url:
        return nse_download_headers()
    if ref.source == DocSource.bse or "bseindia.com" in ref.url:
        return bse_download_headers()
    return {"User-Agent": _USER_AGENT}


def _ext_from_url(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".pdf", ".zip", ".xlsx", ".xls"):
        if path.endswith(ext):
            return ext
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "zip" in ct:
        return ".zip"
    return ".bin"


def download_documents(
    ctx: ListedDocsContext,
    refs: list[DocumentRef],
) -> list[DownloadedDocument]:
    ctx.documents_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_manifest(ctx)
    by_sha: dict[str, str] = {
        r.get("sha256", ""): r.get("local_path", "")
        for r in existing
        if r.get("sha256") and r.get("local_path")
    }

    results: list[DownloadedDocument] = []
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        if not any(r.source == DocSource.nse for r in refs):
            client.get("https://www.nseindia.com/", headers=nse_download_headers())
        if not any(r.source == DocSource.bse for r in refs):
            client.get("https://www.bseindia.com/", headers=bse_download_headers())

        for ref in refs:
            headers = _headers_for(ref)
            try:
                resp = client.get(ref.url, headers=headers)
                resp.raise_for_status()
                data = resp.content
                if len(data) < 200:
                    results.append(DownloadedDocument(ref=ref, status="failed", error="empty response"))
                    continue
                sha = hashlib.sha256(data).hexdigest()
                if sha in by_sha:
                    results.append(
                        DownloadedDocument(
                            ref=ref,
                            status="skipped_duplicate",
                            local_path=by_sha[sha],
                            sha256=sha,
                            file_size=len(data),
                        )
                    )
                    continue
                ext = _ext_from_url(ref.url, resp.headers.get("content-type", ""))
                fname = f"{ref.source.value}_{ref.category.value}_{_slug(ref.title)}_{sha[:10]}{ext}"
                dest = ctx.documents_dir / fname
                dest.write_bytes(data)
                by_sha[sha] = str(dest.relative_to(ctx.output_dir))
                results.append(
                    DownloadedDocument(
                        ref=ref,
                        status="saved",
                        local_path=str(dest.relative_to(ctx.output_dir)),
                        sha256=sha,
                        file_size=len(data),
                    )
                )
            except Exception as e:  # noqa: BLE001
                results.append(DownloadedDocument(ref=ref, status="failed", error=str(e)))

    _write_manifest(ctx, results, existing)
    return results


def _load_manifest(ctx: ListedDocsContext) -> list[dict]:
    if not ctx.manifest_path.exists():
        return []
    try:
        data = json.loads(ctx.manifest_path.read_text())
        return data.get("documents", []) if isinstance(data, dict) else data
    except Exception:
        return []


def _write_manifest(ctx: ListedDocsContext, new: list[DownloadedDocument], existing: list[dict]) -> None:
    by_url = {r.get("url", ""): r for r in existing if isinstance(r, dict)}
    for d in new:
        row = {
            "url": d.ref.url,
            "title": d.ref.title,
            "category": d.ref.category.value,
            "source": d.ref.source.value,
            "report_year": d.ref.report_year,
            "fy_label": d.ref.fy_label,
            "published": d.ref.published,
            "status": d.status,
            "local_path": d.local_path,
            "sha256": d.sha256,
            "file_size": d.file_size,
            "error": d.error,
            "meta": d.ref.meta,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
        by_url[d.ref.url] = row

    payload = {
        "company": ctx.company_name,
        "ticker": ctx.ticker,
        "bse_scrip": ctx.bse_scrip,
        "website": ctx.website,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "documents": list(by_url.values()),
    }
    ctx.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.manifest_path.write_text(json.dumps(payload, indent=2))
