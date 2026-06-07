# Company Profile Pipeline

Builds a cited company profile from public sources:

- company overview and financials
- products / product families / offerings
- customers / clients / OEMs
- JSON + Markdown outputs under `outputs/<company_name>/company_profile.md`

The main entry point is now `company-profiles`, which accepts company names and automatically routes each company through the listed or unlisted path.

This repository is organized for the Bynd AI Engineering Intern take-home (`assessment.pdf`): produce sourced one-pagers for **Bharat Forge Limited** and **Brakes India Private Limited** from minimal input.

Please refer to [WRITEUP.md](WRITEUP.md) for rationale and [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for a plain-language explanation of ticker/website resolution, filings, and verification.

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env
```

```bash
company-profiles "Bharat Forge Limited" "Brakes India Private Limited"
```

Useful overrides:

```bash
company-profiles \
  "Bharat Forge Limited" \
  "Brakes India Private Limited" \
  --ticker "Bharat Forge Limited=BHARATFORG" \
  --cin "Brakes India Private Limited=U35999TN1962PTC004928" \
  --website "Brakes India Private Limited=https://www.brakesindia.com/"
```

Outputs:

```text
outputs/<company_slug>/
  company_profile.md
  company_profile.json
  listed_docs/          # listed companies
  website/              # unlisted companies
```

## Routing Logic

`company-profiles` does best-effort resolution:

- Searches Screener for a high-confidence listed-company match.
- If listed, resolves NSE ticker, BSE scrip, and company website, then uses Screener + NSE/BSE filings.
- If no strong listed match, treats the company as unlisted, resolves Tofler and official website, then uses Tofler + website extraction.
- Manual `--ticker`, `--cin`, and `--website` overrides are supported and should be used for production runs when known.

## Pipelines


| CLI                | Purpose                                                                    |
| ------------------ | -------------------------------------------------------------------------- |
| `company-profiles` | Batch auto-router for one or more company names                            |
| `company-profile`  | Single-company profile when listing status/ticker/website is already known |
| `company-scrape`   | Overview + financials only                                                 |
| `listed-docs`      | Fetch/extract NSE/BSE filings for listed companies                         |


## Architecture

```text
Company names
  ↓
Resolver
  ├─ listed: Screener ticker → NSE/BSE filings → LlamaParse → section extraction → verified KG
  └─ unlisted: Tofler + official website → keyword-guided crawl/images → verified KG
  ↓
Unified profile assembler
  ↓
company_profile.json + company_profile.md
```

### Why This Architecture

The assessment rewards trust over polish. The pipeline is therefore evidence-first:

- Financials come from structured providers where possible: Screener for listed companies and Tofler for private companies. This is faster and less error-prone than parsing every financial table from filings.
- Listed products/customers come from NSE/BSE annual reports and investor presentations because those are high-confidence public sources.
- Unlisted products/customers come from the official website and website-hosted public documents because private-company filings are sparse or paywalled.
- LLM extraction is section-scoped and evidence-gated: if a product/customer cannot be tied to a quote or source, it should not appear.
- Outputs are Markdown/JSON rather than a pixel-perfect rendered page because the assessment says clean structured output is sufficient.

## Product / Customer Extraction Rules

The extraction layer follows `prod_client_instructions.md`:

- Extract only explicitly stated products, product families, business lines, service offerings, customers, clients, and OEMs.
- Keep source evidence and citations for every item.
- Prefer annual reports and investor presentations for listed companies.
- Use official company website evidence for unlisted companies.
- Deduplicate names and increase confidence when multiple sources agree.
- Avoid retrieval over whole PDFs; identify relevant sections first, then extract from product/customer sections.

## Environment Variables


| Variable                                  | Required?                         | Why                                                    |
| ----------------------------------------- | --------------------------------- | ------------------------------------------------------ |
| `CLAUDE_API_KEY`                          | Yes                               | Products/customers extraction and image interpretation |
| `LLAMAPARSE_API_KEY`                      | Yes for listed filings            | PDF to Markdown parsing                                |
| `FIRECRAWL_API_KEY`                       | Recommended for unlisted websites | URL mapping / fallback scraping                        |
| `SCREENER_USERNAME` / `SCREENER_PASSWORD` | Recommended for listed overview   | Full Screener Key Insights require login               |


Without Screener login, listed financials still work, but overview may be thinner.

## Evaluation Metrics

The assessment weights evaluation as: architecture/reasoning 30%, output quality 30%, honesty/hallucination handling 25%, write-up/self-evaluation 15%.

Use these checks for each generated company profile:

- **Routing accuracy:** listed/unlisted classification, ticker match, official website match.
- **Financial completeness:** expected FY periods populated, units correct, derived metrics footnoted.
- **Citation coverage:** every product/customer has a page/PDF citation and quote.
- **Product/customer precision:** no news headlines, generic marketing terms, or inferred customers.
- **Product/customer recall:** major product portfolios and named OEM/customer lists are covered.
- **Reproducibility:** rerunning with cached `knowledge_graph.json` preserves outputs.
- **Artifact completeness:** profile Markdown, JSON, KG, summaries, documents/pages/images where applicable.

## Honest Gaps

- Automatic ticker and website discovery is search-based and can be wrong; use overrides for final runs.
- Unlisted customer discovery often depends on logos/images, so OCR/vision quality matters.
- The current KG is stored as JSON files, not PostgreSQL.
- Section detection is heuristic/Claude-based, not exactly the Gemini section detector described in the planning notes.
- Some sites block crawlers, hide content behind JavaScript, or expose products only in PDFs.
- Tofler coverage can be incomplete or paywalled for some private companies.
- Confidence is evidence-based but not a formal calibrated probability.

## Included Assessment Outputs

The required companies have generated outputs in:

- `outputs/bharat_forge/company_profile.md`
- `outputs/bharat_forge/company_profile.json`
- `outputs/brakes_india/company_profile.md`
- `outputs/brakes_india/company_profile.json`

See `INSTRUCTIONS.md` for run commands and `WRITEUP.md` for the short self-evaluation requested in the assessment.