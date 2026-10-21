"""Canonical evaluation rows and the input adapters that produce them.

Two inputs are supported: a YNAB plan export (the archive or its extracted
``Register.csv``) and the synced local history. Both are read-only.
"""

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from sqlalchemy.orm import Session

from src.models import Account, Category, CategoryGroup, Payee, Transaction
from src.money import MoneyError, parse_date, to_milliunits


class EvaluationInputError(ValueError):
    """The evaluation input is missing or not a recognised history source."""


@dataclass(frozen=True)
class EvalRow:
    line: int                # 1-based position in the input (stable tie-breaker)
    date: str                # YYYY-MM-DD
    account: str
    payee: str
    category_group: str
    category: str            # display name
    category_label: str      # stable label: synced category id, or "Group: Category" for exports
    memo: str
    milliunits: int
    hidden_category: bool = False


REGISTER_COLUMNS = ("Account", "Date", "Payee", "Category Group/Category", "Category Group",
                    "Category", "Memo", "Outflow", "Inflow")
EXPORT_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d")
HIDDEN_GROUP = "Hidden Categories"


def _export_amount(value: str) -> int:
    """Export amounts are plain decimals, optionally with a currency symbol and thousands commas."""
    text = re.sub(r"[^0-9.,\-]", "", value or "")
    if not text:
        return 0
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return to_milliunits(text)


def read_export_text(path: Path) -> Tuple[str, bytes]:
    """Return (register CSV text, raw bytes used for fingerprinting)."""
    if not path.exists():
        raise EvaluationInputError(f"Input not found: {path}")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith("Register.csv")]
            if len(names) != 1:
                raise EvaluationInputError(
                    f"{path.name} is not a YNAB plan export: expected one '... - Register.csv' inside the archive.")
            raw = archive.read(names[0])
    else:
        raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig"), raw
    except UnicodeDecodeError as exc:
        raise EvaluationInputError(f"{path.name} is not UTF-8 text") from exc


def load_export(path: Path) -> Tuple[List[EvalRow], str]:
    """Parse a YNAB export register. Returns (rows, sha256 of the register)."""
    text, raw = read_export_text(Path(path))
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REGISTER_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise EvaluationInputError(f"Not a YNAB register export; missing columns: {', '.join(missing)}")
    rows: List[EvalRow] = []
    for line, rec in enumerate(reader, start=1):
        try:
            date = parse_date(rec["Date"], EXPORT_DATE_FORMATS)
            amount = _export_amount(rec["Inflow"]) - _export_amount(rec["Outflow"])
        except MoneyError as exc:
            raise EvaluationInputError(f"Line {line}: {exc}") from exc
        group, name = rec["Category Group"].strip(), rec["Category"].strip()
        rows.append(EvalRow(
            line=line, date=date, account=rec["Account"].strip(), payee=rec["Payee"].strip(),
            category_group=group, category=name,
            category_label=rec["Category Group/Category"].strip() if name else "",
            memo=rec["Memo"] or "", milliunits=amount, hidden_category=group == HIDDEN_GROUP,
        ))
    return rows, hashlib.sha256(raw).hexdigest()


def load_synced(db: Session, plan_id: str) -> Tuple[List[EvalRow], str]:
    """Read synced, non-deleted history of a plan. Returns (rows, sha256 of the canonical rows)."""
    query = (
        db.query(Transaction, Account.name, Payee.name, Category, CategoryGroup.name)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .outerjoin(CategoryGroup, Category.category_group_id == CategoryGroup.id)
        .filter(Transaction.plan_id == plan_id, Transaction.deleted == False)  # noqa: E712
        .order_by(Transaction.date, Transaction.ynab_transaction_id)
    )
    rows: List[EvalRow] = []
    digest = hashlib.sha256()
    for line, (txn, account, payee, category, group) in enumerate(query.all(), start=1):
        milli = txn.amount_milliunits if txn.amount_milliunits is not None else int(round(txn.amount or 0))
        row = EvalRow(
            line=line, date=txn.date, account=account or "", payee=payee or "",
            category_group=group or "", category=category.name if category else "",
            category_label=category.id if category else "", memo=txn.memo or "",
            milliunits=milli, hidden_category=bool(category and category.deleted),
        )
        digest.update(repr(row).encode())
        rows.append(row)
    return rows, digest.hexdigest()
