"""HTML renderer — a clean, GPIL-inspired four-region layout with inline
citations, confidence chips, an honest Gaps panel, and a full source list.
Self-contained (inline CSS), no external assets.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape

from ..financials import FINANCIAL_ROW_ORDER, FINANCIAL_UNITS, display_periods, financial_label, period_key
from ..schemas import ClaimStatus, FinancialCell, OnePager

_CONF_CLASS = {"High": "c-high", "Medium": "c-med", "Low": "c-low"}

_CSS = """
:root{--navy:#1f2a44;--ink:#1a1a1a;--mut:#666;--line:#e3e6ee;--bg:#f5f6fa}
*{box-sizing:border-box}body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial;color:var(--ink);background:var(--bg);margin:0;padding:24px}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:4px}
.note{color:var(--mut);font-size:12px;margin:8px 0 18px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.card h2{background:var(--navy);color:#fff;font-size:15px;margin:0;padding:10px 14px}
.card .body{padding:12px 16px}
ul{margin:0;padding-left:18px}li{margin:6px 0}
.cite{color:#2563eb;font-size:11px;font-weight:600;text-decoration:none}
.chip{font-size:10px;padding:1px 6px;border-radius:10px;margin-left:4px;white-space:nowrap}
.c-high{background:#e6f4ea;color:#137333}.c-med{background:#fef7e0;color:#a86b00}.c-low{background:#fce8e6;color:#c5221f}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
thead th{background:var(--navy);color:#fff}
.derived td{color:#555;font-style:italic;background:#fafbfd}
.conflict{color:#c5221f;font-weight:700}
.gaps{background:#fff8f6;border-color:#f3c9c0}
.gaps h2{background:#8a2b1f}
.src{font-size:12px;color:#333}.src a{color:#2563eb}
.tag{display:inline-block;font-size:10px;background:#eef;border-radius:4px;padding:0 5px;margin-left:4px;color:#334}
.full{grid-column:1 / -1}
"""


def _chip(c) -> str:
    lbl = c.confidence.label.value
    return f'<span class="chip {_CONF_CLASS.get(lbl,"")}">{lbl}</span>'


def _cites(c) -> str:
    sids = sorted({e.source_id for e in c.evidence})
    return " ".join(f'<span class="cite">[{s}]</span>' for s in sids)


def _bullets(claims) -> str:
    if not claims:
        return "<p><em>Not found in available sources.</em></p>"
    items = []
    for c in claims:
        flag = ' <span class="conflict">⚠ CONFLICT</span>' if c.status == ClaimStatus.conflicted else ""
        items.append(f"<li>{escape(c.text)} {_cites(c)}{_chip(c)}{flag}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def render_html(op: OnePager) -> str:
    e = op.entity
    sub = [escape(e.listing_status)]
    if e.ticker:
        sub.append(escape(e.ticker))
    if e.country:
        sub.append(escape(e.country))
    sub.append(f"data-richness: {escape(e.data_richness_tier)}")

    gaps_html = ""
    if op.gaps:
        gi = "".join(f"<li><b>[{escape(g.section.value)}]</b> {escape(g.description)} "
                     f"— <em>{escape(g.reason)}</em></li>" for g in op.gaps)
        gaps_html = (f'<div class="card gaps full"><h2>Gaps &amp; Unverifiable Items '
                     f'(honest &ldquo;not found&rdquo;)</h2><div class="body"><ul>{gi}</ul></div></div>')

    src_items = []
    for s in op.sources:
        meta = f"{s.source_type.value} · tier {s.reliability_tier} · {s.access}"
        if s.publication_date:
            meta += f" · {escape(str(s.publication_date))}"
        if s.snapshot_path:
            meta += f" · snapshot: {escape(s.snapshot_path)}"
        src_items.append(f'<li class="src"><b>[{s.id}]</b> '
                         f'<a href="{escape(s.url)}" target="_blank">{escape(s.title or s.url)}</a> '
                         f'<span class="tag">{escape(meta)}</span></li>')
    sources_html = "<ul>" + "".join(src_items) + "</ul>" if src_items else "<p><em>No sources.</em></p>"

    cov = op.coverage_report
    md = op.run_metadata or {}
    cov_html = (f"<p>Verified: <b>{cov.verified}</b> · conflicted: {cov.conflicted} · "
                f"dropped (failed verification): {cov.unverified_dropped} · gaps: {len(op.gaps)}</p>"
                f"<p>Confidence mix: {escape(str(cov.confidence_histogram))}</p>"
                f"<p>Writer: {escape(str(md.get('models',{}).get('writer','')))} · "
                f"Verifier: {escape(str(md.get('models',{}).get('verifier','')))} · "
                f"calls: {escape(str(md.get('budget',{}).get('calls','')))}</p>")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{escape(e.canonical_name)} — One-Pager</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{escape(e.canonical_name)} — Company One-Pager</h1>
<div class="sub">{' · '.join(sub)}</div>
<div class="sub">{escape(e.disambiguation_note)}</div>
<div class="note">Every line is grounded in a retrievable source (see Sources). Confidence:
<span class="chip c-high">High</span> <span class="chip c-med">Medium</span>
<span class="chip c-low">Low</span>. Unverifiable items are listed honestly under Gaps,
not invented.</div>
<div class="grid">
  <div class="card"><h2>Company Overview</h2><div class="body">{_bullets(op.overview)}</div></div>
  <div class="card"><h2>Financial Overview</h2><div class="body">{_financials_table(op)}</div></div>
  <div class="card"><h2>Select Products</h2><div class="body">{_bullets(op.products)}</div></div>
  <div class="card"><h2>Select Clients</h2><div class="body">{_bullets(op.clients)}</div></div>
  {gaps_html}
  <div class="card full"><h2>Sources</h2><div class="body">{sources_html}</div></div>
  <div class="card full"><h2>Coverage / Self-Check</h2><div class="body">{cov_html}</div></div>
</div></div></body></html>"""


def _financials_table(op: OnePager) -> str:
    cells = [c for c in op.financials if isinstance(c, FinancialCell)]
    if not cells:
        return "<p><em>No multi-year financial figures could be verified from available sources.</em></p>"
    periods = op.financial_periods or display_periods([c.period for c in cells], count=3, skip_latest=1)
    by_metric: dict[str, dict[str, FinancialCell]] = defaultdict(dict)
    for c in cells:
        by_metric[c.metric][c.period] = c

    head = "<tr><th>Metric (unit)</th>" + "".join(f"<th>{escape(p)}</th>" for p in periods) + "<th>Src · Conf</th></tr>"
    rows = []
    for metric in FINANCIAL_ROW_ORDER:
        rc = by_metric[metric]
        unit = FINANCIAL_UNITS.get(metric) or (next(iter(rc.values())).unit if rc else "")
        label = escape(financial_label(metric, unit))
        cls = ' class="derived"' if any(c.basis == "derived" for c in rc.values()) else ""
        if any(c.basis == "derived" for c in rc.values()):
            label += " (derived)"
        tds, cites, confs = [], set(), set()
        for p in periods:
            cell = rc.get(p)
            if cell is None:
                tds.append("<td>—</td>")
            else:
                flag = ' <span class="conflict">⚠</span>' if cell.status == ClaimStatus.conflicted else ""
                tds.append(f"<td>{_fmt(cell.numeric_value)}{flag}</td>")
                cites |= {e.source_id for e in cell.evidence}
                confs.add(cell.confidence.label.value)
        cite_str = " ".join(f'<span class="cite">[{s}]</span>' for s in sorted(cites))
        conf = min(confs, key=lambda x: ["High", "Medium", "Low"].index(x)) if confs else ""
        chip = f'<span class="chip {_CONF_CLASS.get(conf,"")}">{conf}</span>' if conf else ""
        rows.append(f"<tr{cls}><td>{label}</td>" + "".join(tds) + f"<td>{cite_str} {chip}</td></tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.1f}"


def _pk(p: str):
    return period_key(p)
