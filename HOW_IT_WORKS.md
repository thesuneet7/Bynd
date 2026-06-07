# How the Pipeline Works

This is a short guide to how the system finds the right company, pulls data, and decides what products/customers to trust.

---

## 1. How it finds the stock ticker (listed companies)

**Input:** company name, e.g. `Bharat Forge Limited`

**What happens:**

1. If you pass `--ticker BHARATFORG`, it uses that. No guessing.
2. Otherwise it searches the web for that name on **screener.in** (a popular Indian stock data site).
3. It reads the URL, e.g. `screener.in/company/BHARATFORG` → ticker = `BHARATFORG`.
4. It scores how close the result name is to your input. If the match is weak (below ~75%), it does **not** treat the company as listed.
5. It opens that Screener page to confirm the ticker works and to read the BSE code and website link.

**Is this deterministic?**

- **Partially.** Same name + same search results → same ticker most of the time.
- **Not fully.** Web search can return a similar but wrong company (e.g. another “Brakes” company). That is why you can override with `--ticker`.

---

## 2. How it finds the official website (unlisted companies)

**Input:** company name, e.g. `Brakes India Private Limited`

**What happens:**

1. If you pass `--website https://www.brakesindia.com/`, it uses that.
2. Otherwise it searches for `"Company Name" official website`.
3. It throws away directory/aggregator sites (LinkedIn, Zauba, Ampliz, Tofler, etc.).
4. It scores candidates by:
  - Does the page title look like the company name?
  - Does the domain contain words from the name? (e.g. `brakesindia.com` for Brakes India)
5. It tries the top few URLs with a real HTTP request and keeps the first one that loads.

**Is this deterministic?**

- **Partially.** Domain scoring is rule-based, but the starting list comes from web search.
- **Not fully.** Wrong site or subsidiary site is possible. Use `--website` when you know the URL.

---

## 3. How NSE/BSE filings are downloaded (listed companies only)

**When:** Only after a listed ticker is confirmed (e.g. `BHARATFORG`).

**Where they come from:**


| Source  | What it uses            | What it gets                                                      |
| ------- | ----------------------- | ----------------------------------------------------------------- |
| **NSE** | Official NSE India APIs | Annual reports + investor presentations for that symbol           |
| **BSE** | Official BSE India APIs | Same, using the BSE scrip code (from Screener page or BSE lookup) |


**Steps:**

1. Resolve ticker → NSE symbol + BSE scrip code.
2. Ask NSE and BSE for filings from the last N years (default: 3).
3. Keep only relevant docs: **annual reports** and **major investor presentations** (not every tiny filing).
4. Download PDFs to `outputs/<company>/listed_docs/`.
5. Save a **manifest.json** listing every file, URL, year, and local path.

**Is this deterministic?**

- **Mostly yes**, once the ticker and BSE code are correct.
- Filings come from exchange APIs/URLs tied to that symbol — not from a random Google result.
- Same ticker + same year window → same list of exchange documents (unless the exchange adds/removes a filing).

---

## 4. How listed-company reports are scanned and products/customers extracted

**Goal:** Only claim a product or customer if the PDF actually says it.

**Steps:**

1. **Parse PDF** — LlamaParse turns each filing into page-by-page text.
2. **Find the right sections** — Keyword rules look for headings like “Products”, “Business segments”, “Key customers”, “OEMs”. Irrelevant pages are skipped.
3. **Extract with LLM** — Claude reads only those sections and proposes products/customers. Each item must include a **verbatim quote** from the section.
4. **Verify (deterministic check)** — Code checks:
  - The product/customer name appears in the parsed page text.
  - The quote (or a long enough chunk of it) appears verbatim in that text.
  - If either fails → item is **dropped**. No quote, no row in the output.
5. **Merge across documents** — Same product/customer from multiple filings is merged into one row with multiple citations.
6. **Cross-check flag** — If the same name shows up in **2+ documents**, it is marked `cross-checked` and gets a higher confidence score.

**Is this deterministic?**

- **Verification is deterministic.** The rules are fixed: name in text + quote in text. Same PDF text → same pass/fail.
- **Extraction is not fully deterministic.** The LLM may propose slightly different items on reruns, but only proposals that pass verification are kept.
- **Overall:** conservative by design — it prefers missing an item over inventing one.

---

## 5. How unlisted company websites are parsed for products/customers

**When:** Company is treated as unlisted and an official website URL is known.

**Steps:**

1. **Explore the site** — A browser (Playwright) visits the homepage and linked pages. It prioritizes links whose text/URL looks like products, customers, or about.
2. **Harvest sections only** — It does not dump the whole site. It looks for headings like “Products”, “Solutions”, “Customers”, “OEMs”, “About us”, and grabs text (and images) under those headings.
3. **Extract with LLM** — Same idea as filings: Claude proposes items only from that section text, with a quote.
4. **Verify (deterministic check)** — Quote must exist in the section text; name must appear in the quote or section. Fail → dropped.
5. **Images** — Customer logos in image sections can be read with vision; still needs a visible name/relationship in the image or alt text.
6. **Merge** — Duplicate names (e.g. “Drum brakes” vs “drum brakes”) are merged; multiple page citations increase confidence.

**Website about text** — For overview, the system also scrapes About-style pages and merges that narrative with Tofler text, removing duplicate sentences.

**Is this deterministic?**

- **Verification is deterministic** (same as filings).
- **Which pages get visited** can vary slightly (site layout, timeouts, crawl order).
- **LLM extraction** can vary between runs.
- **Weaker than filings** — websites change often, marketing copy is vaguer, and customers are often just logos. Expect lower recall, not magic completeness.

---

## 6. Quick honesty table


| Step                   | Mostly deterministic? | Main risk                                                                                                                                         |
| ---------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ticker from name       | No                    | Wrong company on Screener (but it doesn't matter as we can always manually input company ticker the rest information automation is the main task) |
| Website from name      | No                    | Aggregator or wrong domain (same as above we can manually search and plug in the website the rest is the main task)                               |
| NSE/BSE download       | Yes (given ticker)    | Wrong ticker in → wrong filings                                                                                                                   |
| PDF verify + merge     | Yes                   | LLM misses items that are in the PDF (but cross-verify algorithm makes sure that anything that is there in the first place, is correct)           |
| Website verify + merge | Partly                | Misses logo-only customers; crawl may skip pages                                                                                                  |


**Best practice:** When you know them, pass `--ticker`, `--cin`, and `--website` so the fuzzy search steps are skipped.