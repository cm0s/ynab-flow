"""Reconcile source records with YNAB history and resolve repayment routing.

Only explicit links (ledger YNAB id or matching import_id) or an unambiguous,
description-backed legacy match establish an existing transaction. Equal
date/amount alone is only a *potential* match and blocks the row until the
user resolves it. Nothing here writes to YNAB.
"""

import json
import logging
from datetime import date as _date, timedelta
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.identity import IMPORT_ID_PREFIX
from src.models import Account, Category, ImportRow, Payee, Plan, SourceRecord, Transaction
from src.normalizer import normalize
from src.sync_service import SyncService

logger = logging.getLogger(__name__)

COMPLETED = ("created", "existing")
WRITABLE_RECONCILIATION = ("new", "confirmed_new")
# States owned by earlier steps or by the user; reconciliation never overwrites them.
STICKY_RECONCILIATION = ("quarantined", "legacy_unresolved", "ambiguous", "confirmed_new")
TRANSFER_WINDOW_DAYS = 3
EVIDENCE_RATIO = 0.8


def refresh_history(db: Session, plan: Plan, client) -> Tuple[bool, str]:
    """Delta-sync transactions. Returns (fresh, message); never raises on API errors."""
    try:
        SyncService(db, client).sync_transactions(plan)
        return True, ""
    except Exception as exc:  # network failures included: stay unverified
        logger.warning("History refresh failed: %s", exc)
        db.rollback()
        return False, f"YNAB history could not be refreshed: {exc}"


def _source_text(record: SourceRecord) -> List[str]:
    canonical = json.loads(record.evidence_json or "{}").get("canonical", {})
    return [t for t in (canonical.get("description"), canonical.get("merchant")) if t]


def _has_description_evidence(record: SourceRecord, txn: Transaction, payee_names: Dict[str, str]) -> bool:
    target = normalize(txn.memo or "").cleaned_memo.upper()
    target += " " + (payee_names.get(txn.payee_id or "", "")).upper()
    target = target.strip()
    if not target:
        return False
    for text in _source_text(record):
        cleaned = normalize(text).cleaned_memo.upper()
        if not cleaned:
            continue
        if cleaned in target or (len(target) > 3 and target.strip() in cleaned):
            return True
        if SequenceMatcher(None, cleaned, target).ratio() >= EVIDENCE_RATIO:
            return True
    return False


def _link(db: Session, row: ImportRow, record: SourceRecord, txn: Transaction, note: str) -> None:
    record.ynab_transaction_id = txn.ynab_transaction_id
    record.write_state = "existing"
    record.reconciliation_state = "linked"
    row.reconciliation_state = "linked"
    row.reconciliation_note = note
    row.write_state = "existing"
    row.ynab_transaction_id = txn.ynab_transaction_id


