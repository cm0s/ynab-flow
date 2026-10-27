"""Spending from receipt items (D6, D7), computed from the local database on request.

Counted receipts are purchases (and refunds, negatively) that were read as
receipts and not excluded; shipping and payment notices never count. A receipt
becomes lines (its items, plus an "Other" line when the items do not add up to
the total), and the lines are scaled exactly (largest remainder, integer
milliunits) to the amount actually counted:

- linked: the bank amount of its linked rows, in the plan currency;
- unlinked in the plan currency: its own total;
- unlinked in another currency: not counted, listed separately (no conversion).

No AI request or other external call is made here.
"""

from collections import Counter, defaultdict
from fractions import Fraction
from math import floor
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from src.categories import CategoryDirectory
from src.models import Account, ImportBatch, ImportRow, Receipt
from src.receipts.browse import receipt_date
from src.receipts.items import linked_rows, resolve, row_category
from src.receipts.review import CLASSIFICATION, user_fields

COUNTED_KINDS = ("purchase", "refund")
READ_STATUSES = ("extracted", "needs_check")
OTHER = "Other (fees, discounts)"
UNCATEGORIZED = "Uncategorized"
GROUPS = ("category", "merchant", "item")


def counted(receipt: Receipt) -> bool:
    """Receipts that count as spending: read purchases and refunds, not excluded."""
    if receipt.excluded or receipt.kind not in COUNTED_KINDS or receipt.status == "not_receipt":
        return False
    return receipt.status in READ_STATUSES or CLASSIFICATION in user_fields(receipt)


def plan_currency(db: Session, plan_id: str) -> str:
    """The plan's currency: the most common account currency, else the most common import row currency."""
    accounts = Counter(c for (c,) in db.query(Account.currency).filter(Account.plan_id == plan_id) if c)
    if accounts:
        return accounts.most_common(1)[0][0]
    rows = Counter(c for (c,) in db.query(ImportRow.currency).join(ImportBatch, ImportRow.batch_id == ImportBatch.id)
                   .filter(ImportBatch.plan_id == plan_id) if c)
    return rows.most_common(1)[0][0] if rows else ""


