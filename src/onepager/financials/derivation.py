"""Derived financial metrics for the canonical 3-FY table."""
from __future__ import annotations

from .contract import FINANCIAL_UNITS, canonical_metric, period_key
from ..schemas import ClaimType, FinancialCell, Section


def derive_financial_metrics(reported: list[FinancialCell], *, start_id: int) -> list[FinancialCell]:
    derived: list[FinancialCell] = []
    n = start_id

    by_metric: dict[str, dict[str, FinancialCell]] = {}
    for cell in reported:
        metric = canonical_metric(cell.metric)
        by_metric.setdefault(metric, {})[cell.period] = cell

    all_periods = sorted({c.period for c in reported}, key=lambda p: period_key(p))

    def cell(metric: str, period: str) -> FinancialCell | None:
        return by_metric.get(metric, {}).get(period)

    def has(metric: str, period: str) -> bool:
        return cell(metric, period) is not None

    def add(metric: str, period: str, value: float, bases: list[FinancialCell], unit: str | None = None) -> None:
        nonlocal n
        if has(metric, period):
            return
        n += 1
        rounded = round(value, 1)
        derived.append(
            FinancialCell(
                id=f"fin-{n}",
                section=Section.financials,
                text=f"{metric} {period}: {rounded} {unit or FINANCIAL_UNITS.get(metric, '')}".strip(),
                claim_type=ClaimType.quantitative,
                metric=metric,
                period=period,
                numeric_value=rounded,
                unit=unit or FINANCIAL_UNITS.get(metric, ""),
                basis="derived",
                derived_from=[b.id for b in bases],
                evidence=[ev for b in bases for ev in b.evidence],
            )
        )

    revenue = by_metric.get("revenue", {})
    for prev, cur in zip(all_periods, all_periods[1:]):
        prev_cell, cur_cell = revenue.get(prev), revenue.get(cur)
        if prev_cell and cur_cell and prev_cell.numeric_value not in (None, 0) and cur_cell.numeric_value is not None:
            add(
                "revenue_growth_pct",
                cur,
                (cur_cell.numeric_value - prev_cell.numeric_value) / abs(prev_cell.numeric_value) * 100,
                [prev_cell, cur_cell],
                "%",
            )

    for period in all_periods:
        rev = cell("revenue", period)
        mat_cost = cell("material_cost", period)
        mat_margin = cell("material_margin", period)
        op_ebitda = cell("operating_ebitda", period)
        borrowings = cell("borrowings", period)
        cash = cell("cash_and_equivalents", period)
        ebit = cell("ebit", period)
        capital_employed = cell("capital_employed", period)

        if rev and mat_cost and rev.numeric_value is not None and mat_cost.numeric_value is not None:
            add("material_margin", period, rev.numeric_value - mat_cost.numeric_value, [rev, mat_cost])
            mat_margin = cell("material_margin", period) or (derived[-1] if derived else None)

        if rev and mat_margin and rev.numeric_value not in (None, 0) and mat_margin.numeric_value is not None:
            add("material_margin_pct", period, mat_margin.numeric_value / rev.numeric_value * 100, [mat_margin, rev], "%")

        if rev and op_ebitda and rev.numeric_value not in (None, 0) and op_ebitda.numeric_value is not None:
            add("operating_ebitda_pct", period, op_ebitda.numeric_value / rev.numeric_value * 100, [op_ebitda, rev], "%")

        if borrowings and cash and borrowings.numeric_value is not None and cash.numeric_value is not None:
            add("net_debt", period, borrowings.numeric_value - cash.numeric_value, [borrowings, cash])

        if ebit and capital_employed and capital_employed.numeric_value not in (None, 0) and ebit.numeric_value is not None:
            add("roce_pct", period, ebit.numeric_value / capital_employed.numeric_value * 100, [ebit, capital_employed], "%")

    return derived
