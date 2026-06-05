"""Run context for listed-docs pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ListedDocsContext:
    company_name: str
    ticker: str
    output_dir: Path
    bse_scrip: str | None = None
    website: str | None = None
    log: list[str] = field(default_factory=list)

    @property
    def documents_dir(self) -> Path:
        return self.output_dir / "documents"

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "manifest.json"

    @property
    def summary_path(self) -> Path:
        return self.output_dir / "fetch_summary.md"

    @property
    def parsed_dir(self) -> Path:
        return self.output_dir / "parsed"

    @property
    def extraction_dir(self) -> Path:
        return self.output_dir / "extraction"

    @property
    def knowledge_graph_path(self) -> Path:
        return self.output_dir / "knowledge_graph.json"

    @property
    def extraction_summary_path(self) -> Path:
        return self.output_dir / "extraction_summary.md"

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print(f"  · {msg}", flush=True)
