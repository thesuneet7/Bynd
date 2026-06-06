# Instructions

These instructions map to `assessment.pdf`: build a system that creates sourced company one-pagers from minimal input, and run it on Bharat Forge Limited and Brakes India Private Limited.

## Main Command

Run one or more companies through the unified pipeline:

```bash
company-profiles "Bharat Forge Limited" "Brakes India Private Limited"
```

Use overrides when available:

```bash
company-profiles \
  "Bharat Forge Limited" \
  "Brakes India Private Limited" \
  --ticker "Bharat Forge Limited=BHARATFORG" \
  --cin "Brakes India Private Limited=U35999TN1962PTC004928" \
  --website "Brakes India Private Limited=https://www.brakesindia.com/"
```

Batch from a file:

```bash
company-profiles --input-file companies.txt
```

## Output Contract

Each company writes:

```text
outputs/<company_slug>/
  company_profile.md
  company_profile.json
```

The profile must contain the assessment anchors:

- Company Overview
- Financial Overview
- Products
- Customers / Clients / OEMs
- Citations for non-obvious claims, figures, products, and customers

Listed companies may also write:

```text
outputs/<company_slug>/listed_docs/
  documents/
  parsed/
  extraction/
  manifest.json
  knowledge_graph.json
  fetch_summary.md
  extraction_summary.md
```

Unlisted companies may also write:

```text
outputs/<company_slug>/website/
  images/
  sections/
  extraction/
  knowledge_graph.json
  extraction_summary.md
```

## Routing Behavior

- Listed route: Screener resolution, Screener overview/financials, NSE/BSE annual reports and presentations, LlamaParse, section-scoped product/customer extraction.
- Unlisted route: Tofler overview/financials, official website resolution, keyword-guided website crawl, image/logo capture, product/customer extraction.
- Existing `knowledge_graph.json` files are reused when present to avoid rerunning expensive extraction.

## Recommended Evaluation Checklist

- Classification: listed/unlisted route is correct.
- Entity resolution: ticker, CIN, and website are correct.
- Financials: FY23-FY25 table is populated and derived metrics have footnotes.
- Products: named offerings are real products or business lines, not news headlines.
- Customers: named customers/OEMs have explicit evidence; weak website-only names should be treated cautiously.
- Citations: every product/customer row includes a source URL or document path and quote.
- Reproducibility: rerun with `--skip-fetch --skip-extract` and compare output shape.

Assessment weights:

- Architecture & reasoning: 30%
- Output quality: 30%
- Honesty & hallucination handling: 25%
- Write-up & self-evaluation: 15%

## Known Limitations

- Search-based resolution can choose the wrong ticker or website; pass overrides for production.
- Unlisted company websites often use images/logos for customers; OCR/vision may be required for full recall.
- Some private-company financials are unavailable or partial on Tofler.
- This stores JSON artifacts on disk; PostgreSQL persistence is not implemented.
- The listed PDF pipeline uses LlamaParse plus heuristic/Claude sectioning. It does not yet implement a dedicated Gemini section-detection service.
- Full automated accuracy evaluation is not implemented; use the checklist above and spot-check citations.
