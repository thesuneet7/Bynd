"""CLI for the standalone screener/tofler scrape pipeline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from onepager.config import OUTPUTS_DIR
from onepager.schemas import Entity

from .pipeline import run_company_scrape, write_snapshot

DEFAULT_COMPANIES = [
    {
        "title": "Bharat Forge",
        "provider": "screener.in",
        "entity": Entity(
            input_name="Bharat Forge Limited",
            canonical_name="Bharat Forge Limited",
            listing_status="listed",
            ticker="BHARATFORG",
            country="India",
        ),
        "fetch": "screener",
    },
    {
        "title": "Brakes India",
        "provider": "tofler.in",
        "entity": Entity(
            input_name="brakes india pvt ltd",
            canonical_name="Brakes India Private Limited",
            listing_status="unlisted",
            registry_id="U35999TN1962PTC004928",
            country="India",
        ),
        "fetch": "tofler",
    },
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scrape company overview + financials from screener.in or tofler.in (no LLM pipeline)."
    )
    ap.add_argument(
        "--demo",
        action="store_true",
        help="Run Bharat Forge (screener) + Brakes India (tofler) demo scrape",
    )
    ap.add_argument("--name", help="Company legal name")
    ap.add_argument("--ticker", help="NSE/BSE ticker (listed / screener)")
    ap.add_argument("--cin", help="CIN / registry id (unlisted / tofler)")
    ap.add_argument(
        "--provider",
        choices=("screener", "tofler"),
        help="Force provider (default: screener if --ticker, else tofler)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=OUTPUTS_DIR / "financials_snapshot.md",
        help="Output markdown path",
    )
    ap.add_argument(
        "--screener-login",
        action="store_true",
        help="Force a fresh screener.in login (uses SCREENER_USERNAME/PASSWORD from .env)",
    )
    ap.add_argument(
        "--test-login",
        action="store_true",
        help="Only test screener.in login + Key Insights access, then exit",
    )
    args = ap.parse_args(argv)

    if args.test_login:
        return _test_screener_login(force=args.screener_login)

    if args.demo:
        results = [
            run_company_scrape(
                spec["entity"],
                provider=spec["fetch"],
                force_screener_login=args.screener_login and spec["fetch"] == "screener",
            )
            for spec in DEFAULT_COMPANIES
        ]
        write_snapshot(results, args.out, title="Financial snapshot (screener / tofler only)")
        print(f"Wrote {args.out}")
        return 0

    if not args.name:
        ap.error("Provide --name, or use --demo / --test-login")

    provider = args.provider or ("screener" if args.ticker else "tofler")
    entity = Entity(
        input_name=args.name,
        canonical_name=args.name,
        listing_status="listed" if provider == "screener" else "unlisted",
        ticker=args.ticker,
        registry_id=args.cin,
        country="India",
    )
    result = run_company_scrape(
        entity,
        provider=provider,
        force_screener_login=args.screener_login,
    )
    write_snapshot([result], args.out, title=f"{args.name} — company scrape")
    print(f"Wrote {args.out}")
    if result.error:
        print(f"Warning: {result.error}", file=sys.stderr)
    return 0


def _test_screener_login(*, force: bool) -> int:
    from onepager.config import SETTINGS
    from onepager.financials.overview import parse_screener_commentary
    from onepager.financials.screener_session import (
        ScreenerAuthError,
        clear_session_cache,
        fetch_commentary_html,
        get_screener_client,
        session_is_valid,
    )

    if not SETTINGS.screener_username or not SETTINGS.screener_password:
        print(
            "Missing credentials. Add to .env:\n"
            "  SCREENER_USERNAME=your@email.com\n"
            "  SCREENER_PASSWORD=your_password\n"
            "Optional: SCREENER_LOGIN=auto   # httpx | browser | auto",
            file=sys.stderr,
        )
        return 1

    if force:
        clear_session_cache()
    try:
        client = get_screener_client(force_login=force)
    except ScreenerAuthError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1

    if client is None:
        print("No session client returned.", file=sys.stderr)
        return 1

    ok = session_is_valid(client)
    html, via = fetch_commentary_html(458, client)
    points = len(parse_screener_commentary(html)) if via == "session" else 0
    print(f"session_valid={ok} commentary_via={via} commentary_bytes={len(html)} key_points={points}")
    if ok and via == "session" and points >= 3:
        print("OK — screener.in login works; Key Insights reachable for Bharat Forge (id=458).")
        return 0
    print("Login may have succeeded but Key Insights still blocked.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
