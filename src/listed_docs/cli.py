#!/usr/bin/env python3
"""CLI — fetch and extract NSE/BSE documents for listed companies."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from onepager.config import OUTPUTS_DIR

from .context import ListedDocsContext
from .extraction import run_extraction
from .pipeline import run_listed_docs_fetch


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _ctx_from_manifest(outdir: Path) -> ListedDocsContext:
    manifest = outdir / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(f"No manifest at {manifest}")
    data = json.loads(manifest.read_text())
    return ListedDocsContext(
        company_name=str(data.get("company") or "Unknown"),
        ticker=str(data.get("ticker") or ""),
        output_dir=outdir,
        bse_scrip=data.get("bse_scrip"),
        website=data.get("website"),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Listed-docs pipeline: fetch NSE/BSE annual reports & presentations, "
            "then extract products/clients via LlamaParse + Claude."
        )
    )
    sub = ap.add_subparsers(dest="command")

    fetch_p = sub.add_parser("fetch", help="Discover and download relevant exchange documents")
    fetch_p.add_argument("--name", help="Company legal name")
    fetch_p.add_argument("--ticker", help="NSE ticker symbol (e.g. BHARATFORG)")
    fetch_p.add_argument("--hint", help="Optional ticker hint (NSE: TICKER)")
    fetch_p.add_argument("--years", type=int, default=3, help="Report years to fetch (default: 3)")
    fetch_p.add_argument("--outdir", type=Path, help="Output directory")
    fetch_p.add_argument("--demo", action="store_true", help="Fetch Bharat Forge (BHARATFORG)")

    extract_p = sub.add_parser("extract", help="Extract products/clients from manifest PDFs")
    extract_p.add_argument("--outdir", type=Path, required=True, help="listed_docs output directory")
    extract_p.add_argument("--demo", action="store_true", help="Use outputs/bharat_forge/listed_docs")
    extract_p.add_argument("--force", action="store_true", help="Re-extract all documents (ignore cache)")
    extract_p.add_argument(
        "--section-mode",
        choices=("heuristic", "claude"),
        default="heuristic",
        help="Section detection: keyword heuristic (default) or Claude on page outlines",
    )

    # Backward-compatible top-level flags (default command = fetch)
    ap.add_argument("--name", help=argparse.SUPPRESS)
    ap.add_argument("--ticker", help=argparse.SUPPRESS)
    ap.add_argument("--hint", help=argparse.SUPPRESS)
    ap.add_argument("--years", type=int, default=3, help=argparse.SUPPRESS)
    ap.add_argument("--outdir", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--extract", action="store_true", help="Run extraction on --outdir manifest")

    args = ap.parse_args(argv)
    command = args.command

    if args.extract and command is None:
        command = "extract"

    if command == "extract":
        outdir = args.outdir
        if args.demo and outdir is None:
            outdir = OUTPUTS_DIR / "bharat_forge" / "listed_docs"
        if outdir is None:
            ap.error("extract requires --outdir (or --demo)")
        outdir = outdir.resolve()
        ctx = _ctx_from_manifest(outdir)
        print(f"\n=== Listed docs extract: {ctx.company_name} ({ctx.ticker}) ===", flush=True)
        run_extraction(
            ctx,
            force=bool(getattr(args, "force", False)),
            section_mode=getattr(args, "section_mode", "heuristic") or "heuristic",
        )
        print(f"\nWrote {ctx.knowledge_graph_path}")
        print(f"Wrote {ctx.extraction_summary_path}")
        return 0

    # fetch (explicit subcommand or legacy invocation)
    if command == "fetch" or command is None:
        if args.demo:
            name, ticker = "Bharat Forge Limited", "BHARATFORG"
        else:
            name = getattr(args, "name", None) or args.name
            ticker = getattr(args, "ticker", None) or args.ticker
            if not name or not ticker:
                ap.error("Provide --name and --ticker, use --demo, or run: listed-docs fetch --help")
            name, ticker = name, ticker

        slug = _slugify(ticker) if ticker else _slugify(name)
        outdir = args.outdir or (OUTPUTS_DIR / slug / "listed_docs")
        outdir.mkdir(parents=True, exist_ok=True)

        print(f"\n=== Listed docs fetch: {name} ({ticker}) ===", flush=True)
        result = run_listed_docs_fetch(
            company_name=name,
            ticker=ticker,
            output_dir=outdir,
            hint=getattr(args, "hint", None),
            years=getattr(args, "years", 3) or 3,
        )
        print(f"\nWrote {result.ctx.manifest_path}")
        print(f"Wrote {result.ctx.summary_path}")
        print(f"Documents dir: {result.ctx.documents_dir}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
