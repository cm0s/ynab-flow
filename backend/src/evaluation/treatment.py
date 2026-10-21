"""Explicit treatment of every input row (evaluated, payee-only or excluded with a reason)."""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set

from src.evaluation.data import EvalRow

EVALUATED = "evaluated"
PAYEE_ONLY = "evaluated_payee_only"          # transfers: no ordinary category
EXCLUDED_STARTING_BALANCE = "excluded_starting_balance"
EXCLUDED_SPLIT_LINE = "excluded_split_line"
EXCLUDED_BLANK_DESCRIPTION = "excluded_blank_description"
EXCLUDED_UNCATEGORIZED = "excluded_uncategorized"

TREATMENTS = (EVALUATED, PAYEE_ONLY, EXCLUDED_STARTING_BALANCE, EXCLUDED_SPLIT_LINE,
              EXCLUDED_BLANK_DESCRIPTION, EXCLUDED_UNCATEGORIZED)
USABLE = (EVALUATED, PAYEE_ONLY)

TAG_REFUND = "refund"
TAG_HIDDEN = "hidden_category"

_SPLIT_MEMO = re.compile(r"^Split \(\d+/\d+\)")
TRANSFER_PREFIX = "Transfer : "
STARTING_BALANCE = "Starting Balance"
INCOME_GROUPS = ("Inflow",)


@dataclass
class Treated:
    row: EvalRow
    treatment: str
    tags: Set[str] = field(default_factory=set)


def treat(row: EvalRow) -> Treated:
    """Exactly one treatment per row, in this precedence order."""
    if row.payee == STARTING_BALANCE:
        return Treated(row, EXCLUDED_STARTING_BALANCE)
    if _SPLIT_MEMO.match(row.memo):
        return Treated(row, EXCLUDED_SPLIT_LINE)
    if not row.memo.strip():
        return Treated(row, EXCLUDED_BLANK_DESCRIPTION)
    if row.payee.startswith(TRANSFER_PREFIX):
        return Treated(row, PAYEE_ONLY)
    if not row.category_label:
        return Treated(row, EXCLUDED_UNCATEGORIZED)
    tags = set()
    if row.milliunits > 0 and row.category_group not in INCOME_GROUPS:
        tags.add(TAG_REFUND)
    if row.hidden_category:
        tags.add(TAG_HIDDEN)
    return Treated(row, EVALUATED, tags)


def treat_all(rows: Sequence[EvalRow]) -> List[Treated]:
    return [treat(r) for r in rows]


def treatment_counts(treated: Sequence[Treated]) -> Dict[str, int]:
    counts = Counter(t.treatment for t in treated)
    return {name: counts.get(name, 0) for name in TREATMENTS}


def duplicate_count(treated: Sequence[Treated]) -> int:
    """Rows identical to an earlier one (same date, account, amount, description). They are kept."""
    seen = Counter((t.row.date, t.row.account, t.row.milliunits, t.row.memo) for t in treated if t.treatment in USABLE)
    return sum(n - 1 for n in seen.values() if n > 1)