def reconcile_rows(db: Session, plan: Plan, rows: Iterable[ImportRow], history_fresh: bool) -> None:
    """Update reconciliation/transfer state on rows that still need writing."""
    rows = [r for r in rows if r.source_record is not None and r.write_state not in COMPLETED]
    if not rows:
        return
    records = [r.source_record for r in rows]
    account_ids = {rec.account_id for rec in records}
    dates = {rec.date for rec in records}

    live: Dict[str, Transaction] = {}
    by_import: Dict[str, Transaction] = {}
    by_key: Dict[Tuple[str, str, int], List[Transaction]] = {}
    for t in db.query(Transaction).filter(Transaction.plan_id == plan.id, Transaction.account_id.in_(account_ids)).all():
        by_import_key = t.import_id
        if by_import_key:
            by_import[by_import_key] = t
        live[t.ynab_transaction_id] = t
        if not t.deleted and t.date in dates and t.amount_milliunits is not None:
            by_key.setdefault((t.account_id, t.date, t.amount_milliunits), []).append(t)
    payee_names = {p.id: p.name for p in db.query(Payee).filter(Payee.plan_id == plan.id).all()}
    ledger_ynab_ids = {
        r[0] for r in db.query(SourceRecord.ynab_transaction_id)
        .filter(SourceRecord.plan_id == plan.id, SourceRecord.ynab_transaction_id.isnot(None)).all()
    }
    # multiplicity of unwritten source records per (account, date, amount)
    multiplicity: Dict[Tuple[str, str, int], int] = {}
    for rec in db.query(SourceRecord).filter(
        SourceRecord.plan_id == plan.id, SourceRecord.account_id.in_(account_ids),
        SourceRecord.date.in_(dates), SourceRecord.write_state.notin_(COMPLETED),
    ).all():
        key = (rec.account_id, rec.date, rec.amount_milliunits)
        multiplicity[key] = multiplicity.get(key, 0) + 1

    for row in rows:
        rec = row.source_record
        if row.reconciliation_state in ("quarantined", "legacy_unresolved", "ambiguous"):
            continue
        if _reconcile_transfer(db, plan, row, rec, history_fresh):
            continue
        if row.reconciliation_state == "confirmed_new":
            continue

        # 1/2. explicit links
        if rec.ynab_transaction_id:
            txn = live.get(rec.ynab_transaction_id)
            if txn is not None and not txn.deleted:
                _link(db, row, rec, txn, "Linked to existing YNAB transaction.")
                continue
            if txn is not None and txn.deleted or (txn is None and history_fresh):
                row.reconciliation_state = rec.reconciliation_state = "deleted_link"
                row.reconciliation_note = "The linked YNAB transaction was deleted; confirm whether to recreate it."
                continue
        txn = by_import.get(rec.import_id)
        if txn is not None:
            if txn.deleted:
                row.reconciliation_state = rec.reconciliation_state = "deleted_link"
                row.reconciliation_note = "A transaction with this import ID was deleted in YNAB; confirm whether to recreate it."
            else:
                _link(db, row, rec, txn, "Found existing YNAB transaction with this import ID.")
            continue

        if not history_fresh:
            row.reconciliation_state = "unverified"
            row.reconciliation_note = "YNAB history is unavailable, so duplicates cannot be ruled out."
            continue

        # 3. legacy / external-import evidence
        key = (rec.account_id, rec.date, rec.amount_milliunits)
        candidates = [
            c for c in by_key.get(key, [])
            if c.ynab_transaction_id not in ledger_ynab_ids
            and not (c.import_id or "").startswith(IMPORT_ID_PREFIX)
        ]
        if not candidates:
            row.reconciliation_state = rec.reconciliation_state = "new"
            row.reconciliation_note = ""
            continue
        evidenced = [c for c in candidates if _has_description_evidence(rec, c, payee_names)]
        if len(candidates) == 1 and len(evidenced) == 1 and multiplicity.get(key, 1) == 1:
            _link(db, row, rec, evidenced[0], "Matched an earlier YNAB import by account, date, amount and description.")
        else:
            row.reconciliation_state = rec.reconciliation_state = "potential"
            row.reconciliation_note = (
                f"{len(candidates)} YNAB transaction(s) share this account, date and amount "
                f"({len(evidenced)} also match the description); confirm a link or mark this as a new purchase."
            )


def candidate_transactions(db: Session, row: ImportRow) -> List[dict]:
    """Live YNAB transactions a blocked row could be linked to (same account, date and amount)."""
    rec = row.source_record
    if rec is None or rec.amount_milliunits is None:
        return []
    linked = {
        r[0] for r in db.query(SourceRecord.ynab_transaction_id)
        .filter(SourceRecord.plan_id == rec.plan_id, SourceRecord.id != rec.id,
                SourceRecord.ynab_transaction_id.isnot(None)).all()
    }
    found = db.query(Transaction).filter(
        Transaction.plan_id == rec.plan_id, Transaction.account_id == rec.account_id,
        Transaction.date == rec.date, Transaction.amount_milliunits == rec.amount_milliunits,
        Transaction.deleted == False,  # noqa: E712
    ).all()
    payees = {p.id: p.name for p in db.query(Payee).filter(Payee.plan_id == rec.plan_id).all()}
    category_ids = {t.category_id for t in found if t.category_id}
    categories = {c.id: c.name for c in db.query(Category).filter(Category.id.in_(category_ids)).all()} if category_ids else {}
    return [
        {"ynab_transaction_id": t.ynab_transaction_id, "date": t.date,
         "amount_milliunits": t.amount_milliunits, "payee": payees.get(t.payee_id, ""),
         "category": categories.get(t.category_id, ""), "memo": t.memo or ""}
        for t in found
        if t.ynab_transaction_id not in linked and not (t.import_id or "").startswith(IMPORT_ID_PREFIX)
    ]


# ---------------------------------------------------------------------------
# Repayment routing
# ---------------------------------------------------------------------------

