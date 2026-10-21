"""Stable category identity: local ``categories.id`` internally, names only for display."""

from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.models import Category, CategoryGroup


class CategoryDirectory:
    """All categories of a plan, loaded once, for id <-> name resolution."""

    def __init__(self, db: Session, plan_id: str):
        self._by_id: Dict[str, Category] = {}
        self._ids_by_name: Dict[str, List[str]] = {}
        rows = (
            db.query(Category)
            .join(CategoryGroup, Category.category_group_id == CategoryGroup.id)
            .filter(CategoryGroup.plan_id == plan_id)
            .all()
        )
        for c in rows:
            self._by_id[c.id] = c
            if not c.deleted:
                self._ids_by_name.setdefault(c.name, []).append(c.id)

    def __contains__(self, category_id: Optional[str]) -> bool:
        return bool(category_id) and category_id in self._by_id

    def name(self, category_id: Optional[str]) -> Optional[str]:
        c = self._by_id.get(category_id or "")
        return c.name if c else None

    def ynab_id(self, category_id: Optional[str]) -> Optional[str]:
        c = self._by_id.get(category_id or "")
        return c.ynab_category_id if c else None

    def is_deleted(self, category_id: Optional[str]) -> bool:
        c = self._by_id.get(category_id or "")
        return bool(c and c.deleted)

    def resolve_name(self, name: Optional[str]) -> Optional[str]:
        """The id of the single live category with this name, or None when absent or ambiguous."""
        ids = self._ids_by_name.get(name or "", [])
        return ids[0] if len(ids) == 1 else None

    def identify(self, label: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """(id, display name) for a label that is an id, or a legacy/rule name resolved uniquely."""
        if not label:
            return None, None
        if label in self._by_id:
            return label, self._by_id[label].name
        category_id = self.resolve_name(label)
        return category_id, label
