"""Stages 4–5 — Section Agents + Claim Decomposition/Attribution.

Each agent retrieves evidence from the store, then asks Claude to produce ATOMIC
claims using ONLY that evidence, each citing the exact verbatim quote that backs
it. This is closed-book generation over a retrieved set: if the evidence doesn't
say it, the model is instructed not to write it (prefer NOT_FOUND).

Nothing here is trusted yet — every claim still must pass the independent Grok
entailment gate (verify.py) before it can ship.
"""
from __future__ import annotations

from typing import Optional

from ..financials import (
    canonical_metric,
    derive_financial_metrics,
    fetch_screener_financials,
    fetch_tofler_financials,
)
from ..llm import claude
from ..schemas import (
    Claim,
    ClaimType,
    Evidence,
    FinancialCell,
    Section,
)
from ..tools.retrieval import Chunk
from .context import RunContext

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _evidence_block(chunks: list[Chunk]) -> tuple[str, dict[str, Chunk]]:
    lines, idmap = [], {}
    for i, c in enumerate(chunks, 1):
        eid = f"E{i}"
        idmap[eid] = c
        loc = ", ".join(f"{k}={v}" for k, v in c.locator.items())
        # Truncate chunk text sent to the writer LLM to keep prompts small/fast.
        lines.append(f"[{eid}] (source {c.source_id}; {loc})\n{c.text[:700]}")
    return "\n\n".join(lines), idmap


def _attach_evidence(raw_ev: list, idmap: dict[str, Chunk]) -> list[Evidence]:
    out: list[Evidence] = []
    for ev in raw_ev or []:
        if not isinstance(ev, dict):
            continue
        eid = ev.get("id") or ev.get("evidence_id")
        quote = (ev.get("quote") or ev.get("exact_quote") or "").strip()
        chunk = idmap.get(eid)
        if not chunk or not quote:
            continue
        out.append(Evidence(source_id=chunk.source_id, exact_quote=quote, locator=dict(chunk.locator)))
    return out


# --------------------------------------------------------------------------- #
# Generic qualitative / entity agent (overview, products, clients)
# --------------------------------------------------------------------------- #
_GENERIC_SYS = """You are a meticulous equity-research analyst building one section of a
company one-pager. You may use ONLY the evidence provided. Rules:
- Each claim is ONE atomic, verifiable fact (no compound sentences).
- For every claim, cite the evidence ID(s) and copy the EXACT verbatim quote span
  from that evidence that supports it.
- If the evidence does not clearly support a statement, DO NOT write it. It is far
  better to omit a fact than to state something the evidence doesn't back.
- Never use outside/prior knowledge. Never guess numbers, clients, or products.
{extra}
Return JSON: {{"claims": [{{"text": "...", "evidence": [{{"id": "E#", "quote": "verbatim"}}]}}]}}"""

_SECTION_SPEC = {
    Section.overview: {
        "queries": [
            "what the business does core operations segments",
            "company history founded established year promoters ownership",
            "manufacturing facilities plants installed capacity locations",
            "domestic exports markets customers served end-use industries",
            "subsidiaries joint ventures certifications",
        ],
        "extra": "Focus: what the business does, how it operates, what it makes, who it sells to, "
                 "history, facilities, ownership. 6-12 crisp claims.",
        "ctype": ClaimType.qualitative,
    },
    Section.products: {
        "queries": [
            "products manufactured product portfolio range",
            "key products components offerings make",
        ],
        "extra": "Extract DISTINCT named products/components the company makes. One claim per product, "
                 "phrased '<Company> manufactures <product>'. Only products explicitly named in evidence.",
        "ctype": ClaimType.entity_relationship,
    },
    Section.clients: {
        "queries": [
            "key customers clients OEMs supplies to served marquee",
            "named customers relationships supplies components to",
        ],
        "extra": "Extract NAMED customers/clients with explicit textual evidence of the relationship. "
                 "One claim per client, phrased '<Client> is a customer of <Company>'. "
                 "Do NOT infer a client from a logo, a list without context, or marketing fluff.",
        "ctype": ClaimType.entity_relationship,
    },
}


