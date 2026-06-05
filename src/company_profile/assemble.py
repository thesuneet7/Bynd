"""Assemble overview, financials, products, and customers into JSON + markdown."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from company_scrape.pipeline import _financial_table
from onepager.financials import display_periods
from onepager.financials.overview import ProviderOverview
from onepager.schemas import Entity, FinancialCell

from listed_docs.extraction.models import KnowledgeGraph


def _overview_dict(overview: ProviderOverview | None) -> dict[str, Any]:
    if not overview:
        return {"provider": "", "url": "", "about": "", "key_points": [], "note": None}
    return {
        "provider": overview.provider,
        "url": overview.url,
        "about": overview.about,
        "key_points": [{"title": t, "body": b} for t, b in overview.key_points],
        "note": overview.note,
    }


def _financials_dict(cells: list[FinancialCell], *, source_url: str, provider: str) -> dict[str, Any]:
    periods = display_periods([c.period for c in cells], count=3, skip_latest=1)
    by_metric: dict[str, dict[str, Any]] = {}
    for c in cells:
        row = by_metric.setdefault(c.metric, {"metric": c.metric, "unit": c.unit, "periods": {}})
        row["periods"][c.period] = {
            "value": c.numeric_value,
            "basis": c.basis,
            "derived_from": list(c.derived_from or []),
        }
    return {
        "provider": provider,
        "source_url": source_url,
        "display_periods": periods,
        "metrics": list(by_metric.values()),
        "cell_count": len(cells),
    }


def _products_customers_dict(kg: KnowledgeGraph | None) -> dict[str, Any]:
    if kg is None:
        return {"products": [], "customers": [], "documents_processed": 0}
    return {
        "products": kg.products,
        "customers": kg.customers,
        "documents_processed": kg.documents_processed,
    }


def build_profile_json(
    *,
    entity: Entity,
    overview: ProviderOverview | None,
    cells: list[FinancialCell],
    financials_url: str,
    financials_provider: str,
    kg: KnowledgeGraph | None,
    listed_docs_dir: str | None = None,
    website_dir: str | None = None,
) -> dict[str, Any]:
    return {
        "company": entity.canonical_name or entity.input_name,
        "ticker": entity.ticker,
        "listing_status": entity.listing_status,
        "country": entity.country,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": _overview_dict(overview),
        "financials": _financials_dict(cells, source_url=financials_url, provider=financials_provider),
        "products_customers": _products_customers_dict(kg),
        "paths": {
            "listed_docs": listed_docs_dir,
            "website": website_dir,
        },
    }


def _source_label(kg: KnowledgeGraph, *, listing_status: str) -> str:
    if listing_status != "listed":
        return "company website"
    return "exchange filings"


def _format_citation(cite: dict[str, Any]) -> str:
    url = cite.get("url") or ""
    source = cite.get("source") or cite.get("type") or "source"
    if url.startswith("http"):
        return f"[{source}]({url})"
    page = cite.get("page")
    path = cite.get("local_path") or ""
    if page:
        return f"{source} · `{path}` · p.{page}"
    return f"{source} · `{path}`"


def _md_products_customers(kg: KnowledgeGraph | None, *, listing_status: str = "listed") -> list[str]:
    lines: list[str] = []
    if kg is None:
        lines += ["## Products & customers", "", "_Not extracted._", ""]
        return lines

    source_label = _source_label(kg, listing_status=listing_status)
    lines += [
        "## Products",
        "",
        f"_{len(kg.products)} verified products from {source_label}._",
        "",
    ]
    if not kg.products:
        lines.append("_None verified._")
        lines.append("")
    for row in kg.products:
        cross = "cross-checked" if row.get("cross_checked") else "verified"
        lines.append(f"- **{row['product']}** ({cross})")
        for cite in row.get("citations") or []:
            lines.append(f"  - {_format_citation(cite)}")
            quote = (cite.get("quote") or "").strip()
            if quote:
                lines.append(f"    > {quote}")
        lines.append("")

    lines += [
        "## Customers",
        "",
        f"_{len(kg.customers)} verified customers from {source_label}._",
        "",
    ]
    if not kg.customers:
        lines.append("_None verified._")
        lines.append("")
    for row in kg.customers:
        cross = "cross-checked" if row.get("cross_checked") else "verified"
        lines.append(f"- **{row['customer']}** ({cross})")
        for cite in row.get("citations") or []:
            lines.append(f"  - {_format_citation(cite)}")
            quote = (cite.get("quote") or "").strip()
            if quote:
                lines.append(f"    > {quote}")
        lines.append("")

    return lines


def build_profile_markdown(
    *,
    entity: Entity,
    overview: ProviderOverview | None,
    overview_md: list[str],
    cells: list[FinancialCell],
    financials_url: str,
    financials_provider: str,
    kg: KnowledgeGraph | None,
    scrape_error: str | None = None,
) -> str:
    name = entity.canonical_name or entity.input_name
    lines = [
        f"# {name}",
        "",
        f"**Ticker:** `{entity.ticker or '—'}` · **Listing:** {entity.listing_status or 'unknown'}",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "Unified profile: screener/tofler overview & financials + products/customers "
        "(NSE/BSE filings for listed; official website for unlisted).",
        "",
    ]

    lines.extend(overview_md)
    lines.append("")

    if scrape_error:
        lines += [f"**Financials error:** {scrape_error}", ""]
    elif cells:
        lines.extend(
            ["### Financials", ""]
            + _financial_table(
                cells,
                source_note=f"[{financials_provider}]({financials_url})",
                source_url=financials_url,
                provider_label=financials_provider,
            )
        )
        lines.append("")

    lines.extend(_md_products_customers(kg, listing_status=entity.listing_status or "unknown"))
    return "\n".join(lines)


def load_knowledge_graph(path: Path) -> KnowledgeGraph | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return KnowledgeGraph(
        company=str(data.get("company") or ""),
        ticker=str(data.get("ticker") or ""),
        products=list(data.get("products") or []),
        customers=list(data.get("customers") or []),
        documents_processed=int(data.get("documents_processed") or 0),
        extraction_notes=list(data.get("extraction_notes") or []),
    )
