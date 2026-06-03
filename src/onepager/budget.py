"""Budget / rate guard. Enforces hard caps on every paid call so a runaway
loop can't burn through limited credits (Tavily's 1000-credit free tier in
particular). Also accumulates a usage report for the write-up.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .config import SETTINGS


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    counts: dict[str, int] = field(default_factory=dict)
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    grok_input_tokens: int = 0
    grok_output_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    _caps = {
        "ddg": SETTINGS.max_ddg_searches,
        "tavily": SETTINGS.max_tavily_searches,
        "exa": SETTINGS.max_exa_searches,
        "firecrawl": SETTINGS.max_firecrawl_scrapes,
        "claude": SETTINGS.max_claude_calls,
        "grok": SETTINGS.max_grok_calls,
        "llamaparse_pages": SETTINGS.max_llamaparse_pages,
    }

    def charge(self, service: str, amount: int = 1) -> None:
        """Reserve `amount` units of a service. Raises if it would exceed the cap."""
        with self._lock:
            cap = self._caps.get(service)
            used = self.counts.get(service, 0)
            if cap is not None and used + amount > cap:
                raise BudgetExceeded(
                    f"Budget cap hit for '{service}': {used}+{amount} > {cap}. "
                    f"Raise MAX_* in .env if intentional."
                )
            self.counts[service] = used + amount

    def remaining(self, service: str) -> int:
        cap = self._caps.get(service)
        if cap is None:
            return 1 << 30
        return cap - self.counts.get(service, 0)

    def add_tokens(self, model: str, inp: int, out: int) -> None:
        with self._lock:
            if model == "claude":
                self.claude_input_tokens += inp
                self.claude_output_tokens += out
            elif model == "grok":
                self.grok_input_tokens += inp
                self.grok_output_tokens += out

    def report(self) -> dict:
        return {
            "calls": dict(self.counts),
            "claude_tokens": {"in": self.claude_input_tokens, "out": self.claude_output_tokens},
            "grok_tokens": {"in": self.grok_input_tokens, "out": self.grok_output_tokens},
        }


# One shared budget per process / run.
BUDGET = Budget()
