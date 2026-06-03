"""Markdown renderer. Every claim shows an inline citation [S#] and a confidence
chip; financial cells note reported vs derived; gaps are shown explicitly.
"""
from __future__ import annotations

from collections import defaultdict

from ..schemas import ClaimStatus, FinancialCell, OnePager, Section

_CHIP = {"High": "🟢 High", "Medium": "🟡 Med", "Low": "🔴 Low"}


def _cite(claim) -> str:
    sids = sorted({e.source_id for e in claim.evidence})
    return " ".join(f"[{s}]" for s in sids)


def _conf(claim) -> str:
    return _CHIP.get(claim.confidence.label.value, claim.confidence.label.value)


def render_markdown(op: OnePager) -> str:
    e = op.entity
    L: list[str] = []
    L.append(f"# {e.canonical_name} — Company One-Pager\n")
    sub = [e.listing_status]
    if e.ticker:
        sub.append(f"`{e.ticker}`")
    if e.country:
        sub.append(e.country)
    sub.append(f"data-richness: **{e.data_richness_tier}**")
    L.append("> " + " · ".join(sub))
    if e.disambiguation_note:
        L.append(f">\n> {e.disambiguation_note}")
    L.append("")
    L.append("_Every line is grounded in a retrievable source (see Sources). "
             "Confidence chips: 🟢 High · 🟡 Medium · 🔴 Low. "
             "Anything not verifiable is listed honestly under Gaps._\n")

    # Overview
    L.append("## Company Overview\n")
    if op.overview:
        for c in op.overview:
            L.append(f"- {c.text} {_cite(c)} — {_conf(c)}")
    else:
        L.append("- _Not found in available sources._")
    L.append("")

    # Financials
    L.append("## Financial Overview\n")
    L.append(_render_financials(op))
    L.append("")

    # Products
    L.append("## Select Products\n")
    if op.products:
        for c in op.products:
            L.append(f"- {c.text} {_cite(c)} — {_conf(c)}")
    else:
        L.append("- _Not found in available sources._")
    L.append("")

    # Clients
    L.append("## Select Clients\n")
    if op.clients:
        for c in op.clients:
            flag = " ⚠️ CONFLICT" if c.status == ClaimStatus.conflicted else ""
            L.append(f"- {c.text} {_cite(c)} — {_conf(c)}{flag}")
    else:
        L.append("- _Not found in available sources._")
    L.append("")

    # Gaps
    if op.gaps:
        L.append("## Gaps & Unverifiable Items (honest 'not found')\n")
        for g in op.gaps:
            L.append(f"- **[{g.section.value}]** {g.description} — _{g.reason}_")
        L.append("")

    # Sources
    L.append("## Sources\n")
    for s in op.sources:
        meta = [s.source_type.value, f"tier {s.reliability_tier}", s.access]
        if s.publication_date:
            meta.append(s.publication_date)
        L.append(f"- **[{s.id}]** [{s.title or s.url}]({s.url}) — {', '.join(meta)}")
    L.append("")

    # Coverage
    cov = op.coverage_report
    L.append("## Coverage / Self-Check\n")
    L.append(f"- Verified claims emitted: **{cov.verified}** · conflicted: {cov.conflicted} · "
             f"drafted-but-dropped (failed verification): {cov.unverified_dropped}")
    L.append(f"- Confidence mix: {cov.confidence_histogram}")
    L.append(f"- By section: {cov.by_section}")
    L.append(f"- Honest gaps recorded: {len(op.gaps)}")
    md = op.run_metadata or {}
    if md:
        L.append(f"- Models: writer={md.get('models',{}).get('writer')}, "
                 f"verifier={md.get('models',{}).get('verifier')}")
        L.append(f"- API calls: {md.get('budget',{}).get('calls')}")
    L.append("")
    return "\n".join(L)


def _render_financials(op: OnePager) -> str:
    cells = [c for c in op.financials if isinstance(c, FinancialCell)]
    if not cells:
        return "_No multi-year financial figures could be verified from available sources._"

    periods = sorted({c.period for c in cells}, key=_period_key)
    by_metric: dict[str, dict[str, FinancialCell]] = defaultdict(dict)
    order: list[str] = []
    for c in cells:
        if c.metric not in by_metric:
            order.append(c.metric)
        by_metric[c.metric][c.period] = c

    header = "| Metric (unit) | " + " | ".join(periods) + " | Source · Conf |"
    sep = "|" + "---|" * (len(periods) + 2)
    rows = [header, sep]
    for metric in order:
        row_cells = by_metric[metric]
        any_cell = next(iter(row_cells.values()))
        unit = any_cell.unit
        label = f"{metric.title()} ({unit})"
        if any_cell.basis == "derived":
            label += " _(derived)_"
        vals = []
        cites = set()
        conf_labels = set()
        for p in periods:
            cell = row_cells.get(p)
            if cell is None:
                vals.append("—")
            else:
                flag = "⚠️" if cell.status == ClaimStatus.conflicted else ""
                vals.append(f"{_fmt(cell.numeric_value)}{flag}")
                cites |= {e.source_id for e in cell.evidence}
                conf_labels.add(cell.confidence.label.value)
        cite_str = " ".join(f"[{s}]" for s in sorted(cites))
        conf = min(conf_labels, key=lambda x: ["High", "Medium", "Low"].index(x)) if conf_labels else ""
        rows.append(f"| {label} | " + " | ".join(vals) + f" | {cite_str} {_CHIP.get(conf,'')} |")
    note = "\n\n_Cells marked ⚠️ have conflicting sources (see Gaps). '(derived)' rows are " \
           "computed from the reported figures above and cite those same sources._"
    return "\n".join(rows) + note


def _fmt(v) -> str:
    if v is None:
        return "—"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:,.1f}"


def _period_key(p: str):
    digits = "".join(ch for ch in p if ch.isdigit())
    return int(digits) if digits else 0
