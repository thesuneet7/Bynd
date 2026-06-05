"""Canonical financial table contract."""
from __future__ import annotations

import re
from datetime import datetime, timezone


FINANCIAL_ROW_ORDER: list[str] = [
    "revenue",
    "revenue_growth_pct",
    "material_margin",
    "material_margin_pct",
    "operating_ebitda",
    "operating_ebitda_pct",
    "nwc_days",
    "roce_pct",
    "net_debt",
]

FINANCIAL_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "revenue_growth_pct": "Revenue growth",
    "material_margin": "Material margin",
    "material_margin_pct": "Material margin",
    "operating_ebitda": "Operating EBITDA",
    "operating_ebitda_pct": "Operating EBITDA",
    "nwc_days": "Net working capital",
    "roce_pct": "ROCE",
    "net_debt": "Net debt",
}

FINANCIAL_UNITS: dict[str, str] = {
    "revenue": "INR crore",
    "revenue_growth_pct": "%",
    "material_margin": "INR crore",
    "material_margin_pct": "%",
    "operating_ebitda": "INR crore",
    "operating_ebitda_pct": "%",
    "nwc_days": "days",
    "roce_pct": "%",
    "net_debt": "INR crore",
}

_ALIASES: dict[str, str] = {
    "sales": "revenue",
    "total income": "revenue",
    "revenue from operations": "revenue",
    "revenue": "revenue",
    "growth": "revenue_growth_pct",
    "revenue growth": "revenue_growth_pct",
    "revenue growth %": "revenue_growth_pct",
    "revenue growth pct": "revenue_growth_pct",
    "material cost": "material_cost",
    "cost of materials consumed": "material_cost",
    "raw material cost": "material_cost",
    "material margin": "material_margin",
    "gross margin": "material_margin_pct",
    "material margin %": "material_margin_pct",
    "material margin pct": "material_margin_pct",
    "ebitda": "operating_ebitda",
    "operating ebitda": "operating_ebitda",
    "operating profit before depreciation": "operating_ebitda",
    "pbdt": "operating_ebitda",
    "pbdit": "operating_ebitda",
    "ebitda margin": "operating_ebitda_pct",
    "operating ebitda %": "operating_ebitda_pct",
    "operating ebitda pct": "operating_ebitda_pct",
    "borrowings": "borrowings",
    "total borrowings": "borrowings",
    "total debt": "borrowings",
    "gross debt": "borrowings",
    "cash": "cash_and_equivalents",
    "cash and cash equivalents": "cash_and_equivalents",
    "net debt": "net_debt",
    "net working capital days": "nwc_days",
    "nwc days": "nwc_days",
    "working capital days": "nwc_days",
    "roce": "roce_pct",
    "roce %": "roce_pct",
    "return on capital employed": "roce_pct",
    "ebit": "ebit",
    "capital employed": "capital_employed",
}


def canonical_metric(metric: str) -> str:
    clean = re.sub(r"[^a-z0-9%]+", " ", (metric or "").lower()).strip()
    clean = clean.replace(" percent", " %")
    return _ALIASES.get(clean, clean.replace(" ", "_"))


def period_key(period: str) -> int:
    digits = "".join(ch for ch in period if ch.isdigit())
    if not digits:
        return -1
    value = int(digits[-2:])
    return 2000 + value if value < 70 else 1900 + value


def latest_periods(periods: set[str] | list[str], *, count: int = 3) -> list[str]:
    ordered = sorted({p for p in periods if p}, key=period_key)
    return ordered[-count:]


def display_periods(
    periods: set[str] | list[str],
    *,
    count: int = 3,
    skip_latest: int = 1,
) -> list[str]:
    """FY columns for the one-pager (latest `count` years; skip trailing forward/partial FY if present)."""
    ordered = sorted({p for p in periods if p}, key=period_key)
    if skip_latest and len(ordered) > count:
        now = datetime.now(timezone.utc)
        fy_end_year = now.year if now.month >= 4 else now.year - 1
        newest_yy = period_key(ordered[-1]) % 100
        newest_year = 2000 + newest_yy if newest_yy < 70 else 1900 + newest_yy
        if newest_year >= fy_end_year:
            ordered = ordered[:-skip_latest]
    return ordered[-count:]


def financial_label(metric: str, unit: str | None = None) -> str:
    label = FINANCIAL_LABELS.get(metric, metric.replace("_", " ").title())
    unit = unit or FINANCIAL_UNITS.get(metric)
    return f"{label} ({unit})" if unit else label
