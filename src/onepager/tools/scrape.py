"""Clean content extraction via Firecrawl (v2 SDK), with an httpx+selectolax
fallback so the pipeline degrades gracefully if Firecrawl errors or credits run
out. Results are cached on disk by URL hash so re-runs don't re-spend credits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

import httpx

from ..budget import BUDGET, BudgetExceeded
from ..config import CACHE_DIR, SETTINGS

_SCRAPE_CACHE = CACHE_DIR / "scrape"
_SCRAPE_CACHE.mkdir(parents=True, exist_ok=True)


@dataclass
class ScrapeResult:
    url: str
    markdown: str
    title: str = ""
    published: Optional[str] = None
    ok: bool = True
    via: str = "firecrawl"


def _cache_path(url: str) -> "object":
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    return _SCRAPE_CACHE / f"{h}.json"


class Scraper:
    def __init__(self) -> None:
        self._app = None
        if SETTINGS.firecrawl_api_key:
            try:
                from firecrawl import FirecrawlApp

                self._app = FirecrawlApp(api_key=SETTINGS.firecrawl_api_key)
            except Exception:
                self._app = None

    def scrape(self, url: str, *, use_cache: bool = True) -> ScrapeResult:
        cp = _cache_path(url)
        if use_cache and cp.exists():
            d = json.loads(cp.read_text())
            return ScrapeResult(**d)

        # Prefer Firecrawl when enabled (better JS/markdown); httpx is the fallback.
        if SETTINGS.prefer_firecrawl and self._app and BUDGET.remaining("firecrawl") > 0:
            result = self._scrape_firecrawl(url) or self._scrape_fallback(url)
        else:
            result = self._scrape_fallback(url) or self._scrape_firecrawl(url)
        if result and result.ok and result.markdown.strip():
            cp.write_text(json.dumps(result.__dict__))
        return result or ScrapeResult(url=url, markdown="", ok=False, via="none")

    def _scrape_firecrawl(self, url: str) -> Optional[ScrapeResult]:
        if not self._app:
            return None
        try:
            BUDGET.charge("firecrawl")
        except BudgetExceeded:
            return None
        try:
            doc = self._app.scrape(url, formats=["markdown"], only_main_content=True, timeout=30000)
            md = getattr(doc, "markdown", None) or ""
            meta = getattr(doc, "metadata", None)
            title = ""
            published = None
            if meta is not None:
                title = getattr(meta, "title", "") or (meta.get("title", "") if isinstance(meta, dict) else "")
                published = getattr(meta, "published_time", None) or (
                    meta.get("publishedTime") if isinstance(meta, dict) else None
                )
            if not md.strip():
                return None
            return ScrapeResult(url=url, markdown=md, title=title or "", published=published, via="firecrawl")
        except Exception:
            return None

    def _scrape_fallback(self, url: str) -> Optional[ScrapeResult]:
        try:
            from selectolax.parser import HTMLParser

            r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 onepager-agent"})
            r.raise_for_status()
            tree = HTMLParser(r.text)
            for tag in tree.css("script, style, nav, footer, header, noscript"):
                tag.decompose()
            title = tree.css_first("title")
            body = tree.body
            text = body.text(separator="\n", strip=True) if body else r.text
            return ScrapeResult(
                url=url,
                markdown=text,
                title=title.text(strip=True) if title else "",
                via="httpx",
            )
        except Exception:
            return None
