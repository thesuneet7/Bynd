"""Products/customers extraction for unlisted companies via official website crawl."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from listed_docs.extraction.models import KnowledgeGraph

from .website.about import WebsiteAbout, extract_website_about, parse_about_from_sections
from .website.pipeline import run_website_extraction


@dataclass
class UnlistedExtractionResult:
    products: list[dict]
    customers: list[dict]
    notes: list[str]
    kg: KnowledgeGraph | None = None
    website_about: WebsiteAbout | None = None


def _load_existing_knowledge_graph(path: Path) -> KnowledgeGraph | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return KnowledgeGraph(
        company=str(data.get("company") or ""),
        ticker=str(data.get("ticker") or data.get("website") or ""),
        products=list(data.get("products") or []),
        customers=list(data.get("customers") or []),
        documents_processed=int(data.get("documents_processed") or 0),
        extraction_notes=list(data.get("extraction_notes") or []),
    )


def extract_unlisted_products_customers(
    *,
    company_name: str,
    registry_id: str | None = None,
    website: str | None = None,
    output_dir: Path | None = None,
) -> UnlistedExtractionResult:
    """Scrape the company's official website and extract verified products/customers."""
    if not website:
        return UnlistedExtractionResult(
            products=[],
            customers=[],
            notes=[
                "Unlisted products/customers skipped: no official website provided.",
                f"Company: {company_name}",
                f"CIN: {registry_id or 'unknown'}",
                "Pass --website or set entity.website to enable Firecrawl agent extraction.",
            ],
        )
    if output_dir is None:
        return UnlistedExtractionResult(
            products=[],
            customers=[],
            notes=["Unlisted extraction requires output_dir for website artifacts."],
        )

    website_dir = output_dir / "website"
    existing = _load_existing_knowledge_graph(website_dir / "knowledge_graph.json")
    if existing is not None:
        notes = list(existing.extraction_notes)
        notes.insert(0, f"Reused existing website knowledge graph: {website_dir / 'knowledge_graph.json'}")
        about = extract_website_about(website=website, company_name=company_name)
        if about is not None:
            notes.append(f"Website about parsed from {about.url}")
        return UnlistedExtractionResult(
            products=existing.products,
            customers=existing.customers,
            notes=notes,
            kg=existing,
            website_about=about,
        )

    kg, agent_sections = run_website_extraction(
        company_name=company_name,
        website=website,
        output_dir=website_dir,
        registry_id=registry_id,
        return_sections=True,
    )
    about = parse_about_from_sections(agent_sections) or extract_website_about(
        website=website,
        company_name=company_name,
    )
    notes = list(kg.extraction_notes)
    if about is not None:
        notes.append(f"Website about parsed from {about.url}")
    return UnlistedExtractionResult(
        products=kg.products,
        customers=kg.customers,
        notes=notes,
        kg=kg,
        website_about=about,
    )
