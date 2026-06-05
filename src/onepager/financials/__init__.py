"""Financial table contract, derivation, and screener.in provider."""
from .contract import (
    FINANCIAL_LABELS,
    FINANCIAL_ROW_ORDER,
    FINANCIAL_UNITS,
    canonical_metric,
    financial_label,
    display_periods,
    latest_periods,
    period_key,
)
from .derivation import derive_financial_metrics
from .footnotes import build_footnote_index, footnote_superscript, render_derivation_footnotes
from .overview import (
    fetch_provider_overview,
    fetch_screener_overview,
    fetch_tofler_overview,
    parse_screener_overview,
    parse_tofler_overview,
    render_overview_markdown,
)
from .screener import fetch_screener_financials, normalize_ticker, screener_url
from .tofler import expand_legal_name, fetch_tofler_financials, legal_name_variants, resolve_tofler_company

__all__ = [
    "FINANCIAL_LABELS",
    "FINANCIAL_ROW_ORDER",
    "FINANCIAL_UNITS",
    "canonical_metric",
    "derive_financial_metrics",
    "build_footnote_index",
    "footnote_superscript",
    "render_derivation_footnotes",
    "fetch_provider_overview",
    "fetch_screener_overview",
    "fetch_tofler_overview",
    "parse_screener_overview",
    "parse_tofler_overview",
    "render_overview_markdown",
    "display_periods",
    "fetch_screener_financials",
    "expand_legal_name",
    "fetch_tofler_financials",
    "legal_name_variants",
    "resolve_tofler_company",
    "financial_label",
    "latest_periods",
    "normalize_ticker",
    "period_key",
    "screener_url",
]
