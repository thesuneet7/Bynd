#!/usr/bin/env python3
"""Quick screener.in fetch test: python scripts/test_screener.py BHARATFORG"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from onepager.financials import FINANCIAL_ROW_ORDER, fetch_screener_financials
from onepager.financials.screener import fetch_screener_html, parse_screener_page
from onepager.financials.screener_api import discover_schedule_parents, fetch_all_schedules
from onepager.context import RunContext
from onepager.schemas import Entity


def main() -> None:
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "BHARATFORG").upper()
    html, via, cid = fetch_screener_html(ticker)
    schedules = fetch_all_schedules(cid, discover_schedule_parents(html)) if cid else {}
    rows = parse_screener_page(html, schedules, max_periods=5)
    print(f"{ticker}: html={via} company_id={cid} schedule_lines={sum(len(v) for v in schedules.values())}")
    print(f"  mapped rows: {len(rows)}")
    for m in FINANCIAL_ROW_ORDER:
        have = [r for r in rows if r.metric == m]
        if have:
            r = have[-1]
            print(f"  {m}: {r.period} = {r.numeric_value} {r.unit}")

    ctx = RunContext(input_name="Test")
    entity = Entity(
        input_name="Test",
        canonical_name="Test",
        listing_status="listed",
        ticker=ticker,
        country="India",
    )
    cells, err = fetch_screener_financials(ctx, entity, max_periods=5)
    print(f"cells={len(cells)} err={err}")
    for m in FINANCIAL_ROW_ORDER:
        have = [c for c in cells if c.metric == m]
        if have:
            c = have[-1]
            print(f"  {m}: {c.period} = {c.numeric_value} ({c.basis})")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
