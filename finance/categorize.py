"""Assign a spending category to each transaction.

Precedence, first match wins:

    1. a category you typed yourself in data/manual.csv
    2. transfer rules   -> dropped from spending entirely (card payments etc.)
    3. config/rules.csv -> your own merchant rules, top to bottom
    4. the issuer's own Category column, via config/settings.json
    5. "Uncategorized"
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .parsers import Txn, norm


class Rules:
    def __init__(self, settings: dict, rules: list[tuple[re.Pattern, str]]):
        self.settings = settings
        self.rules = rules
        self.categories: list[str] = settings["categories"]
        self.transfers = [re.compile(p, re.I) for p in settings.get("transfer_patterns", [])]
        self.issuer_map = {k.lower(): v for k, v in settings.get("issuer_category_map", {}).items()}

    @classmethod
    def load(cls, config_dir: Path) -> "Rules":
        # settings.json is gitignored (it can hold a real net worth figure) and entirely
        # optional — a fresh clone with none of it set up falls back to
        # settings.example.json's categories/rules plus these hardcoded numbers, so
        # `python3 build.py` works with zero setup. `python3 build.py --setup` writes a
        # real settings.json once someone wants to change them.
        settings_path = config_dir / "settings.json"
        if not settings_path.exists():
            settings_path = config_dir / "settings.example.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings.setdefault("net_worth_start", 0)
        settings.setdefault("default_monthly_income", 3000)
        settings.setdefault("default_monthly_rent", 1500)
        rules: list[tuple[re.Pattern, str]] = []
        rules_path = config_dir / "rules.csv"
        if rules_path.exists():
            with rules_path.open(newline="", encoding="utf-8-sig") as f:
                for lineno, row in enumerate(csv.DictReader(f), start=2):
                    row = {norm(k): (v or "").strip() for k, v in row.items() if k}
                    match, cat = row.get("match", ""), row.get("category", "")
                    if not match or not cat or match.startswith("#"):
                        continue
                    if cat not in settings["categories"]:
                        raise SystemExit(
                            f"config/rules.csv line {lineno}: category {cat!r} is not in "
                            f"settings.json categories"
                        )
                    pattern = match[3:] if match.lower().startswith("re:") else re.escape(match)
                    rules.append((re.compile(pattern, re.I), cat))
        return cls(settings, rules)

    def is_transfer(self, t: Txn) -> bool:
        hay = f"{t.description} {t.notes}"
        return any(p.search(hay) for p in self.transfers)

    def apply(self, t: Txn) -> None:
        if t.kind == "income":
            return
        if self.is_transfer(t):
            t.kind = "transfer"
            t.category = "Transfer"
            return
        if t.category:                       # hand-written in manual.csv
            return
        for pattern, cat in self.rules:
            if pattern.search(t.description):
                t.category = cat
                return
        mapped = self.issuer_map.get(t.issuer_category.strip().lower())
        if mapped:
            t.category = mapped
            return
        t.category = "Uncategorized"


def categorize(txns: list[Txn], rules: Rules) -> None:
    for t in txns:
        rules.apply(t)
