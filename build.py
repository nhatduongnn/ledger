#!/usr/bin/env python3
"""Build the ledger and the dashboard from your raw CSV exports.

    python3 build.py              parse everything, write output/ and dashboard/data.js
    python3 build.py --inspect    just show the header line of every CSV found
    python3 build.py --open       build, then open the dashboard in your browser
    python3 build.py --mock       generate two years of fake data into data/mock/
    python3 build.py --mock-build build dashboard/mock-data.js from data/mock/ (never
                                   touches your real data.js — see dashboard/demo.html)

Nothing here talks to the network. Your CSVs never leave this folder.
"""

from __future__ import annotations

import argparse
import csv
import sys
import webbrowser
from datetime import date
from pathlib import Path

from finance.aggregate import build, write_dashboard_data
from finance.categorize import Rules, categorize
from finance.ledger import load_all, write_master, write_uncategorized
from finance.parsers import detect, ParseError, sniff_rows

ROOT = Path(__file__).resolve().parent


def log(msg=""):
    print(msg, flush=True)


def inspect() -> int:
    """Print what each CSV looks like, without trying to interpret it."""
    files = sorted((ROOT / "data" / "raw").rglob("*.csv")) + \
            sorted((ROOT / "data" / "raw").rglob("*.CSV"))
    if not files:
        log("No CSVs found under data/raw/. Drop your exports into:")
        for d in ("capitalone", "chase", "discover", "bank"):
            log(f"  data/raw/{d}/")
        return 1
    for path in files:
        rel = path.relative_to(ROOT)
        log(f"\n=== {rel}")
        try:
            rows = list(sniff_rows(path))
        except ParseError as e:
            log(f"  could not read: {e}")
            continue
        if not rows:
            log("  no data rows")
            continue
        headers = rows[0][0]
        log(f"  columns : {headers}")
        try:
            log(f"  parser  : {detect(headers).name}")
        except ParseError as e:
            log(f"  parser  : NONE — {e}")
        log(f"  rows    : {len(rows)}")
        log("  sample  :")
        for _, row in rows[:3]:
            log("    " + " | ".join(f"{k}={v}" for k, v in row.items() if v))
    return 0


def ensure_settings() -> bool:
    settings = ROOT / "config" / "settings.json"
    if settings.exists():
        return True
    log("config/settings.json doesn't exist yet (it's gitignored — it can hold your real")
    log("starting net worth, which config/settings.example.json intentionally doesn't).")
    log("  cp config/settings.example.json config/settings.json")
    log("then edit net_worth_start to your real number and re-run.")
    return False


def run(open_browser: bool) -> int:
    if not ensure_settings():
        return 1
    rules = Rules.load(ROOT / "config")
    txns = load_all(ROOT / "data", log)
    if not txns:
        log("\nNothing to build yet — no transactions found.")
        log("  1. export CSVs from each card into data/raw/<issuer>/")
        log("  2. record your paychecks in data/income.csv")
        log("  3. re-run: python3 build.py")
        return 1

    categorize(txns, rules)

    spend = [t for t in txns if t.kind == "spend"]
    income = [t for t in txns if t.kind == "income"]
    transfers = [t for t in txns if t.kind == "transfer"]

    write_master(txns, ROOT / "output" / "transactions.csv")
    n_unknown = write_uncategorized(txns, ROOT / "output" / "uncategorized.csv")

    data = build(txns, rules.categories, rules.settings.get("net_worth_start", 0))
    write_dashboard_data(data, ROOT / "dashboard" / "data.js")

    log(f"\n{len(txns)} transactions: {len(spend)} spending, {len(income)} income, "
        f"{len(transfers)} transfers excluded")
    if data["months"]:
        log(f"Months covered: {data['months'][0]['key']} -> {data['months'][-1]['key']} "
            f"({len(data['months'])} months)")
    log("Wrote output/transactions.csv and dashboard/data.js")

    if n_unknown:
        unknown_total = sum(t.amount for t in spend if t.category == "Uncategorized")
        share = unknown_total / sum(t.amount for t in spend) * 100 if spend else 0
        log(f"\n{n_unknown} merchant(s) uncategorized — ${unknown_total:,.0f} ({share:.0f}% of spend).")
        log("  See output/uncategorized.csv, then add lines to config/rules.csv and re-run.")

    index = ROOT / "dashboard" / "index.html"
    log(f"\nDashboard: {index}")
    if open_browser:
        webbrowser.open(index.as_uri())
    return 0


