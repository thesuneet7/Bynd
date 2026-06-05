"""Central configuration: keys, model names, budget caps, paths.

Everything tunable lives here so the rest of the code never reads os.environ
directly. Budget caps are loaded once and enforced by `budget.py`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

CACHE_DIR = ROOT / ".cache"
STORE_DIR = ROOT / "store"
OUTPUTS_DIR = ROOT / "outputs"
for _d in (CACHE_DIR, STORE_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # LLMs
    claude_api_key: str = os.getenv("CLAUDE_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    claude_cheap_model: str = os.getenv("CLAUDE_CHEAP_MODEL", "claude-haiku-4-5")

    xai_api_key: str = os.getenv("XAI_API_KEY", "")
    xai_model: str = os.getenv("XAI_MODEL", "grok-4.3")
    xai_base_url: str = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")

    # Tools
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    exa_api_key: str = os.getenv("EXA_API_KEY", "")
    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    llamaparse_api_key: str = os.getenv("LLAMAPARSE_API_KEY", "")

    # Screener.in (optional — unlocks Key Insights / Read More commentary)
    screener_username: str = os.getenv("SCREENER_USERNAME", "") or os.getenv("SCREENER_EMAIL", "")
    screener_password: str = os.getenv("SCREENER_PASSWORD", "")
    # httpx | browser | auto  (auto = httpx then Playwright fallback)
    screener_login: str = os.getenv("SCREENER_LOGIN", "auto")
    # Search: ddg (free) | tavily (default) | exa | ddg_then_tavily |
    # exa_then_tavily | exa_ddg_then_tavily
    search_provider: str = os.getenv("SEARCH_PROVIDER", "tavily")
    prefer_firecrawl: bool = os.getenv("PREFER_FIRECRAWL", "true").lower() in ("1", "true", "yes")

    # Budget caps
    max_ddg_searches: int = field(default_factory=lambda: _int("MAX_DDG_SEARCHES", 4))
    max_tavily_searches: int = field(default_factory=lambda: _int("MAX_TAVILY_SEARCHES", 25))
    max_exa_searches: int = field(default_factory=lambda: _int("MAX_EXA_SEARCHES", 4))
    max_firecrawl_scrapes: int = field(default_factory=lambda: _int("MAX_FIRECRAWL_SCRAPES", 25))
    max_llamaparse_pages: int = field(default_factory=lambda: _int("MAX_LLAMAPARSE_PAGES", 120))
    max_claude_calls: int = field(default_factory=lambda: _int("MAX_CLAUDE_CALLS", 80))
    max_grok_calls: int = field(default_factory=lambda: _int("MAX_GROK_CALLS", 120))
    llm_max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 4000))

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                f"Missing required config: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill the keys."
            )


SETTINGS = Settings()
