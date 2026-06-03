"""LLM clients.

Two vendors on purpose:
  * Claude  -> writer / extractor / orchestrator (the model that PROPOSES claims)
  * Grok    -> independent verifier (the model that GRADES entailment)

Using different vendors for write vs. verify means a fact one model hallucinates
is unlikely to be silently rubber-stamped by the other.

Both clients enforce budget caps and parse strict JSON robustly.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from .budget import BUDGET
from .config import SETTINGS

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Best-effort JSON extraction from an LLM response (handles fences / prose)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Grab the outermost JSON object/array.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise


# --------------------------------------------------------------------------- #
# Claude (writer)
# --------------------------------------------------------------------------- #
class ClaudeClient:
    def __init__(self) -> None:
        SETTINGS.require("claude_api_key")
        import anthropic

        self._c = anthropic.Anthropic(api_key=SETTINGS.claude_api_key, timeout=120.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def complete(
        self,
        system: str,
        user: str,
        *,
        cheap: bool = False,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
    ) -> str:
        BUDGET.charge("claude")
        model = SETTINGS.claude_cheap_model if cheap else SETTINGS.claude_model
        resp = self._c.messages.create(
            model=model,
            max_tokens=max_tokens or SETTINGS.llm_max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = getattr(resp, "usage", None)
        if usage:
            BUDGET.add_tokens("claude", getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip()

    def complete_json(self, system: str, user: str, **kw: Any) -> Any:
        sys2 = system + "\n\nRespond with VALID JSON only. No prose, no markdown fences."
        return extract_json(self.complete(sys2, user, **kw))


# --------------------------------------------------------------------------- #
# Grok (verifier) — OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #
class GrokClient:
    def __init__(self) -> None:
        SETTINGS.require("xai_api_key")
        from openai import OpenAI

        self._c = OpenAI(api_key=SETTINGS.xai_api_key, base_url=SETTINGS.xai_base_url, timeout=120.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def complete(self, system: str, user: str, *, max_tokens: Optional[int] = None, temperature: float = 0.0) -> str:
        BUDGET.charge("grok")
        resp = self._c.chat.completions.create(
            model=SETTINGS.xai_model,
            max_tokens=max_tokens or SETTINGS.llm_max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = getattr(resp, "usage", None)
        if usage:
            BUDGET.add_tokens("grok", getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
        return (resp.choices[0].message.content or "").strip()

    def complete_json(self, system: str, user: str, **kw: Any) -> Any:
        sys2 = system + "\n\nRespond with VALID JSON only. No prose, no markdown fences."
        return extract_json(self.complete(sys2, user, **kw))


# Lazy singletons (so importing the module doesn't require keys until used).
_claude: Optional[ClaudeClient] = None
_grok: Optional[GrokClient] = None


def claude() -> ClaudeClient:
    global _claude
    if _claude is None:
        _claude = ClaudeClient()
    return _claude


def grok() -> GrokClient:
    global _grok
    if _grok is None:
        _grok = GrokClient()
    return _grok