def mock() -> int:
    """Write two years of plausible CSVs into data/mock/ so you can see the dashboard.

    Deterministic (seeded) — mainly useful if you want to regenerate or extend it;
    the checked-in data/mock/ and dashboard/mock-data.js already have a copy.
    """
    import random
    random.seed(7)

    out = ROOT / "data" / "mock"
    for d in ("capitalone", "chase", "discover"):
        (out / d).mkdir(parents=True, exist_ok=True)

    merchants = {
        "chase": [("TRADER JOE'S #451", "Groceries", 40, 130), ("CHIPOTLE 2244", "Food & Drink", 11, 22),
                  ("SHELL OIL 574", "Gas", 32, 68), ("NETFLIX.COM", "Entertainment", 16, 23),
                  ("AMZN Mktp US*2K4L", "Shopping", 12, 180)],
        "capitalone": [("WHOLEFDS MKT 102", "Grocery", 45, 150), ("STARBUCKS STORE 09", "Dining", 5, 14),
                       ("UBER TRIP", "Gas/Automotive", 9, 42), ("SPOTIFY USA", "Entertainment", 11, 17),
                       ("CVS/PHARMACY #7742", "Health Care", 8, 90)],
        "discover": [("SAFEWAY #1834", "Supermarkets", 35, 120), ("DOORDASH*THAI", "Restaurants", 18, 46),
                     ("COMCAST CABLE", "Services", 79, 79), ("TARGET T-2201", "Merchandise", 20, 210),
                     ("DELTA AIR LINES", "Travel/Entertainment", 180, 640)],
    }

    today = date.today()
    start_y, start_m = (today.year - 2, today.month)
    months = []
    y, m = start_y, start_m
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1

    for issuer, items in merchants.items():
        rows = []
        for (yy, mm) in months:
            last_day = today.day if (yy, mm) == (today.year, today.month) else 28
            for name, cat, lo, hi in items:
                for _ in range(random.randint(1, 4)):
                    day = random.randint(1, last_day)
                    rows.append((date(yy, mm, day), name, cat, round(random.uniform(lo, hi), 2)))
            rows.append((date(yy, mm, min(15, last_day)), "PAYMENT - THANK YOU", "Payment",
                         -round(random.uniform(400, 900), 2)))
        rows.sort()

        path = out / issuer / "mock-2y.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if issuer == "chase":
                w.writerow(["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"])
                for d, n, c, a in rows:
                    w.writerow([d.strftime("%m/%d/%Y"), d.strftime("%m/%d/%Y"), n, c,
                                "Payment" if a < 0 else "Sale", f"{-a:.2f}", ""])
            elif issuer == "capitalone":
                w.writerow(["Transaction Date", "Posted Date", "Card No.", "Description", "Category", "Debit", "Credit"])
                for d, n, c, a in rows:
                    w.writerow([d.isoformat(), d.isoformat(), "1234", n, c,
                                f"{a:.2f}" if a > 0 else "", f"{-a:.2f}" if a < 0 else ""])
            else:
                w.writerow(["Trans. Date", "Post Date", "Description", "Amount", "Category"])
                for d, n, c, a in rows:
                    w.writerow([d.strftime("%m/%d/%Y"), d.strftime("%m/%d/%Y"), n, f"{a:.2f}", c])

    # Rent, utilities and paychecks live outside the cards.
    (out / "bank").mkdir(parents=True, exist_ok=True)
    with (out / "bank" / "mock-2y.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Details", "Posting Date", "Description", "Amount", "Type", "Balance"])
        for (yy, mm) in months:
            if (yy, mm) == (today.year, today.month) and today.day < 3:
                continue
            w.writerow(["DEBIT", date(yy, mm, 1).strftime("%m/%d/%Y"), "OAKRIDGE APARTMENTS RENT",
                        "-2200.00", "ACH_DEBIT", ""])
            w.writerow(["DEBIT", date(yy, mm, min(6, today.day if (yy, mm) == (today.year, today.month) else 6)).strftime("%m/%d/%Y"),
                        "PG&E ELECTRIC PAYMENT", f"-{random.uniform(90, 210):.2f}", "ACH_DEBIT", ""])

    with (out / "income.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "source", "amount", "notes"])
        for (yy, mm) in months:
            last_day = today.day if (yy, mm) == (today.year, today.month) else 28
            for day in (15, 28):
                if day <= last_day:
                    w.writerow([date(yy, mm, day).isoformat(), "Acme Corp payroll",
                                f"{random.uniform(3400, 3900):.2f}", ""])

    log(f"Mock CSVs written to {out.relative_to(ROOT)}/")
    log("Build the demo dashboard with:  python3 build.py --mock-build --open")
    return 0


def mock_build(open_browser: bool) -> int:
    """Build dashboard/demo.html's data from data/mock/.

    Writes dashboard/mock-data.js and output/transactions-mock.csv — separate files
    from the real build's outputs, so this can never clobber your actual data.
    """
    mock_root = ROOT / "data" / "mock"
    if not mock_root.exists():
        log("No mock data yet. Run: python3 build.py --mock")
        return 1

    rules = Rules.load(ROOT / "config")
    txns = load_all(mock_root, log)
    categorize(txns, rules)

    data = build(txns, rules.categories, 38200)
    write_dashboard_data(data, ROOT / "dashboard" / "mock-data.js")
    write_master(txns, ROOT / "output" / "transactions-mock.csv")
    n_unknown = write_uncategorized(txns, ROOT / "output" / "uncategorized-mock.csv")

    log(f"\nMock build: {len(txns)} transactions, {len(data['months'])} months "
        f"({data['months'][0]['key']} -> {data['months'][-1]['key']})")
    if n_unknown:
        log(f"{n_unknown} uncategorized merchant(s) — see output/uncategorized-mock.csv")
    demo = ROOT / "dashboard" / "demo.html"
    log(f"Demo dashboard: {demo}")
    if open_browser:
        webbrowser.open(demo.as_uri())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", action="store_true", help="show the columns of every CSV under data/raw/")
    ap.add_argument("--mock", action="store_true", help="write two years of fake CSVs to data/mock/")
    ap.add_argument("--mock-build", action="store_true", help="build dashboard/demo.html's data from data/mock/")
    ap.add_argument("--open", action="store_true", help="open the dashboard when done")
    a = ap.parse_args()

    if a.inspect:
        return inspect()
    if a.mock:
        return mock()
    if a.mock_build:
        return mock_build(a.open)
    return run(a.open)


if __name__ == "__main__":
    sys.exit(main())
