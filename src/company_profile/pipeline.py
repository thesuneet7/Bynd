"""Run overview/financials scrape + listed-docs fetch/extract + unified export."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from company_scrape.pipeline import run_company_scrape
from listed_docs.context import ListedDocsContext
from listed_docs.extraction import run_extraction
from listed_docs.pipeline import run_listed_docs_fetch
from listed_docs.unlisted import extract_unlisted_products_customers
from onepager.config import OUTPUTS_DIR
from onepager.financials.overview import ProviderOverview, merge_overview_with_website, render_overview_markdown
from onepager.schemas import Entity, FinancialCell

from .assemble import build_profile_json, build_profile_markdown, load_knowledge_graph


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


@dataclass
class ProfileResult:
    entity: Entity
    output_dir: Path
    json_path: Path
    markdown_path: Path
    overview: ProviderOverview | None = None
    cells: list[FinancialCell] = field(default_factory=list)
    kg: object = None
    log: list[str] = field(default_factory=list)
    error: str | None = None


def run_company_profile(
    entity: Entity,
    *,
    output_dir: Path | None = None,
    provider: str = "screener",
    years: int = 3,
    skip_fetch: bool = False,
    skip_extract: bool = False,
    force_extract: bool = False,
    force_screener_login: bool = False,
) -> ProfileResult:
    slug = _slugify(entity.ticker or entity.canonical_name or entity.input_name)
    out = output_dir or (OUTPUTS_DIR / slug)
    out.mkdir(parents=True, exist_ok=True)
    listed_dir = out / "listed_docs"

    log: list[str] = []

    # 1) Overview + financials (screener/tofler)
    log.append("Step 1: company overview + financials")
    scrape = run_company_scrape(
        entity,
        provider=provider,
        force_screener_login=force_screener_login,
    )
    log.extend(scrape.log)
    if scrape.error:
        log.append(f"scrape warning: {scrape.error}")

    overview = scrape.overview
    overview_md = scrape.overview_markdown

    # 2) Listed docs fetch (listed companies only)
    kg = None
    if entity.listing_status == "listed" and entity.ticker:
        log.append("Step 2: fetch NSE/BSE annual reports + presentations")
        if not skip_fetch:
            run_listed_docs_fetch(
                company_name=entity.canonical_name or entity.input_name,
                ticker=entity.ticker,
                output_dir=listed_dir,
                years=years,
            )
        else:
            log.append("skipped fetch (--skip-fetch)")

        if listed_dir.joinpath("manifest.json").exists():
            log.append("Step 3: extract products + customers from PDFs")
            if not skip_extract:
                ctx = ListedDocsContext(
                    company_name=entity.canonical_name or entity.input_name,
                    ticker=entity.ticker,
                    output_dir=listed_dir,
                )
                kg = run_extraction(ctx, force=force_extract)
            else:
                kg = load_knowledge_graph(listed_dir / "knowledge_graph.json")
                log.append("skipped extract (--skip-extract)")
        else:
            log.append("no manifest — skipping extraction")
    else:
        log.append("unlisted — listed-docs NSE/BSE skipped")
        if entity.website:
            log.append(f"Step 2: website agent crawl ({entity.website})")
        unlisted = extract_unlisted_products_customers(
            company_name=entity.canonical_name or entity.input_name,
            registry_id=entity.registry_id,
            website=entity.website,
            output_dir=out,
        )
        log.extend(unlisted.notes)
        kg = unlisted.kg
        if unlisted.website_about is not None:
            overview = merge_overview_with_website(
                overview,
                website_about=unlisted.website_about.about,
                website_about_url=unlisted.website_about.url,
                website_sections=unlisted.website_about.sections,
            )
            log.append(f"merged website about into overview ({unlisted.website_about.url})")
            overview_md = render_overview_markdown(overview)

    # 4) Assemble unified profile
    log.append("Step 4: export company profile JSON + markdown")
    profile = build_profile_json(
        entity=entity,
        overview=overview,
        cells=scrape.cells,
        financials_url=scrape.url,
        financials_provider=scrape.provider,
        kg=kg,
        listed_docs_dir=str(listed_dir.relative_to(out)) if listed_dir.exists() else None,
        website_dir=str((out / "website").relative_to(out)) if (out / "website").exists() else None,
    )
    json_path = out / "company_profile.json"
    md_path = out / "company_profile.md"
    json_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        build_profile_markdown(
            entity=entity,
            overview=overview,
            overview_md=overview_md,
            cells=scrape.cells,
            financials_url=scrape.url,
            financials_provider=scrape.provider,
            kg=kg,
            scrape_error=scrape.error,
        ),
        encoding="utf-8",
    )

    return ProfileResult(
        entity=entity,
        output_dir=out,
        json_path=json_path,
        markdown_path=md_path,
        overview=overview,
        cells=scrape.cells,
        kg=kg,
        log=log,
        error=scrape.error,
    )
