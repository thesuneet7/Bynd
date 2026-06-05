"""Fetch canonical financials from screener.in.

Uses the screener schedules API (same data as clicking every '+' row) plus the
annual tables on the company page. Firecrawl is used only to fetch the page HTML
when a plain httpx request is blocked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
from selectolax.parser import HTMLParser

from ..budget import BUDGET, BudgetExceeded
from ..config import SETTINGS
from ..schemas import ClaimType, Entity, Evidence, FinancialCell, Section, Source, SourceType
from .contract import FINANCIAL_UNITS, canonical_metric, latest_periods
from .derivation import derive_financial_metrics
from .screener_api import (
    company_id_from_html,
    discover_schedule_parents,
    fetch_all_schedules,
)
from .screener_session import ScreenerAuthError, get_screener_client

if TYPE_CHECKING:
    from ..pipeline.context import RunContext

SCREENER_BASE = "https://www.screener.in/company"
_USER_AGENT = "Mozilla/5.0 (compatible; ByndAI/1.0)"


@dataclass(frozen=True)
class ScreenerRow:
    metric: str
    period: str
    numeric_value: float
    unit: str
    screener_line: str
    section: str
    note: str = ""


def normalize_ticker(
    ticker: str | None = None,
    *,
    hint: str | None = None,
    name: str = "",
) -> str | None:
    candidates: list[str] = []
    if ticker:
        candidates.append(ticker)
    if hint:
        candidates.append(hint)
    for raw in candidates:
        s = raw.upper().strip()
        for sep in (":", " "):
            if sep in s and any(tag in s for tag in ("NSE", "BSE")):
                s = s.split(sep)[-1].strip()
        s = s.replace(".NS", "").replace(".BO", "").strip()
        if re.fullmatch(r"[A-Z0-9&.-]{2,24}", s):
            return s
    return None


def screener_url(ticker: str, *, consolidated: bool = True) -> str:
    suffix = "/consolidated/" if consolidated else "/"
    return f"{SCREENER_BASE}/{ticker.upper()}{suffix}"


def _parse_number(raw: str) -> float | None:
    if not raw or raw in ("-", "—", ""):
        return None
    s = raw.strip().replace(",", "")
    if s.endswith("%"):
        try:
            return float(s[:-1].strip())
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _period_label(header: str) -> str | None:
    m = re.match(r"Mar\s+(\d{4})", header.strip(), re.I)
    if not m:
        return None
    return f"FY{int(m.group(1)) % 100:02d}"


def _schedule_to_table(
    schedules: dict[str, dict[str, dict[str, str]]],
) -> dict[str, dict[str, dict[str, str]]]:
    """Normalize schedule API data to {section: {line: {FYxx: value}}}."""
    out: dict[str, dict[str, dict[str, str]]] = {}
    for section, lines in schedules.items():
        for line, periods in lines.items():
            for hdr, val in periods.items():
                period = _period_label(hdr)
                if not period or hdr == "isExpandable":
                    continue
                out.setdefault(section, {}).setdefault(line, {})[period] = val
    return out


def _annual_table(tree: HTMLParser, section_id: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    node = tree.css_first(f"section#{section_id}")
    if not node:
        return [], {}
    table = node.css_first("table")
    if not table:
        return [], {}
    rows = table.css("tr")
    if not rows:
        return [], {}
    headers = [c.text(strip=True) for c in rows[0].css("th,td")]
    periods = [_period_label(h) for h in headers[1:]]
    period_headers = [p for p in periods if p]
    data: dict[str, dict[str, str]] = {}
    for row in rows[1:]:
        cells = [c.text(strip=True) for c in row.css("th,td")]
        if not cells or not cells[0]:
            continue
        label = re.sub(r"\s*\+\s*$", "", cells[0]).strip()
        if label.startswith("Compounded") or label.endswith(":"):
            continue
        for period, val in zip(periods, cells[1:]):
            if not period:
                continue
            data.setdefault(label, {})[period] = val
    return period_headers, data


def _merge_tables(
    base: dict[str, dict[str, str]],
    extra: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged = dict(base)
    for line, periods in extra.items():
        merged.setdefault(line, {}).update(periods)
    return merged


def _get_row(table: dict[str, dict[str, str]], *names: str) -> dict[str, str] | None:
    for name in names:
        if name in table:
            return table[name]
    return None


def _material_cost_crore(table: dict[str, dict[str, str]], period: str) -> float | None:
    raw = _get_row(table, "Raw material cost", "Raw Material Cost")
    inv = _get_row(table, "Change in inventory", "Change in Inventory")
    total = 0.0
    found = False
    if raw and period in raw:
        v = _parse_number(raw[period])
        if v is not None:
            total += v
            found = True
    if inv and period in inv:
        v = _parse_number(inv[period])
        if v is not None:
            total += v
            found = True
    pct = _get_row(table, "Material Cost %")
    if not found and pct and period in pct:
        return None  # filled later with revenue * pct
    return round(total, 1) if found else None


def _map_profit_loss(
    periods: list[str],
    pl: dict[str, dict[str, str]],
    *,
    schedule_pl: dict[str, dict[str, str]] | None = None,
) -> list[ScreenerRow]:
    sched = schedule_pl or {}
    pl = _merge_tables(pl, sched)
    out: list[ScreenerRow] = []

    sales = _get_row(pl, "Sales+", "Sales")
    op = _get_row(pl, "Operating Profit")
    opm = _get_row(pl, "OPM %")
    dep = _get_row(pl, "Depreciation")

    for period in periods:
        rev_val = None
        if sales and period in sales:
            rev_val = _parse_number(sales[period])
            if rev_val is not None:
                out.append(
                    ScreenerRow("revenue", period, round(rev_val, 1), "INR crore", "Sales+", "profit-loss")
                )

        mat_cost = _material_cost_crore(pl, period)
        if mat_cost is None and rev_val is not None:
            pct_row = _get_row(pl, "Material Cost %")
            if pct_row and period in pct_row:
                pct = _parse_number(pct_row[period])
                if pct is not None:
                    mat_cost = round(rev_val * pct / 100, 1)
        if mat_cost is not None and rev_val is not None:
            out.append(
                ScreenerRow(
                    "material_cost",
                    period,
                    mat_cost,
                    "INR crore",
                    "Raw material cost + Change in inventory",
                    "profit-loss",
                    note="From screener Expenses (+) breakdown",
                )
            )
            margin = round(rev_val - mat_cost, 1)
            out.append(
                ScreenerRow(
                    "material_margin",
                    period,
                    margin,
                    "INR crore",
                    "Sales − material cost",
                    "profit-loss",
                    note="Derived from expanded screener schedule",
                )
            )
            if rev_val:
                out.append(
                    ScreenerRow(
                        "material_margin_pct",
                        period,
                        round(margin / rev_val * 100, 1),
                        "%",
                        "Material margin %",
                        "profit-loss",
                    )
                )

        if op and dep and period in op and period in dep:
            o, d = _parse_number(op[period]), _parse_number(dep[period])
            if o is not None and d is not None:
                out.append(
                    ScreenerRow(
                        "operating_ebitda",
                        period,
                        round(o + d, 1),
                        "INR crore",
                        "Operating Profit + Depreciation",
                        "profit-loss",
                        note="EBITDA from expanded P&L",
                    )
                )
        if opm and period in opm:
            v = _parse_number(opm[period])
            if v is not None:
                out.append(
                    ScreenerRow(
                        "operating_ebitda_pct",
                        period,
                        round(v, 1),
                        "%",
                        "OPM %",
                        "profit-loss",
                        note="Operating profit margin (screener)",
                    )
                )

    return out


def _map_ratios(periods: list[str], ratios: dict[str, dict[str, str]]) -> list[ScreenerRow]:
    out: list[ScreenerRow] = []
    roce = _get_row(ratios, "ROCE %")
    nwc = _get_row(ratios, "Working Capital Days")
    ccc = _get_row(ratios, "Cash Conversion Cycle")

    for period in periods:
        if roce and period in roce:
            v = _parse_number(roce[period])
            if v is not None:
                out.append(ScreenerRow("roce_pct", period, round(v, 1), "%", "ROCE %", "ratios"))
        if nwc and period in nwc:
            v = _parse_number(nwc[period])
            if v is not None:
                out.append(
                    ScreenerRow(
                        "nwc_days",
                        period,
                        round(v, 1),
                        "days",
                        "Working Capital Days",
                        "ratios",
                    )
                )
        elif ccc and period in ccc:
            v = _parse_number(ccc[period])
            if v is not None:
                out.append(
                    ScreenerRow(
                        "nwc_days",
                        period,
                        round(v, 1),
                        "days",
                        "Cash Conversion Cycle",
                        "ratios",
                        note="Fallback when working capital days absent",
                    )
                )
    return out


def _map_balance(
    periods: list[str],
    bs: dict[str, dict[str, str]],
    *,
    schedule_bs: dict[str, dict[str, str]] | None = None,
) -> list[ScreenerRow]:
    bs = _merge_tables(bs, schedule_bs or {})
    out: list[ScreenerRow] = []
    borrow = _get_row(bs, "Borrowings+", "Borrowings")
    cash = _get_row(bs, "Cash Equivalents", "Cash + Cash Equivalents", "Cash and Cash Equivalents")

    for period in periods:
        if borrow and period in borrow:
            v = _parse_number(borrow[period])
            if v is not None:
                out.append(
                    ScreenerRow(
                        "borrowings",
                        period,
                        round(v, 1),
                        "INR crore",
                        "Borrowings+",
                        "balance-sheet",
                    )
                )
        if cash and period in cash:
            v = _parse_number(cash[period])
            if v is not None:
                out.append(
                    ScreenerRow(
                        "cash_and_equivalents",
                        period,
                        round(v, 1),
                        "INR crore",
                        "Cash Equivalents",
                        "balance-sheet",
                        note="From screener Other Assets (+) breakdown",
                    )
                )
    return out


def parse_screener_page(
    html: str,
    schedules: dict[str, dict[str, dict[str, str]]] | None = None,
    *,
    max_periods: int | None = None,
) -> list[ScreenerRow]:
    tree = HTMLParser(html)
    pl_periods, pl = _annual_table(tree, "profit-loss")
    ratio_periods, ratios = _annual_table(tree, "ratios")
    bs_periods, bs = _annual_table(tree, "balance-sheet")

    sched_norm = _schedule_to_table(schedules or {})
    sched_pl = sched_norm.get("profit-loss", {})
    sched_bs = sched_norm.get("balance-sheet", {})

    periods = latest_periods(
        set(pl_periods) | set(ratio_periods) | set(bs_periods),
        count=max_periods or 99,
    )
    if max_periods:
        periods = latest_periods(periods, count=max_periods)

    return (
        _map_profit_loss(periods, pl, schedule_pl=sched_pl)
        + _map_ratios(periods, ratios)
        + _map_balance(periods, bs, schedule_bs=sched_bs)
    )


def _fetch_html_httpx(url: str, *, client: httpx.Client | None = None) -> str | None:
    try:
        if client is not None:
            r = client.get(url)
        else:
            r = httpx.get(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True, timeout=30)
        if r.status_code == 200 and "profit-loss" in r.text:
            return r.text
    except Exception:
        pass
    return None


def _fetch_html_firecrawl(url: str) -> str | None:
    if not SETTINGS.firecrawl_api_key:
        return None
    try:
        BUDGET.charge("firecrawl")
    except BudgetExceeded:
        return None
    try:
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=SETTINGS.firecrawl_api_key)
        doc = app.scrape(
            url,
            formats=["html"],
            only_main_content=False,
            timeout=60000,
        )
        html = getattr(doc, "html", None) or ""
        return html if "profit-loss" in html else None
    except Exception:
        return None


def fetch_screener_html(
    ticker: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[str, str, int | None]:
    """Return (html, via, company_id)."""
    url = screener_url(ticker)
    html = _fetch_html_httpx(url, client=client)
    if html:
        via = "session" if client is not None else "httpx"
        return html, via, company_id_from_html(html)

    html = _fetch_html_firecrawl(url)
    if html:
        return html, "firecrawl", company_id_from_html(html)

    raise ScreenerParseError(f"Could not fetch screener.in page for {ticker}")


class ScreenerParseError(Exception):
    pass


def fetch_screener_financials(
    ctx: RunContext,
    entity: Entity,
    *,
    ticker: str | None = None,
    start_id: int = 0,
    max_periods: int = 10,
) -> tuple[list[FinancialCell], str | None]:
    sym = normalize_ticker(ticker or entity.ticker, hint=entity.input_hint, name=entity.canonical_name)
    if not sym:
        return [], "no NSE/BSE ticker — pass --hint 'NSE: TICKER' or --ticker"

    url = screener_url(sym)
    session = None
    try:
        session = get_screener_client()
    except ScreenerAuthError as e:
        ctx.note(f"[financials/screener] login failed: {e}")
    try:
        html, via, company_id = fetch_screener_html(sym, client=session)
    except ScreenerParseError as e:
        return [], str(e)

    if "Page not found" in html or "could not find" in html.lower():
        return [], f"screener.in has no page for ticker {sym}"

    schedules: dict[str, dict[str, dict[str, str]]] = {}
    schedule_count = 0
    if company_id:
        parents = discover_schedule_parents(html)
        schedules = fetch_all_schedules(company_id, parents)
        schedule_count = sum(len(lines) for lines in schedules.values())
        ctx.note(
            f"[financials/screener] expanded {schedule_count} schedule rows "
            f"from {len(parents)} (+) parents via API"
        )

    mapped = parse_screener_page(html, schedules, max_periods=max_periods)
    if not mapped:
        return [], f"no tables parsed for {sym}"

    source = _register_screener_source(ctx, sym, url)
    cells: list[FinancialCell] = []
    n = start_id
    for row in mapped:
        n += 1
        quote = (
            f"screener.in {row.section} {row.period} | {row.screener_line}: "
            f"{row.numeric_value} {row.unit}"
        )
        if row.note:
            quote += f" ({row.note})"
        cells.append(
            FinancialCell(
                id=f"fin-{n}",
                section=Section.financials,
                text=f"{row.metric} {row.period}: {row.numeric_value} {row.unit}".strip(),
                claim_type=ClaimType.quantitative,
                metric=canonical_metric(row.metric),
                period=row.period,
                numeric_value=row.numeric_value,
                unit=row.unit or FINANCIAL_UNITS.get(row.metric, ""),
                basis="reported",
                evidence=[
                    Evidence(
                        source_id=source.id,
                        exact_quote=quote,
                        locator={
                            "provider": "screener",
                            "ticker": sym,
                            "section": row.section,
                            "line": row.screener_line,
                            "url": url,
                            "schedules_api": bool(schedules),
                        },
                    )
                ],
            )
        )

    periods = sorted({c.period for c in cells}, key=lambda p: int("".join(x for x in p if x.isdigit()) or "0"))
    ctx.note(
        f"[financials/screener] {sym} page={via}: {len(cells)} metric cells, "
        f"periods {', '.join(periods[-6:])}"
    )
    cells.extend(derive_financial_metrics(cells, start_id=n))
    return cells, None


def _register_screener_source(ctx: RunContext, ticker: str, url: str) -> Source:
    for src in ctx.sources.values():
        if "screener.in" in src.url and ticker in src.url:
            return src
    sid = ctx.new_source_id()
    source = Source(
        id=sid,
        url=url,
        title=f"Screener.in consolidated — {ticker}",
        publisher="screener.in",
        source_type=SourceType.financial_api,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        access="public",
    )
    ctx.register_source(source)
    return source