def scale(amounts: List[int], target: int) -> List[int]:
    """Scale integer amounts to sum exactly to ``target`` (largest remainder; amounts may be negative).

    Amounts that add up to 0 (items read without prices) share the target equally.
    """
    if not amounts:
        return []
    if sum(amounts) == 0:
        amounts = [1] * len(amounts)
    total = sum(amounts)
    exact = [Fraction(a * target, total) for a in amounts]
    floors = [floor(x) for x in exact]
    remainder = target - sum(floors)
    order = sorted(range(len(amounts)), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def _line(receipt: Receipt, description: str, amount: int, category_id: Optional[str], source: str,
          directory: CategoryDirectory, index: Optional[int] = None) -> dict:
    return {"receipt_id": receipt.id, "merchant": receipt.merchant, "date": receipt_date(receipt),
            "description": description, "amount_milliunits": amount, "item_index": index,
            "category_id": category_id, "category_name": directory.name(category_id) if category_id else None,
            "category_source": source}


def receipt_lines(receipt: Receipt, directory: CategoryDirectory, currency: str) -> dict:
    """{"lines": [...]} in the plan currency, or {"uncounted": reason} when it cannot be counted."""
    rows = linked_rows(receipt)
    items = resolve(receipt, directory)
    amounts = [i["amount_milliunits"] for i in items if isinstance(i.get("amount_milliunits"), int)]
    total = receipt.total_milliunits if receipt.total_milliunits is not None else (sum(amounts) if amounts else None)
    if rows:
        target = sum(abs(r.amount_milliunits or 0) for r in rows)
    elif not receipt.currency or not currency or receipt.currency == currency:
        target = total
    else:
        return {"uncounted": f"Not linked to a bank row, and in {receipt.currency} rather than {currency}."}
    if not target:
        return {"uncounted": "No amount."}
    single = {row_category(r, directory) for r in rows}
    fallback = single.pop() if len(single) == 1 else None
    fallback_source = "row" if fallback else "uncategorized"
    lines = [_line(receipt, i.get("description") or "", i["amount_milliunits"], i["category_id"],
                   i["category_source"], directory, i["index"])
             for i in items if isinstance(i.get("amount_milliunits"), int)]
    if not lines:
        lines = [_line(receipt, receipt.merchant or receipt.subject or "", target, fallback, fallback_source, directory)]
    elif total is not None and sum(l["amount_milliunits"] for l in lines) not in (0, total):   # 0: items without prices
        lines.append(_line(receipt, OTHER, total - sum(l["amount_milliunits"] for l in lines), fallback,
                           fallback_source, directory))
    sign = -1 if receipt.kind == "refund" else 1
    for line, amount in zip(lines, scale([l["amount_milliunits"] for l in lines], target)):
        line["amount_milliunits"] = sign * amount
    return {"lines": lines}


def _key(line: dict, group: str) -> tuple:
    if group == "category":
        return (line["category_id"] or "", line["category_name"] or UNCATEGORIZED)
    if group == "merchant":
        label = line["merchant"] or "(no merchant)"
        return (" ".join(label.lower().split()), label)
    label = line["description"] or "(no description)"
    return (" ".join(label.lower().split()), label)


def report(db: Session, plan_id: str, date_from: Optional[str] = None, date_to: Optional[str] = None,
           group: str = "category", q: Optional[str] = None) -> dict:
    if group not in GROUPS:
        raise ValueError(f"Group by one of: {', '.join(GROUPS)}.")
    directory = CategoryDirectory(db, plan_id)
    currency = plan_currency(db, plan_id)
    receipts = [r for r in db.query(Receipt).filter(Receipt.kind.in_(COUNTED_KINDS)).all()
                if counted(r) and (d := receipt_date(r))
                and (not date_from or d >= date_from) and (not date_to or d <= date_to)]
    needle = " ".join((q or "").lower().split())
    lines, uncounted, uncategorized_ids = [], [], set()
    for receipt in receipts:
        result = receipt_lines(receipt, directory, currency)
        if "uncounted" in result:
            uncounted.append({"receipt_id": receipt.id, "merchant": receipt.merchant, "date": receipt_date(receipt),
                              "total_milliunits": receipt.total_milliunits, "currency": receipt.currency,
                              "reason": result["uncounted"]})
            continue
        for line in result["lines"]:
            if line["category_source"] == "uncategorized":
                uncategorized_ids.add(receipt.id)
            if needle and needle not in " ".join(f"{line['description']} {line['merchant']}".lower().split()):
                continue
            lines.append(line)

    months = sorted({(l["date"] or "")[:7] for l in lines})
    groups: Dict[tuple, dict] = {}
    for line in lines:
        key = _key(line, group)
        entry = groups.setdefault(key, {"key": key[0], "label": key[1], "total_milliunits": 0,
                                        "months": defaultdict(int), "lines": []})
        entry["total_milliunits"] += line["amount_milliunits"]
        entry["months"][(line["date"] or "")[:7]] += line["amount_milliunits"]
        entry["lines"].append(line)
    ordered = sorted(groups.values(), key=lambda g: -g["total_milliunits"])
    for entry in ordered:
        entry["months"] = dict(entry["months"])
        entry["lines"].sort(key=lambda l: (l["date"] or "", l["description"]), reverse=True)
    total = sum(l["amount_milliunits"] for l in lines)
    uncategorized = sum(l["amount_milliunits"] for l in lines if l["category_source"] == "uncategorized")
    return {
        "currency": currency, "group": group, "from": date_from, "to": date_to, "months": months,
        "total_milliunits": total, "uncategorized_milliunits": uncategorized,
        "uncategorized_share": round(uncategorized / total, 4) if total else 0.0,
        "receipts_counted": len(receipts) - len(uncounted), "uncounted": uncounted,
        "uncategorized_receipt_ids": sorted(uncategorized_ids), "groups": ordered,
    }
