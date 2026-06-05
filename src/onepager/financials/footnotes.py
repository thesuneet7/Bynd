"""Footnotes for derived financial metrics: formula and input values."""
from __future__ import annotations

from ..schemas import FinancialCell
from .contract import FINANCIAL_ROW_ORDER, FINANCIAL_UNITS, financial_label, period_key


def _fmt_val(v: float | None, unit: str = "") -> str:
    if v is None:
        return "—"
    s = str(int(v)) if float(v).is_integer() else f"{v:.1f}"
    if unit == "%":
        return f"{s}%"
    return f"{s} {unit}".strip() if unit else s


def _prior_period(ordered: list[str], period: str) -> str | None:
    try:
        idx = ordered.index(period)
    except ValueError:
        return None
    return ordered[idx - 1] if idx > 0 else None


def explain_derived_cell(cell: FinancialCell, by_id: dict[str, FinancialCell]) -> str:
    """Human-readable derivation for one derived cell."""
    bases = [by_id[bid] for bid in cell.derived_from if bid in by_id]
    metric = cell.metric
    period = cell.period
    result = _fmt_val(cell.numeric_value, cell.unit or FINANCIAL_UNITS.get(metric, ""))

    if metric == "revenue_growth_pct" and len(bases) >= 2:
        prev_c, cur_c = sorted(bases, key=lambda c: period_key(c.period))
        rv, cv = prev_c.numeric_value, cur_c.numeric_value
        return (
            f"**{period} — Revenue growth:** "
            f"((Revenue_{cur_c.period} − Revenue_{prev_c.period}) / |Revenue_{prev_c.period}|) × 100 "
            f"= (({_fmt_val(cv, 'INR cr')} − {_fmt_val(rv, 'INR cr')}) / |{_fmt_val(rv, 'INR cr')}|) × 100 "
            f"= **{result}**"
        )

    if metric == "material_margin" and len(bases) >= 2:
        rev = next((b for b in bases if b.metric == "revenue"), bases[0])
        cost = next((b for b in bases if b.metric == "material_cost"), bases[1])
        rv, cv = rev.numeric_value, cost.numeric_value
        return (
            f"**{period} — Material margin:** "
            f"Revenue − Material cost = {_fmt_val(rv, 'INR cr')} − {_fmt_val(cv, 'INR cr')} = **{result}**"
        )

    if metric == "material_margin_pct" and len(bases) >= 2:
        margin = next((b for b in bases if b.metric == "material_margin"), bases[0])
        rev = next((b for b in bases if b.metric == "revenue"), bases[1])
        mv, rv = margin.numeric_value, rev.numeric_value
        return (
            f"**{period} — Material margin %:** "
            f"(Material margin / Revenue) × 100 = ({_fmt_val(mv, 'INR cr')} / {_fmt_val(rv, 'INR cr')}) × 100 "
            f"= **{result}**"
        )

    if metric == "operating_ebitda_pct" and len(bases) >= 2:
        ebitda = next((b for b in bases if b.metric == "operating_ebitda"), bases[0])
        rev = next((b for b in bases if b.metric == "revenue"), bases[1])
        ev, rv = ebitda.numeric_value, rev.numeric_value
        return (
            f"**{period} — Operating EBITDA %:** "
            f"(Operating EBITDA / Revenue) × 100 = ({_fmt_val(ev, 'INR cr')} / {_fmt_val(rv, 'INR cr')}) × 100 "
            f"= **{result}**"
        )

    if metric == "net_debt" and len(bases) >= 2:
        debt = next((b for b in bases if b.metric == "borrowings"), bases[0])
        cash = next((b for b in bases if b.metric == "cash_and_equivalents"), bases[1])
        dv, cv = debt.numeric_value, cash.numeric_value
        return (
            f"**{period} — Net debt:** "
            f"Borrowings − Cash & equivalents = {_fmt_val(dv, 'INR cr')} − {_fmt_val(cv, 'INR cr')} = **{result}**"
        )

    if metric == "roce_pct" and len(bases) >= 2:
        ebit = next((b for b in bases if b.metric == "ebit"), bases[0])
        cap = next((b for b in bases if b.metric == "capital_employed"), bases[1])
        ev, cv = ebit.numeric_value, cap.numeric_value
        return (
            f"**{period} — ROCE:** "
            f"(EBIT / Capital employed) × 100 = ({_fmt_val(ev, 'INR cr')} / {_fmt_val(cv, 'INR cr')}) × 100 "
            f"= **{result}**"
        )

    inputs = ", ".join(
        f"{financial_label(b.metric, b.unit)} ({b.period}) = {_fmt_val(b.numeric_value, b.unit or '')}"
        for b in bases
    )
    return f"**{period} — {financial_label(metric, cell.unit)}:** derived from {inputs} → **{result}**"