def transfer_payee_for(db: Session, plan_id: str, source_account: Account) -> Optional[Payee]:
    return (
        db.query(Payee)
        .filter(Payee.plan_id == plan_id, Payee.transfer_account_id == source_account.ynab_account_id,
                Payee.deleted == False)  # noqa: E712
        .first()
    )


def _reconcile_transfer(db: Session, plan: Plan, row: ImportRow, rec: SourceRecord, fresh: bool) -> bool:
    """Handle repayment candidates. Returns True when the row's state was decided here."""
    if row.transfer_state == "none":
        return False
    if row.transfer_state == "linked" and row.write_state in COMPLETED:
        return True
    card = db.get(Account, rec.account_id)
    if card is None:
        return False
    try:
        day = _date.fromisoformat(rec.date)
    except ValueError:
        return False
    window = [(day + timedelta(days=d)).isoformat() for d in range(-TRANSFER_WINDOW_DAYS, TRANSFER_WINDOW_DAYS + 1)]
    post_sides = (
        db.query(Transaction)
        .filter(Transaction.plan_id == plan.id, Transaction.deleted == False,  # noqa: E712
                Transaction.transfer_account_ynab_id == card.ynab_account_id,
                Transaction.amount_milliunits == -rec.amount_milliunits,
                Transaction.date.in_(window))
        .all()
    )
    if len(post_sides) > 1:
        row.transfer_state = "ambiguous"
        row.reconciliation_state = rec.reconciliation_state = "conflict"
        row.reconciliation_note = f"{len(post_sides)} possible Post-side transfers match this repayment; choose one."
        return True
    if len(post_sides) == 1:
        counterpart = post_sides[0].transfer_transaction_ynab_id
        card_side = (
            db.query(Transaction).filter(Transaction.ynab_transaction_id == counterpart, Transaction.deleted == False).first()  # noqa: E712
            if counterpart else None
        )
        if card_side is not None:
            row.transfer_state = "linked"
            row.transfer_account_id = post_sides[0].account_id
            _link(db, row, rec, card_side, "Linked the existing card-side transfer; nothing will be created.")
            return True
        row.transfer_state = "ambiguous"
        row.reconciliation_state = rec.reconciliation_state = "conflict"
        row.reconciliation_note = "A Post-side transfer exists but its card-side counterpart was not found."
        return True
    if not fresh:
        row.reconciliation_state = "unverified"
        row.reconciliation_note = "YNAB history is unavailable, so an existing transfer cannot be ruled out."
        return True
    if row.transfer_account_id:
        source = db.get(Account, row.transfer_account_id)
        if source is None or source.plan_id != plan.id or source.id == card.id or source.closed:
            row.transfer_state = "candidate"
            row.transfer_account_id = None
            row.reconciliation_note = "The chosen transfer account is not valid for this plan."
            return True
        if transfer_payee_for(db, plan.id, source) is None:
            row.transfer_state = "candidate"
            row.reconciliation_state = "conflict"
            row.reconciliation_note = f"No YNAB transfer payee exists for {source.name}."
            return True
        row.transfer_state = "resolved"
        row.reconciliation_state = rec.reconciliation_state = "new"
        row.reconciliation_note = f"Will create one transfer from {source.name}."
        return True
    row.transfer_state = "candidate"
    row.reconciliation_state = "pending"
    row.reconciliation_note = "Repayment: choose the account it was paid from."
    return True


def suggest_transfer_accounts(db: Session, plan_id: str, card_account_id: str) -> List[str]:
    """Local account IDs that previously transferred into this card account."""
    card = db.get(Account, card_account_id)
    if card is None:
        return []
    return sorted({
        t.account_id for t in db.query(Transaction).filter(
            Transaction.plan_id == plan_id, Transaction.deleted == False,  # noqa: E712
            Transaction.transfer_account_ynab_id == card.ynab_account_id).all()
    })


def block_reason(row: ImportRow) -> Optional[str]:
    """Why a row cannot be written right now (None when writable)."""
    if row.write_state in COMPLETED:
        return "Already handled."
    if row.transfer_state in ("candidate", "ambiguous"):
        return row.reconciliation_note or "Repayment routing is unresolved."
    if row.reconciliation_state not in WRITABLE_RECONCILIATION:
        return row.reconciliation_note or f"Reconciliation required ({row.reconciliation_state})."
    return None
