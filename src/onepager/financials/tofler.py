"""Fetch canonical financials from tofler.in for unlisted / private Indian companies."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
import httpx
from selectolax.parser import HTMLParser

from ..budget import BUDGET, BudgetExceeded
from ..config import SETTINGS
from ..schemas import ClaimType, Entity, Evidence, FinancialCell, Section, Source, SourceType
from ..tools.search import Searcher
from .contract import FINANCIAL_UNITS, canonical_metric, display_periods
from .derivation import derive_financial_metrics

if TYPE_CHECKING:
    from ..pipeline.context import RunContext

TOFLER_BASE = "https://www.tofler.in"
_USER_AGENT = "Mozilla/5.0 (compatible; ByndAI/1.0)"
# For fuzzy match only — legal suffixes are expanded before search, not stripped.
_STOP_WORDS = frozenset({"the", "and", "of", "in"})

_ABBREV_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bpvt\.?\b", "private"),
    (r"\bltd\.?\b", "limited"),
    (r"\bllp\.?\b", "limited liability partnership"),
    (r"\bplc\.?\b", "public limited company"),
    (r"\binc\.?\b", "incorporated"),
)


@dataclass(frozen=True)
class ToflerRow:
    metric: str
    period: str
    numeric_value: float
    unit: str
    tofler_line: str
    section: str
    note: str = ""


@dataclass(frozen=True)
class ToflerMatch:
    url: str
    title: str
    score: float
    cin: str | None = None


def expand_legal_name(name: str) -> str:
    """Expand Indian company abbreviations: pvt → private, ltd → limited, etc."""
    s = re.sub(r"[^a-z0-9.]+", " ", (name or "").lower()).strip()
    for pat, repl in _ABBREV_PATTERNS:
        s = re.sub(pat, f" {repl} ", s)
    s = re.sub(r"\.+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def legal_name_variants(name: str) -> list[str]:
    """Search variants: expanded full form first, then original."""
    raw = (name or "").strip()
    expanded = expand_legal_name(raw)
    variants: list[str] = []
    for v in (expanded, raw):
        if v and v not in variants:
            variants.append(v)
    return variants


def _normalize_name(name: str) -> str:
    """Token bag for fuzzy match (after abbreviation expansion)."""
    clean = re.sub(r"[^a-z0-9]+", " ", expand_legal_name(name)).strip()
    return " ".join(w for w in clean.split() if w not in _STOP_WORDS)


def _slugify(name: str) -> str:
    expanded = expand_legal_name(name)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", expanded)).strip("-")


def _slug_tokens(url: str) -> str:
    m = re.search(r"tofler\.in/([^/]+)/company/", url)
    if not m:
        return ""
    return m.group(1).replace("-", " ")


def _name_score(query: str, candidate: str, *, url: str = "") -> float:
    q = _normalize_name(query)
    if not q:
        return 0.0
    scores: list[float] = []
    c = _normalize_name(candidate)
    if c:
        if q == c or q in c or c in q:
            scores.append(0.98)
        else:
            scores.append(SequenceMatcher(None, q, c).ratio())
    slug_text = _slug_tokens(url)
    if slug_text:
        s = _normalize_name(slug_text)
        if q == s or q in s or s in q:
            scores.append(0.99)
        else:
            scores.append(SequenceMatcher(None, q, s).ratio())
    return max(scores) if scores else 0.0


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
    m = re.match(r"Mar\s+(\d{4})", (header or "").strip(), re.I)
    if not m:
        return None
    return f"FY{int(m.group(1)) % 100:02d}"


def _cin_from_url(url: str) -> str | None:
    m = re.search(r"/company/([A-Z0-9]+)/?", url)
    return m.group(1) if m else None


def _guess_tofler_url(entity: Entity) -> str | None:
    if not entity.registry_id:
        return None
    slug = _slugify(entity.canonical_name or entity.input_name)
    return f"{TOFLER_BASE}/{slug}/company/{entity.registry_id}"


def search_tofler_candidates(entity: Entity, *, max_results: int = 8) -> list[ToflerMatch]:
    """Search tofler.in using expanded legal name (pvt → private, ltd → limited)."""
    name = entity.canonical_name or entity.input_name
    expanded = expand_legal_name(name)
    seen_urls: set[str] = set()
    hits: list[ToflerMatch] = []

    queries: list[str] = []
    for variant in legal_name_variants(name):
        queries.append(f'site:tofler.in "{variant}"')
        queries.append(f"site:tofler.in {variant}")
    if entity.registry_id:
        queries.insert(0, f"site:tofler.in {entity.registry_id}")

    try:
        for query in queries:
            for hit in Searcher().search(query, max_results=max_results):
                if "tofler.in" not in hit.url or "/company/" not in hit.url:
                    continue
                if hit.url in seen_urls:
                    continue
                seen_urls.add(hit.url)
                cin = _cin_from_url(hit.url)
                if entity.registry_id and cin and cin != entity.registry_id:
                    continue
                score = max(
                    _name_score(name, hit.title, url=hit.url),
                    _name_score(expanded, hit.title, url=hit.url),
                )
                if entity.registry_id and cin == entity.registry_id:
                    score = max(score, 0.99)
                hits.append(ToflerMatch(url=hit.url, title=hit.title, score=score, cin=cin))
            if hits and hits[0].score >= 0.85:
                break
    except Exception:
        return []
    hits.sort(key=lambda m: m.score, reverse=True)
    return hits


def resolve_tofler_company(entity: Entity, *, min_score: float = 0.75) -> ToflerMatch | None:
    """Best-matching tofler.in company page for this entity."""
    if entity.registry_id:
        direct = _guess_tofler_url(entity)
        if direct:
            try:
                r = httpx.head(
                    direct,
                    headers={"User-Agent": _USER_AGENT},
                    follow_redirects=True,
                    timeout=20,
                )
                if r.status_code == 200:
                    return ToflerMatch(
                        url=str(r.url),
                        title=entity.canonical_name or entity.input_name,
                        score=1.0,
                        cin=entity.registry_id,
                    )
            except Exception:
                pass

    candidates = search_tofler_candidates(entity)
    if not candidates:
        return None
    best = candidates[0]
    if best.score < min_score:
        return None
    return best


def _fetch_html_httpx(url: str) -> str | None:
    try:
        r = httpx.get(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True, timeout=45)
        if r.status_code == 200 and len(r.text) > 5000 and "Mar 20" in r.text:
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

        doc = FirecrawlApp(api_key=SETTINGS.firecrawl_api_key).scrape(
            url,
            formats=["html"],
            only_main_content=False,
            timeout=90000,
        )
        html = getattr(doc, "html", None) or ""
        return html if len(html) > 5000 else None
    except Exception:
        return None


def _page_looks_paywalled(html: str) -> bool:
    """True when the public page has no annual tables (common on paid-only profiles)."""
    if "Mar 20" in html and "Sales" in html:
        return False
    markers = ("buy report", "subscription plans", "see price & plans", "sign up to view")
    lowered = html.lower()
    return any(m in lowered for m in markers) and "profit & loss" not in lowered


def fetch_tofler_html(url: str) -> tuple[str, str]:
    html = _fetch_html_httpx(url)
    if html:
        return html, "httpx"
    html = _fetch_html_firecrawl(url)
    if html:
        return html, "firecrawl"
    raise ToflerParseError(f"Could not fetch tofler.in page: {url}")


class ToflerParseError(Exception):
    pass


def _parse_tables(html: str) -> list[tuple[list[str], dict[str, dict[str, str]]]]:
    tree = HTMLParser(html)
    parsed: list[tuple[list[str], dict[str, dict[str, str]]]] = []
    for table in tree.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [c.text(strip=True) for c in rows[0].css("th,td")]
        periods = [_period_label(h) for h in headers[1:]]
        if not any(periods):
            continue
        data: dict[str, dict[str, str]] = {}
        for row in rows[1:]:
            cells = [c.text(strip=True) for c in row.css("th,td")]
            if not cells or not cells[0]:
                continue
            label = re.sub(r"\s*\+\s*$", "", cells[0]).strip()
            if not label or label.endswith(":"):
                continue
            for period, val in zip(periods, cells[1:]):
                if period:
                    data.setdefault(label, {})[period] = val
        if data:
            parsed.append((periods, data))
    return parsed


def _pick_table(
    tables: list[tuple[list[str], dict[str, dict[str, str]]]],
    *,
    must_have: tuple[str, ...],
    prefer_label: str | None = None,
) -> dict[str, dict[str, str]]:
    candidates = [t for _, t in tables if all(any(m in row for row in t) for m in must_have)]
    if not candidates:
        return {}
    if prefer_label and len(candidates) > 1:
        scored = []
        for t in candidates:
            row = t.get(prefer_label, {})
            probe = row.get("Mar 2025") or (row.get(max(row.keys(), key=str)) if row else "")
            val = _parse_number(probe or "")
            scored.append((val or 0, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    return candidates[-1]


def _get_row(table: dict[str, dict[str, str]], *names: str) -> dict[str, str] | None:
    for name in names:
        if name in table:
            return table[name]
    return None


def _map_from_tables(
    periods: list[str],
    *,
    pl: dict[str, dict[str, str]],
    ratios: dict[str, dict[str, str]],
    balance: dict[str, dict[str, str]],
    highlights: dict[str, dict[str, str]],
) -> list[ToflerRow]:
    out: list[ToflerRow] = []
    sales = _get_row(pl, "Sales+", "Sales") or _get_row(highlights, "Sales")
    cost = _get_row(pl, "Cost of goods", "Cost of Goods", "Raw material cost")
    growth = _get_row(pl, "Sales growth %", "Sales growth %")
    op = _get_row(pl, "Operating profit", "Operating Profit")
    opm = _get_row(pl, "Operating margin %", "Operating margin %")
    dep = _get_row(pl, "Depreciation")
    gross_m = _get_row(highlights, "Gross margin")
    roce = _get_row(ratios, "Pre-tax ROCE", "ROCE %", "ROCE")
    nwc = _get_row(ratios, "Working Capital Days")
    borrow = _get_row(balance, "Borrowings+", "Borrowings")
    cash = _get_row(balance, "Cash and cash equivalents", "Cash Equivalents")

    for period in periods:
        rev_val = None
        if sales and period in sales:
            rev_val = _parse_number(sales[period])
            if rev_val is not None:
                out.append(ToflerRow("revenue", period, round(rev_val, 1), "INR crore", "Sales+", "profit-loss"))

        mat_cost = None
        if cost and period in cost:
            mat_cost = _parse_number(cost[period])
        if mat_cost is not None:
            out.append(
                ToflerRow(
                    "material_cost",
                    period,
                    round(mat_cost, 1),
                    "INR crore",
                    "Cost of goods",
                    "profit-loss",
                    note="From tofler P&L breakdown",
                )
            )
        elif gross_m and rev_val and period in gross_m:
            gm_pct = _parse_number(gross_m[period])
            if gm_pct is not None:
                mat_cost = round(rev_val * (1 - gm_pct / 100), 1)
                out.append(
                    ToflerRow(
                        "material_cost",
                        period,
                        mat_cost,
                        "INR crore",
                        "implied from Gross margin",
                        "highlights",
                        note="Cost of goods unavailable; inferred from gross margin %",
                    )
                )

        if rev_val is not None and mat_cost is not None:
            margin = round(rev_val - mat_cost, 1)
            out.append(ToflerRow("material_margin", period, margin, "INR crore", "Sales − cost of goods", "profit-loss"))
            if rev_val:
                out.append(
                    ToflerRow("material_margin_pct", period, round(margin / rev_val * 100, 1), "%", "Material margin %", "profit-loss")
                )

        if growth and period in growth:
            g = _parse_number(growth[period])
            if g is not None:
                out.append(ToflerRow("revenue_growth_pct", period, round(g, 1), "%", "Sales growth %", "profit-loss"))

        if op and dep and period in op and period in dep:
            o, d = _parse_number(op[period]), _parse_number(dep[period])
            if o is not None and d is not None:
                out.append(
                    ToflerRow(
                        "operating_ebitda",
                        period,
                        round(o + d, 1),
                        "INR crore",
                        "Operating profit + Depreciation",
                        "profit-loss",
                    )
                )
        if opm and period in opm:
            v = _parse_number(opm[period])
            if v is not None:
                out.append(
                    ToflerRow(
                        "operating_ebitda_pct",
                        period,
                        round(v, 1),
                        "%",
                        "Operating margin %",
                        "profit-loss",
                        note="Closest to operating EBITDA %",
                    )
                )

        if roce and period in roce:
            v = _parse_number(roce[period])
            if v is not None:
                out.append(
                    ToflerRow(
                        "roce_pct",
                        period,
                        round(v, 1),
                        "%",
                        "Pre-tax ROCE",
                        "ratios",
                        note="Closest to ROCE",
                    )
                )
        if nwc and period in nwc:
            v = _parse_number(nwc[period])
            if v is not None:
                out.append(ToflerRow("nwc_days", period, round(v, 1), "days", "Working Capital Days", "ratios"))

        if borrow and period in borrow:
            v = _parse_number(borrow[period])
            if v is not None:
                out.append(ToflerRow("borrowings", period, round(v, 1), "INR crore", "Borrowings+", "balance-sheet"))
        if cash and period in cash:
            v = _parse_number(cash[period])
            if v is not None:
                out.append(
                    ToflerRow(
                        "cash_and_equivalents",
                        period,
                        round(v, 1),
                        "INR crore",
                        "Cash and cash equivalents",
                        "balance-sheet",
                    )
                )
    return out


def parse_tofler_html(html: str, *, max_periods: int = 10) -> list[ToflerRow]:
    tables = _parse_tables(html)
    if not tables:
        return []

    all_periods = {p for _, t in tables for row in t.values() for p in row}
    periods = display_periods(all_periods, count=max_periods)

    pl = _pick_table(tables, must_have=("Cost of goods",), prefer_label="Sales+")
    if not pl:
        pl = _pick_table(tables, must_have=("Sales+",), prefer_label="Sales+")
    highlights = _pick_table(tables, must_have=("Gross margin", "Operating profit"))
    balance = _pick_table(tables, must_have=("Borrowings+",), prefer_label="Borrowings+")
    if not balance:
        balance = _pick_table(tables, must_have=("Cash and cash equivalents",))
    ratios = _pick_table(tables, must_have=("Pre-tax ROCE",))
    if not ratios:
        ratios = _pick_table(tables, must_have=("Working Capital Days",))

    return _map_from_tables(
        periods,
        pl=pl,
        ratios=ratios,
        balance=balance,
        highlights=highlights,
    )


def fetch_tofler_financials(
    ctx: RunContext,
    entity: Entity,
    *,
    start_id: int = 0,
    max_periods: int = 3,
) -> tuple[list[FinancialCell], str | None]:
    match = resolve_tofler_company(entity)
    if not match:
        expanded = expand_legal_name(entity.canonical_name or entity.input_name)
        return [], (
            f"no matching company on tofler.in "
            f"(searched as: {expanded!r}; need CIN or clearer legal name)"
        )

    expanded = expand_legal_name(entity.canonical_name or entity.input_name)
    ctx.note(
        f"[financials/tofler] matched {match.title!r} (score={match.score:.2f}, "
        f"query name: {expanded!r}) -> {match.url}"
    )

    try:
        html, via = fetch_tofler_html(match.url)
    except ToflerParseError as e:
        return [], str(e)

    if _page_looks_paywalled(html):
        return [], (
            "tofler.in page loaded but detailed financial tables are not visible "
            "(likely requires a paid Tofler subscription or login — free tier may only show summaries)"
        )

    mapped = parse_tofler_html(html, max_periods=max_periods)
    if not mapped:
        return [], "no financial tables parsed from tofler.in (page had no Mar 20xx P&L tables)"

    source = _register_tofler_source(ctx, match.url, entity)
    cells: list[FinancialCell] = []
    n = start_id
    for row in mapped:
        n += 1
        quote = (
            f"tofler.in {row.section} {row.period} | {row.tofler_line}: "
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
                            "provider": "tofler",
                            "url": match.url,
                            "section": row.section,
                            "line": row.tofler_line,
                            "match_score": match.score,
                            "cin": match.cin,
                        },
                    )
                ],
            )
        )

    periods = sorted({c.period for c in cells}, key=lambda p: int("".join(x for x in p if x.isdigit()) or "0"))
    ctx.note(
        f"[financials/tofler] via {via}: {len(cells)} cells, periods {', '.join(periods)}"
    )
    cells.extend(derive_financial_metrics(cells, start_id=n))
    return cells, None


def _register_tofler_source(ctx: RunContext, url: str, entity: Entity) -> Source:
    for src in ctx.sources.values():
        if url in src.url:
            return src
    sid = ctx.new_source_id()
    source = Source(
        id=sid,
        url=url,
        title=f"Tofler.in — {entity.canonical_name or entity.input_name}",
        publisher="tofler.in",
        source_type=SourceType.financial_api,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        access="public",
    )
    ctx.register_source(source)
    return source