def run_generic_agent(ctx: RunContext, section: Section, start_id: int) -> list[Claim]:
    spec = _SECTION_SPEC[section]
    name = ctx.entity.canonical_name if ctx.entity else ctx.input_name
    queries = [f"{name} {q}" for q in spec["queries"]]
    chunks = ctx.store.search_multi(queries, k_each=5, k_total=12)
    if not chunks:
        ctx.note(f"[{section.value}] no evidence retrieved")
        return []

    block, idmap = _evidence_block(chunks)
    sys = _GENERIC_SYS.format(extra=spec["extra"])
    user = f"Company: {name}\nSection: {section.value}\n\nEVIDENCE:\n{block}"
    ctx.note(f"[{section.value}] drafting claims (Claude)...")
    try:
        data = claude().complete_json(sys, user, max_tokens=2000)
    except Exception as e:  # noqa: BLE001
        ctx.note(f"[{section.value}] agent failed: {e}")
        return []

    claims: list[Claim] = []
    n = start_id
    for item in data.get("claims", []) or []:
        text = (item.get("text") or "").strip()
        ev = _attach_evidence(item.get("evidence"), idmap)
        if not text or not ev:
            continue  # un-evidenced claims never enter the pipeline
        n += 1
        claims.append(
            Claim(id=f"{section.value[:3]}-{n}", section=section, text=text,
                  claim_type=spec["ctype"], evidence=ev)
        )
    ctx.note(f"[{section.value}] drafted {len(claims)} evidence-backed claims")
    return claims


# --------------------------------------------------------------------------- #
# Financials agent (specialized)
# --------------------------------------------------------------------------- #
_FIN_SYS = """You are a financial analyst extracting a canonical 3-FY figures table for a company
one-pager, using ONLY the evidence provided from saved filings, PDFs, spreadsheets, and pages.
Rules:
- Extract REPORTED figures only. For each, cite the evidence ID and the EXACT verbatim
  quote (including the number) that supports it.
- Capture the metric, the fiscal period (e.g. FY24), the numeric value, and the unit
  exactly as reported (e.g. "INR crore", "INR million"). Do NOT convert units.
- Prefer the most recent 3 fiscal years, but include a 4th prior year if needed for growth.
- Extract FINAL table metrics when explicitly reported:
  revenue, revenue_growth_pct, material_margin, material_margin_pct, operating_ebitda,
  operating_ebitda_pct, nwc_days, roce_pct, net_debt.
- Also extract SUPPORTING inputs when reported because code can derive missing rows:
  material_cost, borrowings, cash_and_equivalents, ebit, capital_employed.
- Synonyms:
  revenue = revenue from operations / total income / sales;
  material_cost = cost of materials consumed / raw material cost;
  operating_ebitda = EBITDA / operating EBITDA / PBDIT / operating profit before depreciation;
  nwc_days = net working capital days / working capital days;
  roce_pct = ROCE / return on capital employed;
  borrowings = gross debt / total debt / total borrowings.
- If a metric/year is not in the evidence, simply omit it. NEVER estimate or fill gaps.
Return JSON: {"cells": [{"metric": "revenue", "period": "FY24", "value": 15254.7,
  "unit": "INR crore", "evidence": [{"id": "E#", "quote": "verbatim incl. number"}]}]}"""

_FIN_FALLBACK_SYS = """Extract reported financial figures using ONLY the evidence.
Return strict JSON only: {"cells":[{"metric":"revenue","period":"FY25","value":123.4,
"unit":"INR crore","evidence":[{"id":"E#","quote":"exact quote containing number"}]}]}.
Metrics to extract if present: revenue, operating_ebitda, net_profit, borrowings,
cash_and_equivalents, net_debt, roce_pct, nwc_days. Omit missing metrics."""


