"""Conservative receipt ↔ import-row matching (D4).

Each (receipt, row) pair is scored from amount, date, merchant and card
signals. A pair is linked automatically only when it scores ≥ 0.75 and is the
unique mutual best (no other row ≥ 0.6 for the receipt, no other receipt
≥ 0.6 for the row); other pairs ≥ 0.45 are proposed for the user to choose.
Refunds match only inflows and purchases only outflows; shipping and payment
notices attach as supporting evidence to a linked purchase with the same
reference. Matching never changes a row's bank values or reconciliation state.
"""

import json
import re
from datetime import date
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.models import ImportBatch, ImportRow, Receipt, ReceiptMatch
from src.money import MoneyError, to_milliunits
from src.reconciliation import COMPLETED

W_AMOUNT, W_DATE, W_MERCHANT, W_CARD = 0.45, 0.25, 0.25, 0.05
AUTO_LINK, RIVAL, CANDIDATE = 0.75, 0.6, 0.45
WINDOW_DAYS = 30
MAX_SUBSET_ITEMS = 12
MATCHABLE = ("extracted", "needs_check", "candidate")
GENERIC_TOKENS = {"ag", "sa", "gmbh", "ltd", "inc", "online", "shop", "store", "www", "com", "the", "und", "and",
                  "zuerich", "zurich", "bern", "basel", "geneve", "lausanne", "twint", "paypal", "sumup"}


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower().replace("ü", "u").replace("ä", "a").replace("ö", "o"))
    return {w for w in words if len(w) >= 3 and w not in GENERIC_TOKENS and not w.isdigit()}


def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    try:
        return (date.fromisoformat(a) - date.fromisoformat(b)).days
    except (TypeError, ValueError):
        return None


def _evidence(row: ImportRow) -> dict:
    try:
        return json.loads(row.evidence_json or "{}")
    except ValueError:
        return {}


def _items(receipt: Receipt) -> List[dict]:
    try:
        return [i for i in json.loads(receipt.items_json or "[]") if isinstance(i.get("amount_milliunits"), int)]
    except (ValueError, AttributeError):
        return []


def item_subset(items: List[dict], target: int) -> Optional[List[dict]]:
    """Items whose amounts sum exactly to ``target`` (partial shipments); None when no subset fits."""
    items = items[:MAX_SUBSET_ITEMS]
    for size in range(1, len(items) + 1):
        for combo in combinations(items, size):
            if sum(i["amount_milliunits"] for i in combo) == target:
                return list(combo)
    return None


def score(receipt: Receipt, row: ImportRow) -> Tuple[float, Dict[str, float]]:
    """(score in [0, 1], per-signal contributions); 0 when the kinds are incompatible."""
    bank = row.amount_milliunits
    if bank is None:
        return 0.0, {}
    if (receipt.kind == "refund") != (bank > 0):
        return 0.0, {}
    signals: Dict[str, float] = {}
    target = abs(int(bank))
    evidence = _evidence(row)
    foreign_cur = (evidence.get("Foreign Currency") or "").strip().upper()
    total = receipt.total_milliunits
    if total is not None:
        same_currency = not receipt.currency or not row.currency or receipt.currency == row.currency
        if same_currency and total == target:
            signals["amount"] = W_AMOUNT
        elif foreign_cur and receipt.currency == foreign_cur:
            foreign = _foreign_milliunits(evidence.get("Amount in foreign currency"))
            if foreign is not None and abs(foreign) == total:
                signals["amount"] = W_AMOUNT
        elif not same_currency and target and abs(total - target) <= 0.02 * target:
            signals["amount"] = 0.25
        if "amount" not in signals and same_currency and total > target and item_subset(_items(receipt), target):
            signals["partial_amount"] = 0.30
    gap = _days(row.date, receipt.purchase_date)
    if gap is not None and -1 <= gap <= 10:
        signals["date"] = round(W_DATE * (1.0 if gap <= 3 else (10 - gap) / 7), 4)
    merchant = _tokens(receipt.merchant)
    bank_text = " ".join(filter(None, (row.original_memo, row.merchant_stem, evidence.get("Merchant", ""))))
    if merchant:
        overlap = len(merchant & _tokens(bank_text)) / len(merchant)
        if overlap >= 0.5:
            signals["merchant"] = round(W_MERCHANT * overlap, 4)
    card = re.sub(r"\D", "", evidence.get("Card number", ""))[-4:]
    if receipt.card_hint and card and receipt.card_hint == card:
        signals["card"] = W_CARD
    return round(min(1.0, sum(signals.values())), 4), signals


