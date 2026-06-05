"""Shared data models for financials scrapes and company profiles."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    annual_report = "annual_report"
    investor_presentation = "investor_presentation"
    company_website = "company_website"
    regulatory_filing = "regulatory_filing"
    financial_api = "financial_api"
    news = "news"
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


class ClaimStatus(str, Enum):
    verified = "VERIFIED"
    unverified = "UNVERIFIED"


class Source(BaseModel):
    id: str
    url: str
    title: str = ""
    publisher: str = ""
    source_type: SourceType = SourceType.other
    publication_date: Optional[str] = None
    retrieved_at: str = ""
    access: str = "public"


class Evidence(BaseModel):
    source_id: str
    exact_quote: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    id: str = ""
    section: Section = Section.financials
    text: str = ""
    claim_type: ClaimType = ClaimType.quantitative
    value: Optional[dict[str, Any]] = None
    evidence: list[Evidence] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.verified


class FinancialCell(Claim):
    metric: str = ""
    period: str = ""
    numeric_value: Optional[float] = None
    unit: str = "INR_crore"
    basis: str = "reported"
    derived_from: list[str] = Field(default_factory=list)


class Entity(BaseModel):
    input_name: str
    input_hint: Optional[str] = None
    canonical_name: str = ""
    aliases: list[str] = Field(default_factory=list)
    country: str = ""
    listing_status: str = "unknown"
    ticker: Optional[str] = None
    registry_id: Optional[str] = None
    website: Optional[str] = None