def _merge_financial_cells(primary: list[FinancialCell], secondary: list[FinancialCell]) -> list[FinancialCell]:
    """Keep primary values; fill only missing (metric, period) from secondary."""
    seen = {(c.metric, c.period) for c in primary}
    out = list(primary)
    for cell in secondary:
        key = (cell.metric, cell.period)
        if key not in seen:
            seen.add(key)
            out.append(cell)
    return out


def run_financials_agent(ctx: RunContext, start_id: int) -> list[FinancialCell]:
    name = ctx.entity.canonical_name if ctx.entity else ctx.input_name
    cells: list[FinancialCell] = []
    n = start_id

    if ctx.entity:
        listed = ctx.entity.listing_status == "listed" and ctx.entity.ticker
        if listed:
            screener_cells, skip = fetch_screener_financials(
                ctx, ctx.entity, ticker=ctx.entity.ticker, start_id=n
            )
            if screener_cells:
                cells = screener_cells
            elif skip:
                ctx.note(f"[financials] screener.in: {skip}")

        if not cells and ctx.entity.listing_status != "listed":
            tofler_cells, skip = fetch_tofler_financials(ctx, ctx.entity, start_id=n)
            if tofler_cells:
                cells = tofler_cells
            elif skip:
                ctx.note(f"[financials] tofler.in: {skip}")

        if cells:
            for c in cells:
                if c.id.startswith("fin-"):
                    try:
                        n = max(n, int(c.id.rsplit("-", 1)[-1]))
                    except ValueError:
                        pass

    queries = [
        f"{name} revenue from operations total income",
        f"{name} cost of materials consumed raw material cost material margin",
        f"{name} operating EBITDA PBDIT operating profit",
        f"{name} net profit profit after tax PAT",
        f"{name} total borrowings cash equivalents net debt balance sheet",
        f"{name} financial highlights five year FY24 FY23 FY22",
        f"{name} return on capital employed ROCE working capital days",
    ]
    chunks = ctx.store.search_multi(queries, k_each=6, k_total=20)
    if not chunks:
        if cells:
            ctx.note("[financials] no filing evidence; using screener.in figures only")
            return cells
        ctx.note("[financials] no evidence retrieved")
        return []

    block, idmap = _evidence_block(chunks)
    user = f"Company: {name}\n\nEVIDENCE:\n{block}"
    ctx.note("[financials] extracting figures (Claude)...")
    try:
        data = claude().complete_json(_FIN_SYS, user, max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        ctx.note(f"[financials] expanded extraction failed ({e}); retrying compact prompt")
        try:
            data = claude().complete_json(_FIN_FALLBACK_SYS, user, max_tokens=1800)
        except Exception as e2:  # noqa: BLE001
            ctx.note(f"[financials] agent failed: {e2}")
            return cells if cells else []

    extracted: list[FinancialCell] = []
    for item in data.get("cells", []) or []:
        metric = canonical_metric((item.get("metric") or "").strip())
        period = (item.get("period") or "").strip()
        ev = _attach_evidence(item.get("evidence"), idmap)
        val = item.get("value")
        if not metric or not period or not ev or val is None:
            continue
        try:
            num = float(str(val).replace(",", ""))
        except (TypeError, ValueError):
            continue
        n += 1
        extracted.append(
            FinancialCell(
                id=f"fin-{n}", section=Section.financials,
                text=f"{metric} {period}: {num} {item.get('unit', '')}".strip(),
                claim_type=ClaimType.quantitative, metric=metric, period=period,
                numeric_value=num, unit=(item.get("unit") or "INR crore"),
                basis="reported", evidence=ev,
            )
        )
    ctx.note(f"[financials] extracted {len(extracted)} reported cells from filings")
    extracted += derive_financial_metrics(extracted, start_id=n)
    if cells:
        return _merge_financial_cells(cells, extracted)
    return extracted
