"""Stage 6 — Verification / Entailment Gate (the honesty firewall).

For every claim, an INDEPENDENT model (Grok) is asked: do these verbatim quotes
actually entail this claim? The writer (Claude) does not get to grade its own
work. Claims that aren't entailed/partial are dropped downstream.

Derived financial cells (growth %, margins) are arithmetic over base cells, so
they are not sent to the judge; their entailment is inherited from the base cells
they were computed from.
"""
from __future__ import annotations

import json

from ..llm import grok
from ..schemas import Claim, Entailment, FinancialCell, SourceType, Verification
from ..config import SETTINGS
from .context import RunContext

_SYS = """You are a STRICT verification judge for a sourced company one-pager.
For each item you receive a CLAIM and one or more QUOTES copied verbatim from real
sources. Decide whether the quotes actually support the claim:
- "entailed": the quotes clearly and directly state the claim (incl. the exact number/name).
- "partial": the quotes support a weaker/related version, or support it indirectly.
- "contradicted": the quotes state something that conflicts with the claim.
- "none": the quotes do not actually contain the fact asserted (DEFAULT when unsure).
Be skeptical. A number must appear in the quote to entail a numeric claim. A client/
product name must appear in the quote to entail an entity claim.
Return JSON: {"results": [{"id": "<id>", "entailment": "<label>", "rationale": "<short>"}]}"""

_BATCH = 8


def _payload(claims: list[Claim]) -> list[dict]:
    items = []
    for c in claims:
        quotes = [e.exact_quote for e in c.evidence][:4]
        items.append({"id": c.id, "claim": c.text, "quotes": quotes})
    return items


def _is_structured_financial_claim(ctx: RunContext, claim: Claim) -> bool:
    if not claim.evidence:
        return False
    for ev in claim.evidence:
        src = ctx.sources.get(ev.source_id)
        if not src or src.source_type != SourceType.financial_api:
            return False
        if (ev.locator or {}).get("provider") not in ("screener", "tofler"):
            return False
    return True


def verify_claims(ctx: RunContext, claims: list[Claim]) -> None:
    # Split reported vs derived.
    reported = [c for c in claims if not (isinstance(c, FinancialCell) and c.basis == "derived")]
    derived = [c for c in claims if isinstance(c, FinancialCell) and c.basis == "derived"]
    structured_api = [c for c in reported if _is_structured_financial_claim(ctx, c)]
    reported_for_judge = [c for c in reported if c not in structured_api]

    results: dict[str, dict] = {}
    ctx.note(
        f"[verify] checking {len(reported_for_judge)} claims with Grok "
        f"({len(structured_api)} structured API)..."
    )
    for i in range(0, len(reported_for_judge), _BATCH):
        batch = reported_for_judge[i : i + _BATCH]
        user = "Verify each item:\n" + json.dumps(_payload(batch), ensure_ascii=False, indent=2)
        try:
            data = grok().complete_json(_SYS, user, max_tokens=2000)
            for r in data.get("results", []) or []:
                if isinstance(r, dict) and r.get("id"):
                    results[r["id"]] = r
        except Exception as e:  # noqa: BLE001
            ctx.note(f"[verify] batch failed ({e}); marking batch unverified")

    for c in reported:
        if _is_structured_financial_claim(ctx, c):
            provider = (c.evidence[0].locator or {}).get("provider", "api")
            c.verification = Verification(
                entailment=Entailment.entailed,
                judge_model=f"{provider}.in",
                rationale=f"Structured line item from {provider}.in financial tables.",
            )
            continue
        r = results.get(c.id, {})
        label = str(r.get("entailment", "none")).lower()
        try:
            ent = Entailment(label)
        except ValueError:
            ent = Entailment.none
        c.verification = Verification(entailment=ent, judge_model=SETTINGS.xai_model,
                                      rationale=str(r.get("rationale", ""))[:300])

    # Derived cells inherit entailment from their base cells, including other
    # derived cells that were themselves computed from verified bases.
    by_id = {c.id: c for c in [*reported, *derived]}
    unresolved = list(derived)
    for _ in range(max(1, len(derived))):
        progressed = False
        still_unresolved = []
        for d in unresolved:
            bases = [by_id.get(bid) for bid in d.derived_from]
            bases = [b for b in bases if b]
            if bases and all(b.verification.entailment in (Entailment.entailed, Entailment.partial) for b in bases):
                worst = Entailment.entailed if all(
                    b.verification.entailment == Entailment.entailed for b in bases) else Entailment.partial
                d.verification = Verification(entailment=worst, judge_model="derived",
                                              rationale="Computed from verified base figures.")
                progressed = True
            else:
                still_unresolved.append(d)
        unresolved = still_unresolved
        if not unresolved or not progressed:
            break
    for d in unresolved:
        d.verification = Verification(entailment=Entailment.none, judge_model="derived",
                                      rationale="Base figures not verified.")

    passed = sum(1 for c in claims if c.verification.entailment in (Entailment.entailed, Entailment.partial))
    ctx.note(f"[verify] {passed}/{len(claims)} claims passed the entailment gate")
