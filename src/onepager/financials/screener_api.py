"""Screener.in schedule API — loads all expandable (+) table rows.

Clicking "+" calls `Company.showSchedule` → GET /api/company/{id}/schedules/
This module walks that tree recursively (e.g. Expenses → Material Cost % → Raw material cost).
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

SCHEDULES_API = "https://www.screener.in/api/company/{company_id}/schedules/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ByndAI/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.screener.in/",
}
# Annual tables only — skip quarterly/cash-flow (+) calls to reduce 429 rate limits.
_PRIORITY_SECTIONS = frozenset({"profit-loss", "balance-sheet"})
_REQUEST_GAP_S = 0.4
_MAX_RETRIES = 4


def company_id_from_html(html: str) -> int | None:
    m = re.search(r'data-company-id="(\d+)"', html)
    return int(m.group(1)) if m else None


def discover_schedule_parents(html: str, *, annual_only: bool = True) -> list[tuple[str, str]]:
    """Parse onclick handlers: Company.showSchedule('Parent', 'section', this)."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in re.finditer(
        r"""showSchedule\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]""",
        html,
    ):
        key = (m.group(1), m.group(2))
        if annual_only and key[1] not in _PRIORITY_SECTIONS:
            continue
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def fetch_schedule(
    company_id: int,
    parent: str,
    section: str,
    *,
    consolidated: bool = True,
    client: httpx.Client | None = None,
) -> dict[str, dict[str, str]]:
    params: dict[str, str] = {"parent": parent, "section": section}
    if consolidated:
        params["consolidated"] = ""
    url = SCHEDULES_API.format(company_id=company_id)
    own = client is None
    if own:
        client = httpx.Client(timeout=30, headers=_HEADERS)
    try:
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            if attempt:
                time.sleep(_REQUEST_GAP_S * (2**attempt))
            resp = client.get(url, params=params)
            if resp.status_code == 429:
                last_err = httpx.HTTPStatusError(
                    "rate limited",
                    request=resp.request,
                    response=resp,
                )
                continue
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return {}
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        if last_err:
            raise last_err
        return {}
    finally:
        if own:
            client.close()


def fetch_all_schedules(
    company_id: int,
    parents: list[tuple[str, str]],
    *,
    consolidated: bool = True,
) -> dict[str, dict[str, dict[str, str]]]:
    """Return {section: {line_item: {period: value}}} including nested (+) rows."""
    merged: dict[str, dict[str, dict[str, str]]] = {}
    visited: set[tuple[str, str]] = set()
    queue = list(parents)

    with httpx.Client(timeout=30, headers=_HEADERS) as client:
        while queue:
            parent, section = queue.pop(0)
            key = (parent, section)
            if key in visited:
                continue
            visited.add(key)

            try:
                time.sleep(_REQUEST_GAP_S)
                block = fetch_schedule(
                    company_id, parent, section, consolidated=consolidated, client=client
                )
            except Exception:
                continue

            for line, periods in block.items():
                if "isExpandable" in periods:
                    nested_parent = line
                    if (nested_parent, section) not in visited:
                        queue.append((nested_parent, section))
                    periods = {k: v for k, v in periods.items() if k != "isExpandable"}

                merged.setdefault(section, {})[line] = periods

    return merged
