"""Per-company evidence store with local embeddings.

No external embedding key is needed: we use fastembed (ONNX, BAAI/bge-small).
Each chunk carries its full provenance (source_id + locator + text) so retrieval
returns evidence *with* provenance attached — provenance is never reconstructed
after the fact. Falls back to a keyword scorer if fastembed is unavailable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

_EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"


@dataclass
class Chunk:
    id: str
    text: str
    source_id: str
    locator: dict[str, Any] = field(default_factory=dict)


class _Embedder:
    """Lazy fastembed wrapper with a keyword fallback."""

    def __init__(self) -> None:
        self._model = None
        self._ok = True
        try:
            import os

            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=_EMBED_MODEL_NAME, threads=os.cpu_count() or 4)
        except Exception:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok and self._model is not None

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = list(self._model.embed(texts, batch_size=128))
        arr = np.array(vecs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


_embedder: Optional[_Embedder] = None


def _get_embedder() -> _Embedder:
    global _embedder
    if _embedder is None:
        _embedder = _Embedder()
    return _embedder


# Signals that a page/section is information-rich for a one-pager. Weighted so a
# financial-statement page (dense with these) scores far above boilerplate (notices,
# governance, auditor language, related-party legalese).
_RELEVANCE_TERMS: dict[str, float] = {
    # financials (high value)
    "revenue from operations": 6, "total income": 5, "profit before tax": 4,
    "profit for the year": 4, "ebitda": 5, "operating profit": 4, "net profit": 4,
    "earnings per share": 3, "balance sheet": 4, "statement of profit": 5,
    "cash flow": 3, "borrowings": 3, "net worth": 3, "total equity": 3,
    "return on": 3, "financial highlights": 6, "₹ crore": 3, "in crore": 3,
    "revenue": 2, "turnover": 3, "margin": 2,
    # business / overview / products / clients
    "manufactures": 3, "manufacturing": 2, "products": 2, "product portfolio": 4,
    "installed capacity": 4, "segment": 2, "business overview": 4, "our business": 3,
    "customers": 3, "clients": 3, "oem": 3, "supplies to": 3, "exports": 2,
    "incorporated": 2, "subsidiary": 2, "joint venture": 2, "facilities": 2,
    "end-use": 2, "served": 1,
}
# Boilerplate signals that should DROP a page even if it has stray keywords.
_NEGATIVE_TERMS: dict[str, float] = {
    "notice is hereby given": 8, "ordinary business": 4, "special business": 4,
    "proxy": 4, "corporate governance report": 5, "remuneration of directors": 4,
    "secretarial audit": 5, "related party transactions": 3, "csr": 3,
    "registered office": 2, "e-voting": 5, "postal ballot": 5,
}


def relevance_score(text: str) -> float:
    """Cheap, free heuristic: how likely is this page useful for a one-pager?"""
    low = (text or "").lower()
    if len(low) < 120:
        return 0.0
    score = sum(w for term, w in _RELEVANCE_TERMS.items() if term in low)
    score -= sum(w for term, w in _NEGATIVE_TERMS.items() if term in low)
    # Pages with a grid of numbers (tables) get a small boost.
    digits = sum(c.isdigit() for c in low)
    if digits / max(len(low), 1) > 0.04:
        score += 2
    return float(score)


def chunk_text(text: str, *, target: int = 900, overlap: int = 150) -> list[str]:
    """Split on paragraph boundaries into ~target-char windows with overlap."""
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= target:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) > target:
                # hard-split a very long paragraph (e.g. a table dump)
                for i in range(0, len(p), target - overlap):
                    chunks.append(p[i : i + target])
                buf = ""
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


class EvidenceStore:
    """In-memory vector store scoped to a single company run."""

    def __init__(self, max_chunks: int = 700) -> None:
        self.chunks: list[Chunk] = []
        self._matrix: Optional[np.ndarray] = None
        self._n = 0
        self.max_chunks = max_chunks  # safety ceiling so embedding stays fast

    def add(self, text: str, source_id: str, locator: Optional[dict] = None) -> int:
        if len(self.chunks) >= self.max_chunks:
            return 0
        added = 0
        for piece in chunk_text(text):
            if len(self.chunks) >= self.max_chunks:
                break
            self._n += 1
            self.chunks.append(
                Chunk(id=f"E{self._n}", text=piece, source_id=source_id, locator=dict(locator or {}))
            )
            added += 1
        self._matrix = None  # invalidate cached embeddings
        return added

    def warm_index(self) -> None:
        """Pre-build the embedding matrix once after ingestion (avoids a long silent wait
        on the first agent retrieval, and surfaces progress in the run log)."""
        self._ensure_index()

    def _ensure_index(self) -> None:
        emb = _get_embedder()
        if self._matrix is not None or not self.chunks:
            return
        if emb.available:
            self._matrix = emb.embed([c.text for c in self.chunks])

    def search(self, query: str, k: int = 6) -> list[Chunk]:
        if not self.chunks:
            return []
        emb = _get_embedder()
        self._ensure_index()
        if emb.available and self._matrix is not None:
            qv = emb.embed([query])[0]
            scores = self._matrix @ qv
        else:
            scores = np.array([_keyword_score(query, c.text) for c in self.chunks], dtype=np.float32)
        order = np.argsort(-scores)[:k]
        return [self.chunks[i] for i in order]

    def search_multi(self, queries: list[str], k_each: int = 5, k_total: int = 12) -> list[Chunk]:
        """Union of retrievals across several queries, de-duped, capped."""
        seen: dict[str, Chunk] = {}
        for q in queries:
            for c in self.search(q, k=k_each):
                seen.setdefault(c.id, c)
            if len(seen) >= k_total * 2:
                break
        return list(seen.values())[: max(k_total, 1)]


def _keyword_score(query: str, text: str) -> float:
    q = set(re.findall(r"\w+", query.lower()))
    t = re.findall(r"\w+", text.lower())
    if not q or not t:
        return 0.0
    tset = set(t)
    return len(q & tset) / (len(q) ** 0.5)
