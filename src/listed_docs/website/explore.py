"""Keyword-guided exploration: navigate until product/customer sections, then harvest those only."""
from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

from onepager.config import SETTINGS

from .keywords import classify_heading, link_priority
from .scrape import normalize_website, same_site

_LOG = Callable[[str], None]

_IMG_EXT = re.compile(r"\.(png|jpe?g|gif|webp|avif)(\?|$)", re.I)
_SKIP_IMG = re.compile(r"(linkedin|facebook|twitter|instagram|gdpr|cookie|favicon|spinner|loader|icon-|\.svg$)", re.I)
_URL_PRODUCT = re.compile(
    r"(product|offering|solution|business.unit|portfolio|amtix|polymer|casting|friction|component|manufactur)",
    re.I,
)
_URL_CUSTOMER = re.compile(r"(customer|client|oem|case.stud|partner|who-we-serve)", re.I)

_SKIP_CLICK = re.compile(
    r"(logout|sign.?in|login|cookie|privacy|linkedin\.com|facebook\.com|twitter\.com|youtube\.com|mailto:|tel:)",
    re.I,
)

# In-browser: find headings matching keywords, harvest container text + images only.
_FIND_SECTIONS_JS = r"""
() => {
  const productKw = /products?|product portfolio|product range|our offerings?|offerings?|solutions?|what we (make|manufacture|offer)|business lines?|business units?|our businesses|components?|applications?|portfolio|manufactur/i;
  const customerKw = /customers?|clients?|client base|key accounts?|strategic accounts?|oems?|who we serve|marquee customers?|customer success|case stud|trusted by|our customers?|partner/i;
  const skipImg = /linkedin|facebook|twitter|instagram|gdpr|cookie|favicon|spinner|loader/i;

  function bucketFor(text) {
    const t = (text || '').trim();
    if (!t || t.length > 140) return null;
    if (customerKw.test(t)) return 'customers';
    if (productKw.test(t)) return 'products';
    return null;
  }

  function harvest(heading) {
    const container = heading.closest('section, article, [class*="section"], [class*="block"], .container, .row, main')
      || heading.parentElement;
    const root = container || heading;
    const text = root.innerText || '';
    const images = [];
    root.querySelectorAll('img[src], img[data-src]').forEach(img => {
      const src = img.src || img.getAttribute('data-src') || '';
      if (!src || skipImg.test(src)) return;
      images.push({ src, alt: (img.alt || '').trim() });
    });
    return { text: text.slice(0, 24000), images };
  }

  const seen = new Set();
  const out = [];
  const selectors = 'h1,h2,h3,h4,h5,h6,.section-title,.heading,.title,[class*="heading"],[class*="title"]';
  document.querySelectorAll(selectors).forEach(el => {
    const heading = (el.innerText || el.getAttribute('aria-label') || '').trim().split('\n')[0];
    const bucket = bucketFor(heading);
    if (!bucket) return;
    const key = bucket + '|' + heading.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    const { text, images } = harvest(el);
    if (text.length < 30) return;
    out.push({ bucket, heading, text, images });
  });
  return out;
}
"""

_COLLECT_TARGETS_JS = r"""
() => {
  const skip = /cookie|gdpr|linkedin|facebook|twitter|youtube|instagram|privacy policy/i;
  const nodes = [...document.querySelectorAll(
    'a[href], button, [role=button], [role=tab], summary, .accordion-button, [data-bs-toggle], .nav-link'
  )];
  return nodes.map((el, i) => ({
    i,
    text: (el.innerText || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 120),
    href: el.href || '',
    tag: el.tagName,
  })).filter(x => (x.text || x.href) && !skip.test(x.text) && !skip.test(x.href));
}
"""

_CLICK_BY_INDEX_JS = r"""
(index) => {
  const nodes = [...document.querySelectorAll(
    'a[href], button, [role=button], [role=tab], summary, .accordion-button, [data-bs-toggle], .nav-link'
  )];
  const el = nodes[index];
  if (el) el.click();
}
"""


@dataclass
class SiteImage:
    src_url: str
    local_path: str
    alt: str
    page_url: str
    section_heading: str = ""
    bucket: str = ""
    interaction: str = "initial"


@dataclass
class RelevantSection:
    url: str
    bucket: str
    heading: str
    text: str
    interaction: str = "initial"
    state_id: str = ""
    images: list[SiteImage] = field(default_factory=list)


