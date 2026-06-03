"""Web search for source discovery.

Default: **DuckDuckGo** (free, no API key). Optional fallbacks: **Exa** for
semantic discovery and **Tavily** when search snippets are useful.

Alternatives you can wire later (not built in): Brave Search API, SerpAPI,
Google Programmable Search, Perplexity Sonar. DDG is the best zero-cost default
for this assignment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..budget import BUDGET, BudgetExceeded
from ..config import SETTINGS


@dataclass
class SearchHit:
    url: str
    title: str
    content: str
    score: float = 0.0
    provider: str = ""


def _search_ddg(query: str, *, max_results: int) -> list[SearchHit]:
    try:
        BUDGET.charge("ddg")
    except BudgetExceeded:
        return []
    try:
        from ddgs import DDGS

        hits: list[SearchHit] = []
        for r in DDGS().text(query, max_results=max_results):
            hits.append(
                SearchHit(
                    url=r.get("href", "") or r.get("link", ""),
                    title=r.get("title", ""),
                    content=r.get("body", "") or "",
                    provider="ddg",
                )
            )
        return hits
    except Exception:
        return []


def _search_tavily(
    query: str,
    *,
    max_results: int,
    depth: str = "basic",
    include_domains: Optional[list[str]] = None,
) -> list[SearchHit]:
    if not SETTINGS.tavily_api_key:
        return []
    try:
        BUDGET.charge("tavily")
    except BudgetExceeded:
        return []
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=SETTINGS.tavily_api_key)
        resp = client.search(
            query=query,
            max_results=max_results,
            search_depth=depth,
            include_domains=include_domains or None,
        )
        return [
            SearchHit(
                url=r.get("url", ""),
                title=r.get("title", ""),
                content=r.get("content", "") or "",
                score=float(r.get("score", 0.0) or 0.0),
                provider="tavily",
            )
            for r in resp.get("results", []) or []
        ]
    except Exception:
        return []


def _search_exa(
    query: str,
    *,
    max_results: int,
    include_domains: Optional[list[str]] = None,
) -> list[SearchHit]:
    if not SETTINGS.exa_api_key:
        return []
    try:
        BUDGET.charge("exa")
    except BudgetExceeded:
        return []
    try:
        import httpx

        payload: dict = {
            "query": query,
            "numResults": max_results,
            "type": "neural",
            "contents": {"text": {"maxCharacters": 800}},
        }
        if include_domains:
            payload["includeDomains"] = include_domains
        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": SETTINGS.exa_api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        hits: list[SearchHit] = []
        for r in data.get("results", []) or []:
            hits.append(
                SearchHit(
                    url=r.get("url", ""),
                    title=r.get("title", "") or "",
                    content=r.get("text", "") or r.get("summary", "") or "",
                    score=float(r.get("score", 0.0) or 0.0),
                    provider="exa",
                )
            )
        return hits
    except Exception:
        return []


class Searcher:
    """Unified search: DDG, Exa, and Tavily with budget-aware fallbacks."""

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        depth: str = "basic",
        include_domains: Optional[list[str]] = None,
    ) -> list[SearchHit]:
        provider = (SETTINGS.search_provider or "ddg_then_tavily").lower()
        hits: list[SearchHit] = []

        if provider in ("ddg", "ddg_then_tavily"):
            hits = _search_ddg(query, max_results=max_results)

        if provider in ("exa", "exa_then_tavily", "exa_ddg_then_tavily"):
            hits = _search_exa(query, max_results=max_results, include_domains=include_domains)

        if provider == "exa_ddg_then_tavily" and len(hits) < max(2, max_results // 2):
            extra = _search_ddg(query, max_results=max_results)
            seen = {h.url for h in hits}
            for h in extra:
                if h.url and h.url not in seen:
                    hits.append(h)
                    seen.add(h.url)

        if provider == "tavily" or (provider == "ddg_then_tavily" and len(hits) < max(2, max_results // 2)):
            extra = _search_tavily(
                query, max_results=max_results, depth=depth, include_domains=include_domains
            )
            seen = {h.url for h in hits}
            for h in extra:
                if h.url and h.url not in seen:
                    hits.append(h)
                    seen.add(h.url)

        if provider in ("exa_then_tavily", "exa_ddg_then_tavily") and len(hits) < max(2, max_results // 2):
            extra = _search_tavily(
                query, max_results=max_results, depth=depth, include_domains=include_domains
            )
            seen = {h.url for h in hits}
            for h in extra:
                if h.url and h.url not in seen:
                    hits.append(h)
                    seen.add(h.url)

        return hits[:max_results]
