"""Orchestration — the stateful pipeline graph.

entity -> discovery -> ingestion -> section agents (overview/financials/products/
clients) -> verify (Grok entailment gate) -> confidence -> assemble + honesty lint.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from ..budget import BUDGET
from ..config import SETTINGS
from ..schemas import Claim, OnePager, Section
from .agents import run_financials_agent, run_generic_agent
from .archive import archive_candidates
from .assemble import assemble
from .confidence import score_claims
from .context import RunContext
from .discovery import discover_sources
from .document_ingestion import ingest_archived_documents
from .entity import resolve_entity
from .ingestion import ingest
from .planner import recursive_research
from .verify import verify_claims


def build_onepager(
    name: str,
    hint: Optional[str] = None,
    *,
    outdir: Optional[Path] = None,
    ticker: Optional[str] = None,
) -> OnePager:
    t0 = time.time()
    ctx = RunContext(input_name=name, input_hint=hint, output_dir=outdir)
    print(f"\n=== One-pager: {name} (hint: {hint or 'none'}) ===", flush=True)

    # 1) Entity resolution
    resolve_entity(ctx)
    if ticker and ctx.entity:
        ctx.entity = ctx.entity.model_copy(update={"ticker": ticker.upper().strip()})
        ctx.note(f"Ticker override: {ctx.entity.ticker}")

    # 2) Discovery + 3) Ingestion
    candidates = discover_sources(ctx)
    archive_candidates(ctx, candidates)
    ingest(ctx, candidates)

    if not ctx.store.chunks:
        ctx.note("No sources ingested — producing an honest empty page.")
    else:
        ctx.note(f"Building local embedding index for {len(ctx.store.chunks)} chunks...")
        ctx.store.warm_index()
        ctx.note("Embedding index ready.")

    # 3b) Recursive research loop: targeted searches for financials/products/clients,
    # ingesting and embedding new evidence until coverage is good enough or budgets stop us.
    research_summary = recursive_research(ctx)
    archive_ingest_summary = ingest_archived_documents(ctx)
    if archive_ingest_summary.get("chunks"):
        ctx.note(f"Refreshing embedding index for {len(ctx.store.chunks)} chunks after archive ingestion...")
        ctx.store.warm_index()
        ctx.note("Embedding index ready.")

    # 4) Section agents (claim drafting, grounded in evidence)
    claims_by_section: dict[Section, list[Claim]] = {}
    claims_by_section[Section.overview] = run_generic_agent(ctx, Section.overview, 0)
    claims_by_section[Section.financials] = run_financials_agent(ctx, 0)
    claims_by_section[Section.products] = run_generic_agent(ctx, Section.products, 0)
    claims_by_section[Section.clients] = run_generic_agent(ctx, Section.clients, 0)

    all_claims = [c for v in claims_by_section.values() for c in v]

    # 6) Verify (independent Grok entailment gate)
    verify_claims(ctx, all_claims)

    # 7) Confidence scoring
    score_claims(ctx, all_claims)

    # 8) Assemble + honesty lint
    onepager = assemble(ctx, claims_by_section)

    onepager.run_metadata = {
        "models": {"writer": SETTINGS.claude_model, "verifier": SETTINGS.xai_model,
                   "embeddings": "BAAI/bge-small-en-v1.5 (local)"},
        "budget": BUDGET.report(),
        "research": {
            "coverage": research_summary,
            "archive_ingestion": archive_ingest_summary,
            "searched_queries": ctx.searched_queries,
            "trace": ctx.research_trace,
            "archive_manifest": str(ctx.research_dir / "manifest.json") if ctx.research_dir else None,
        },
        "elapsed_sec": round(time.time() - t0, 1),
        "log": ctx.log,
    }
    print(f"=== Done in {onepager.run_metadata['elapsed_sec']}s | "
          f"calls={BUDGET.report()['calls']} ===\n", flush=True)
    return onepager
