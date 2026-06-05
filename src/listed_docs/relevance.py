"""Select documents useful for products/clients extraction."""
from __future__ import annotations

import re

from .models import DocCategory, DocumentRef

# Administrative LODR noise — not useful for products/clients.
_NOISE_PHRASES = (
    "intimation",
    "schedule of",
    "schedule of proposed",
    "schedule of institutional",
    "schedule of analyst",
    "recording of",
    "transcript",
    "newspaper publication",
    "newspaper advert",
    "postal ballot",
    "closure of trading window",
    "credit rating",
    "change in director",
    "resignation of",
    "appointment of",
    "outcome of postal ballot",
    "outcome of agm",
    "shareholder meeting / postal ballot",
    "loss of share certificate",
    "rumour verification",
    "monitoring agency report",
    "compliance report",
    "regulation 74",
    "regulation 76",
)

_STRONG_PRESENTATION_PHRASES = (
    "investor presentation",
    "earnings update",
    "earnings presentation",
    "earnings call presentation",
    "investor deck",
    "investor meet - outcome",
    "investor meet – outcome",
    "analyst / investor meet - outcome",
    "analyst/investor meet - outcome",
)


def _blob(ref: DocumentRef, extra: str = "") -> str:
    parts = [ref.title, extra]
    for v in (ref.meta or {}).values():
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts).lower()


def is_major_investor_presentation(ref: DocumentRef) -> bool:
    """True for yearly/quarterly decks — not meet intimations or admin filings."""
    meta = ref.meta or {}
    subcat = str(meta.get("subcat") or meta.get("SUBCATNAME") or "")
    inv_field = str(meta.get("investor_presentation") or meta.get("Investor_Presentation") or "")
    blob = _blob(ref, subcat)

    if inv_field and ".pdf" in inv_field.lower():
        return True

    if subcat.strip().lower() == "investor presentation":
        return True

    if any(p in blob for p in _STRONG_PRESENTATION_PHRASES):
        if any(n in blob for n in _NOISE_PHRASES):
            # "outcome" filings that include the deck are OK
            if "outcome" in blob and ("presentation" in blob or "earnings" in blob):
                return True
            if "intimation" in blob or "schedule" in blob:
                return False
        return True

    return False


def presentation_score(ref: DocumentRef) -> int:
    """Higher = more likely the canonical yearly deck."""
    blob = _blob(ref)
    score = 0
    if "investor presentation" in blob:
        score += 50
    if "earnings update" in blob:
        score += 40
    if "earnings presentation" in blob:
        score += 35
    if (ref.meta or {}).get("investor_presentation"):
        score += 30
    if re.search(r"\bq4\b", blob) or "fourth quarter" in blob or "full year" in blob:
        score += 20
    if re.search(r"\bfy\s*\d{2}\b", blob) or re.search(r"20\d{2}[-/]20\d{2}", blob):
        score += 10
    if re.search(r"\bq[123]\b", blob):
        score -= 15
    if "intimation" in blob or "schedule" in blob:
        score -= 100
    if "transcript" in blob:
        score -= 100
    return score


def infer_fy_label(ref: DocumentRef) -> str | None:
    if ref.fy_label:
        return ref.fy_label
    blob = _blob(ref)
    m = re.search(r"fy\s*'?(\d{2})\b", blob, re.I)
    if m:
        return f"FY{m.group(1)}"
    m = re.search(r"20(\d{2})[-/]20(\d{2})", blob)
    if m:
        return f"FY{m.group(2)}"
    if ref.report_year:
        return f"FY{int(ref.report_year) % 100:02d}"
    if ref.published:
        try:
            y = int(ref.published[:4])
            return f"FY{y % 100:02d}"
        except ValueError:
            pass
    return None


def cap_presentations_per_fy(refs: list[DocumentRef], *, max_per_fy: int = 1) -> list[DocumentRef]:
    """Keep the highest-scoring deck(s) per FY."""
    by_fy: dict[str, list[DocumentRef]] = {}
    unknown: list[DocumentRef] = []
    for ref in refs:
        fy = infer_fy_label(ref) or "unknown"
        if fy == "unknown":
            unknown.append(ref)
        else:
            by_fy.setdefault(fy, []).append(ref)

    kept: list[DocumentRef] = []
    for fy, group in sorted(by_fy.items()):
        ordered = sorted(group, key=presentation_score, reverse=True)
        kept.extend(ordered[:max_per_fy])

    # If FY could not be inferred, keep only the single best-scoring orphan.
    if unknown:
        best = max(unknown, key=presentation_score)
        if presentation_score(best) > 0:
            kept.append(best)
    return kept


def select_relevant_documents(refs: list[DocumentRef]) -> list[DocumentRef]:
    """Annual reports (all in window) + major investor presentations (capped per FY)."""
    annual = [r for r in refs if r.category == DocCategory.annual_report]
    presentations = [r for r in refs if r.category == DocCategory.investor_presentation and is_major_investor_presentation(r)]
    presentations = cap_presentations_per_fy(presentations, max_per_fy=1)
    return annual + presentations
