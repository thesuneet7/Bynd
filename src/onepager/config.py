"""Central configuration: keys, model names, budget caps, paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

CACHE_DIR = ROOT / ".cache"
OUTPUTS_DIR = ROOT / "outputs"
for _d in (CACHE_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    claude_api_key: str = os.getenv("CLAUDE_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    claude_cheap_model: str = os.getenv("CLAUDE_CHEAP_MODEL", "claude-haiku-4-5")

    llamaparse_api_key: str = os.getenv("LLAMAPARSE_API_KEY", "")

    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    prefer_firecrawl: bool = os.getenv("PREFER_FIRECRAWL", "true").lower() in ("1", "true", "yes")

    screener_username: str = os.getenv("SCREENER_USERNAME", "") or os.getenv("SCREENER_EMAIL", "")
    screener_password: str = os.getenv("SCREENER_PASSWORD", "")
    screener_login: str = os.getenv("SCREENER_LOGIN", "auto")

    max_llamaparse_pages: int = field(default_factory=lambda: _int("MAX_LLAMAPARSE_PAGES", 0))
    max_firecrawl_scrapes: int = field(default_factory=lambda: _int("MAX_FIRECRAWL_SCRAPES", 25))
    max_website_agent_rounds: int = field(default_factory=lambda: _int("MAX_WEBSITE_AGENT_ROUNDS", 5))
    max_website_map_urls: int = field(default_factory=lambda: _int("MAX_WEBSITE_MAP_URLS", 80))
    max_website_explore_pages: int = field(default_factory=lambda: _int("MAX_WEBSITE_EXPLORE_PAGES", 40))
    max_website_clicks_per_page: int = field(default_factory=lambda: _int("MAX_WEBSITE_CLICKS_PER_PAGE", 35))
    max_website_images: int = field(default_factory=lambda: _int("MAX_WEBSITE_IMAGES", 200))
    max_website_vision_images: int = field(default_factory=lambda: _int("MAX_WEBSITE_VISION_IMAGES", 60))
    max_claude_calls: int = field(default_factory=lambda: _int("MAX_CLAUDE_CALLS", 80))
    llm_max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 4000))

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                f"Missing required config: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill the keys."
            )


SETTINGS = Settings()
