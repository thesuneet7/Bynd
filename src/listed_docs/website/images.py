"""Vision-parse images harvested from keyword-matched product/customer sections only."""
from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onepager.budget import BUDGET, BudgetExceeded
from onepager.config import SETTINGS
from onepager.llm import extract_json

from listed_docs.extraction.models import ExtractedItem, SOURCE_CONFIDENCE

from .explore import RelevantSection, SiteImage

_VISION_SYS = """You analyze an image from a specific section of a company's website.
The section heading tells you whether this is a products/offerings area or a customers/clients area.
Extract ONLY names/text visibly printed in the image (logos, award certificates, product photos with labels).
Do NOT guess or use outside knowledge.

Return JSON:
{
  "image_type": "customer_logo" | "product" | "award_certificate" | "product_photo" | "other",
  "entities": [
    {"kind": "customer" | "product", "name": "...", "evidence": "exact visible text/wording in image"}
  ]
}
Return {"entities": []} if no readable company/product names are visible."""


@dataclass
class ImageParseResult:
    image: SiteImage
    section_heading: str
    bucket: str
    entities: list[dict[str, str]]
    image_type: str = "other"


def _media_type(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt and mt.startswith("image/"):
        return mt
    if path.suffix.lower() == ".png":
        return "image/png"
    return "image/jpeg"


def _vision_parse(
    path: Path,
    *,
    company: str,
    section: RelevantSection,
    img: SiteImage,
) -> dict[str, Any]:
    SETTINGS.require("claude_api_key")
    import anthropic

    data = path.read_bytes()
    if len(data) < 400 or len(data) > 4_500_000:
        return {"entities": [], "image_type": "other"}

    b64 = base64.standard_b64encode(data).decode()
    client = anthropic.Anthropic(api_key=SETTINGS.claude_api_key, timeout=90.0)
    BUDGET.charge("claude")
    resp = client.messages.create(
        model=SETTINGS.claude_cheap_model,
        max_tokens=1200,
        temperature=0.0,
        system=_VISION_SYS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": _media_type(path), "data": b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Company: {company}\n"
                            f"Page URL: {section.url}\n"
                            f"Section heading: {section.heading}\n"
                            f"Section type: {section.bucket}\n"
                            f"Image alt text: {img.alt or '(none)'}\n"
                            f"Image file: {path.name}"
                        ),
                    },
                ],
            }
        ],
    )
    usage = getattr(resp, "usage", None)
    if usage:
        BUDGET.add_tokens("claude", getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    try:
        return extract_json(text)
    except Exception:
        return {"entities": [], "image_type": "other"}


def _slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", Path(path).stem.lower()).strip("_") or "image"


def parse_section_images(
    sections: list[RelevantSection],
    *,
    company: str,
    max_images: int | None = None,
) -> tuple[list[ExtractedItem], list[ExtractedItem], list[ImageParseResult], list[str]]:
    """Vision-parse images saved from keyword-matched sections only."""
    cap = max_images or SETTINGS.max_website_vision_images
    products: list[ExtractedItem] = []
    customers: list[ExtractedItem] = []
    parsed: list[ImageParseResult] = []
    notes: list[str] = []
    count = 0

    for section in sections:
        for img in section.images:
            if count >= cap:
                notes.append(f"vision cap reached ({cap})")
                return products, customers, parsed, notes
            path = Path(img.local_path)
            if not path.exists():
                continue
            count += 1
            try:
                raw = _vision_parse(path, company=company, section=section, img=img)
            except BudgetExceeded as e:
                notes.append(f"vision budget exhausted: {e}")
                return products, customers, parsed, notes
            except Exception as e:  # noqa: BLE001
                notes.append(f"vision failed {path.name}: {e}")
                continue

            entities = raw.get("entities") if isinstance(raw, dict) else []
            if not isinstance(entities, list):
                entities = []
            img_type = str(raw.get("image_type") or "other")
            clean_entities: list[dict[str, str]] = []
            doc_id = f"img_{_slug(img.local_path)}"
            source_label = f"Company Website Image — {section.heading}"

            for row in entities:
                if not isinstance(row, dict):
                    continue
                kind = str(row.get("kind") or "").lower()
                name = str(row.get("name") or "").strip()
                evidence = str(row.get("evidence") or "").strip()
                if not name or not evidence or name.lower() not in evidence.lower():
                    continue
                # Align vision kind with section bucket when possible
                if section.bucket == "products" and kind != "product":
                    if kind == "customer":
                        continue
                    kind = "product"
                if section.bucket == "customers" and kind != "customer":
                    if kind == "product":
                        continue
                    kind = "customer"
                if kind not in ("customer", "product"):
                    continue
                clean_entities.append({"kind": kind, "name": name, "evidence": evidence})
                item = ExtractedItem(
                    name=name,
                    evidence=evidence[:400],
                    source=source_label,
                    confidence=SOURCE_CONFIDENCE["company_website"],
                    bucket="customers" if kind == "customer" else "products",
                    page=None,
                    document_id=doc_id,
                    local_path=img.local_path,
                    verified=True,
                )
                if kind == "customer":
                    customers.append(item)
                else:
                    products.append(item)

            parsed.append(
                ImageParseResult(
                    image=img,
                    section_heading=section.heading,
                    bucket=section.bucket,
                    entities=clean_entities,
                    image_type=img_type,
                )
            )
            if clean_entities:
                notes.append(f"{path.name} under «{section.heading}»: {len(clean_entities)} entities")

    return products, customers, parsed, notes
