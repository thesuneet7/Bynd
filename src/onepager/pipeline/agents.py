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
_FIN_SYS = """You are a financial analyst extracting a multi-year figures table for a company
one-pager, using ONLY the evidence provided (annual reports / filings / results).
Rules:
- Extract REPORTED figures only. For each, cite the evidence ID and the EXACT verbatim
  quote (including the number) that supports it.
- Capture the metric, the fiscal period (e.g. FY24), the numeric value, and the unit
  exactly as reported (e.g. "INR crore", "INR million"). Do NOT convert units.
- Cover these metrics where present: revenue (revenue from operations / total income),
  EBITDA (or operating profit), net profit (PAT), total debt / net debt / borrowings,
  total equity, capital employed (for RoCE). Prefer the most recent 3-4 fiscal years.
- If a metric/year is not in the evidence, simply omit it. NEVER estimate or fill gaps.
Return JSON: {"cells": [{"metric": "revenue", "period": "FY24", "value": 15254.7,
  "unit": "INR crore", "evidence": [{"id": "E#", "quote": "verbatim incl. number"}]}]}"""


def run_financials_agent(ctx: RunContext, start_id: int) -> list[FinancialCell]:
    name = ctx.entity.canonical_name if ctx.entity else ctx.input_name
    queries = [
        f"{name} revenue from operations total income",
        f"{name} EBITDA operating profit",
        f"{name} net profit profit after tax PAT",
        f"{name} total borrowings net debt balance sheet",
        f"{name} financial highlights five year FY24 FY23 FY22",
        f"{name} return on capital employed equity",
    ]
    chunks = ctx.store.search_multi(queries, k_each=5, k_total=14)
    if not chunks:
        ctx.note("[financials] no evidence retrieved")
        return []

    block, idmap = _evidence_block(chunks)
    user = f"Company: {name}\n\nEVIDENCE:\n{block}"
    ctx.note("[financials] extracting figures (Claude)...")
    try:
        data = claude().complete_json(_FIN_SYS, user, max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        ctx.note(f"[financials] agent failed: {e}")
        return []

    cells: list[FinancialCell] = []
    n = start_id
    for item in data.get("cells", []) or []:
        metric = (item.get("metric") or "").strip().lower()
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
        cells.append(
            FinancialCell(
                id=f"fin-{n}", section=Section.financials,
                text=f"{metric} {period}: {num} {item.get('unit', '')}".strip(),
                claim_type=ClaimType.quantitative, metric=metric, period=period,
                numeric_value=num, unit=(item.get("unit") or "INR crore"),
                basis="reported", evidence=ev,
            )
        )
    ctx.note(f"[financials] extracted {len(cells)} reported cells")
    cells += _derive_metrics(cells, start_id=n)
    return cells


def _derive_metrics(reported: list[FinancialCell], start_id: int) -> list[FinancialCell]:
    """Compute derived metrics (growth %, EBITDA margin) from reported cells.

    Derived cells are clearly labeled basis='derived' and link to the base cells
    they were computed from — we never present a calculation as a reported figure.
    """
    derived: list[FinancialCell] = []
    n = start_id

    def by_metric(m: str) -> dict[str, FinancialCell]:
        return {c.period: c for c in reported if c.metric == m}

    rev = by_metric("revenue")
    if not rev:
        rev = by_metric("total income") or by_metric("revenue from operations")

    # Revenue growth % (period over prior period)
    periods = sorted(rev.keys())
    for prev, cur in zip(periods, periods[1:]):
        a, b = rev[prev].numeric_value, rev[cur].numeric_value
        if a and b and a != 0:
            n += 1
            g = round((b - a) / abs(a) * 100, 1)
            derived.append(FinancialCell(
                id=f"fin-{n}", section=Section.financials,
                text=f"revenue growth {cur}: {g}%", claim_type=ClaimType.quantitative,
                metric="revenue growth", period=cur, numeric_value=g, unit="%",
                basis="derived", derived_from=[rev[prev].id, rev[cur].id],
                evidence=[*rev[prev].evidence, *rev[cur].evidence],
            ))

    # EBITDA margin %
    ebitda = by_metric("ebitda") or by_metric("operating profit")
    for period, ec in ebitda.items():
        rc = rev.get(period)
        if rc and rc.numeric_value and ec.numeric_value is not None and rc.numeric_value != 0:
            n += 1
            margin = round(ec.numeric_value / rc.numeric_value * 100, 1)
            derived.append(FinancialCell(
                id=f"fin-{n}", section=Section.financials,
                text=f"EBITDA margin {period}: {margin}%", claim_type=ClaimType.quantitative,
                metric="ebitda margin", period=period, numeric_value=margin, unit="%",
                basis="derived", derived_from=[ec.id, rc.id],
                evidence=[*ec.evidence, *rc.evidence],
            ))
    return derived
