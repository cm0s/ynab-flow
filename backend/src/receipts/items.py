"""Budget categories of receipt items (D5).

Only the user's and the AI's choices are stored on the item; the others are
resolved when asked, so later row edits and splits are reflected at once.
Order: user > linked row's split line for the item > linked row's single
category > AI > uncategorized.
"""

from typing import List, Optional

from src.categories import CategoryDirectory
from src.models import ImportRow, Receipt
from src.reconciliation import COMPLETED
from src.receipts.review import items as receipt_items

STORED_SOURCES = ("user", "ai")


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").lower().split())


def linked_rows(receipt: Receipt) -> List[ImportRow]:
    """Rows this receipt is the primary evidence for (several for partial shipments)."""
    return [m.row for m in receipt.matches if m.state == "linked" and m.role == "primary" and m.row is not None]


def row_category(row: ImportRow, directory: Optional[CategoryDirectory] = None) -> Optional[str]:
    """The category the row has or was pushed with (split rows have none); names only when no id was kept."""
    if row.splits:
        return None
    pushed = row.write_state in COMPLETED
    found = (row.sent_category_id if pushed else None) or row.edited_category_id or row.category_id
    if found or directory is None:
        return found
    return directory.resolve_name((row.sent_category if pushed else None) or row.edited_category or None)


def _split_categories(rows: List[ImportRow], items: List[dict]) -> List[Optional[str]]:
    """Per item, the category of the split line written for it: same description, or one line per item in order."""
    found: List[Optional[str]] = [None] * len(items)
    by_memo = {}
    for row in rows:
        for line in row.splits:
            by_memo.setdefault(_norm(line.memo), line.category_id)
    for index, item in enumerate(items):
        found[index] = by_memo.get(_norm(item.get("description")))
    for row in rows:
        lines = sorted(row.splits, key=lambda s: s.position)
        if len(lines) == len(items) and all(abs(line.amount_milliunits) == item.get("amount_milliunits")
                                            for line, item in zip(lines, items)):
            for index, line in enumerate(lines):
                found[index] = found[index] or line.category_id
    return found


def resolve(receipt: Receipt, directory: CategoryDirectory) -> List[dict]:
    """The receipt's items with ``category_id``, ``category_name``, ``category_source`` and an optional ``note``."""
    items = receipt_items(receipt)
    rows = linked_rows(receipt)
    split = _split_categories(rows, items)
    single = {row_category(r, directory) for r in rows}
    row_cat = single.pop() if len(single) == 1 else None
    out = []
    for index, item in enumerate(items):
        stored, stored_source = item.get("category_id"), item.get("category_source")
        candidates = [
            ("user", stored if stored_source == "user" else None),
            ("split", split[index]),
            ("row", row_cat),
            ("ai", stored if stored_source == "ai" else None),
        ]
        view = {**{k: v for k, v in item.items() if k not in ("category_id", "category_source")}, "index": index,
                "category_id": None, "category_name": None, "category_source": "uncategorized"}
        for source, category_id in candidates:
            if not category_id:
                continue
            if directory.is_deleted(category_id) or category_id not in directory:
                name = directory.name(category_id)
                view["note"] = (f"Category '{name}' was deleted in YNAB." if name
                                else "The category is not in this budget.")
                break
            view.update(category_id=category_id, category_name=directory.name(category_id), category_source=source)
            break
        out.append(view)
    return out
