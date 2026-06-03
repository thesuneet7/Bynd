"""Command-line entrypoint.

Examples:
  onepager "Bharat Forge Limited" --hint "NSE: BHARATFORG" --slug bharat_forge
  onepager "Brakes India Private Limited" --hint "TVS group, Chennai" --slug brakes_india
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import OUTPUTS_DIR
from .pipeline.run import build_onepager
from .render.html import render_html
from .render.markdown import render_markdown


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a fully-sourced company one-pager.")
    ap.add_argument("name", help="Company name")
    ap.add_argument("--hint", default=None, help="Optional hint (website, ticker, group, city)")
    ap.add_argument("--slug", default=None, help="Output folder name under outputs/")
    ap.add_argument("--outdir", default=None, help="Override output directory")
    args = ap.parse_args(argv)

    slug = args.slug or _slugify(args.name)
    outdir = Path(args.outdir) if args.outdir else (OUTPUTS_DIR / slug)
    outdir.mkdir(parents=True, exist_ok=True)

    onepager = build_onepager(args.name, args.hint)

    (outdir / "onepager.json").write_text(onepager.model_dump_json(indent=2))
    (outdir / "onepager.md").write_text(render_markdown(onepager))
    (outdir / "onepager.html").write_text(render_html(onepager))

    print(f"Wrote outputs to {outdir}/")
    print(f"  - onepager.json  (canonical, full provenance)")
    print(f"  - onepager.md    (human-readable)")
    print(f"  - onepager.html  (GPIL-style layout)")
    cov = onepager.coverage_report
    print(f"Summary: {cov.verified} verified claims, {len(onepager.gaps)} honest gaps, "
          f"confidence={cov.confidence_histogram}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
