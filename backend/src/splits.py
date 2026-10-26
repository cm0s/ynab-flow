"""Split lines on import rows: one bank transaction recorded under several categories.

Amounts are integer milliunits and must sum exactly to the row's bank amount.
The same validation runs on AI output, on save and again at push time, so a
category deleted in YNAB after saving still blocks the push.
"""

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from src.categories import CategoryDirectory
from src.models import ImportRow, ImportRowSplit
from src.money import milliunits_to_decimal

ORIGINS = ("receipt", "ai", "user")


class SplitError(ValueError):
    """A split that cannot be saved or pushed; the message says why in plain terms."""


@dataclass
class SplitLine:
    amount_milliunits: int
    category_id: str
    payee: str = ""
    memo: str = ""
    origin: str = "user"


def format_amount(milliunits: int, currency: str = "") -> str:
    value = milliunits_to_decimal(milliunits)
    text = f"{value:.2f}" if value == value.quantize(Decimal("0.01")) else f"{value:.3f}"
    return f"{currency} {text}".strip()


def unassigned(lines: Iterable[SplitLine], bank_milliunits: int) -> int:
    """Bank amount not yet covered by the lines (0 when the split adds up)."""
    return int(bank_milliunits) - sum(int(line.amount_milliunits) for line in lines)


def validate_lines(
    lines: List[SplitLine],
    bank_milliunits: Optional[int],
    directory: CategoryDirectory,
    transfer_state: str = "none",
    currency: str = "",
) -> None:
    """Raise SplitError unless the lines form a valid split of the bank amount."""
    if transfer_state != "none":
        raise SplitError("Transfers and card repayments cannot be split.")
    if bank_milliunits is None:
        raise SplitError("This row has no exact bank amount, so it cannot be split.")
    if bank_milliunits == 0:
        raise SplitError("A zero-amount transaction cannot be split.")
    if len(lines) < 2:
        raise SplitError("A split needs at least two lines.")
    sign = 1 if bank_milliunits > 0 else -1
    for number, line in enumerate(lines, start=1):
        if not isinstance(line.amount_milliunits, int) or isinstance(line.amount_milliunits, bool):
            raise SplitError(f"Line {number}: the amount must be whole milliunits.")
        if line.amount_milliunits == 0 or (line.amount_milliunits > 0) != (sign > 0):
            kind = "an outflow" if sign < 0 else "an inflow"
            raise SplitError(f"Line {number}: every line must be {kind}, like the bank amount.")
        if line.category_id not in directory:
            raise SplitError(f"Line {number}: choose a category from this budget.")
        if directory.is_deleted(line.category_id):
            raise SplitError(f"Line {number}: category '{directory.name(line.category_id)}' was deleted in YNAB; "
                             "choose another category.")
        if line.origin not in ORIGINS:
            raise SplitError(f"Line {number}: unknown origin '{line.origin}'.")
    gap = unassigned(lines, bank_milliunits)
    if gap:
        total = bank_milliunits - gap
        raise SplitError(
            f"Split lines total {format_amount(total, currency)} but the bank amount is "
            f"{format_amount(bank_milliunits, currency)}: {format_amount(abs(gap))} is "
            f"{'unassigned' if abs(total) < abs(bank_milliunits) else 'over-assigned'}."
        )


def row_lines(row: ImportRow) -> List[SplitLine]:
    return [SplitLine(amount_milliunits=int(s.amount_milliunits), category_id=s.category_id,
                      payee=s.payee or "", memo=s.memo or "", origin=s.origin or "user") for s in row.splits]


def validate_row(row: ImportRow, directory: CategoryDirectory) -> None:
    validate_lines(row_lines(row), row.amount_milliunits, directory, row.transfer_state, row.currency)


def set_splits(db: Session, row: ImportRow, lines: List[SplitLine], directory: CategoryDirectory) -> None:
    """Replace the row's split lines after validation; bank values are never touched."""
    validate_lines(lines, row.amount_milliunits, directory, row.transfer_state, row.currency)
    row.splits.clear()
    db.flush()
    for position, line in enumerate(lines):
        row.splits.append(ImportRowSplit(
            position=position, amount_milliunits=line.amount_milliunits, category_id=line.category_id,
            payee=(line.payee or "").strip(), memo=(line.memo or "").strip(), origin=line.origin,
        ))


def clear_splits(row: ImportRow) -> None:
    row.splits.clear()


def split_views(row: ImportRow, directory: Optional[CategoryDirectory] = None) -> List[dict]:
    """API view of the row's lines, with category display names when a directory is given."""
    views = []
    for line in row_lines(row):
        view = asdict(line)
        if directory is not None:
            view["category_name"] = directory.name(line.category_id)
            view["category_deleted"] = directory.is_deleted(line.category_id)
        views.append(view)
    return views
