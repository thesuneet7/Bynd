"""Extract company narrative from official website about pages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .scrape import WebsiteScraper, normalize_website

_ABOUT_PATHS = (
    "/about-us",
    "/about",
    "/company",
    "/who-we-are",
    "/our-company",
    "/about-us/",
)

_SKIP_LINE = re.compile(
    r"(cookie|privacy policy|all rights reserved|follow us|subscribe|contact us|"
    r"careers|job openings|linkedin|facebook|twitter|instagram|youtube|"
    r"skip to content|menu|home\s*$|read more|key features|visit us|stay tuned)",
    re.I,
)
_PRODUCT_NOISE = re.compile(
    r"(key features|compatibility of|piston options|pad wear|maintenance free|"
    r"product range|specifications|download brochure|view product)",
    re.I,
)
_NEWS_NOISE = re.compile(
    r"(makes its debut|teacher.?s day|read more|celebrate|proud to contribute|"
    r"stay tuned|visit us|undefined)",
    re.I,
)
_JUNK_PAGE = re.compile(r"(page you are looking for is not found|gdpr cookie|404\b)", re.I)
_ABOUT_HEADING = re.compile(r"^#{1,4}\s+about\b", re.I)
_ABOUT_HEADING_TEXT = re.compile(r"^about\b|who we are|our company|company overview|corporate profile", re.I)
_NARRATIVE_BOOST = re.compile(
    r"(heritage|promoted by|founded|established|leading|manufactur|we are|our mission|our vision)",
    re.I,
)
_REGISTRY_LINE = re.compile(
    r"(incorporated on|authorized share capital|paid[- ]up capital|corporate identification|"
    r"cin of|registered address|directors? -|annual general meeting|agm\b)",
    re.I,
)
_HEADING_LINE = re.compile(r"^#{1,4}\s+|^\*\*[^*]+\*\*\s*$")


@dataclass(frozen=True)
class WebsiteAbout:
    url: str
    about: str
    sections: list[tuple[str, str]]


def _normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", (text or "").strip()))
    out: list[str] = []
    for part in parts:
        s = part.strip()
        if len(s) >= 40:
            out.append(s)
    return out


def _is_near_duplicate(candidate: str, existing: list[str], *, threshold: float = 0.82) -> bool:
    norm = _normalize_sentence(candidate)
    if not norm:
        return True
    for item in existing:
        other = _normalize_sentence(item)
        if not other:
            continue
        if norm == other or norm in other or other in norm:
            return True
        if SequenceMatcher(None, norm, other).ratio() >= threshold:
            return True
    return False


def _paragraphs_from_markdown(markdown: str) -> list[str]:
    blocks: list[str] = []
    for raw in re.split(r"\n{2,}", markdown or ""):
        lines: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or _HEADING_LINE.match(line) or _SKIP_LINE.search(line):
                continue
            line = re.sub(r"^[-*•]\s+", "", line)
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            if len(line) >= 30:
                lines.append(line)
        if not lines:
            continue
        paragraph = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if len(paragraph) >= 60 and not _SKIP_LINE.search(paragraph):
            blocks.append(paragraph)
    return blocks


def _score_paragraph(paragraph: str, company_name: str) -> float:
    score = min(len(paragraph) / 400.0, 1.0)
    if _REGISTRY_LINE.search(paragraph):
        score -= 0.35
    if _PRODUCT_NOISE.search(paragraph):
        score -= 0.5
    if _NEWS_NOISE.search(paragraph):
        score -= 0.65
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", company_name.lower()).split() if len(t) > 3]
    if tokens and any(token in paragraph.lower() for token in tokens[:2]):
        score += 0.15
    if _NARRATIVE_BOOST.search(paragraph):
        score += 0.2
    return score


def _is_junk_page(markdown: str) -> bool:
    sample = (markdown or "")[:2500]
    return bool(_JUNK_PAGE.search(sample))


def _sections_from_headings(markdown: str) -> list[tuple[str, str]]:
    lines = (markdown or "").splitlines()
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in lines:
        if re.match(r"^#{1,4}\s+", line.strip()):
            if current_heading and current_lines:
                body = re.sub(r"\s+", " ", " ".join(current_lines)).strip()
                if len(body) >= 60:
                    sections.append((current_heading, body))
            current_heading = re.sub(r"^#{1,4}\s+", "", line.strip())
            current_lines = []
            continue
        if current_heading:
            if line.strip().startswith("!["):
                continue
            if _SKIP_LINE.search(line):
                continue
            current_lines.append(line.strip())
    if current_heading and current_lines:
        body = re.sub(r"\s+", " ", " ".join(current_lines)).strip()
        if len(body) >= 60:
            sections.append((current_heading, body))
    return sections


def parse_about_from_page(markdown: str, *, company_name: str, url: str) -> WebsiteAbout | None:
    if _is_junk_page(markdown):
        return None

    heading_sections = _sections_from_headings(markdown)
    about_sections = [
        (heading, body)
        for heading, body in heading_sections
        if _ABOUT_HEADING_TEXT.search(heading.strip())
    ]
    if about_sections:
        ranked_sections = sorted(
            about_sections,
            key=lambda item: _score_paragraph(item[1], company_name),
            reverse=True,
        )
        primary_heading, primary_body = ranked_sections[0]
        extras = [
            (heading, body[:1200])
            for heading, body in ranked_sections[1:3]
            if not _is_near_duplicate(body, [primary_body])
        ]
        return WebsiteAbout(url=url, about=primary_body[:2400], sections=[(primary_heading, primary_body[:1200])] + extras)

    paragraphs = _paragraphs_from_markdown(markdown)
    if not paragraphs:
        return None
    ranked = sorted(paragraphs, key=lambda p: _score_paragraph(p, company_name), reverse=True)
    narrative = [p for p in ranked if not _REGISTRY_LINE.search(p) and not _NEWS_NOISE.search(p)]
    chosen = narrative[:4] if narrative else [p for p in ranked if not _NEWS_NOISE.search(p)][:2]
    if not chosen:
        return None
    about = " ".join(chosen).strip()
    if len(about) < 80 or _score_paragraph(about, company_name) < 0.2:
        return None
    sections: list[tuple[str, str]] = []
    for paragraph in chosen[1:]:
        if not _is_near_duplicate(paragraph, [about]):
            sections.append(("Company background", paragraph))
    return WebsiteAbout(url=url, about=about, sections=sections)


def parse_about_from_sections(sections: list) -> WebsiteAbout | None:
    """Build about text from keyword-harvested website sections (bucket='about')."""
    about_sections = [s for s in sections if getattr(s, "bucket", "") == "about"]
    if not about_sections:
        return None
    ranked = sorted(about_sections, key=lambda s: len(getattr(s, "text", "") or ""), reverse=True)
    primary = ranked[0]
    text = re.sub(r"\s+", " ", (getattr(primary, "text", "") or "").strip())
    if len(text) < 80:
        return None
    url = str(getattr(primary, "url", "") or "").rstrip("/")
    heading = str(getattr(primary, "heading", "") or "About").strip()
    extras: list[tuple[str, str]] = []
    for sec in ranked[1:3]:
        body = re.sub(r"\s+", " ", (getattr(sec, "text", "") or "").strip())
        title = str(getattr(sec, "heading", "") or "About").strip()
        if len(body) >= 80 and not _is_near_duplicate(body, [text]):
            extras.append((title, body[:1200]))
    return WebsiteAbout(url=url, about=text[:2400], sections=[(heading, text[:1200])] + extras)


def extract_website_about(*, website: str, company_name: str) -> WebsiteAbout | None:
    """Scrape likely about pages and return the best narrative block."""
    root = normalize_website(website)
    scraper = WebsiteScraper()
    candidates: list[str] = []
    seen: set[str] = set()
    mapped = scraper.map_site(root, limit=30)
    for url in [*mapped, *[f"{root}{path}".rstrip("/") for path in _ABOUT_PATHS], root]:
        if url in seen:
            continue
        seen.add(url)
        candidates.append(url)

    best: WebsiteAbout | None = None
    best_score = 0.0
    for url in candidates:
        page = scraper.scrape(url)
        if not page.ok or not page.markdown.strip():
            continue
        parsed = parse_about_from_page(page.markdown, company_name=company_name, url=page.url or url)
        if parsed is None:
            continue
        score = _score_paragraph(parsed.about, company_name)
        low_url = (page.url or url).lower()
        if any(token in low_url for token in ("about", "who-we-are", "our-company", "company")):
            score += 0.25
        if low_url.rstrip("/") == root:
            score -= 0.1
        if score > best_score:
            best = parsed
            best_score = score
    if best is None or best_score < 0.25:
        return None
    return best
