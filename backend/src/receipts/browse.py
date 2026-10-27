"""Receipts page: filtered list, detail with evidence, and candidate rows from any import.

Views never contain mailbox credentials or usernames: sources carry the
mailbox label (``me``, ``partner``) and folder only.
"""

import json
from datetime import date
from typing import List, Optional

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Session

from src.categories import CategoryDirectory
from src.models import ImportBatch, ImportRow, Receipt, ReceiptMatch, ReceiptSource
from src.receipts import items as item_categories
from src.receipts.matching import CANDIDATE, WINDOW_DAYS, score
from src.receipts.review import extracted, user_fields

DUPLICATE_DAYS = 3
MAX_PAGE_SIZE = 200
MAX_CANDIDATES = 20


def _day(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").lower().split())


def receipt_date(receipt: Receipt) -> Optional[str]:
    """The purchase date, else the message date: the date reports and lists use."""
    return receipt.purchase_date or receipt.message_date


def _linked_exists():
    return exists().where(and_(ReceiptMatch.receipt_id == Receipt.id, ReceiptMatch.state == "linked"))


def list_receipts(db: Session, status: Optional[str] = None, kind: Optional[str] = None,
                  linked: Optional[bool] = None, mailbox: Optional[str] = None, q: Optional[str] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None,
                  page: int = 1, page_size: int = 50) -> dict:
    query = db.query(Receipt)
    if status:
        query = query.filter(Receipt.status == status)
    if kind:
        query = query.filter(Receipt.kind == kind)
    if linked is not None:
        query = query.filter(_linked_exists() if linked else ~_linked_exists())
    if mailbox:
        query = query.filter(exists().where(and_(ReceiptSource.receipt_id == Receipt.id,
                                                 ReceiptSource.mailbox == mailbox)))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Receipt.merchant.ilike(like), Receipt.subject.ilike(like),
                                 Receipt.sender.ilike(like), Receipt.reference.ilike(like)))
    day = func.coalesce(Receipt.purchase_date, Receipt.message_date)
    if date_from:
        query = query.filter(day >= date_from)
    if date_to:
        query = query.filter(day <= date_to)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    page = max(1, page)
    total = query.count()
    receipts = query.order_by(day.desc(), Receipt.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "receipts": [summary(db, r) for r in receipts]}


def possible_duplicates(db: Session, receipt: Receipt) -> List[str]:
    """Other receipts with the same merchant and total within a few days (never excluded automatically)."""
    when = _day(receipt_date(receipt))
    if receipt.total_milliunits is None or when is None or receipt.kind != "purchase" or receipt.status == "not_receipt":
        return []
    others = db.query(Receipt).filter(Receipt.id != receipt.id, Receipt.total_milliunits == receipt.total_milliunits,
                                      Receipt.kind == "purchase", Receipt.status != "not_receipt").all()
    out = []
    for other in others:
        other_day = _day(receipt_date(other))
        if (other_day and abs((other_day - when).days) <= DUPLICATE_DAYS and _norm(other.merchant) == _norm(receipt.merchant)
                and other.currency == receipt.currency):
            out.append(other.id)
    return out


def summary(db: Session, receipt: Receipt) -> dict:
    linked = [m for m in receipt.matches if m.state == "linked"]
    return {
        "id": receipt.id, "status": receipt.status, "kind": receipt.kind, "merchant": receipt.merchant,
        "date": receipt_date(receipt), "purchase_date": receipt.purchase_date,
        "total_milliunits": receipt.total_milliunits, "currency": receipt.currency, "subject": receipt.subject,
        "mailboxes": sorted({s.mailbox for s in receipt.sources}), "linked_rows": len(linked),
        "excluded": bool(receipt.excluded), "user_fields": user_fields(receipt), "error": receipt.error,
        "possible_duplicates": possible_duplicates(db, receipt),
    }


def row_summary(row: ImportRow, directory: Optional[CategoryDirectory] = None) -> dict:
    category = item_categories.row_category(row, directory)
    return {
        "id": row.id, "date": row.date, "amount_milliunits": row.amount_milliunits, "currency": row.currency,
        "bank_text": row.original_memo, "write_state": row.write_state,
        "in_ynab": row.write_state in ("created", "existing"), "batch": row.batch.filename if row.batch else None,
        "category": directory.name(category) if directory and category else (row.edited_category or None),
        "split": bool(row.splits),
    }


def detail(db: Session, receipt: Receipt, directory: CategoryDirectory) -> dict:
    matches = []
    for m in sorted(receipt.matches, key=lambda m: (m.state != "linked", -m.score)):
        if m.row is None:
            continue
        matches.append({"state": m.state, "origin": m.origin, "role": m.role, "score": m.score,
                        "signals": json.loads(m.signals_json or "{}"), "row": row_summary(m.row, directory)})
    return {
        **summary(db, receipt),
        "reference": receipt.reference, "card_hint": receipt.card_hint, "sender": receipt.sender,
        "message_date": receipt.message_date, "extraction_method": receipt.extraction_method,
        "local_total_milliunits": receipt.local_total_milliunits,
        "extracted": extracted(receipt),
        "items": item_categories.resolve(receipt, directory),
        "sources": sorted(({"mailbox": s.mailbox, "folder": s.folder} for s in receipt.sources),
                          key=lambda s: (s["mailbox"], s["folder"])),
        "text_excerpt": receipt.text_excerpt,
        "matches": matches,
    }


def candidates(db: Session, receipt: Receipt, directory: Optional[CategoryDirectory] = None) -> List[dict]:
    """Rows from any import (pushed ones included) within the window, scored like automatic matching."""
    when = _day(receipt_date(receipt))
    if when is None:
        return []
    existing = {m.import_row_id: m for m in receipt.matches}
    rows = (db.query(ImportRow).join(ImportBatch, ImportRow.batch_id == ImportBatch.id)
            .filter(ImportRow.transfer_state == "none", ImportRow.amount_milliunits.isnot(None)).all())
    out = []
    for row in rows:
        row_day = _day(row.date)
        if row_day is None or abs((row_day - when).days) > WINDOW_DAYS:
            continue
        value, signals = score(receipt, row)
        match = existing.get(row.id)
        if value < CANDIDATE and match is None:
            continue
        out.append({"score": value, "signals": signals, "state": match.state if match else None,
                    "row": row_summary(row, directory)})
    out.sort(key=lambda c: (c["state"] != "linked", -c["score"]))
    return out[:MAX_CANDIDATES]
