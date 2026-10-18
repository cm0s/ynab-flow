"""
FR-5: Historical Matching Engine

Reuses prior classified YNAB transactions to predict payee/category
for new transactions before falling back to ML.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, List, Tuple, Dict
from collections import Counter, defaultdict
from sqlalchemy.orm import Session
from src.models import Transaction, Payee, Category
from src.normalizer import normalize


@dataclass
class HistoricalMatch:
    payee: Optional[str]
    category: Optional[str]
    confidence: float          # 0.0 – 1.0
    source_type: str           # "exact" | "fuzzy"


@dataclass
class _CachedTxn:
    """Pre-computed data for a historical transaction."""
    cleaned_memo: str
    merchant_stem: str
    amount: float
    account_id: str
    payee_name: Optional[str]
    category_name: Optional[str]
    # Pre-computed for fast fuzzy filtering
    memo_trigrams: frozenset


def _trigrams(s: str) -> frozenset:
    """Extract character trigrams for cheap similarity pre-filtering."""
    s = s.upper()
    if len(s) < 3:
        return frozenset([s])
    return frozenset(s[i:i+3] for i in range(len(s) - 2))


class HistoricalMatcher:
    """
    Searches previously classified YNAB transactions for matches.
    Exact matches are tried first (FR-5.1), then fuzzy (FR-5.2–5.4).

    Pre-computes normalized memos and resolved names at init time
    so that repeated calls to match() are fast.
    """

    def __init__(self, db: Session, plan_id: str):
        self.db = db
        self.plan_id = plan_id
        self._cache: List[_CachedTxn] = []
        # Exact match lookup: upper(cleaned_memo) -> list of (payee, category) pairs
        self._exact_index: Dict[str, List[Tuple[Optional[str], Optional[str]]]] = defaultdict(list)
        self._build_cache()

    def _build_cache(self):
        """Load all historical transactions once and pre-compute normalized memos."""
        txns = (
            self.db.query(Transaction)
            .filter(
                Transaction.plan_id == self.plan_id,
                Transaction.deleted == False,
                Transaction.memo.isnot(None),
            )
            .all()
        )

        # Batch-resolve payee and category names
        payee_ids = {t.payee_id for t in txns if t.payee_id}
        cat_ids = {t.category_id for t in txns if t.category_id}

        payee_map = {}
        if payee_ids:
            for p in self.db.query(Payee).filter(Payee.id.in_(payee_ids)).all():
                payee_map[p.id] = p.name

        cat_map = {}
        if cat_ids:
            for c in self.db.query(Category).filter(Category.id.in_(cat_ids)).all():
                cat_map[c.id] = c.name

        for txn in txns:
            payee_name = payee_map.get(txn.payee_id)
            cat_name = cat_map.get(txn.category_id)
            if not payee_name and not cat_name:
                continue
            norm = normalize(txn.memo or "")
            memo_upper = norm.cleaned_memo.upper()

            # Build exact index
            self._exact_index[memo_upper].append((payee_name, cat_name))

            # Build fuzzy cache
            self._cache.append(_CachedTxn(
                cleaned_memo=norm.cleaned_memo,
                merchant_stem=norm.merchant_stem,
                amount=txn.amount or 0,
                account_id=txn.account_id or "",
                payee_name=payee_name,
                category_name=cat_name,
                memo_trigrams=_trigrams(norm.cleaned_memo),
            ))

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
        Pick the most common payee/category pair. O(1) lookup.
        """
        matches = self._exact_index.get(normalized_memo.upper())
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
        Uses trigram pre-filtering to avoid expensive SequenceMatcher on
        clearly dissimilar entries.
        """
        query_trigrams = _trigrams(normalized_memo)
        min_overlap = max(1, len(query_trigrams) // 5)  # At least 20% trigram overlap

        scored: List[Tuple[float, Optional[str], Optional[str]]] = []

        for ct in self._cache:
            # Cheap trigram pre-filter: skip if too few trigrams in common
            overlap = len(query_trigrams & ct.memo_trigrams)
            if overlap < min_overlap:
                continue

            score = self._similarity_score(
                normalized_memo, merchant_stem, amount, account_id,
                ct.cleaned_memo, ct.merchant_stem, ct.amount, ct.account_id,
            )
            if score >= 0.6:  # Minimum similarity threshold
                scored.append((score, ct.payee_name, ct.category_name))

        if not scored:
            return None

        # Sort by score descending and pick best
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_payee, best_cat = scored[0]

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
