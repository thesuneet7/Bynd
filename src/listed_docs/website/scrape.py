"""Firecrawl scrape/map with httpx fallback and on-disk cache."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from onepager.budget import BUDGET, BudgetExceeded
from onepager.config import CACHE_DIR, SETTINGS

_SCRAPE_CACHE = CACHE_DIR / "website_scrape"
_SCRAPE_CACHE.mkdir(parents=True, exist_ok=True)

_USER_AGENT = "Mozilla/5.0 (compatible; ByndAI/1.0)"


@dataclass
class WebPage:
    url: str
    markdown: str
    title: str = ""
    via: str = "firecrawl"
    ok: bool = True


def normalize_website(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("website URL is required")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def same_site(url: str, root: str) -> bool:
    try:
        a = urlparse(url).netloc.lower().replace("www.", "")
        b = urlparse(root).netloc.lower().replace("www.", "")
        return bool(a) and a == b
    except Exception:
        return False


def _cache_path(url: str) -> object:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    return _SCRAPE_CACHE / f"{h}.json"


class WebsiteScraper:
    def __init__(self) -> None:
        self._app = None
        if SETTINGS.firecrawl_api_key:
            try:
                from firecrawl import FirecrawlApp

                self._app = FirecrawlApp(api_key=SETTINGS.firecrawl_api_key)
            except Exception:
                self._app = None

    def map_site(self, root_url: str, *, limit: int | None = None) -> list[str]:
        root = normalize_website(root_url)
        cap = limit or SETTINGS.max_website_map_urls
        out = [root]
        if self._app:
            for search in ("products services customers clients", "about portfolio"):
                try:
                    data = self._app.map(root, search=search, limit=cap)
                except Exception:
                    continue
                for link in getattr(data, "links", None) or []:
                    u = str(link).strip().rstrip("/")
                    if u and same_site(u, root) and u not in out:
                        out.append(u)
        out.extend(_common_paths(root))
        return rank_urls(out[:cap], root=root)

    def scrape(self, url: str, *, use_cache: bool = True) -> WebPage:
        url = url.rstrip("/")
        cp = _cache_path(url)
        if use_cache and cp.exists():
            d = json.loads(cp.read_text())
            return WebPage(**d)

        if SETTINGS.prefer_firecrawl and self._app and BUDGET.remaining("firecrawl") > 0:
            page = self._scrape_firecrawl(url) or self._scrape_httpx(url)
        else:
            page = self._scrape_httpx(url) or self._scrape_firecrawl(url)

        if page and page.ok and page.markdown.strip():
            cp.write_text(json.dumps(page.__dict__, ensure_ascii=False), encoding="utf-8")
        return page or WebPage(url=url, markdown="", ok=False, via="none")

    def _scrape_firecrawl(self, url: str) -> Optional[WebPage]:
        if not self._app:
            return None
        try:
            BUDGET.charge("firecrawl")
        except BudgetExceeded:
            return None
        try:
            doc = self._app.scrape(url, formats=["markdown"], only_main_content=True, timeout=45_000)
            md = getattr(doc, "markdown", None) or ""
            meta = getattr(doc, "metadata", None)
            title = ""
            if meta is not None:
                title = getattr(meta, "title", "") or (meta.get("title", "") if isinstance(meta, dict) else "")
            if not md.strip():
                return None
            return WebPage(url=url, markdown=md, title=title or "", via="firecrawl")
        except Exception:
            return None

    def _scrape_httpx(self, url: str) -> Optional[WebPage]:
        try:
            from selectolax.parser import HTMLParser

            r = httpx.get(
                url,
                timeout=25,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
            r.raise_for_status()
            tree = HTMLParser(r.text)
            for tag in tree.css("script, style, nav, footer, header, noscript"):
                tag.decompose()
            title = tree.css_first("title")
            body = tree.body
            text = body.text(separator="\n", strip=True) if body else r.text
            return WebPage(url=url, markdown=text, title=title.text(strip=True) if title else "", via="httpx")
        except Exception:
            return None


def _common_paths(root: str) -> list[str]:
    paths = (
        "/products",
        "/products/",
        "/product",
        "/services",
        "/solutions",
        "/about-us",
        "/about",
        "/company",
        "/customers",
        "/clients",
        "/industries",
        "/applications",
        "/portfolio",
    )
    return [f"{root}{p}".rstrip("/") for p in paths]


_LINK_MD = re.compile(r"\]\((https?://[^)]+)\)")
_LINK_BARE = re.compile(r"(https?://[^\s\])<>\"']+)")


_SKIP_PATH = re.compile(r"(\.(png|jpe?g|gif|svg|webp|ico|pdf|zip|css|js)$|/wp-content/|undefined)", re.I)


def _usable_url(url: str, root: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http") or " " in u or '"' in u:
        return False
    if _SKIP_PATH.search(u):
        return False
    return same_site(u, root)


def links_from_markdown(markdown: str, *, root: str) -> list[str]:
    root = normalize_website(root)
    found: list[str] = []
    for pattern in (_LINK_MD, _LINK_BARE):
        for m in pattern.finditer(markdown or ""):
            u = m.group(1).strip().rstrip("/").rstrip(",.")
            if _usable_url(u, root) and u not in found:
                found.append(u)
    return found


_RELEVANT_PATH = re.compile(
    r"(product|service|solution|offering|portfolio|catalog|customer|client|oem|case.?stud|"
    r"about|business|segment|industr|partner|who.we|our.work|application)",
    re.I,
)


def rank_urls(urls: list[str], *, root: str) -> list[str]:
    """Prioritize on-site URLs likely to mention products or customers."""
    root = normalize_website(root)

    def score(u: str) -> tuple[int, int]:
        low = u.lower()
        s = 0
        if u == root:
            s -= 5
        if _RELEVANT_PATH.search(low):
            s -= 3
        if _SKIP_PATH.search(low):
            s += 8
        if any(k in low for k in ("contact", "career", "privacy", "cookie", "login", "news", "blog", "media")):
            s += 4
        return (s, len(low))

    uniq = []
    seen: set[str] = set()
    for u in urls:
        u = u.rstrip("/")
        if u in seen or not _usable_url(u, root):
            continue
        seen.add(u)
        uniq.append(u)
    return sorted(uniq, key=score)
