"""Deterministic verification — entity must appear verbatim in parsed page text."""
from __future__ import annotations

import re

from .models import ExtractedItem, ParsedPageRecord

_MIN_QUOTE_CHARS = 18
_MIN_NAME_TOKEN_LEN = 2


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) >= _MIN_NAME_TOKEN_LEN]


def _name_in_text(name: str, text: str) -> bool:
    name_norm = _norm(name)
    text_norm = _norm(text)
    if not name_norm:
        return False
    if name_norm in text_norm:
        return True
    # Acronym / shortened form: all significant name tokens appear in order
    name_toks = _tokens(name)
    if not name_toks:
        return False
    if len(name_toks) == 1:
        return name_toks[0] in text_norm.split()
    pattern = r"\b" + r"\W+".join(re.escape(t) for t in name_toks) + r"\b"
    return bool(re.search(pattern, text_norm))


def _best_quote_span(quote: str, page_text: str) -> tuple[bool, str]:
    """Return whether quote is grounded in page_text and the best matching substring."""
    qn = _norm(quote)
    pn = _norm(page_text)
    if not qn or not pn:
        return False, quote

    if len(qn) >= _MIN_QUOTE_CHARS and qn in pn:
        return True, quote.strip()

    words = qn.split()
    if len(words) < 4:
        return qn in pn, quote.strip()

    # Longest consecutive word n-gram from the quote that appears in the page.
    for n in range(len(words), 3, -1):
        for i in range(0, len(words) - n + 1):
            chunk = " ".join(words[i : i + n])
            if len(chunk) >= _MIN_QUOTE_CHARS and chunk in pn:
                # Recover original casing snippet from page_text when possible
                return True, _recover_original_snippet(chunk, page_text) or quote.strip()

    return False, quote.strip()


def _recover_original_snippet(norm_chunk: str, page_text: str) -> str:
    words = norm_chunk.split()
    if not words:
        return ""
    pattern = r"\b" + r"\W+".join(re.escape(w) for w in words[:12]) + r"\b"
    m = re.search(pattern, page_text, flags=re.IGNORECASE)
    if not m:
        return norm_chunk
    start = max(0, m.start() - 20)
    end = min(len(page_text), m.end() + 120)
    return re.sub(r"\s+", " ", page_text[start:end]).strip()


def _locate_page(
    quote: str,
    pages: dict[int, str],
    *,
    claimed: int | None,
) -> int | None:
    order: list[int] = []
    if claimed and claimed in pages:
        order.append(claimed)
    order.extend(p for p in sorted(pages) if p not in order)

    for pg in order:
        ok, _ = _best_quote_span(quote, pages[pg])
        if ok:
            return pg
    return None


def verify_item(
    item: ExtractedItem,
    pages: list[ParsedPageRecord],
    *,
    local_path: str,
) -> ExtractedItem | None:
    """Keep item only if name + evidence are grounded in parsed PDF text."""
    by_page = {p.page: p.content for p in pages}
    if not by_page:
        return None

    page_no = _locate_page(item.evidence, by_page, claimed=item.page)
    if page_no is None:
        return None

    page_text = by_page[page_no]
    ok, grounded_quote = _best_quote_span(item.evidence, page_text)
    if not ok:
        return None

    if not _name_in_text(item.name, grounded_quote) and not _name_in_text(item.name, page_text):
        return None

    return ExtractedItem(
        name=item.name.strip(),
        evidence=grounded_quote[:400],
        source=item.source,
        confidence=0.0,  # set after merge from deterministic rules
        bucket=item.bucket,
        page=page_no,
        document_id=item.document_id,
        verified=True,
        local_path=local_path,
    )


def verify_items(
    items: list[ExtractedItem],
    pages: list[ParsedPageRecord],
    *,
    local_path: str,
) -> tuple[list[ExtractedItem], int]:
    verified: list[ExtractedItem] = []
    rejected = 0
    for item in items:
        v = verify_item(item, pages, local_path=local_path)
        if v is None:
            rejected += 1
        else:
            verified.append(v)
    return verified, rejected