def _foreign_milliunits(text) -> Optional[int]:
    try:
        return to_milliunits(str(text or "").replace("'", "").strip())
    except MoneyError:
        return None


def matchable_rows(batch: ImportBatch) -> List[ImportRow]:
    return [r for r in batch.rows if r.write_state not in COMPLETED and r.transfer_state == "none"
            and r.amount_milliunits is not None]


def run_matching(db: Session, rows: Iterable[ImportRow]) -> dict:
    """Recompute automatic proposals and links for these rows; user decisions are kept."""
    rows = list(rows)
    if not rows:
        return {"proposed": 0, "linked": 0}
    row_ids = {r.id for r in rows}
    existing = db.query(ReceiptMatch).filter(ReceiptMatch.import_row_id.in_(row_ids)).all()
    for m in existing:
        if m.origin == "auto" and m.role == "primary":
            db.delete(m)
    db.flush()
    kept = {(m.receipt_id, m.import_row_id): m for m in existing if not (m.origin == "auto" and m.role == "primary")}
    decided_rows = {m.import_row_id for m in kept.values() if m.state == "linked" and m.role == "primary"}
    decided_receipts = {m.receipt_id for m in db.query(ReceiptMatch).filter(
        ReceiptMatch.state == "linked", ReceiptMatch.role == "primary").all()}

    dates = [r.date for r in rows]
    receipts = [rc for rc in db.query(Receipt).filter(Receipt.status.in_(MATCHABLE),
                                                       Receipt.kind.in_(("purchase", "refund"))).all()
                if rc.purchase_date and any(abs(_days(d, rc.purchase_date) or 999) <= WINDOW_DAYS for d in dates)]
    scores: Dict[Tuple[str, str], Tuple[float, dict]] = {}
    for receipt in receipts:
        for row in rows:
            value, signals = score(receipt, row)
            if value >= CANDIDATE and (receipt.id, row.id) not in kept:
                scores[(receipt.id, row.id)] = (value, signals)

    linked = 0
    for (receipt_id, row_id), (value, signals) in scores.items():
        rivals_for_receipt = [v for (rc, rw), (v, _) in scores.items() if rc == receipt_id and rw != row_id and v >= RIVAL]
        rivals_for_row = [v for (rc, rw), (v, _) in scores.items() if rw == row_id and rc != receipt_id and v >= RIVAL]
        auto = (value >= AUTO_LINK and not rivals_for_receipt and not rivals_for_row
                and row_id not in decided_rows and receipt_id not in decided_receipts)
        db.add(ReceiptMatch(receipt_id=receipt_id, import_row_id=row_id, score=value,
                            signals_json=json.dumps(signals), state="linked" if auto else "proposed",
                            origin="auto", role="primary"))
        if auto:
            linked += 1
            decided_rows.add(row_id)
            decided_receipts.add(receipt_id)
    db.flush()
    attach_supporting(db, row_ids)
    return {"proposed": len(scores) - linked, "linked": linked}


def attach_supporting(db: Session, row_ids: set) -> None:
    """Shipping and payment notices with the same reference support an existing purchase link."""
    linked = db.query(ReceiptMatch).filter(ReceiptMatch.import_row_id.in_(row_ids), ReceiptMatch.state == "linked",
                                           ReceiptMatch.role == "primary").all()
    for match in linked:
        reference = match.receipt.reference
        if not reference:
            continue
        notices = db.query(Receipt).filter(Receipt.reference == reference, Receipt.kind.in_(("shipping", "payment")),
                                           Receipt.status != "not_receipt").all()
        for notice in notices:
            exists = db.query(ReceiptMatch).filter_by(receipt_id=notice.id, import_row_id=match.import_row_id).first()
            if not exists:
                db.add(ReceiptMatch(receipt_id=notice.id, import_row_id=match.import_row_id, score=match.score,
                                    signals_json=json.dumps({"reference": 1.0}), state="linked", origin="auto",
                                    role="supporting"))
    db.flush()


def match_batches(db: Session, batches: Iterable[ImportBatch]) -> dict:
    totals = {"proposed": 0, "linked": 0}
    for batch in batches:
        result = run_matching(db, matchable_rows(batch))
        for key in totals:
            totals[key] += result[key]
    return totals


