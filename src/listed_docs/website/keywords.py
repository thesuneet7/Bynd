"""Keyword signals for finding product/customer sections on company websites."""
from __future__ import annotations

import re

# Headings / nav labels that indicate a products section.
PRODUCT_SIGNALS: tuple[str, ...] = (
    r"products?",
    r"product\s+portfolio",
    r"product\s+range",
    r"product\s+catalogue?",
    r"our\s+offerings?",
    r"offerings?",
    r"solutions?",
    r"what\s+we\s+(make|manufacture|offer|do)",
    r"business\s+lines?",
    r"business\s+units?",
    r"our\s+businesses",
    r"components?",
    r"applications?",
    r"portfolio",
    r"range\s+of",
    r"manufactur",
    r"service\s+offerings?",
)

# Headings / nav labels that indicate a customers section.
CUSTOMER_SIGNALS: tuple[str, ...] = (
    r"customers?",
    r"clients?",
    r"client\s+base",
    r"key\s+accounts?",
    r"strategic\s+accounts?",
    r"oems?",
    r"oem\s+relationships?",
    r"who\s+we\s+serve",
    r"marquee\s+customers?",
    r"customer\s+success",
    r"case\s+stud(?:y|ies)",
    r"supplies?\s+to",
    r"partner(?:s|ships?)?",
    r"our\s+customers?",
    r"trusted\s+by",
    r"leading\s+(?:oems?|automakers?|manufacturers?)",
)

PRODUCT_HEADING_RE = re.compile(r"|".join(PRODUCT_SIGNALS), re.I)
CUSTOMER_HEADING_RE = re.compile(r"|".join(CUSTOMER_SIGNALS), re.I)
PRODUCT_TEXT_RE = re.compile(r"|".join(PRODUCT_SIGNALS + (r"brake|forging|casting|component",)), re.I)
CUSTOMER_TEXT_RE = re.compile(
    r"|".join(CUSTOMER_SIGNALS + (r"award|supplier|oem",)),
    re.I,
)

_SKIP_NAV = re.compile(
    r"(privacy|cookie|career|job|login|logout|contact\s*us|media\s*centre|news|blog|"
    r"linkedin|facebook|twitter|instagram|youtube|compliance|sitemap|terms)",
    re.I,
)


def classify_heading(text: str) -> str | None:
    t = (text or "").strip()
    if not t or len(t) > 120:
        return None
    if CUSTOMER_HEADING_RE.search(t):
        return "customers"
    if PRODUCT_HEADING_RE.search(t):
        return "products"
    return None


def link_priority(text: str, href: str = "") -> int:
    """Lower = explore sooner. 99 = deprioritize / skip."""
    combined = f"{text} {href}".strip()
    if not combined:
        return 99
    if _SKIP_NAV.search(combined):
        return 80
    if classify_heading(text) == "customers" or CUSTOMER_TEXT_RE.search(combined):
        return 0
    if classify_heading(text) == "products" or PRODUCT_TEXT_RE.search(combined):
        return 1
    if re.search(r"about|company|business|industr", combined, re.I):
        return 3
    return 5
