"""Load every input file, dedupe, and write the master ledger."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .parsers import ParseError, Txn, assign_ids, read_file, read_income, read_manual

MASTER_COLUMNS = [
    "date", "account", "description", "amount", "category", "kind",
    "issuer_category", "post_date", "source_file", "txn_id", "notes",
]


def load_raw(raw_dir: Path, log) -> list[Txn]:
    """Read data/raw/<account>/*.csv — the folder name becomes the account name."""
    txns: list[Txn] = []
    if not raw_dir.exists():
        return txns
    for account_dir in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        files = sorted(account_dir.glob("*.csv")) + sorted(account_dir.glob("*.CSV"))
        for path in files:
            try:
                got = read_file(path, account_dir.name)
            except ParseError as e:
                log(f"  !! {account_dir.name}/{path.name}: {e}")
                continue
            txns.extend(got)
            log(f"  {account_dir.name}/{path.name}: {len(got)} rows")
    return txns


def dedupe(txns: list[Txn], log) -> list[Txn]:
    """Drop rows that overlapping exports report twice.

    Two identical charges on the same day are real and must both survive, so the
    count that matters is the *most* times a given charge appears in any single
    file — not the total across files.
    """
    def key(t: Txn):
        return (t.account, t.date, t.description.lower(), round(t.amount, 2))

    per_file: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        per_file[key(t)][t.source_file] += 1

    want = {k: max(counts.values()) for k, counts in per_file.items()}
    kept: list[Txn] = []
    taken: dict[tuple, int] = defaultdict(int)
    for t in sorted(txns, key=lambda t: (t.date, t.source_file)):
        k = key(t)
        if taken[k] < want[k]:
            taken[k] += 1
            kept.append(t)

    dropped = len(txns) - len(kept)
    if dropped:
        log(f"  deduped {dropped} duplicate row(s) from overlapping exports")
    return kept


def load_all(data_dir: Path, log) -> list[Txn]:
    """Read everything under a data directory (data/ normally, data/sample/ for previews)."""
    log("Reading card and bank exports...")
    raw = data_dir / "raw"
    txns = load_raw(raw if raw.exists() else data_dir, log)

    manual = read_manual(data_dir / "manual.csv")
    if manual:
        log(f"  manual.csv: {len(manual)} rows")
    txns += manual

    income = read_income(data_dir / "income.csv")
    log(f"  income.csv: {len(income)} rows")
    txns += income

    txns = dedupe(txns, log)
    txns.sort(key=lambda t: (t.date, t.account, t.description))
    assign_ids(txns)
    return txns


def write_master(txns: list[Txn], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_COLUMNS)
        w.writeheader()
        for t in txns:
            row = t.as_row()
            w.writerow({k: row.get(k, "") for k in MASTER_COLUMNS})


def write_uncategorized(txns: list[Txn], path: Path) -> int:
    """Merchant-level report of what the rules missed, biggest spend first."""
    totals: dict[str, list] = {}
    for t in txns:
        if t.kind == "spend" and t.category == "Uncategorized":
            e = totals.setdefault(t.description, [0, 0.0, t.account, t.issuer_category])
            e[0] += 1
            e[1] += t.amount
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["description", "times", "total", "account", "issuer_category", "suggested_rule_line"])
        for desc, (n, amt, acct, icat) in sorted(totals.items(), key=lambda kv: -kv[1][1]):
            w.writerow([desc, n, round(amt, 2), acct, icat, f"{desc},<Category>,"])
    return len(totals)
