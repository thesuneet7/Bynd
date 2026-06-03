# Write-up (~1 page)

## What we built

A retrieval-and-verification pipeline (not “an LLM that writes a one-pager”). Every emitted line is an atomic **Claim** with verbatim **Evidence**, graded by a **second model** (Grok) for entailment, then scored for confidence. Unverifiable facts are dropped; gaps are explicit in the schema (Brakes India financials are thin by design).

## Runs (API usage)

| Company | Time | Claude | Grok | DDG | Tavily | LlamaParse | Firecrawl |
|---------|------|--------|------|-----|--------|------------|-----------|
| Bharat Forge | ~202s | 4 | 4 | 2 | 0 | 0 | 6 |
| Brakes India | ~190s | 4 | 6 | 2 | 0 | 0 | 8 |

Current mode: free DuckDuckGo discovery + Firecrawl scraping (capped), local `pypdf` for PDFs, local embeddings, no LlamaParse. Tavily remains a capped fallback but was not used in these runs.

## Output quality — honest assessment

### Bharat Forge (data-rich)

**Strengths**
- Dense, sourced overview (11 claims) from the annual report plus scraped web sources.
- Financial table includes FY23–FY25 revenue/EBITDA/net profit/assets from a retrieved financial source, with derived growth and margin rows.
- Product coverage improved to 7 verified product/category claims.
- 31 verified claims emitted; 2 drafted claims failed verification and were dropped by the entailment gate.

**Weaknesses / limits**
- The pipeline still parsed the archived seeded annual report (`AR51.pdf`) as the main annual-report PDF. Latest FY24/FY25 figures came from a third-party financial page rather than the official annual report, so these are marked Low confidence.
- No named clients survived verification in the refreshed Bharat run; the output honestly records a client gap.
- Confidence is mostly Low/Medium because many new facts came from tier-5 third-party pages; corroboration should be improved.
- Local `pypdf` text is weaker on complex tables than layout-aware parsing (LlamaParse disabled to protect credits).

### Brakes India (data-sparse)

**Strengths**
- Firecrawl/DDG materially improved sparse-company coverage: Tofler, official site, TVS Girling, IndiaMART and Tracxn were retrieved.
- FY25 revenue, EBITDA, PAT and equity were extracted from a retrieved Tofler financial page and verified.
- Products improved to 14 verified claims; 4 client names survived verification.
- 35 verified claims emitted; 10 drafted claims failed verification and were dropped.

**Weaknesses / limits**
- Financials are single-year only; no audited multi-year MCA filing was retrieved.
- Several product/client claims depend on lower-tier directories (IndiaMART/Tracxn/TVS Girling), so confidence is mostly Low despite verification.
- Tofler is marked paywalled/registry-style; the extracted public page may not expose all underlying filings to a reviewer.

## Trade-offs

| Choice | Why | Cost |
|--------|-----|------|
| PDF-first via `pypdf` | Protect LlamaParse credits; fast | Weaker table OCR on dense filings |
| DDG-first discovery | Finds extra sources without Tavily credits | Search quality less predictable than paid APIs |
| Firecrawl capped scraping | Better markdown from JS-heavy pages | Uses Firecrawl credits; cache prevents re-spend |
| Grok verify (batched) | Cross-vendor honesty firewall | 4–6 calls / run |
| Page relevance triage (35 pages) | 265-page AR in seconds | Might drop a rare fact on a low-scored page |
| No LlamaParse by default | Protects parsing credits | Complex annual-report tables are less reliable |

## What I’d build next

1. **Force latest official annual report** — when DDG finds `Annual_Report_2024.pdf`, prioritize that over archive seeds and parse it first.
2. **Explicit financial gaps** when fewer than N periods verified (especially unlisted).
3. **Persist embedding index** per company slug to avoid re-embedding on re-runs.
4. **Optional** 10–15 LlamaParse pages on top-scored financial statement pages only (user-controlled cap).
5. **Corroboration pass** — second source for High confidence (e.g. investor presentation + AR).

## Self-evaluation vs rubric

- **Architecture & reasoning:** Pipeline stages, grounding contract, two-model verify — strong.
- **Output quality:** Brakes improved materially; Bharat is dense but needs the latest official annual report prioritized.
- **Honesty:** Unverified drafted claims were dropped; Bharat client gap is explicit.
- **Write-up:** Limits above are real, not cosmetic.
