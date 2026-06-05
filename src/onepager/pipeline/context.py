"""Shared run state threaded through every stage (the 'graph state')."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..schemas import Entity, Gap, Source
from ..tools.retrieval import EvidenceStore


@dataclass
class RunContext:
    input_name: str
    input_hint: Optional[str] = None
    output_dir: Optional[Path] = None
    entity: Optional[Entity] = None
    store: EvidenceStore = field(default_factory=EvidenceStore)
    sources: dict[str, Source] = field(default_factory=dict)  # id -> Source
    gaps: list[Gap] = field(default_factory=list)
    searched_queries: list[str] = field(default_factory=list)
    research_trace: list[dict[str, Any]] = field(default_factory=list)
    archived_by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    _src_counter: int = 0
    log: list[str] = field(default_factory=list)

    def new_source_id(self) -> str:
        self._src_counter += 1
        return f"S{self._src_counter}"

    def register_source(self, source: Source) -> None:
        self.sources[source.id] = source

    @property
    def research_dir(self) -> Optional[Path]:
        if self.output_dir is None:
            return None
        return self.output_dir / "research"

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print(f"  · {msg}", flush=True)
