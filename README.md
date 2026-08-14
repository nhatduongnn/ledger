# Ledger

A personal finance dashboard that parses your own credit card CSV exports (Capital One,
Chase, Discover, plus a generic bank format), categorizes every transaction, and renders
a month-by-month and year-over-year view — spending by category, savings rate, net
worth, and a full transaction drill-down per category per month. Runs entirely locally:
your CSVs and the numbers built from them never leave your machine.

## Try it with fake data first — no setup

Open [`dashboard/demo.html`](dashboard/demo.html) directly in a browser. It's pre-built
from two years of fully synthetic transactions (`data/mock/`) and always shows a
**Demo data** badge, so there's no chance of confusing it with your real numbers.

## Use it with your real data

1. **Export CSVs from each card** and drop them into:
   ```
   data/raw/capitalone/
   data/raw/chase/
   data/raw/discover/
   data/raw/bank/        (rent, utilities, anything not on a card)
   ```
   Any filename works. The parser auto-detects which issuer a CSV came from by its
   column headers.

2. **Build it:**
   ```bash
   python3 build.py
   ```
   Then open `dashboard/index.html` in a browser. Re-run `build.py` any time you add new
   exports; it dedupes automatically across overlapping date ranges.

That's the whole setup — no config file required. Any month with no `data/income.csv`
entry assumes **$3,000/mo income**; any month with no `Rent/Housing` entry assumes
**$1,500/mo rent**; net worth starts at **$0**. Assumed numbers are clearly labeled
("Estimated rent...", "Estimated income...") if you drill into that category in the
dashboard, so they're never silently mistaken for real data.

To use your real numbers instead of the assumed ones:
```bash
python3 build.py --setup
```
Prompts for your starting net worth, typical monthly income, and typical monthly rent,
and writes them to `config/settings.json` (gitignored — never committed). Safe to
re-run anytime to change them; it shows your current values as the default.

For finer-grained real data — actual paycheck dates/amounts instead of a flat monthly
assumption, actual rent instead of the flat default — add rows to `data/income.csv` and
`data/manual.csv` directly:
```csv
date,source,amount,notes
2026-08-15,Paycheck,2750.00,
```
A real entry always wins over the assumed default, and only for the month it's dated
in — so backfilling real data one month at a time works fine, no need to do it all at
once.

If a merchant lands in "Uncategorized," `build.py` tells you and writes
`output/uncategorized.csv` — add a line to `config/rules.csv` and re-run.

## What stays local

`.gitignore` excludes everything with real numbers in it: `data/raw/`, `data/income.csv`,
`data/manual.csv`, `config/settings.json`, `dashboard/data.js`, and `output/`. Only the
application code and the synthetic demo data are tracked in this repo.

## How it works

```
finance/parsers.py     issuer-specific CSV readers, normalized to one Txn shape
finance/categorize.py  merchant-name rules (config/rules.csv) + issuer-category fallback
finance/ledger.py      merges every source, dedupes overlapping exports, fills any
                        month missing income/rent with the assumed default
finance/aggregate.py   rolls the ledger up into monthly totals + per-transaction detail
build.py               the CLI — orchestrates the above, writes dashboard/data.js
dashboard/index.html   the UI — vanilla JS, reads dashboard/data.js, no build step
```

`python3 build.py --inspect` prints the detected columns/parser for every CSV under
`data/raw/` without processing anything — useful if a new export doesn't parse cleanly.
