"""Receipt corrections: the user's values win over every later extraction.

The ordinary receipt columns hold the effective values, so matching, splits and
AI requests read them unchanged. ``extracted_json`` keeps the last extracted
value of each correctable field, and ``user_fields_json`` names the fields the
user corrected. Every extraction (fetch, rescue, AI re-read) goes through
``apply_extraction``, which refreshes the snapshot but never writes a
user-owned field.
"""

import json
from datetime import date
from typing import Iterable, List, Optional

from src.models import Receipt

FIELDS = ("merchant", "purchase_date", "total_milliunits", "currency", "kind", "reference", "items")
CLASSIFICATION = "classification"          # the receipt / not-a-receipt decision, kept in ``status``
KINDS = ("purchase", "refund", "shipping", "payment")
NOT_RECEIPT = "not_receipt"
MAX_TEXT = 200


class CorrectionError(ValueError):
    """A correction that cannot be stored; the message says why in plain terms."""


def user_fields(receipt: Receipt) -> List[str]:
    try:
        value = json.loads(receipt.user_fields_json or "[]")
    except ValueError:
        return []
    return [f for f in value if isinstance(f, str)]


def extracted(receipt: Receipt) -> dict:
    try:
        value = json.loads(receipt.extracted_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def items(receipt: Receipt) -> List[dict]:
    try:
        value = json.loads(receipt.items_json or "[]")
    except ValueError:
        return []
    return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []


def _read(receipt: Receipt, field: str):
    if field == "items":
        return items(receipt)
    if field == "status":
        return receipt.status
    return getattr(receipt, field)


def _write(receipt: Receipt, field: str, value) -> None:
    if field == "items":
        receipt.items_json = json.dumps(value or [], ensure_ascii=False)
    elif field == "status":
        receipt.status = value
    else:
        setattr(receipt, field, value)


def _owned(receipt: Receipt, field: str, value=None) -> bool:
    if field == "status":
        # The user's decision only fixes which side it is on: a rescued receipt can still go from waiting
        # to read, but no extraction can turn it back into "not a receipt", or the reverse.
        if CLASSIFICATION not in user_fields(receipt):
            return False
        return (value == NOT_RECEIPT) != (receipt.status == NOT_RECEIPT)
    return field in user_fields(receipt)


def apply_extraction(receipt: Receipt, values: dict) -> None:
    """Record extracted values; columns the user corrected keep the user's value."""
    snapshot = extracted(receipt)
    for field, value in values.items():
        if field not in FIELDS and field != "status":
            continue
        snapshot[field] = value
        if not _owned(receipt, field, value):
            _write(receipt, field, value)
    receipt.extracted_json = json.dumps(snapshot, ensure_ascii=False)


def _mark(receipt: Receipt, field: str, owned: bool) -> None:
    fields = [f for f in user_fields(receipt) if f != field]
    if owned:
        fields.append(field)
    receipt.user_fields_json = json.dumps(fields)


def _iso_date(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise CorrectionError("Dates must be written as YYYY-MM-DD.") from exc


def _milliunits(value, what: str, allow_none: bool = True) -> Optional[int]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorrectionError(f"The {what} must be a whole number of milliunits.")
    if value <= 0:
        raise CorrectionError(f"The {what} must be a positive amount.")
    return value


def _text(value, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CorrectionError(f"The {what} must be text.")
    return value.strip()[:MAX_TEXT]


def _items(value) -> List[dict]:
    if not isinstance(value, list):
        raise CorrectionError("Items must be a list.")
    out = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise CorrectionError(f"Item {index} is not valid.")
        description = _text(item.get("description"), f"description of item {index}")
        if not description:
            raise CorrectionError(f"Item {index} needs a description.")
        quantity = item.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity <= 0:
            raise CorrectionError(f"The quantity of item {index} must be a positive number.")
        clean = {"description": description, "quantity": quantity,
                 "amount_milliunits": _milliunits(item.get("amount_milliunits"), f"amount of item {index}",
                                                  allow_none=False)}
        for key in ("category_id", "category_source"):           # item categories travel with the item
            if item.get(key):
                clean[key] = item[key]
        out.append(clean)
    return out


def clean_value(field: str, value):
    """Validate one corrected field; raises CorrectionError with the reason."""
    if field == "merchant":
        return _text(value, "merchant")
    if field == "reference":
        return _text(value, "reference")
    if field == "purchase_date":
        return _iso_date(value)
    if field == "total_milliunits":
        return _milliunits(value, "total")
    if field == "currency":
        text = _text(value, "currency").upper()
        if len(text) != 3 or not text.isalpha():
            raise CorrectionError("The currency must be a three-letter code such as CHF.")
        return text
    if field == "kind":
        if value not in KINDS:
            raise CorrectionError(f"The kind must be one of: {', '.join(KINDS)}.")
        return value
    if field == "items":
        return _items(value)
    raise CorrectionError(f"'{field}' cannot be corrected.")


def correct(receipt: Receipt, changes: dict) -> List[str]:
    """Store the user's values for these fields; nothing is changed when one of them is invalid."""
    cleaned = {field: clean_value(field, value) for field, value in changes.items()}
    for field, value in cleaned.items():
        _write(receipt, field, value)
        _mark(receipt, field, True)
    return list(cleaned)


def reset(receipt: Receipt, fields: Iterable[str]) -> None:
    """Return fields to their extracted value and to extraction ownership."""
    snapshot = extracted(receipt)
    for field in fields:
        if field == CLASSIFICATION:
            if "status" in snapshot:
                receipt.status = snapshot["status"]
            _mark(receipt, CLASSIFICATION, False)
            continue
        if field not in FIELDS:
            raise CorrectionError(f"'{field}' cannot be reset.")
        if field in snapshot:
            _write(receipt, field, snapshot[field])
        _mark(receipt, field, False)


def classify(receipt: Receipt, status: str) -> None:
    """The user's receipt / not-a-receipt decision; later fetches keep it."""
    receipt.status = status
    _mark(receipt, CLASSIFICATION, True)


def own_items(receipt: Receipt) -> None:
    """Freeze the item list (e.g. once the user categorized an item) so re-reads cannot renumber it."""
    _mark(receipt, "items", True)