@dataclass
class ExploreResult:
    website: str
    sections: list[RelevantSection] = field(default_factory=list)
    urls_visited: int = 0
    notes: list[str] = field(default_factory=list)


def _state_id(url: str, bucket: str, heading: str, interaction: str) -> str:
    blob = f"{url}|{bucket}|{heading}|{interaction}"
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def links_from_html(html: str, *, root: str) -> list[str]:
    from selectolax.parser import HTMLParser

    root = normalize_website(root)
    found: list[tuple[int, str]] = []
    tree = HTMLParser(html or "")
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        text = node.text(strip=True)
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urljoin(root + "/", href).split("#")[0].rstrip("/")
        if not abs_url.startswith("http") or not same_site(abs_url, root):
            continue
        if _IMG_EXT.search(abs_url):
            continue
        prio = link_priority(text, abs_url)
        if prio >= 80:
            continue
        found.append((prio, abs_url))
    found.sort(key=lambda x: (x[0], x[1]))
    out: list[str] = []
    for _, u in found:
        if u not in out:
            out.append(u)
    return out


def _download_image(url: str, dest) -> bool:
    import httpx

    try:
        r = httpx.get(url, timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 ByndAI"})
        r.raise_for_status()
        if len(r.content) < 300:
            return False
        dest.write_bytes(r.content)
        return True
    except Exception:
        return False


def _img_filename(url: str, heading: str) -> str:
    path = urlparse(url).path
    name = path.split("/")[-1] or "image"
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)[:60]
    h = hashlib.sha1(f"{url}|{heading}".encode()).hexdigest()[:8]
    if not _IMG_EXT.search(name):
        name += ".png" if "png" in url.lower() else ".jpg"
    stem, dot, ext = name.rpartition(".")
    return f"{stem}_{h}.{ext}" if dot else f"{name}_{h}"


