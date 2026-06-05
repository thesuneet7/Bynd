"""Data models for the listed-docs fetch pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DocCategory(str, Enum):
    annual_report = "annual_report"
    investor_presentation = "investor_presentation"
    earnings_transcript = "earnings_transcript"
    quarterly_results = "quarterly_results"
    other = "other"


class DocSource(str, Enum):
    nse = "nse"
    bse = "bse"
    company_ir = "company_ir"
    screener = "screener"


@dataclass
class DocumentRef:
    title: str
    url: str
    category: DocCategory
    source: DocSource
    report_year: int | None = None
    fy_label: str | None = None
    published: str | None = None
    media_type: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DownloadedDocument:
    ref: DocumentRef
    status: str  # saved | failed | skipped_duplicate
    local_path: str | None = None
    sha256: str | None = None
    file_size: int = 0
    error: str = ""
