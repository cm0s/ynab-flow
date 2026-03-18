"""
FR-5: Historical Matching Engine

Reuses prior classified YNAB transactions to predict payee/category
for new transactions before falling back to ML.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, List, Tuple
from collections import Counter
from sqlalchemy.orm import Session
from src.models import Transaction, Payee, Category
from src.normalizer import normalize


@dataclass
class HistoricalMatch:
    payee: Optional[str]
    category: Optional[str]
    confidence: float          # 0.0 – 1.0
    source_type: str           # "exact" | "fuzzy"


class HistoricalMatcher:
    """
    Searches previously classified YNAB transactions for matches.
    Exact matches are tried first (FR-5.1), then fuzzy (FR-5.2–5.4).
    """

    def __init__(self, db: Session, plan_id: str):
        self.db = db
        self.plan_id = plan_id

    def match(
        self,
        normalized_memo: str,
        merchant_stem: str,
        amount: float = 0.0,
        account_id: str = "",
    ) -> Optional[HistoricalMatch]:
        """Try exact first, then fuzzy."""
        result = self._exact_match(normalized_memo)
        if result:
            return result
        return self._fuzzy_match(normalized_memo, merchant_stem, amount, account_id)

    # ------------------------------------------------------------------
    # FR-5.1  Exact historical lookup
    # ------------------------------------------------------------------

    def _exact_match(self, normalized_memo: str) -> Optional[HistoricalMatch]:
        """
        Find transactions whose memo normalizes to exactly the same string.
        Pick the most common payee/category pair.
        """
        txns = (
            self.db.query(Transaction)
            .filter(
                Transaction.plan_id == self.plan_id,
                Transaction.deleted == False,
                Transaction.memo.isnot(None),
            )
            .all()
        )

        matches: List[Tuple[Optional[str], Optional[str]]] = []
        for txn in txns:
            norm = normalize(txn.memo or "")
            if norm.cleaned_memo.upper() == normalized_memo.upper():
                payee_name = self._resolve_payee(txn.payee_id)
                cat_name = self._resolve_category(txn.category_id)
                if payee_name or cat_name:
                    matches.append((payee_name, cat_name))

        if not matches:
            return None

        # Most common pair wins
        most_common = Counter(matches).most_common(1)[0]
        payee, category = most_common[0]
        count = most_common[1]
        confidence = min(1.0, 0.85 + 0.05 * count)  # Boost with frequency

        return HistoricalMatch(
            payee=payee,
            category=category,
            confidence=confidence,
            source_type="exact",
        )

    # ------------------------------------------------------------------
    # FR-5.2–5.4  Fuzzy similarity lookup
    # ------------------------------------------------------------------

    def _fuzzy_match(
        self,
        normalized_memo: str,
        merchant_stem: str,
        amount: float,
        account_id: str,
    ) -> Optional[HistoricalMatch]:
        """
        Score historical transactions by weighted similarity.
        Features: memo similarity, stem similarity, amount sign, amount bucket, account.
        """
        txns = (
            self.db.query(Transaction)
            .filter(
                Transaction.plan_id == self.plan_id,
                Transaction.deleted == False,
                Transaction.memo.isnot(None),
            )
            .all()
        )

        scored: List[Tuple[float, str, Optional[str], Optional[str]]] = []

        for txn in txns:
            norm = normalize(txn.memo or "")
            score = self._similarity_score(
                normalized_memo, merchant_stem, amount, account_id,
                norm.cleaned_memo, norm.merchant_stem, txn.amount or 0, txn.account_id or "",
            )
            if score >= 0.6:  # Minimum similarity threshold
                payee_name = self._resolve_payee(txn.payee_id)
                cat_name = self._resolve_category(txn.category_id)
                if payee_name or cat_name:
                    scored.append((score, txn.id, payee_name, cat_name))

        if not scored:
            return None

        # Sort by score descending and pick best
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, _, best_payee, best_cat = scored[0]

        return HistoricalMatch(
            payee=best_payee,
            category=best_cat,
            confidence=round(best_score, 3),
            source_type="fuzzy",
        )

    @staticmethod
    def _similarity_score(
        memo1: str, stem1: str, amount1: float, account1: str,
        memo2: str, stem2: str, amount2: float, account2: str,
    ) -> float:
        """Weighted composite similarity (FR-5.3)."""
        # Memo similarity (weight 0.4)
        memo_sim = SequenceMatcher(None, memo1.upper(), memo2.upper()).ratio()

        # Stem similarity (weight 0.3)
        stem_sim = SequenceMatcher(None, stem1.upper(), stem2.upper()).ratio()

        # Amount sign match (weight 0.1)
        sign_match = 1.0 if (amount1 >= 0) == (amount2 >= 0) else 0.0

        # Amount bucket match (weight 0.1) — same order of magnitude
        bucket_match = 0.0
        if amount1 != 0 and amount2 != 0:
            ratio = abs(amount1) / abs(amount2) if abs(amount2) > 0 else 0
            if 0.5 <= ratio <= 2.0:
                bucket_match = 1.0
            elif 0.2 <= ratio <= 5.0:
                bucket_match = 0.5

        # Account match (weight 0.1)
        account_match = 1.0 if account1 == account2 else 0.0

        return (
            0.4 * memo_sim
            + 0.3 * stem_sim
            + 0.1 * sign_match
            + 0.1 * bucket_match
            + 0.1 * account_match
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_payee(self, payee_id: Optional[str]) -> Optional[str]:
        if not payee_id:
            return None
        payee = self.db.query(Payee).filter(Payee.id == payee_id).first()
        return payee.name if payee else None

    def _resolve_category(self, category_id: Optional[str]) -> Optional[str]:
        if not category_id:
            return None
        cat = self.db.query(Category).filter(Category.id == category_id).first()
        return cat.name if cat else None
