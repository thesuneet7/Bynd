"""Orchestrate listed-company document discovery and download."""
from __future__ import annotations

from dataclasses import dataclass, field

from .context import ListedDocsContext
from .download import download_documents
from .models import DocCategory, DocumentRef
from .relevance import select_relevant_documents
from .resolve import resolve_listed_company
from .sources.bse import fetch_bse_documents
from .sources.nse import fetch_nse_documents
from .years import YearWindow, last_n_report_years


@dataclass
class FetchResult:
    ctx: ListedDocsContext
    window: YearWindow
    discovered: list[DocumentRef] = field(default_factory=list)
    downloaded: list = field(default_factory=list)


def _dedupe_refs(refs: list[DocumentRef]) -> list[DocumentRef]:
    seen: set[str] = set()
    out: list[DocumentRef] = []
    for r in refs:
        if r.url in seen:
            continue
        seen.add(r.url)
        out.append(r)
    return out


def _prioritize(refs: list[DocumentRef]) -> list[DocumentRef]:
    order = {DocCategory.annual_report: 0, DocCategory.investor_presentation: 1}
    return sorted(
        refs,
        key=lambda r: (
            order.get(r.category, 9),
            -(r.report_year or 0),
            r.source.value,
            r.title,
        ),
    )


def run_listed_docs_fetch(
    *,
    company_name: str,
    ticker: str,
    output_dir,
    hint: str | None = None,
    years: int = 3,
) -> FetchResult:
    window = last_n_report_years(years)
    symbol, bse_scrip, website = resolve_listed_company(name=company_name, ticker=ticker, hint=hint)
    ctx = ListedDocsContext(
        company_name=company_name,
        ticker=symbol,
        output_dir=output_dir,
        bse_scrip=bse_scrip,
        website=website,
    )
    ctx.note(f"Resolved {symbol} | BSE scrip={bse_scrip or 'unknown'}")
    ctx.note(f"Year window: {window.years}")
    ctx.note("Scope: NSE/BSE annual reports + major investor presentations only")

    raw: list[DocumentRef] = []
    nse_raw = fetch_nse_documents(symbol, window)
    ctx.note(f"[nse] raw filings: {len(nse_raw)}")
    raw.extend(nse_raw)

    if bse_scrip:
        bse_raw = fetch_bse_documents(bse_scrip, window)
        ctx.note(f"[bse] raw filings: {len(bse_raw)}")
        raw.extend(bse_raw)
    else:
        ctx.note("[bse] skipped — no scrip code")

    raw = _dedupe_refs(raw)
    refs = select_relevant_documents(raw)
    ar_n = sum(1 for r in refs if r.category == DocCategory.annual_report)
    pres_n = sum(1 for r in refs if r.category == DocCategory.investor_presentation)
    ctx.note(f"Selected after relevance filter: {len(refs)} ({ar_n} annual reports, {pres_n} presentations)")

    refs = _prioritize(refs)
    downloaded = download_documents(ctx, refs)
    saved = sum(1 for d in downloaded if d.status == "saved")
    failed = sum(1 for d in downloaded if d.status == "failed")
    dup = sum(1 for d in downloaded if d.status == "skipped_duplicate")
    ctx.note(f"Downloaded: {saved} saved, {dup} duplicates skipped, {failed} failed")

    _write_summary(ctx, window, refs, downloaded, raw_count=len(raw))
    return FetchResult(ctx=ctx, window=window, discovered=refs, downloaded=downloaded)


def _write_summary(ctx, window, refs, downloaded, *, raw_count: int) -> None:
    by_cat: dict[str, list] = {}
    for d in downloaded:
        if d.status not in ("saved", "skipped_duplicate"):
            continue
        cat = d.ref.category.value
        by_cat.setdefault(cat, []).append(d)

    lines = [
        f"# Listed company documents — {ctx.company_name}",
        "",
        f"**Ticker:** `{ctx.ticker}` · **BSE scrip:** `{ctx.bse_scrip or '—'}`",
        f"**Year window:** {', '.join(str(y) for y in window.years)}",
        "",
        "_Refined fetch: exchange annual reports (last 3 years) + top investor presentation per FY._",
        f"_Filtered from {raw_count} raw NSE/BSE filings._",
        "",
        "## Summary",
        "",
    ]
    for cat in ("annual_report", "investor_presentation"):
        items = by_cat.get(cat, [])
        if not items:
            continue
        lines.append(f"### {cat.replace('_', ' ').title()} ({len(items)})")
        lines.append("")
        for d in sorted(items, key=lambda x: (x.ref.report_year or 0, x.ref.title), reverse=True):
            path = d.local_path or "—"
            src = d.ref.source.value
            yr = d.ref.fy_label or d.ref.report_year or ""
            lines.append(f"- [{src}] {d.ref.title} {yr} → `{path}`")
        lines.append("")

    failed = [d for d in downloaded if d.status == "failed"]
    if failed:
        lines.append("## Failed downloads")
        lines.append("")
        for d in failed:
            lines.append(f"- {d.ref.title}: {d.error}")
        lines.append("")

    lines.append("## Pipeline log")
    lines.append("")
    for note in ctx.log:
        lines.append(f"- `{note}`")
    lines.append("")

    ctx.summary_path.write_text("\n".join(lines))
