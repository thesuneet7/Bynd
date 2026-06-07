"""Company overview text from screener.in and tofler.in."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from selectolax.parser import HTMLParser, Node

from ..schemas import Entity
from .screener import (
    ScreenerParseError,
    fetch_screener_html,
    normalize_ticker,
    screener_url,
    _register_screener_source,
)
from .screener_session import ScreenerAuthError, _looks_like_commentary, fetch_commentary_html, get_screener_client
from .tofler import (
    ToflerParseError,
    fetch_tofler_html,
    resolve_tofler_company,
    _register_tofler_source,
)

if TYPE_CHECKING:
    from ..context import RunContext


@dataclass
class ProviderOverview:
    provider: str
    url: str
    about: str = ""
    key_points: list[tuple[str, str]] = field(default_factory=list)
    note: str | None = None
    website_about_url: str = ""
    website_about: str = ""

    @property
    def full_text(self) -> str:
        parts: list[str] = []
        if self.about:
            parts.append(f"About: {self.about}")
        for title, body in self.key_points:
            parts.append(f"{title}: {body}")
        return "\n\n".join(parts)


_REGISTRY_MARKERS = re.compile(
    r"(incorporated on|authorized share capital|paid[- ]up capital|corporate identification|"
    r"cin of|registered address|annual general meeting|\bagm\b|directors? -)",
    re.I,
)


def _normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", (text or "").strip()))
    return [p.strip() for p in parts if len(p.strip()) >= 35]


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


def _unique_sentences(sentences: list[str], *, existing: list[str]) -> list[str]:
    out: list[str] = []
    seen = list(existing)
    for sentence in sentences:
        if _is_near_duplicate(sentence, seen):
            continue
        out.append(sentence)
        seen.append(sentence)
    return out


def merge_overview_with_website(
    overview: ProviderOverview | None,
    *,
    website_about: str = "",
    website_about_url: str = "",
    website_sections: list[tuple[str, str]] | None = None,
) -> ProviderOverview | None:
    """Combine provider overview with website narrative, deduplicating overlapping text."""
    if not overview:
        if not website_about.strip():
            return None
        return ProviderOverview(
            provider="company website",
            url=website_about_url,
            about=website_about.strip(),
            website_about=website_about.strip(),
            website_about_url=website_about_url,
            note="Overview sourced from the company's official website.",
        )
    if not website_about.strip():
        return overview

    provider_sentences = _split_sentences(overview.about)
    website_sentences = _split_sentences(website_about)
    narrative_provider = [s for s in provider_sentences if not _REGISTRY_MARKERS.search(s)]
    registry_provider = [s for s in provider_sentences if _REGISTRY_MARKERS.search(s)]

    merged_parts: list[str] = []
    if website_sentences:
        merged_parts.extend(website_sentences)
    merged_parts.extend(_unique_sentences(narrative_provider, existing=merged_parts))
    merged_about = " ".join(merged_parts).strip() or overview.about or website_about.strip()

    key_points = list(overview.key_points)
    seen_titles = {title.lower() for title, _ in key_points}
    supplemental = list(website_sections or []) + _website_key_points(website_about)
    for title, body in supplemental:
        if title.lower() in seen_titles:
            continue
        if _is_near_duplicate(body, [existing_body for _, existing_body in key_points]):
            continue
        if _is_near_duplicate(body, _split_sentences(merged_about)):
            continue
        key_points.append((title, body))
        seen_titles.add(title.lower())

    if registry_provider and not any(title.lower() == "registry details" for title, _ in key_points):
        key_points.append(("Registry details", " ".join(registry_provider)))

    notes: list[str] = []
    if overview.note:
        notes.append(overview.note)
    notes.append(
        "About section merged from provider overview and the company's official website "
        "(duplicate sentences removed)."
    )
    return ProviderOverview(
        provider=overview.provider,
        url=overview.url,
        about=merged_about,
        key_points=key_points,
        note=" ".join(notes),
        website_about=website_about.strip(),
        website_about_url=website_about_url,
    )


def _website_key_points(website_about: str) -> list[tuple[str, str]]:
    sentences = _split_sentences(website_about)
    if len(sentences) <= 1:
        return []
    extras = _unique_sentences(sentences[1:], existing=[sentences[0]])
    if not extras:
        return []
    return [("From company website", " ".join(extras[:3]))]


def _normalize_text(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "")).strip()
    s = re.sub(r"\.([A-Z])", r". \1", s)
    s = re.sub(r"\s+\.", ".", s)
    return s


def _strip_inline_citations(text: str) -> str:
    return re.sub(r"\s*\[\d+\]\s*", " ", text).strip()


def _node_text(node: Node | None) -> str:
    if node is None:
        return ""
    return _normalize_text(node.text(separator=" ", strip=True))


def _parse_key_point_paragraphs(root: Node | None) -> list[tuple[str, str]]:
    if root is None:
        return []
    out: list[tuple[str, str]] = []
    for p in root.css("p"):
        body = _node_text(p)
        if not body or len(body) < 20:
            continue
        strong = p.css_first("strong")
        title = _normalize_text(strong.text(strip=True)) if strong else "Key point"
        title = re.sub(r":+$", "", title).strip()
        if strong and body.lower().startswith(title.lower()):
            body = _normalize_text(body[len(title) :])
        body = re.sub(r"^:\s*", "", body)
        body = _strip_inline_citations(body)
        if body:
            out.append((title, body))
    return out


def _is_junk_key_point(title: str, body: str) -> bool:
    low = f"{title} {body}".lower()
    junk = (
        "already registered",
        "request an update",
        "protected by copyright",
        "over 50 lakh investors",
        "warren buffett",
        "login here",
    )
    return any(j in low for j in junk)


def parse_screener_commentary(html: str) -> list[tuple[str, str]]:
    """Key Insights from /wiki/company/{id}/commentary/v2/ (requires login + XHR header)."""
    if not _looks_like_commentary(html):
        return []
    tree = HTMLParser(html)
    for sel in (".commentary", "main", "article", ".modal-content", "body"):
        node = tree.css_first(sel)
        points = _parse_key_point_paragraphs(node)
        if points:
            filtered = [(t, b) for t, b in points if not _is_junk_key_point(t, b)]
            return filtered
    return [
        (t, b)
        for t, b in _parse_key_point_paragraphs(tree.body)
        if not _is_junk_key_point(t, b)
    ]


def parse_screener_overview(
    html: str,
    *,
    url: str,
    commentary_html: str | None = None,
    commentary_via: str | None = None,
) -> ProviderOverview:
    tree = HTMLParser(html)
    profile = tree.css_first(".company-profile") or tree.css_first("#company-info")
    about = ""
    key_points: list[tuple[str, str]] = []
    note: str | None = None

    if profile:
        about = _node_text(profile.css_first(".about p") or profile.css_first(".about"))
        commentary = profile.css_first(".commentary")
        key_points.extend(_parse_key_point_paragraphs(commentary))

    if commentary_html:
        wiki_points = parse_screener_commentary(commentary_html)
        if len(wiki_points) >= 3:
            # Logged-in wiki modal — replace teaser key points with full Key Insights list.
            if not about and wiki_points:
                about = wiki_points[0][1] if wiki_points[0][0].lower() == "key point" else about
            key_points = [
                (t, b)
                for t, b in wiki_points
                if not (t.lower() == "key point" and b == about)
            ]
            note = (
                f"Full Key Insights from screener.in ({commentary_via or 'session'}); "
                f"{len(key_points)} sections parsed."
            )
        elif wiki_points:
            seen = {t.lower() for t, _ in key_points}
            for title, body in wiki_points:
                if title.lower() not in seen:
                    key_points.append((title, body))
                    seen.add(title.lower())
            note = f"Partial Key Insights via screener.in ({commentary_via or 'session'})."
        elif commentary_via == "session":
            note = "Logged in but Key Insights could not be parsed from the wiki response."

    if not note and profile and profile.css_first("button[data-url*='commentary']"):
        note = (
            "Set **SCREENER_USERNAME** and **SCREENER_PASSWORD** in `.env` to fetch full Key Insights "
            "(the Read More modal). Without login, only the public About blurb and first visible key point are parsed."
        )

    return ProviderOverview(provider="screener.in", url=url, about=about, key_points=key_points, note=note)


def parse_tofler_overview(html: str, *, url: str) -> ProviderOverview:
    tree = HTMLParser(html)
    blocks = [_node_text(p) for p in tree.css("p.company_description")]
    blocks = [b for b in blocks if b and not b.startswith("** All rupee")]
    about = blocks[0] if blocks else ""
    extra = blocks[1] if len(blocks) > 1 else ""
    key_points: list[tuple[str, str]] = []
    if extra:
        key_points.append(("Company profile", extra))
    note = None
    if len(blocks) > 1:
        note = (
            "Tofler hides part of the overview behind **Read More** in the UI; "
            "the full text is present in the page HTML and was parsed from both description blocks."
        )
    return ProviderOverview(provider="tofler.in", url=url, about=about, key_points=key_points, note=note)


def fetch_screener_overview(ctx: RunContext, entity: Entity, *, ticker: str | None = None) -> ProviderOverview | None:
    sym = normalize_ticker(ticker or entity.ticker, hint=entity.input_hint, name=entity.canonical_name)
    if not sym:
        return None
    url = screener_url(sym)
    session = None
    try:
        session = get_screener_client()
    except ScreenerAuthError as e:
        ctx.note(f"[overview/screener] login failed: {e}")
    try:
        html, via, company_id = fetch_screener_html(sym, client=session)
    except ScreenerParseError:
        return None
    if "Page not found" in html:
        return None

    commentary_html: str | None = None
    commentary_via: str | None = None
    if company_id and session is not None:
        commentary_html, commentary_via = fetch_commentary_html(company_id, session)
        if commentary_via != "session":
            try:
                session = get_screener_client(force_login=True)
                commentary_html, commentary_via = fetch_commentary_html(company_id, session)
                ctx.note("[overview/screener] refreshed Screener session for Key Insights")
            except ScreenerAuthError as e:
                ctx.note(f"[overview/screener] session refresh failed: {e}")
        if commentary_via == "session" and len(commentary_html) < 800:
            ctx.note(f"[overview/screener] commentary fetch short ({len(commentary_html)} bytes) — session may be invalid")
    elif company_id and not session:
        ctx.note("[overview/screener] no session — set SCREENER_USERNAME/SCREENER_PASSWORD for full Key Insights")

    overview = parse_screener_overview(
        html,
        url=url,
        commentary_html=commentary_html,
        commentary_via=commentary_via,
    )
    _register_screener_source(ctx, sym, url)
    ctx.note(
        f"[overview/screener] {sym} page={via}: about={bool(overview.about)}, "
        f"key_points={len(overview.key_points)}, session={session is not None}"
    )
    return overview


def fetch_tofler_overview(ctx: RunContext, entity: Entity) -> ProviderOverview | None:
    match = resolve_tofler_company(entity)
    if not match:
        return None
    try:
        html, via = fetch_tofler_html(match.url)
    except ToflerParseError:
        return None
    overview = parse_tofler_overview(html, url=match.url)
    _register_tofler_source(ctx, match.url, entity)
    ctx.note(f"[overview/tofler] via {via}: about={bool(overview.about)}, sections={len(overview.key_points)}")
    return overview


def fetch_provider_overview(ctx: RunContext, entity: Entity, *, provider: str | None = None) -> ProviderOverview | None:
    if provider == "screener" or (provider is None and entity.listing_status == "listed"):
        return fetch_screener_overview(ctx, entity)
    if provider == "tofler" or (provider is None and entity.listing_status != "listed"):
        return fetch_tofler_overview(ctx, entity)
    return None


def _overview_sources(overview: ProviderOverview) -> str:
    parts = [f"[{overview.provider}]({overview.url})"]
    if overview.website_about_url:
        parts.append(f"[company website]({overview.website_about_url})")
    if len(parts) == 1:
        return f"_Source: {parts[0]}_"
    return "_Sources: " + ", ".join(parts) + "_"


def render_overview_markdown(overview: ProviderOverview | None) -> list[str]:
    if not overview or not (overview.about or overview.key_points):
        return ["### Company overview", "", "_No overview text parsed._", ""]
    lines = [
        "### Company overview",
        "",
        _overview_sources(overview),
        "",
    ]
    if overview.about:
        lines += ["**About**", "", overview.about, ""]
    if overview.key_points:
        lines += ["**Key points**", ""]
        for title, body in overview.key_points:
            lines.append(f"- **{title}:** {body}")
        lines.append("")
    if overview.note:
        lines.append(f"_{overview.note}_")
        lines.append("")
    return lines
