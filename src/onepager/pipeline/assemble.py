"""Stage 8 — Assembly + Honesty Lint.

Consolidates verified claims into the OnePager, flags conflicting financials,
records honest NOT_FOUND gaps, and runs the build-breaking Honesty Lint: every
emitted claim MUST have evidence + an acceptable entailment label. If that
invariant is violated, assembly raises rather than ship an unsourced claim.
"""
from __future__ import annotations

from collections import defaultdict

from ..financials import FINANCIAL_ROW_ORDER, display_periods
from ..schemas import (
    Claim,
    ClaimStatus,
    Confidence,
    Entailment,
    FinancialCell,
    Gap,
    OnePager,
    Section,
)
from .context import RunContext


class HonestyLintError(RuntimeError):
    pass


def _dedupe(claims: list[Claim]) -> list[Claim]:
    best: dict[str, Claim] = {}
    for c in claims:
        key = c.text.strip().lower()
        if key not in best or c.confidence.score > best[key].confidence.score:
            best[key] = c
    return list(best.values())


def _consolidate_financials(ctx: RunContext, cells: list[FinancialCell]) -> tuple[list[FinancialCell], list[Gap]]:
    reported = [c for c in cells if c.basis == "reported" and c.status == ClaimStatus.verified]
    derived = [c for c in cells if c.basis == "derived" and c.status == ClaimStatus.verified]
    gaps: list[Gap] = []

    groups: dict[tuple[str, str], list[FinancialCell]] = defaultdict(list)
    for c in reported:
        groups[(c.metric, c.period)].append(c)

    consolidated: list[FinancialCell] = []
    for (metric, period), group in groups.items():
        def tier_of(cell: FinancialCell) -> int:
            return min((ctx.sources[ev.source_id].reliability_tier
                        for ev in cell.evidence if ev.source_id in ctx.sources), default=5)

        best = sorted(group, key=lambda c: (tier_of(c), -c.confidence.score))[0]
        vals = [c.numeric_value for c in group if c.numeric_value is not None]
        if len(vals) > 1:
            lo, hi = min(vals), max(vals)
            if hi != 0 and (hi - lo) / abs(hi) > 0.05:  # >5% disagreement = material
                best.status = ClaimStatus.conflicted
                best.confidence = Confidence(
                    score=best.confidence.score * 0.6, label=best.confidence.label,
                    rationale=f"CONFLICT: sources disagree on {metric} {period} "
                              f"(values {sorted(set(round(v,1) for v in vals))}); showing highest-tier source.",
                )
                gaps.append(Gap(section=Section.financials,
                                description=f"Sources disagree on {metric} {period}: {sorted(set(round(v,1) for v in vals))}",
                                reason="Material (>5%) discrepancy between sources; not silently resolved."))
        consolidated.append(best)

    # Keep only derived cells whose base cells survived consolidation.
    kept_ids = {c.id for c in consolidated}
    derived = [d for d in derived if all(b in kept_ids for b in d.derived_from)]
    return consolidated + derived, gaps


def assemble(ctx: RunContext, claims_by_section: dict[Section, list[Claim]]) -> OnePager:
    ctx.note("Assembling one-pager + honesty lint...")
    entity = ctx.entity

    # Financials get special consolidation/conflict handling.
    fin_cells = [c for c in claims_by_section.get(Section.financials, []) if isinstance(c, FinancialCell)]
    financials, fin_gaps = _consolidate_financials(ctx, fin_cells)
    financial_periods = display_periods([c.period for c in financials], count=3, skip_latest=1)

    def emittable(section: Section) -> list[Claim]:
        kept = [c for c in claims_by_section.get(section, []) if c.is_emittable()]
        return _dedupe(kept)

    overview = emittable(Section.overview)
    products = emittable(Section.products)
    clients = emittable(Section.clients)

    gaps: list[Gap] = list(ctx.gaps) + fin_gaps
    if financial_periods and financials:
        present = {(c.metric, c.period) for c in financials}
        missing = [
            f"{metric} {period}"
            for metric in FINANCIAL_ROW_ORDER
            for period in financial_periods
            if (metric, period) not in present
        ]
        if missing:
            gaps.append(
                Gap(
                    section=Section.financials,
                    description="Some canonical 3-FY financial table cells could not be verified.",
                    reason="Missing cells are shown as dashes rather than estimated.",
                    searched=missing[:30],
                )
            )
    # Honest NOT_FOUND gaps for empty sections.
    if not overview:
        gaps.append(Gap(section=Section.overview, description="No verifiable overview facts found.",
                        reason="No retrieved source supported overview claims."))
    if not financials:
        gaps.append(Gap(section=Section.financials,
                        description="No multi-year financial figures could be verified from available sources.",
                        reason="Financials absent/paywalled (typical for unlisted entities) — not invented."))
    if not products:
        gaps.append(Gap(section=Section.products, description="No products could be verified from available sources.",
                        reason="No source explicitly named products."))
    if not clients:
        gaps.append(Gap(section=Section.clients, description="No clients could be verified from available sources.",
                        reason="No source explicitly named customers; logos alone are not evidence."))

    onepager = OnePager(
        entity=entity, overview=overview, financials=financials, financial_periods=financial_periods,
        products=products, clients=clients, gaps=gaps,
        sources=[ctx.sources[sid] for sid in sorted(ctx.sources)],
    )
    _honesty_lint(onepager)
    onepager.coverage_report = _coverage(onepager, claims_by_section)
    ctx.note(f"Assembled: {len(overview)} overview, {len(financials)} financial cells, "
             f"{len(products)} products, {len(clients)} clients, {len(gaps)} gaps")
    return onepager


def _honesty_lint(op: OnePager) -> None:
    """Build-breaking invariant: nothing un-sourced ships."""
    emitted_fin_ids = {c.id for c in op.financials}
    for c in op.all_claims():
        if not c.evidence:
            raise HonestyLintError(f"Claim {c.id} has no evidence but was emitted: {c.text!r}")
        if c.verification.entailment not in (Entailment.entailed, Entailment.partial):
            raise HonestyLintError(f"Claim {c.id} emitted without acceptable entailment: {c.text!r}")
        if isinstance(c, FinancialCell) and c.basis == "derived":
            for b in c.derived_from:
                if b not in emitted_fin_ids:
                    raise HonestyLintError(f"Derived cell {c.id} references missing base {b}")


def _coverage(op: OnePager, drafted: dict[Section, list[Claim]]) -> "object":
    from ..schemas import CoverageReport

    emitted = op.all_claims()
    hist = {"High": 0, "Medium": 0, "Low": 0}
    for c in emitted:
        hist[c.confidence.label.value] = hist.get(c.confidence.label.value, 0) + 1
    total_drafted = sum(len(v) for v in drafted.values())
    return CoverageReport(
        verified=sum(1 for c in emitted if c.status == ClaimStatus.verified),
        conflicted=sum(1 for c in emitted if c.status == ClaimStatus.conflicted),
        unverified_dropped=total_drafted - len(emitted),
        not_found=len(op.gaps),
        confidence_histogram=hist,
        by_section={
            "overview": len(op.overview), "financials": len(op.financials),
            "products": len(op.products), "clients": len(op.clients),
        },
    )
