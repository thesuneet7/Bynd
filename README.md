# Company One-Pager Agent

AI system that produces a **fully-sourced, confidence-tagged** company one-pager from minimal input (company name + optional hint). Built for the Bynd take-home assignment.

**Core rule (Grounding Contract):** no claim is emitted unless it is backed by a retrieved source span and passes an independent verification step. The LLM never acts as the source of a fact.

## Quick start

```bash
cd /path/to/ByndAI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp .env.example .env   # fill API keys
```

Run on the two assignment companies:

```bash
onepager "Bharat Forge Limited" --hint "NSE: BHARATFORG" --slug bharat_forge
onepager "Brakes India Private Limited" --hint "TVS group, Chennai" --slug brakes_india
```

Outputs land in `outputs/<slug>/`:
- `onepager.json` — canonical (full provenance)
- `onepager.md` — human-readable
- `onepager.html` — GPIL-style four-region layout

## Architecture (short)

```
Input (name + hint)
  → Entity resolution (offline for assignment companies)
  → Source discovery (seeded URLs; Tavily optional, capped)
  → Ingestion (pypdf + httpx; local embeddings; page relevance triage)
  → Section agents (Claude: overview / financials / products / clients)
  → Verification gate (Grok: entailment per claim)
  → Confidence scoring (deterministic)
  → Assemble + Honesty Lint
  → Render JSON / MD / HTML
```

| Stage | Model / tool | API cost (strict defaults) |
|-------|----------------|----------------------------|
| Entity (Bharat / Brakes) | Offline lookup | **0** |
| Discovery | **DuckDuckGo** (free) + seeds; Tavily fallback (capped) | **0–2 Tavily** |
| PDF | `pypdf` local | **0** |
| Web | **Firecrawl** (preferred) → `httpx` fallback; disk cache | **≤10 Firecrawl** |
| Embeddings | `fastembed` (local) | **0** |
| Draft claims | Claude Sonnet | ~4 calls / run |
| Verify | Grok | ~3 calls / run |

**Caps** in `.env` (fail closed): `MAX_DDG_SEARCHES=6`, `MAX_TAVILY_SEARCHES=2`, `MAX_FIRECRAWL_SCRAPES=10`, `MAX_LLAMAPARSE_PAGES=0`, `MAX_CLAUDE_CALLS=20`, `MAX_GROK_CALLS=25`.

### Web search alternatives

| Provider | Cost | In this repo |
|----------|------|----------------|
| **DuckDuckGo** (`duckduckgo-search`) | Free | **Default** (`SEARCH_PROVIDER=ddg_then_tavily`) |
| **Tavily** | Paid (your tightest pool) | Optional fallback when DDG is thin |
| Brave Search API | Paid free tier | Not wired — add if you get a key |
| SerpAPI / Google CSE | Paid | Not wired |
| **Firecrawl map/crawl** | Uses Firecrawl credits | Could replace search for known domains later |

Set `SEARCH_PROVIDER=ddg` to never touch Tavily, or `tavily` to use Tavily only.

See `ARCHITECTURE.md` for full design rationale.

## Repo layout

```
src/onepager/
  schemas.py          # Source, Evidence, Claim, FinancialCell, OnePager
  llm.py              # Claude (writer) + Grok (verifier)
  budget.py           # Hard API caps
  pipeline/           # entity, discovery, ingestion, agents, verify, assemble, run
  tools/              # search, scrape, pdf, retrieval
  render/             # markdown, html
outputs/
  bharat_forge/       # assignment run outputs
  brakes_india/
```

## Assignment outputs included

- `outputs/bharat_forge/onepager.{json,md,html}`
- `outputs/brakes_india/onepager.{json,md,html}`

See `WRITEUP.md` for honest quality assessment and limits.

## Do not commit secrets

`.env` and `api_keys.txt` are gitignored. Use `.env.example` as a template.
