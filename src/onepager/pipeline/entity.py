"""Stage 1 — Entity Resolution & Disambiguation.

Pins the input to a canonical legal entity BEFORE any retrieval, so we don't
contaminate the evidence store with a namesake company. Determines the
data-richness tier (listed -> rich, unlisted -> sparse) which branches the
source strategy downstream.

Note: entity metadata (ticker/website/CIN) is *routing* information used to find
sources; it is not a body claim. Anything that appears in the one-pager itself
still goes through the full grounding+verification path.
"""
from __future__ import annotations

from ..llm import claude
from ..schemas import Entity
from ..tools.search import Searcher
from .context import RunContext

_SYS = """You are an entity-resolution analyst. Given a company name and optional hint,
plus web search snippets, identify the SPECIFIC legal entity. Be conservative: if the
search results don't establish a field, leave it null/empty — never invent a ticker,
CIN, or website. Return JSON with keys:
  canonical_name, aliases (list), country, listing_status ("listed"|"unlisted"|"unknown"),
  ticker (or null), registry_id (CIN or null), website (or null),
  disambiguation_note (1-2 sentences identifying which entity and ruling out namesakes),
  data_richness_tier ("rich"|"moderate"|"sparse")."""


_KNOWN_ENTITIES = {
    "bharat forge": Entity(
        input_name="Bharat Forge Limited",
        canonical_name="Bharat Forge Limited",
        aliases=["Bharat Forge", "BFL"],
        country="India",
        listing_status="listed",
        ticker="BHARATFORG",
        website="https://www.bharatforge.com",
        disambiguation_note=(
            "Resolved offline to Bharat Forge Limited, the Pune-based listed "
            "forging and auto-components company."
        ),
        data_richness_tier="rich",
    ),
    "brakes india": Entity(
        input_name="Brakes India Private Limited",
        canonical_name="Brakes India Private Limited",
        aliases=["Brakes India", "Brakes India Pvt Ltd"],
        country="India",
        listing_status="unlisted",
        registry_id="U35999TN1962PTC004928",
        website="https://www.brakesindia.com",
        disambiguation_note=(
            "Resolved offline to Brakes India Private Limited, the unlisted "
            "Chennai-based auto-components and foundry company associated with the TVS group."
        ),
        data_richness_tier="sparse",
    ),
}


def resolve_entity(ctx: RunContext) -> Entity:
    ctx.note(f"Resolving entity: {ctx.input_name!r} (hint: {ctx.input_hint or 'none'})")
    lowered = ctx.input_name.lower()
    for key, known in _KNOWN_ENTITIES.items():
        if key in lowered:
            entity = known.model_copy(update={"input_name": ctx.input_name, "input_hint": ctx.input_hint})
            ctx.entity = entity
            ctx.note(
                f"Resolved offline -> {entity.canonical_name} | {entity.listing_status} | "
                f"tier={entity.data_richness_tier} | ticker={entity.ticker or '-'}"
            )
            return entity

    query = ctx.input_name + (f" {ctx.input_hint}" if ctx.input_hint else "") + " company official website investor relations"
    snippets = []
    try:
        for hit in Searcher().search(query, max_results=5):
            snippets.append(f"- {hit.title} | {hit.url}\n  {hit.content[:300]}")
    except Exception as e:  # noqa: BLE001
        ctx.note(f"entity search failed: {e}")

    user = (
        f"Company name: {ctx.input_name}\n"
        f"Hint: {ctx.input_hint or 'none'}\n\n"
        f"Search snippets:\n" + ("\n".join(snippets) if snippets else "(none)")
    )
    try:
        data = claude().complete_json(_SYS, user, cheap=True, max_tokens=800)
    except Exception as e:  # noqa: BLE001
        ctx.note(f"entity LLM failed ({e}); using minimal fallback")
        data = {}

    entity = Entity(
        input_name=ctx.input_name,
        input_hint=ctx.input_hint,
        canonical_name=data.get("canonical_name") or ctx.input_name,
        aliases=data.get("aliases") or [],
        country=data.get("country") or "",
        listing_status=data.get("listing_status") or "unknown",
        ticker=data.get("ticker"),
        registry_id=data.get("registry_id"),
        website=data.get("website"),
        disambiguation_note=data.get("disambiguation_note") or "",
        data_richness_tier=data.get("data_richness_tier") or "unknown",
    )
    ctx.entity = entity
    ctx.note(
        f"Resolved -> {entity.canonical_name} | {entity.listing_status} | "
        f"tier={entity.data_richness_tier} | ticker={entity.ticker or '-'}"
    )
    return entity
