"""Standalone screener/tofler scrape pipeline (no LLM, no web discovery, no snapshots)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from onepager.financials import (
    display_periods,
    fetch_provider_overview,
    fetch_screener_financials,
    fetch_tofler_financials,
    footnote_superscript,
    render_derivation_footnotes,
    render_overview_markdown,
    FINANCIAL_ROW_ORDER,
    FINANCIAL_UNITS,
    financial_label,
)
from onepager.financials.screener_session import clear_session_cache, get_screener_client
from onepager.pipeline.context import RunContext
from onepager.schemas import Entity, FinancialCell


@dataclass
class ScrapeResult:
    entity: Entity
    provider: str
    url: str
    overview_markdown: list[str]
    financials_markdown: list[str]
    cells: list[FinancialCell]
    log: list[str] = field(default_factory=list)
    error: str | None = None


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _financial_table(
    cells: list[FinancialCell],
    *,
    source_note: str,
    source_url: str,
    provider_label: str,
) -> list[str]:
    if not cells:
        return ["_No figures returned._", ""]
    periods = display_periods([c.period for c in cells], count=3, skip_latest=1)
    by_metric: dict[str, dict[str, FinancialCell]] = {}
    for c in cells:
        by_metric.setdefault(c.metric, {})[c.period] = c

    lines = [
        f"_Source: {source_note}_",
        "",
        "_Numbered markers (¹ ² …) link to derivation footnotes below (derived rows only)._",
        "",
        "| Metric | " + " | ".join(periods) + " | Basis |",
        "|---|" + "---|" * len(periods) + "---|",
    ]
    for metric in FINANCIAL_ROW_ORDER:
        row = by_metric.get(metric, {})
        unit = FINANCIAL_UNITS.get(metric, "")
        label = financial_label(metric, unit)
        vals: list[str] = []
        bases: set[str] = set()
        for p in periods:
            cell = row.get(p)
            if cell is None:
                sup = footnote_superscript(cells, periods, metric, p)
                vals.append(f"—{sup}" if sup else "—")
            else:
                sup = footnote_superscript(cells, periods, metric, p) if cell.basis == "derived" else ""
                vals.append(f"{_fmt(cell.numeric_value)}{sup}")
                bases.add(cell.basis)
        basis = "/".join(sorted(bases)) if bases else "—"
        if any(row.values()) or metric == "revenue_growth_pct":
            lines.append(f"| {label} | " + " | ".join(vals) + f" | {basis} |")
    lines.extend(
        render_derivation_footnotes(
            cells,
            periods,
            source_url=source_url,
            provider_label=provider_label,
        )
    )
    return lines


def run_company_scrape(
    entity: Entity,
    *,
    provider: str,
    max_periods: int = 10,
    force_screener_login: bool = False,
) -> ScrapeResult:
    ctx = RunContext(input_name=entity.input_name)
    provider_label = provider

    if force_screener_login and provider == "screener":
        clear_session_cache()
        get_screener_client(force_login=True)

    overview = fetch_provider_overview(ctx, entity, provider=provider)
    url = overview.url if overview else provider

    if provider == "screener":
        cells, err = fetch_screener_financials(ctx, entity, max_periods=max_periods)
    else:
        cells, err = fetch_tofler_financials(ctx, entity, max_periods=max_periods)

    if not err and overview is None:
        url = next((s.url for s in ctx.sources.values() if provider.split(".")[0] in s.url), provider)

    fin_md: list[str] = []
    if not err:
        fin_md = ["### Financials", ""] + _financial_table(
            cells,
            source_note=f"[{provider_label}]({url})",
            source_url=url,
            provider_label=provider_label,
        )
        fin_md.append(f"**Cells fetched:** {len(cells)}")
        fin_md.append("")

    return ScrapeResult(
        entity=entity,
        provider=provider,
        url=url,
        overview_markdown=render_overview_markdown(overview),
        financials_markdown=fin_md,
        cells=cells,
        log=ctx.log,
        error=err,
    )


def render_snapshot_markdown(results: list[ScrapeResult], *, title: str = "Company scrape") -> str:
    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Standalone pipeline: **screener.in** (listed) or **tofler.in** (unlisted) only — "
        "no web search, Firecrawl snapshots, or LLM agents.",
        "",
        "Display window: **FY23, FY24, FY25** (skips screener forward column when present).",
        "",
    ]
    for res in results:
        listing = res.entity.canonical_name or res.entity.input_name
        status = res.entity.listing_status or "unknown"
        lines.append(f"## {listing} ({status})")
        lines.append("")
        lines.extend(res.overview_markdown)
        for note in res.log:
            lines.append(f"- `{note}`")
        lines.append("")
        if res.error:
            lines.append(f"**Error:** {res.error}")
            lines.append("")
            continue
        lines.extend(res.financials_markdown)
    return "\n".join(lines)


def write_snapshot(results: list[ScrapeResult], path: Path, *, title: str = "Company scrape") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_snapshot_markdown(results, title=title))
    return path
