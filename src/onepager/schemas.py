"""Core data model. These schemas make the Grounding Contract mechanically
checkable: a final lint asserts that every VERIFIED claim has >=1 piece of
evidence and an acceptable entailment label, otherwise the build fails.

Flow of an assertion:   Source  ->  Evidence (a span into a Source)  ->  Claim
Confidence and verification are attached to each Claim, never assumed.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class SourceType(str, Enum):
    annual_report = "annual_report"
    investor_presentation = "investor_presentation"
    company_website = "company_website"
    regulatory_filing = "regulatory_filing"
    financial_api = "financial_api"
    news = "news"
    logo_db = "logo_db"
    other = "other"


class Section(str, Enum):
    overview = "overview"
    financials = "financials"
    products = "products"
    clients = "clients"


class ClaimType(str, Enum):
    qualitative = "qualitative"
    quantitative = "quantitative"
    entity_relationship = "entity_relationship"


class Entailment(str, Enum):
    entailed = "entailed"
    partial = "partial"
    contradicted = "contradicted"
    none = "none"


class ClaimStatus(str, Enum):
    verified = "VERIFIED"
    unverified = "UNVERIFIED"
    not_found = "NOT_FOUND"
    conflicted = "CONFLICTED"


class ConfidenceLabel(str, Enum):
    high = "High"
    medium = "Medium"
    low = "Low"


# --------------------------------------------------------------------------- #
# Source & Evidence
# --------------------------------------------------------------------------- #
# Reliability tiers (1 = most trustworthy). Drives the confidence score.
RELIABILITY_TIER: dict[SourceType, int] = {
    SourceType.regulatory_filing: 1,
    SourceType.annual_report: 1,
    SourceType.investor_presentation: 2,
    SourceType.financial_api: 2,
    SourceType.company_website: 3,
    SourceType.news: 3,
    SourceType.logo_db: 4,
    SourceType.other: 5,
}


class Source(BaseModel):
    id: str
    url: str
    title: str = ""
    publisher: str = ""
    source_type: SourceType = SourceType.other
    publication_date: Optional[str] = None
    retrieved_at: str = ""
    access: str = "public"  # public | paywalled | login_required
    snapshot_path: Optional[str] = None  # local capture so a reviewer can re-open it

    @property
    def reliability_tier(self) -> int:
        return RELIABILITY_TIER.get(self.source_type, 5)


class Evidence(BaseModel):
    source_id: str
    exact_quote: str  # verbatim span that supports the claim
    locator: dict[str, Any] = Field(default_factory=dict)  # page/table/cell/char_span


# --------------------------------------------------------------------------- #
# Confidence & Verification
# --------------------------------------------------------------------------- #
class Verification(BaseModel):
    entailment: Entailment = Entailment.none
    judge_model: str = ""
    rationale: str = ""


class Confidence(BaseModel):
    score: float = 0.0  # 0..1
    label: ConfidenceLabel = ConfidenceLabel.low
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #
class Claim(BaseModel):
    id: str
    section: Section
    text: str
    claim_type: ClaimType = ClaimType.qualitative
    value: Optional[dict[str, Any]] = None
    evidence: list[Evidence] = Field(default_factory=list)
    corroboration_count: int = 0
    verification: Verification = Field(default_factory=Verification)
    confidence: Confidence = Field(default_factory=Confidence)
    status: ClaimStatus = ClaimStatus.unverified

    def is_emittable(self) -> bool:
        """Grounding Contract gate: only verified, evidence-backed claims ship."""
        return (
            self.status == ClaimStatus.verified
            and len(self.evidence) >= 1
            and self.verification.entailment in (Entailment.entailed, Entailment.partial)
        )


class FinancialCell(Claim):
    """A financial figure is just a quantitative claim with extra structure.

    `basis='derived'` means we computed it (e.g. growth %) from `derived_from`
    base cells — we never pretend a filing reported a number we calculated.
    """
    metric: str = ""
    period: str = ""  # e.g. FY24
    numeric_value: Optional[float] = None
    unit: str = "INR_crore"
    basis: str = "reported"  # reported | derived
    derived_from: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Gaps (honest "not found")
# --------------------------------------------------------------------------- #
class Gap(BaseModel):
    """A first-class record of something we looked for and could not verify."""
    section: Section
    description: str
    searched: list[str] = Field(default_factory=list)
    reason: str = ""


# --------------------------------------------------------------------------- #
# Entity
# --------------------------------------------------------------------------- #
class Entity(BaseModel):
    input_name: str
    input_hint: Optional[str] = None
    canonical_name: str = ""
    aliases: list[str] = Field(default_factory=list)
    country: str = ""
    listing_status: str = "unknown"  # listed | unlisted | unknown
    ticker: Optional[str] = None
    registry_id: Optional[str] = None  # CIN for Indian companies
    website: Optional[str] = None
    disambiguation_note: str = ""
    data_richness_tier: str = "unknown"  # rich | moderate | sparse | unknown


# --------------------------------------------------------------------------- #
# The one-pager
# --------------------------------------------------------------------------- #
class CoverageReport(BaseModel):
    verified: int = 0
    unverified_dropped: int = 0
    not_found: int = 0
    conflicted: int = 0
    confidence_histogram: dict[str, int] = Field(default_factory=dict)
    by_section: dict[str, int] = Field(default_factory=dict)


class OnePager(BaseModel):
    entity: Entity
    overview: list[Claim] = Field(default_factory=list)
    financials: list[FinancialCell] = Field(default_factory=list)
    products: list[Claim] = Field(default_factory=list)
    clients: list[Claim] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    coverage_report: CoverageReport = Field(default_factory=CoverageReport)
    run_metadata: dict[str, Any] = Field(default_factory=dict)

    def all_claims(self) -> list[Claim]:
        return [*self.overview, *self.financials, *self.products, *self.clients]
