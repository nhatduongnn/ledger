"""CSV readers for credit-card and bank exports.

Every parser normalises to a single convention:

    amount > 0  ->  money left your pocket (a purchase, a bill)
    amount < 0  ->  money came back (a refund, a statement credit)

Issuers disagree about sign, column names, and whether debits and credits
share a column, so each profile carries its own ``amount`` function.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y")


@dataclass
class Txn:
    date: date
    description: str
    amount: float          # positive = outflow
    account: str           # "chase", "capitalone", ... (folder name)
    kind: str              # "spend" | "income" | "transfer"
    issuer_category: str = ""
    category: str = ""
    post_date: str = ""
    source_file: str = ""
    txn_id: str = ""
    notes: str = ""

    def as_row(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["amount"] = round(self.amount, 2)
        return d


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# header handling
# --------------------------------------------------------------------------

def norm(h: str) -> str:
    """Collapse a header cell to a comparable key: 'Trans. Date' -> 'transdate'."""
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def parse_date(s: str) -> date:
    s = (s or "").strip()
    if not s:
        raise ParseError("empty date")
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    # Some exports carry a timestamp: "2026-03-04 00:00:00"
    head = s.split(" ")[0].split("T")[0]
    if head != s:
        return parse_date(head)
    raise ParseError(f"unrecognised date {s!r}")


def money(s: str) -> float:
    """'$1,234.56' / '(45.00)' / '-45.00' / '' -> float."""
    s = (s or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    if not s or s in {"-", "--"}:
        return 0.0
    v = float(s)
    return -v if neg else v


def pick(row: dict, *keys: str) -> str:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k]).strip()
    return ""


# --------------------------------------------------------------------------
# issuer profiles
# --------------------------------------------------------------------------

@dataclass
class Profile:
    name: str
    required: set          # normalised headers that must all be present
    amount: object         # (row) -> float, positive = outflow
    date_keys: tuple = ("transactiondate", "transdate", "date", "postingdate", "postdate")
    desc_keys: tuple = ("description", "payee", "merchant", "details")
    cat_keys: tuple = ("category",)
    post_keys: tuple = ("posteddate", "postdate", "postingdate")
    type_keys: tuple = ("type", "transactiontype", "details")
    score_bonus: int = 0


def _neg_is_spend(row):
    """Chase / most banks: purchases are negative."""
    return -money(pick(row, "amount"))


def _pos_is_spend(row):
    """Discover: purchases are positive."""
    return money(pick(row, "amount"))


def _debit_credit(row):
    """Capital One: split columns, both stored positive."""
    return money(pick(row, "debit")) - money(pick(row, "credit"))


PROFILES = [
    Profile(
        "capitalone",
        {"transactiondate", "posteddate", "description", "debit", "credit"},
        _debit_credit,
        score_bonus=2,
    ),
    Profile(
        "chase_card",
        {"transactiondate", "postdate", "description", "amount", "type"},
        _neg_is_spend,
        score_bonus=1,
    ),
    Profile(
        "discover",
        {"transdate", "postdate", "description", "amount", "category"},
        _pos_is_spend,
        score_bonus=1,
    ),
    Profile(
        "chase_bank",
        {"postingdate", "description", "amount", "type"},
        _neg_is_spend,
    ),
    Profile(
        "generic_debit_credit",
        {"date", "description", "debit", "credit"},
        _debit_credit,
    ),
    Profile(
        "generic",                      # last resort: date + description + amount
        {"date", "description", "amount"},
        _neg_is_spend,
    ),
]


def detect(headers: list[str]) -> Profile:
    keys = {norm(h) for h in headers}
    best, best_score = None, 0
    for p in PROFILES:
        if p.required <= keys:
            score = len(p.required) + p.score_bonus
            if score > best_score:
                best, best_score = p, score
    if best is None:
        raise ParseError(
            "no parser matches these columns: "
            + ", ".join(repr(h) for h in headers)
            + "\n  Run `python3 build.py --inspect` and send me this header line; "
              "adding a profile in finance/parsers.py is a two-line change."
        )
    return best


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def sniff_rows(path: Path):
    """Yield dict rows keyed by normalised header, skipping junk preamble lines."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()

    # Some exports prepend blank lines or an account summary block. The header is
    # the first line that parses to >=3 fields and contains a date-ish column.
    start = 0
    for i, line in enumerate(lines[:20]):
        cells = next(csv.reader([line]), [])
        keys = {norm(c) for c in cells}
        if len(cells) >= 3 and keys & {
            "date", "transactiondate", "transdate", "postingdate", "postdate", "posteddate"
        }:
            start = i
            break

    reader = csv.reader(lines[start:])
    headers = next(reader, [])
    if not headers:
        raise ParseError("file is empty")
    nkeys = [norm(h) for h in headers]
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        yield headers, dict(zip(nkeys, cells))


def read_file(path: Path, account: str) -> list[Txn]:
    rows = list(sniff_rows(path))
    if not rows:
        return []
    headers = rows[0][0]
    profile = detect(headers)

    out: list[Txn] = []
    for _, row in rows:
        try:
            d = parse_date(pick(row, *profile.date_keys))
        except ParseError:
            continue
        desc = pick(row, *profile.desc_keys)
        amt = profile.amount(row)
        if amt == 0 and not desc:
            continue
        out.append(
            Txn(
                date=d,
                description=" ".join(desc.split()),
                amount=amt,
                account=account,
                kind="spend",
                issuer_category=pick(row, *profile.cat_keys),
                post_date=pick(row, *profile.post_keys),
                source_file=path.name,
                notes=pick(row, *profile.type_keys),
            )
        )
    return out


def read_income(path: Path) -> list[Txn]:
    """data/income.csv -> date,source,amount,notes  (amount positive = money in)."""
    if not path.exists():
        return []
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {norm(k): v for k, v in row.items() if k}
            raw = pick(row, "date")
            if not raw or raw.startswith("#"):
                continue
            amt = money(pick(row, "amount", "gross", "net"))
            if amt == 0:
                continue
            out.append(
                Txn(
                    date=parse_date(raw),
                    description=pick(row, "source", "description", "payer") or "Income",
                    amount=-abs(amt),          # inflow, in the outflow-positive convention
                    account="income",
                    kind="income",
                    category="Income",
                    source_file=path.name,
                    notes=pick(row, "notes", "note"),
                )
            )
    return out


def read_manual(path: Path) -> list[Txn]:
    """data/manual.csv -> date,description,amount,category,account (cash, Venmo, ...)."""
    if not path.exists():
        return []
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {norm(k): v for k, v in row.items() if k}
            raw = pick(row, "date")
            if not raw or raw.startswith("#"):
                continue
            amt = money(pick(row, "amount"))
            if amt == 0:
                continue
            out.append(
                Txn(
                    date=parse_date(raw),
                    description=pick(row, "description", "payee") or "Manual entry",
                    amount=amt,
                    account=pick(row, "account") or "manual",
                    kind="spend",
                    category=pick(row, "category"),
                    source_file=path.name,
                    notes=pick(row, "notes", "note"),
                )
            )
    return out


def assign_ids(txns: list[Txn]) -> None:
    """Stable id per transaction so re-running over overlapping exports dedupes."""
    seen: dict[str, int] = {}
    for t in txns:
        base = "|".join([t.account, t.date.isoformat(), t.description.lower(), f"{t.amount:.2f}"])
        n = seen.get(base, 0)
        seen[base] = n + 1
        t.txn_id = hashlib.sha1(f"{base}|{n}".encode()).hexdigest()[:16]
