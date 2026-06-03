"""Stage 7 — Confidence Scoring.

Confidence is COMPUTED, not vibed. It blends source reliability tier, number of
independent corroborating sources, recency, entailment strength, and extraction
reliability. The result drives the High/Medium/Low chip shown next to each claim.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..schemas import (
    Claim,
    ClaimStatus,
    Confidence,
    ConfidenceLabel,
    Entailment,
    FinancialCell,
)
from .context import RunContext

_TIER_SCORE = {1: 1.0, 2: 0.82, 3: 0.62, 4: 0.42, 5: 0.25}
_W = {"tier": 0.30, "entail": 0.30, "corrob": 0.20, "recency": 0.10, "extract": 0.10}


def _recency_score(ctx: RunContext, claim: Claim) -> float:
    years = []
    for ev in claim.evidence:
        src = ctx.sources.get(ev.source_id)
        if src and src.publication_date:
            for token in str(src.publication_date).replace("-", " ").split():
                if token.isdigit() and len(token) == 4:
                    years.append(int(token))
    if not years:
        return 0.5
    newest = max(years)
    age = datetime.now(timezone.utc).year - newest
    return max(0.2, min(1.0, 1.0 - 0.15 * age))


def _best_tier(ctx: RunContext, claim: Claim) -> int:
    tiers = [ctx.sources[ev.source_id].reliability_tier for ev in claim.evidence if ev.source_id in ctx.sources]
    return min(tiers) if tiers else 5


def _corroboration(claim: Claim) -> int:
    return len({ev.source_id for ev in claim.evidence})


def _extraction_score(claim: Claim) -> float:
    # Numbers pulled from a parsed filing page (table) are more reliable than prose.
    has_page = any("page" in ev.locator for ev in claim.evidence)
    if isinstance(claim, FinancialCell):
        return 0.95 if has_page else 0.7
    return 0.85 if has_page else 0.7


def score_claims(ctx: RunContext, claims: list[Claim]) -> None:
    for c in claims:
        ent = c.verification.entailment
        if ent == Entailment.entailed:
            ent_score = 1.0
        elif ent == Entailment.partial:
            ent_score = 0.6
        else:
            ent_score = 0.0

        tier = _best_tier(ctx, c)
        n_src = _corroboration(c)
        s = (
            _W["tier"] * _TIER_SCORE.get(tier, 0.25)
            + _W["entail"] * ent_score
            + _W["corrob"] * min(1.0, (n_src - 1) / 2.0)
            + _W["recency"] * _recency_score(ctx, c)
            + _W["extract"] * _extraction_score(c)
        )
        s = round(s, 3)

        if s >= 0.75:
            label = ConfidenceLabel.high
        elif s >= 0.5:
            label = ConfidenceLabel.medium
        else:
            label = ConfidenceLabel.low

        c.corroboration_count = n_src
        c.confidence = Confidence(
            score=s, label=label,
            rationale=f"tier={tier}, sources={n_src}, entailment={ent.value}",
        )

        # Status: only entailed/partial claims become VERIFIED.
        if ent in (Entailment.entailed, Entailment.partial):
            c.status = ClaimStatus.verified
        elif ent == Entailment.contradicted:
            c.status = ClaimStatus.conflicted
        else:
            c.status = ClaimStatus.unverified
