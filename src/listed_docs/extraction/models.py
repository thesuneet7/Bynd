"""Data models for the products/clients extraction layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CanonicalBucket = Literal[
    "Products",
    "Customers",
    "Operations",
    "Competitors",
    "Risks",
    "Other",
]

EXTRACTION_BUCKETS: tuple[CanonicalBucket, ...] = (
    "Products",
    "Customers",
    "Operations",
    "Competitors",
    "Risks",
)

# Buckets we actually extract into the knowledge graph.
EXTRACTION_TARGETS: tuple[CanonicalBucket, ...] = (
    "Products",
    "Customers",
)

SOURCE_CONFIDENCE = {
    "annual_report": 1.0,
    "investor_presentation": 0.9,
    "company_website": 0.8,
    "news": 0.5,
}


@dataclass
class ParsedPageRecord:
    page: int
    content: str
    document_type: str


@dataclass
class SectionRange:
    section: str
    start_page: int
    end_page: int
    bucket: CanonicalBucket = "Other"


@dataclass
class ExtractedItem:
    name: str
    evidence: str
    source: str
    confidence: float
    bucket: str
    page: int | None = None
    document_id: str = ""
    local_path: str = ""
    verified: bool = False


@dataclass
class DocumentExtraction:
    document_id: str
    title: str
    category: str
    fy_label: str
    local_path: str
    pages_parsed: int
    sections: list[SectionRange] = field(default_factory=list)
    products: list[ExtractedItem] = field(default_factory=list)
    customers: list[ExtractedItem] = field(default_factory=list)
    segments: list[ExtractedItem] = field(default_factory=list)
    competitors: list[ExtractedItem] = field(default_factory=list)
    risks: list[ExtractedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class KnowledgeGraph:
    company: str
    ticker: str
    products: list[dict[str, Any]] = field(default_factory=list)
    customers: list[dict[str, Any]] = field(default_factory=list)
    documents_processed: int = 0
    extraction_notes: list[str] = field(default_factory=list)
