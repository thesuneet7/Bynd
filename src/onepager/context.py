"""Lightweight run context for screener/tofler scrapes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .schemas import Entity, Source


@dataclass
class RunContext:
    input_name: str
    input_hint: Optional[str] = None
    output_dir: Optional[Path] = None
    entity: Optional[Entity] = None
    sources: dict[str, Source] = field(default_factory=dict)
    _src_counter: int = 0
    log: list[str] = field(default_factory=list)

    def new_source_id(self) -> str:
        self._src_counter += 1
        return f"S{self._src_counter}"

    def register_source(self, source: Source) -> None:
        self.sources[source.id] = source

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print(f"  · {msg}", flush=True)
