# Write-Up   
  
## (For a bit in-depth determinism analysis for the whole code refer to HOW_IT_WORKS.md)

## Goal

The assessment asks for a system that writes a sourced company one-pager from minimal input, ideally just a company name. The output should cover company overview, financial snapshot, products, and clients/customers, with citations for every non-obvious claim.

I built a unified CLI, `company-profiles`, that takes one or more company names, resolves whether each is listed or unlisted, and routes it through the appropriate data pipeline.

## Approach

For listed companies, the system uses Screener for overview and financial tables, then fetches NSE/BSE annual reports and investor presentations. PDFs are parsed with LlamaParse, relevant sections are selected, and Claude extracts products/customers only from those sections. Extracted items are kept only when they have supporting evidence.

For unlisted companies, the system uses Tofler for overview and financials, then resolves the official company website and extracts product/customer evidence from website pages and website-hosted public documents. This is necessary because private companies often do not have investor-relations pages or clean public filings.

The final artifact is a Markdown and JSON profile under `outputs/<company_slug>/`.

A plain-language walkthrough of resolution, filings, and verification is in [HOW_IT_WORKS.md](HOW_IT_WORKS.md). Short version:

- **Ticker (listed):** Web search on screener.in → read symbol from URL → fuzzy name match (≥ ~75%) → confirm on Screener. Override with `--ticker` for a fixed result.
- **Website (unlisted):** Web search for “official website” → block aggregators → score domain vs company name → HTTP-check top hits. Override with `--website`.
- **NSE/BSE filings:** Once ticker (+ BSE scrip) is known, official exchange APIs list annual reports and investor presentations for the last N years; PDFs are saved with a manifest. This step is largely deterministic given the correct symbol.
- **Listed products/customers:** PDFs → LlamaParse → keyword section pick → LLM extract with quotes → **deterministic verify** (name + quote must appear in parsed text) → merge; cross-checked if seen in 2+ docs.
- **Unlisted products/customers:** Playwright crawl → keyword sections (products/customers/about) → LLM extract with quotes → same **deterministic verify** → merge. Less reliable than filings; logos-only customers are a known gap.

Nothing in the name→ticker or name→website steps is fully deterministic without overrides. The trust layer is: **no verified quote → no row in the output.**

## Why These Choices

Financials are pulled from structured providers because this is faster and less error-prone than table extraction from arbitrary PDFs. Filings are still used for listed products/customers because annual reports and investor presentations are high-confidence sources. The unlisted path is weaker by nature, so it is intentionally more conservative: when evidence is missing, the output should omit the claim instead of inventing it.

The implementation favors traceability over design polish. Markdown/JSON are easier to inspect and evaluate than a rendered one-pager, and the assignment explicitly says clean structured output is acceptable.

## Results

Generated outputs are included for the two required companies:

- `outputs/bharat_forge/company_profile.md`
- `outputs/bharat_forge/company_profile.json`
- `outputs/brakes_india/company_profile.md`
- `outputs/brakes_india/company_profile.json`

Bharat Forge is data-rich: the listed-company route can use Screener plus NSE/BSE filings, so products and financials are much better supported.

Brakes India is data-sparse: the unlisted route gets Tofler financials and official-site product/customer evidence. Some customer evidence comes from website-hosted public documents rather than clean HTML pages, which reflects the sparse-source problem described in the assessment.

## What Works

- The unified CLI can take company names and route listed vs unlisted companies.
- Profiles include overview, financials, products, and customers in one Markdown/JSON output.
- Financial tables include multi-year values and derived metric footnotes.
- Product/customer rows carry source citations and quotes.
- Existing knowledge graphs are reused, making repeated runs faster and cheaper.

## What Does Not Fully Work

- Automatic company resolution is best-effort. Search can return wrong tickers or aggregator websites; production runs should pass ticker/CIN/website overrides when known.
- Unlisted customer extraction is difficult when customers appear only as logos or in PDFs. Full recall would require stronger OCR/vision and document retrieval.
- Confidence scores are heuristic evidence scores, not calibrated probabilities.
- The current persistence layer is file-based JSON, not PostgreSQL.
- The section detection pipeline is not a dedicated Gemini service, though it follows the same principle: select relevant product/customer sections before extraction.
- Not every overview sentence is represented as a separate structured claim object; the profile cites the overview source section, but a production system should attach source spans at finer granularity.

## Trade-Offs

- **Accuracy vs latency:** Using structured financial providers improves speed and reduces financial parsing errors, but relies on provider coverage.
- **Recall vs hallucination risk:** The extractor is conservative. It may miss a product/customer if evidence is weak, but this is better than inventing one.
- **Automation vs manual overrides:** Auto-resolution is convenient for minimal input, but explicit ticker/CIN/website overrides are safer for analyst-facing runs.
- **Markdown/JSON vs visual polish:** The output is less polished than a designed one-pager, but easier to audit.

## What I Would Build Next

- Add a claim-level citation model for every overview sentence, not just section-level source attribution.
- Add OCR/vision for logos and product images in the unlisted website path.
- Store the knowledge graph in PostgreSQL with refresh schedules: filings daily, presentations quarterly, annual reports yearly, websites monthly.
- Add automated evaluation against a small hand-labeled gold set for products/customers.
- Add a confidence policy that distinguishes single-source website evidence from cross-checked filings.
- Add a lightweight HTML renderer once the sourcing layer is more robust.

