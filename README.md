# Company Profile Pipeline

Builds a **verified company profile** from public sources — no web-scraping discovery loop, no embeddings.

## Pipelines

| CLI | Purpose |
|-----|---------|
| `company-profile` | **End-to-end** — overview + financials + products/customers → `company_profile.json` + `.md` |
| `company-scrape` | Screener/tofler overview + financials only |
| `listed-docs` | NSE/BSE annual reports + investor decks → verified products/customers |

### Listed company (full run)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

cp .env.example .env   # see required keys below

company-profile --demo
# or
company-profile --name "Bharat Forge Limited" --ticker BHARATFORG
```

Outputs under `outputs/<slug>/`:
- `company_profile.json` / `company_profile.md` — unified profile
- `listed_docs/` — PDFs, `knowledge_graph.json`, extraction artifacts

### Unlisted company

```bash
company-profile --name "Brakes India Private Limited" --cin U35999TN1962PTC004928
```

Overview + financials come from **tofler.in**. Products/customers extraction for unlisted companies is **not implemented yet** (stub in `listed_docs/unlisted.py`).

## Architecture

```
company-profile
  ├─ screener.in / tofler.in     → overview + FY23–25 financials
  ├─ NSE/BSE (listed only)       → last 3y annual reports + presentations
  ├─ LlamaParse + Claude         → propose products/customers
  ├─ Deterministic verify        → quote + name must match parsed PDF text
  └─ Merge                       → company_profile.json + .md (full citations)
```

## Repo layout

```
src/
  onepager/           # shared: config, financials providers, Claude client
  company_scrape/     # screener/tofler scrape
  listed_docs/        # NSE/BSE fetch + products/customers extraction
  company_profile/    # unified export
outputs/
  <slug>/company_profile.{json,md}
  <slug>/listed_docs/
```

## Environment variables

| Variable | Required? | Why |
|----------|-----------|-----|
| `CLAUDE_API_KEY` | **Yes** (listed products/customers) | Extraction from PDFs |
| `LLAMAPARSE_API_KEY` | **Yes** (listed products/customers) | PDF parsing |
| `SCREENER_USERNAME` / `SCREENER_PASSWORD` | **Yes for listed overview** | Public screener page only gives a short About blurb + teaser; **full Key Insights (14 sections)** need a logged-in session hitting the wiki commentary API |
| Screener creds | No for financials | FY table is on the public consolidated page (httpx/session) |

Without screener login you still get financials and a thin overview; `company_profile.md` will note that Key Insights are incomplete.

## TODO

- [ ] **Unlisted products/customers** — MCA filings, tofler documents, company website (same verify gate as listed-docs)
