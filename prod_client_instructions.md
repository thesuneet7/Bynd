# Company Intelligence Extraction Framework (Products & Customers)

## Objective

Build a deterministic system that extracts:

- Products
- Product Categories
- Business Segments
- Customers / Clients
- Competitors
- Risks

---

# Parsing Layer

Use:

```text
LlamaParse
```

Convert:

```text
PDF
↓
Markdown
```

Store:

```json
{
  "page": 123,
  "content": "...",
  "document_type": "annual_report"
}
```

---

# Section Detection Layer

Use Gemini 2.5 Pro.

Goal:

Identify document structure.

Prompt:

"Identify all major sections and return page ranges."

Expected Output:

```json
[
  {
    "section": "Business Overview",
    "start_page": 10,
    "end_page": 18
  },
  {
    "section": "Products",
    "start_page": 19,
    "end_page": 26
  }
]
```

---

# Section Classification

Map all sections into canonical buckets.

Examples:

```json
{
  "Products": [
    "Products",
    "Product Portfolio",
    "Offerings",
    "Solutions"
  ],

  "Customers": [
    "Customers",
    "Key Accounts",
    "OEM Relationships",
    "Client Base"
  ],

  "Operations": [
    "Business Overview",
    "Operations",
    "Business Segments"
  ]
}
```

Store grouped content.

---

# Product Extraction

## Source Priority

1. Annual Report
2. Investor Presentation
3. Company Website

---

## Product Extraction Prompt

Extract:

- Products
- Product Families
- Business Lines
- Service Offerings

Rules:

- Only explicitly stated products
- No hallucinations
- Deduplicate
- Include source evidence

Output:

```json
[
  {
    "product": "Forged Components",
    "evidence": "...",
    "source": "Annual Report FY25",
    "confidence": 0.98
  }
]
```

---

## Product Confidence

Annual Report = 1.0

Investor Presentation = 0.9

Company Website = 0.8

News = 0.5

Final confidence increases when multiple sources agree.

Example:

```json
{
  "product": "Defense Systems",
  "sources": [
    "Annual Report",
    "Presentation",
    "Website"
  ],
  "confidence": 0.99
}
```

---

# Customer Extraction

## Source Priority

1. Investor Presentations
2. Annual Reports
3. Regulatory Filings
4. Website

---

## Customer Extraction Prompt

Extract:

- Customers
- Clients
- OEMs
- Strategic Accounts

Rules:

- Evidence required
- No assumptions
- No inferred customers

Output:

```json
[
  {
    "customer": "Tata Motors",
    "evidence": "...",
    "confidence": 0.95
  }
]
```

---

# Cross Verification

Never trust a customer from a single weak source.

Good:

```text
Investor Presentation
+
Annual Report
```

Bad:

```text
One random website
```

Verification increases confidence.

---

# Knowledge Graph Generation

Create:

```json
{
  "company": "Bharat Forge",

  "products": [],

  "customers": [],

  "segments": [],

  "competitors": [],

  "risks": []
}
```

Persist in PostgreSQL.

---

# Update Strategy

Annual Reports:

- Refresh yearly

Investor Presentations:

- Refresh quarterly

Website:

- Refresh monthly

Filings:

- Refresh daily

---

# Architecture

```text
NSE Symbol
        ↓
Annual Report
        ↓
Investor Presentation
        ↓
Company Website
        ↓
LlamaParse
        ↓
Markdown
        ↓
Gemini Section Detection
        ↓
Structured Extraction
        ↓
Cross Validation
        ↓
Knowledge Graph
        ↓
PostgreSQL
```

---

# Important Rule

For products and customers:

DO NOT perform retrieval across the entire PDF.

First identify relevant sections.

Then run extraction only on:

- Products
- Portfolio
- Segments
- Customers
- Key Accounts
- OEM Relationships

This dramatically improves accuracy, reduces token cost, and minimizes hallucinations.