def explore_site(
    website: str,
    *,
    images_dir,
    log: _LOG | None = None,
    max_pages: int | None = None,
    max_clicks_per_page: int | None = None,
    max_images: int | None = None,
) -> ExploreResult:
    """Navigate site until product/customer keyword sections appear; harvest only those."""
    from playwright.sync_api import sync_playwright

    root = normalize_website(website)
    max_pages = max_pages or SETTINGS.max_website_explore_pages
    max_clicks = max_clicks_per_page or SETTINGS.max_website_clicks_per_page
    max_images = max_images or SETTINGS.max_website_images

    def note(msg: str) -> None:
        if log:
            log(msg)

    result = ExploreResult(website=root)
    images_dir.mkdir(parents=True, exist_ok=True)

    seen_sections: set[str] = set()
    seen_urls: set[str] = set()
    seen_img_urls: set[str] = set()
    url_queue: deque[str] = deque([root])

    def _safe_snapshot(pg, fallback_url: str) -> tuple[str, str]:
        for _ in range(3):
            try:
                pg.wait_for_load_state("domcontentloaded", timeout=8_000)
                return pg.content(), pg.url.rstrip("/") or fallback_url
            except Exception:
                pg.wait_for_timeout(400)
        return "", fallback_url

    def _url_bucket(page_url: str) -> str | None:
        if _URL_CUSTOMER.search(page_url):
            return "customers"
        if _URL_PRODUCT.search(page_url):
            return "products"
        return None

    def _save_section(
        page_url: str,
        bucket: str,
        heading: str,
        text: str,
        images_raw: list,
        interaction: str,
    ) -> bool:
        if len(text.strip()) < 30:
            return False
        sid = _state_id(page_url, bucket, heading, interaction)
        if sid in seen_sections:
            return False
        seen_sections.add(sid)

        section_images: list[SiteImage] = []
        for img_row in images_raw or []:
            if len(seen_img_urls) >= max_images:
                break
            src = str(img_row.get("src") or "").strip()
            alt = str(img_row.get("alt") or "").strip()
            if not src or src in seen_img_urls or _SKIP_IMG.search(src):
                continue
            fname = _img_filename(src, heading)
            local = images_dir / bucket / fname
            local.parent.mkdir(parents=True, exist_ok=True)
            if _download_image(src, local):
                seen_img_urls.add(src)
                section_images.append(
                    SiteImage(
                        src_url=src,
                        local_path=str(local),
                        alt=alt,
                        page_url=page_url,
                        section_heading=heading,
                        bucket=bucket,
                        interaction=interaction,
                    )
                )

        result.sections.append(
            RelevantSection(
                url=page_url,
                bucket=bucket,
                heading=heading,
                text=text.strip(),
                interaction=interaction,
                state_id=sid,
                images=section_images,
            )
        )
        note(
            f"  found [{bucket}] «{heading}» @ {page_url} — {len(text)} chars, {len(section_images)} images"
        )
        return True

    def harvest_sections(page, page_url: str, interaction: str, *, page_title: str = "") -> int:
        found = 0
        try:
            raw_sections = page.evaluate(_FIND_SECTIONS_JS)
        except Exception as e:  # noqa: BLE001
            result.notes.append(f"section scan failed @ {page_url}: {e}")
            raw_sections = []

        for row in raw_sections or []:
            bucket = str(row.get("bucket") or "")
            heading = str(row.get("heading") or "").strip()
            text = str(row.get("text") or "").strip()
            if bucket not in ("products", "customers") or not heading:
                continue
            if _save_section(page_url, bucket, heading, text, row.get("images") or [], interaction):
                found += 1

        # URL looks like a product/customer page but no heading matched — use main content.
        if found == 0:
            bucket = _url_bucket(page_url)
            if bucket:
                try:
                    main = page.evaluate(
                        """() => {
                        const main = document.querySelector('main, #main, .main, article, .content, .site-content') || document.body;
                        const text = (main.innerText || '').slice(0, 24000);
                        const images = [];
                        main.querySelectorAll('img[src]').forEach(img => {
                          images.push({ src: img.src, alt: (img.alt || '').trim() });
                        });
                        return { text, images };
                    }"""
                    )
                except Exception:
                    main = {}
                heading = page_title or page_url.split("/")[-1].replace("-", " ").title()
                if _save_section(
                    page_url,
                    bucket,
                    heading,
                    str(main.get("text") or ""),
                    main.get("images") or [],
                    interaction,
                ):
                    found += 1
        return found

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (compatible; ByndAI/1.0)")
        page = context.new_page()

        while url_queue and len(seen_urls) < max_pages:
            url = url_queue.popleft().rstrip("/")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            result.urls_visited += 1
            note(f"explore: {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(600)
            except Exception as e:  # noqa: BLE001
                result.notes.append(f"goto failed {url}: {e}")
                continue

            html, cur_url = _safe_snapshot(page, url)
            try:
                title = page.title()
            except Exception:
                title = ""
            harvest_sections(page, cur_url, "initial", page_title=title)

            if html:
                for link in links_from_html(html, root=root):
                    if link not in seen_urls and link not in url_queue:
                        url_queue.append(link)

            try:
                targets = page.evaluate(_COLLECT_TARGETS_JS)
            except Exception:
                targets = []

            targets_sorted = sorted(
                targets,
                key=lambda t: link_priority(str(t.get("text") or ""), str(t.get("href") or "")),
            )

            clicks_done = 0
            for item in targets_sorted[:max_clicks]:
                label = str(item.get("text") or item.get("href") or "")[:100]
                if _SKIP_CLICK.search(label):
                    continue
                idx = item.get("i")
                try:
                    page.evaluate(_CLICK_BY_INDEX_JS, idx)
                    page.wait_for_timeout(700)
                except Exception:
                    continue

                clicks_done += 1
                cur_url = page.url.rstrip("/")
                if not same_site(cur_url, root):
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                        page.wait_for_timeout(400)
                    except Exception:
                        pass
                    continue

                post_html, snap_url = _safe_snapshot(page, cur_url)
                interaction = f"click:{label}"
                try:
                    post_title = page.title()
                except Exception:
                    post_title = ""
                harvest_sections(page, snap_url, interaction, page_title=post_title)

                if post_html:
                    for link in links_from_html(post_html, root=root):
                        if link not in seen_urls and link not in url_queue:
                            url_queue.append(link)

            if clicks_done:
                result.notes.append(f"{url}: {clicks_done} clicks, sections so far={len(result.sections)}")

        browser.close()

    n_prod = sum(1 for s in result.sections if s.bucket == "products")
    n_cust = sum(1 for s in result.sections if s.bucket == "customers")
    n_img = sum(len(s.images) for s in result.sections)
    result.notes.insert(
        0,
        f"keyword-guided: {result.urls_visited} URLs → {len(result.sections)} sections "
        f"({n_prod} product, {n_cust} customer), {n_img} images",
    )
    return result