def explain_missing_revenue_growth(
    period: str,
    *,
    ordered_periods: list[str],
    by_metric: dict[str, dict[str, FinancialCell]],
) -> str | None:
    """Why revenue growth is blank for a display column (needs prior-year revenue)."""
    if period in by_metric.get("revenue_growth_pct", {}):
        return None
    if by_metric.get("revenue", {}).get(period) is None:
        return None
    prev = _prior_period(ordered_periods, period)
    if prev is None:
        return None
    prev_rev = by_metric.get("revenue", {}).get(prev)
    if prev_rev is None or prev_rev.numeric_value in (None, 0):
        return (
            f"**{period} — Revenue growth:** not computed — "
            f"((Revenue_{period} − Revenue_{prev}) / |Revenue_{prev}|) × 100 requires "
            f"**Revenue ({prev})**; prior-year revenue is missing or zero in the scraped data."
        )
    return None


def build_footnote_index(
    cells: list[FinancialCell],
    display: list[str],
) -> dict[tuple[str, str], int]:
    """Assign stable footnote numbers for (metric, period) in display order."""
    by_id = {c.id: c for c in cells}
    by_metric: dict[str, dict[str, FinancialCell]] = {}
    for c in cells:
        by_metric.setdefault(c.metric, {})[c.period] = c
    all_periods = sorted({c.period for c in cells}, key=period_key)

    index: dict[tuple[str, str], int] = {}
    n = 0

    for period in display:
        if explain_missing_revenue_growth(period, ordered_periods=all_periods, by_metric=by_metric):
            n += 1
            index[("revenue_growth_pct", period)] = n

    for c in sorted(
        [x for x in cells if x.basis == "derived" and x.period in display],
        key=lambda x: (FINANCIAL_ROW_ORDER.index(x.metric) if x.metric in FINANCIAL_ROW_ORDER else 99, period_key(x.period)),
    ):
        key = (c.metric, c.period)
        if key in index:
            continue
        n += 1
        index[key] = n

    return index


def render_derivation_footnotes(
    cells: list[FinancialCell],
    display: list[str],
    *,
    source_url: str | None = None,
    provider_label: str | None = None,
) -> list[str]:
    """Numbered footnote lines for derived cells and explained gaps (e.g. FY23 growth)."""
    by_id = {c.id: c for c in cells}
    by_metric: dict[str, dict[str, FinancialCell]] = {}
    for c in cells:
        by_metric.setdefault(c.metric, {})[c.period] = c
    all_periods = sorted({c.period for c in cells}, key=period_key)
    idx = build_footnote_index(cells, display)

    ordered_keys = sorted(idx.keys(), key=lambda k: idx[k])
    if not ordered_keys:
        return []

    link = ""
    if source_url:
        label = provider_label or "source"
        link = f" ([{label}]({source_url}))"

    lines = ["", "### Derived metrics (footnotes)", ""]
    if source_url:
        label = provider_label or "View company page"
        lines.append(f"_Reported inputs and derivations trace to [{label}]({source_url})._")
        lines.append("")
    for metric, period in ordered_keys:
        n = idx[(metric, period)]
        if metric == "revenue_growth_pct" and period not in by_metric.get("revenue_growth_pct", {}):
            text = explain_missing_revenue_growth(
                period, ordered_periods=all_periods, by_metric=by_metric
            )
        else:
            cell = by_metric.get(metric, {}).get(period)
            text = explain_derived_cell(cell, by_id) if cell else ""
        if text:
            lines.append(f"{n}. {text}{link}")
    lines.append("")
    return lines


_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _marker(n: int) -> str:
    return str(n).translate(_SUPERSCRIPT)


def footnote_superscript(cells: list[FinancialCell], display: list[str], metric: str, period: str) -> str:
    idx = build_footnote_index(cells, display)
    n = idx.get((metric, period))
    return _marker(n) if n else ""
