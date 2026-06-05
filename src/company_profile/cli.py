#!/usr/bin/env python3
"""CLI — unified company profile (overview + financials + products/customers)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from onepager.config import OUTPUTS_DIR
from onepager.schemas import Entity

from .pipeline import run_company_profile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Build a unified company profile: screener/tofler overview & financials "
            "+ NSE/BSE products/customers → company_profile.json + company_profile.md"
        )
    )
    ap.add_argument("--demo", action="store_true", help="Bharat Forge (BHARATFORG)")
    ap.add_argument("--name", help="Company legal name")
    ap.add_argument("--ticker", help="NSE ticker (listed)")
    ap.add_argument("--cin", help="CIN (unlisted / tofler)")
    ap.add_argument("--website", help="Official company website (required for unlisted products/customers)")
    ap.add_argument("--provider", choices=("screener", "tofler"), help="Data provider")
    ap.add_argument("--outdir", type=Path, help="Output directory (default: outputs/<slug>)")
    ap.add_argument("--years", type=int, default=3, help="Report years for listed-docs fetch")
    ap.add_argument("--skip-fetch", action="store_true", help="Reuse existing listed_docs manifest")
    ap.add_argument("--skip-extract", action="store_true", help="Reuse existing knowledge_graph.json")
    ap.add_argument("--force-extract", action="store_true", help="Re-run PDF extraction")
    ap.add_argument("--screener-login", action="store_true", help="Force fresh screener.in login")
    args = ap.parse_args(argv)

    if args.demo:
        entity = Entity(
            input_name="Bharat Forge Limited",
            canonical_name="Bharat Forge Limited",
            listing_status="listed",
            ticker="BHARATFORG",
            country="India",
        )
        provider = "screener"
    else:
        if not args.name:
            ap.error("Provide --name or --demo")
        provider = args.provider or ("screener" if args.ticker else "tofler")
        entity = Entity(
            input_name=args.name,
            canonical_name=args.name,
            listing_status="listed" if args.ticker else "unlisted",
            ticker=args.ticker,
            registry_id=args.cin,
            website=args.website,
            country="India",
        )

    print(f"\n=== Company profile: {entity.canonical_name} ===", flush=True)
    result = run_company_profile(
        entity,
        output_dir=args.outdir,
        provider=provider,
        years=args.years,
        skip_fetch=args.skip_fetch,
        skip_extract=args.skip_extract,
        force_extract=args.force_extract,
        force_screener_login=args.screener_login,
    )
    for line in result.log:
        print(f"  · {line}", flush=True)
    print(f"\nWrote {result.json_path}")
    print(f"Wrote {result.markdown_path}")
    if result.error:
        print(f"Warning: {result.error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
