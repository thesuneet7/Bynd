#!/usr/bin/env python3
"""Test tofler.in financial fetch: python scripts/test_tofler.py "Brakes India" """
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from onepager.financials import FINANCIAL_ROW_ORDER, fetch_tofler_financials
from onepager.financials.tofler import resolve_tofler_company
from onepager.context import RunContext
from onepager.schemas import Entity


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "Brakes India Private Limited"
    entity = Entity(
        input_name=name,
        canonical_name=name,
        listing_status="unlisted",
        registry_id="U35999TN1962PTC004928",
        country="India",
    )
    match = resolve_tofler_company(entity)
    print("match:", match.url if match else None, f"score={match.score:.2f}" if match else "")
    ctx = RunContext(input_name=name)
    cells, err = fetch_tofler_financials(ctx, entity, max_periods=3)
    print("err:", err, "cells:", len(cells))
    periods = sorted({c.period for c in cells})
    print("periods:", periods)
    for m in FINANCIAL_ROW_ORDER:
        have = [c for c in cells if c.metric == m]
        print(f"  {m}: {len(have)}", end="")
        if have:
            print(" ->", [(c.period, c.numeric_value) for c in have])
        else:
            print()


if __name__ == "__main__":
    main()
