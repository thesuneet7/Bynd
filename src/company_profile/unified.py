#!/usr/bin/env python3
"""Unified auto-routing CLI for listed and unlisted company profiles."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import httpx

from listed_docs.resolve import resolve_listed_company
from onepager.config import OUTPUTS_DIR
from onepager.financials.screener import normalize_ticker
from onepager.schemas import Entity

from .pipeline import ProfileResult, run_company_profile

_USER_AGENT = "Mozilla/5.0 (compatible; ByndAI/1.0)"

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(limited|ltd|private|pvt|public|plc|inc|incorporated|llp|company|co)\b\.?",
    re.I,
)
_BAD_WEBSITE_HOSTS = (
    "screener.in",
    "tofler.in",
    "zaubacorp.com",
    "thecompanycheck.com",
    "ambitionbox.com",
    "ampliz.com",
    "signalhire.com",
    "rocketreach.co",
    "zoominfo.com",
    "crunchbase.com",
    "tracxn.com",
    "pitchbook.com",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "wikipedia.org",
    "bseindia.com",
    "nseindia.com",
    "moneycontrol.com",
)


@dataclass
class Resolution:
    entity: Entity
    provider: str
    output_dir: Path
    notes: list[str] = field(default_factory=list)


def _ddg_search(query: str, *, max_results: int = 8) -> list[tuple[str, str]]:
    try:
        from ddgs import DDGS

        return [
            (str(r.get("href") or ""), str(r.get("title") or ""))
            for r in DDGS().text(query, max_results=max_results)
            if r.get("href")
        ]
    except Exception:
        return []


def _clean_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    s = _LEGAL_SUFFIX_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _slugify_company(name: str) -> str:
    clean = _clean_name(name) or name.lower()
    return re.sub(r"[^a-z0-9]+", "_", clean).strip("_") or "company"


def _score_name(query: str, candidate: str) -> float:
    q = _clean_name(query)
    c = _clean_name(candidate)
    if not q or not c:
        return 0.0
    if q == c or q in c or c in q:
        return 0.98
    return SequenceMatcher(None, q, c).ratio()


def _score_website_candidate(name: str, title: str, host: str) -> float:
    title_score = _score_name(name, title)
    host_words = re.sub(r"[^a-z0-9]+", " ", host.replace(".", " ")).strip()
    host_compact = re.sub(r"[^a-z0-9]+", "", host)
    name_tokens = [t for t in _clean_name(name).split() if len(t) > 2]
    if not name_tokens:
        return title_score
    token_hits = sum(1 for token in name_tokens if token in host_words or token in host_compact)
    host_score = token_hits / len(name_tokens)
    if host_compact.startswith("".join(name_tokens[:2])):
        host_score = max(host_score, 0.95)
    return round((0.65 * host_score) + (0.35 * title_score), 3)


def _parse_screener_symbol(url: str) -> str | None:
    m = re.search(r"screener\.in/company/([A-Z0-9&.-]+)", url, re.I)
    if not m:
        return None
    symbol = m.group(1).upper().strip()
    if symbol in {"COMPARE", "USER", "GUIDES"}:
        return None
    return symbol


def _listed_candidate_from_search(name: str) -> tuple[str, str, float] | None:
    hits: list[tuple[str, str, float]] = []
    queries = [
        f'site:screener.in/company "{name}"',
        f"site:screener.in/company {_clean_name(name)}",
    ]
    seen: set[str] = set()
    for query in queries:
        for url, title in _ddg_search(query, max_results=8):
            symbol = _parse_screener_symbol(url)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            score = max(_score_name(name, title), _score_name(name, url.replace("-", " ")))
            hits.append((symbol, title, score))
        if hits and max(h[2] for h in hits) >= 0.82:
            break
    if not hits:
        return None
    hits.sort(key=lambda row: row[2], reverse=True)
    if hits[0][2] < 0.75:
        return None
    return hits[0]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def _usable_website(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    host = _host(url)
    if not host or any(bad in host for bad in _BAD_WEBSITE_HOSTS):
        return False
    return "." in host


def _validate_website(url: str) -> str | None:
    try:
        with httpx.Client(headers={"User-Agent": _USER_AGENT}, follow_redirects=True, timeout=12) as client:
            r = client.get(url)
            if r.status_code >= 400:
                return None
            return str(r.url).rstrip("/")
    except Exception:
        return None


def resolve_official_website(name: str) -> tuple[str | None, list[str]]:
    """Best-effort official website discovery for unlisted companies."""
    notes: list[str] = []
    queries = [
        f'"{name}" official website',
        f'{_clean_name(name)} company official website',
    ]
    candidates: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for query in queries:
        for url, title in _ddg_search(query, max_results=10):
            if not _usable_website(url):
                continue
            host = _host(url)
            if host in seen:
                continue
            seen.add(host)
            score = _score_website_candidate(name, title, host)
            candidates.append((url, title, score))
        if candidates and max(c[2] for c in candidates) >= 0.75:
            break
    candidates.sort(key=lambda row: row[2], reverse=True)
    for url, title, score in candidates[:5]:
        valid = _validate_website(url)
        if valid:
            notes.append(f"resolved website: {valid} (score={score:.2f}, title={title or 'n/a'})")
            return valid, notes
    notes.append("official website not resolved automatically")
    return None, notes


def resolve_for_profile(
    name: str,
    *,
    ticker: str | None = None,
    cin: str | None = None,
    website: str | None = None,
    outdir: Path | None = None,
) -> Resolution:
    notes: list[str] = []
    ticker = normalize_ticker(ticker)
    if ticker is None and not cin:
        candidate = _listed_candidate_from_search(name)
        if candidate is not None:
            symbol, title, score = candidate
            ticker = symbol
            notes.append(f"listed candidate from screener search: {symbol} (score={score:.2f}, title={title or 'n/a'})")

    if ticker:
        try:
            symbol, _, listed_website = resolve_listed_company(name=name, ticker=ticker)
            entity = Entity(
                input_name=name,
                canonical_name=name,
                listing_status="listed",
                ticker=symbol,
                registry_id=cin,
                website=website or listed_website,
                country="India",
            )
            return Resolution(
                entity=entity,
                provider="screener",
                output_dir=outdir or (OUTPUTS_DIR / _slugify_company(name)),
                notes=notes + [f"resolved listed company: NSE={symbol}"],
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"listed resolution failed for {ticker}: {exc}")

    if website is None:
        website, website_notes = resolve_official_website(name)
        notes.extend(website_notes)
    entity = Entity(
        input_name=name,
        canonical_name=name,
        listing_status="unlisted",
        ticker=None,
        registry_id=cin,
        website=website,
        country="India",
    )
    return Resolution(
        entity=entity,
        provider="tofler",
        output_dir=outdir or (OUTPUTS_DIR / _slugify_company(name)),
        notes=notes + ["resolved unlisted company"],
    )


def _parse_overrides(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"Override must be NAME=VALUE, got: {raw}")
        name, value = raw.split("=", 1)
        out[_clean_name(name)] = value.strip()
    return out


def run_unified_profiles(
    names: list[str],
    *,
    outdir: Path | None = None,
    tickers: dict[str, str] | None = None,
    cins: dict[str, str] | None = None,
    websites: dict[str, str] | None = None,
    years: int = 3,
    skip_fetch: bool = False,
    skip_extract: bool = False,
    force_extract: bool = False,
    force_screener_login: bool = False,
) -> list[ProfileResult]:
    results: list[ProfileResult] = []
    for name in names:
        key = _clean_name(name)
        company_out = (outdir / _slugify_company(name)) if outdir else None
        resolution = resolve_for_profile(
            name,
            ticker=(tickers or {}).get(key),
            cin=(cins or {}).get(key),
            website=(websites or {}).get(key),
            outdir=company_out,
        )
        print(f"\n=== Unified profile: {name} ===", flush=True)
        for note in resolution.notes:
            print(f"  · {note}", flush=True)
        result = run_company_profile(
            resolution.entity,
            output_dir=resolution.output_dir,
            provider=resolution.provider,
            years=years,
            skip_fetch=skip_fetch,
            skip_extract=skip_extract,
            force_extract=force_extract,
            force_screener_login=force_screener_login,
        )
        for line in result.log:
            print(f"  · {line}", flush=True)
        print(f"  → {result.markdown_path}", flush=True)
        print(f"  → {result.json_path}", flush=True)
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Auto-route company names through listed or unlisted profile pipelines."
    )
    ap.add_argument("names", nargs="*", help="Company names to profile")
    ap.add_argument("--name", action="append", dest="name_flags", help="Company name; can be repeated")
    ap.add_argument("--input-file", type=Path, help="Text file with one company name per line")
    ap.add_argument("--ticker", action="append", help="Override ticker as NAME=TICKER; can be repeated")
    ap.add_argument("--cin", action="append", help="Override CIN as NAME=CIN; can be repeated")
    ap.add_argument("--website", action="append", help="Override website as NAME=URL; can be repeated")
    ap.add_argument("--outdir", type=Path, help="Parent output directory (default: outputs/<company_slug>)")
    ap.add_argument("--years", type=int, default=3, help="Listed filings report years to fetch")
    ap.add_argument("--skip-fetch", action="store_true", help="Reuse listed_docs manifest when present")
    ap.add_argument("--skip-extract", action="store_true", help="Reuse existing knowledge_graph.json when present")
    ap.add_argument("--force-extract", action="store_true", help="Force re-extraction from listed PDFs")
    ap.add_argument("--screener-login", action="store_true", help="Force fresh screener.in login")
    args = ap.parse_args(argv)

    names = list(args.names or [])
    names.extend(args.name_flags or [])
    if args.input_file:
        names.extend(
            line.strip()
            for line in args.input_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if not names:
        ap.error("Provide at least one company name")

    try:
        tickers = _parse_overrides(args.ticker)
        cins = _parse_overrides(args.cin)
        websites = _parse_overrides(args.website)
    except ValueError as exc:
        ap.error(str(exc))

    run_unified_profiles(
        names,
        outdir=args.outdir,
        tickers=tickers,
        cins=cins,
        websites=websites,
        years=args.years,
        skip_fetch=args.skip_fetch,
        skip_extract=args.skip_extract,
        force_extract=args.force_extract,
        force_screener_login=args.screener_login,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