def match_active_batches(db: Session) -> dict:
    return match_batches(db, db.query(ImportBatch).filter(ImportBatch.status == "active").all())


def rematch_receipt(db: Session, receipt: Receipt) -> dict:
    """After a receipt correction: recompute matching for the active rows near its date (user decisions kept)."""
    when = receipt.purchase_date or receipt.message_date
    rows = [r for b in db.query(ImportBatch).filter(ImportBatch.status == "active").all() for r in matchable_rows(b)
            if when and abs(_days(r.date, when) or 999) <= WINDOW_DAYS]
    return run_matching(db, rows)


# ---------------------------------------------------------------------------
# User decisions and split suggestions
# ---------------------------------------------------------------------------

def decide(db: Session, row: ImportRow, receipt: Receipt, action: str) -> ReceiptMatch:
    """link | unlink | reject. Linking the same receipt to several rows is allowed (partial shipments)."""
    match = db.query(ReceiptMatch).filter_by(receipt_id=receipt.id, import_row_id=row.id).first()
    if match is None:
        value, signals = score(receipt, row)
        match = ReceiptMatch(receipt_id=receipt.id, import_row_id=row.id, score=value,
                             signals_json=json.dumps(signals), role="primary")
        db.add(match)
    if action == "link":
        if receipt.kind in ("shipping", "payment"):
            match.role = "supporting"
        else:
            for other in db.query(ReceiptMatch).filter(ReceiptMatch.import_row_id == row.id,
                                                       ReceiptMatch.receipt_id != receipt.id,
                                                       ReceiptMatch.state == "linked",
                                                       ReceiptMatch.role == "primary").all():
                other.state, other.origin = "proposed", "user"     # one primary receipt per row
        match.state = "linked"
    elif action == "unlink":
        match.state = "proposed"
    elif action == "reject":
        match.state = "rejected"
    else:
        raise ValueError("Invalid action. Use: link, unlink, reject.")
    match.origin = "user"
    db.flush()
    return match


def split_suggestion(receipt: Receipt, row: ImportRow) -> List[dict]:
    """Receipt items as split lines for this row (categories left for the user or the AI).

    Items that fit the row exactly are used (a subset for partial shipments). When
    the receipt total equals the bank amount but the items do not add up (discount,
    shipping fee), the difference goes into the largest line, so the lines always
    sum to the bank amount. Returns [] when no exact split can be suggested.
    """
    bank = row.amount_milliunits
    items = _items(receipt)
    if bank is None or len(items) < 2:
        return []
    target, sign = abs(int(bank)), (1 if bank > 0 else -1)
    chosen = items if sum(i["amount_milliunits"] for i in items) == target else item_subset(items, target)
    if chosen is None and receipt.total_milliunits == target:
        chosen = [dict(i) for i in items]
        gap = target - sum(i["amount_milliunits"] for i in chosen)
        largest = max(chosen, key=lambda i: i["amount_milliunits"])
        largest["amount_milliunits"] += gap
        largest["description"] = f"{largest['description']} (incl. difference to bank amount)"
        if any(i["amount_milliunits"] <= 0 for i in chosen):
            return []
    if not chosen or len(chosen) < 2:
        return []
    return [{"amount_milliunits": sign * i["amount_milliunits"], "category_id": "",
             "memo": i["description"][:200], "origin": "receipt"} for i in chosen]


def receipt_views(row: ImportRow) -> List[dict]:
    """Receipt evidence for the review table (rejected pairs are hidden)."""
    views = []
    for m in sorted(row.receipt_matches, key=lambda m: (m.state != "linked", -m.score)):
        if m.state == "rejected" or m.receipt is None:
            continue
        r = m.receipt
        views.append({
            "receipt_id": r.id, "state": m.state, "origin": m.origin, "role": m.role, "score": m.score,
            "signals": json.loads(m.signals_json or "{}"), "merchant": r.merchant, "kind": r.kind,
            "reference": r.reference, "purchase_date": r.purchase_date, "total_milliunits": r.total_milliunits,
            "currency": r.currency, "status": r.status, "items": _items(r),
            "mailboxes": sorted({s.mailbox for s in r.sources}),
            "split_suggestion": split_suggestion(r, row) if m.role == "primary" else [],
        })
    return views
