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
from src.models import TRUSTED_PROVENANCE, Transaction, Payee, Category
from src.normalizer import normalize

# Contextual fallback evidence is capped below auto-acceptance (0.95).
SAME_SIGN_FALLBACK_CAP = 0.90
ANY_SIGN_FALLBACK_CAP = 0.70
# Calibrated on household history (docs/classification-evaluation.md): fuzzy matches and exact
# matches backed by fewer than three agreeing examples were wrong too often to skip review.
BELOW_AUTO_ACCEPT = 0.94
FUZZY_CAP = BELOW_AUTO_ACCEPT
MIN_AGREEING_FOR_AUTO = 3


@dataclass
class HistoricalMatch:
    payee: Optional[str]
    category: Optional[str]    # display name of category_id
    confidence: float          # aggregate: min over the targets present; 0.0 – 1.0
    source_type: str           # "exact" | "fuzzy"
    payee_confidence: float = 0.0
    category_confidence: float = 0.0
    conflict: bool = False     # competing labels in the evidence set
    context: str = ""          # "account+sign" | "sign" | "any" | "" (fuzzy)
    evidence_count: int = 0
    amount_similarity: float = 0.0
    explanation: str = ""
    category_id: Optional[str] = None  # stable local category id


@dataclass
class _Entry:
    """One exact-index entry."""
    payee: Optional[str]
    category: Optional[str]    # local category id
    account_id: str
    sign: int
    amount: float


def _sign(amount: float) -> int:
    return 1 if amount >= 0 else -1


def _target_confidence(labels: List[str], sort_key=None) -> Tuple[Optional[str], float, bool]:
    """Winning label, share-times-frequency confidence, and whether labels conflict.

    confidence = winning share * min(1, 0.85 + 0.05 * winning_count), capped below
    auto-acceptance when fewer than MIN_AGREEING_FOR_AUTO examples agree; ties resolve
    deterministically to the smallest label under ``sort_key`` (default: the label).
    """
    if not labels:
        return None, 0.0, False
    counts = Counter(labels)
    top = max(counts.values())
    winner = sorted((label for label, n in counts.items() if n == top), key=sort_key)[0]
    share = top / len(labels)
    confidence = share * min(1.0, 0.85 + 0.05 * top)
    if top < MIN_AGREEING_FOR_AUTO:
        confidence = min(confidence, BELOW_AUTO_ACCEPT)
    return winner, round(confidence, 4), len(counts) > 1


@dataclass
class _CachedTxn:
    """Pre-computed data for a historical transaction."""
    cleaned_memo: str
    merchant_stem: str
    amount: float              # currency units
    account_id: str
    payee_name: Optional[str]
    category_id: Optional[str]
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
        # Exact match lookup: upper(cleaned_memo) -> entries with account/sign context
        self._exact_index: Dict[str, List[_Entry]] = defaultdict(list)
        self._category_names: Dict[str, str] = {}
        self._build_cache()

    def _category_key(self, category_id: str):
        """Order categories by display name, then id, so ties are stable across renames of others."""
        return (self._category_names.get(category_id, ""), category_id)

    def _build_cache(self):
        """Load all historical transactions once and pre-compute normalized memos."""
        txns = (
            self.db.query(Transaction)
            .filter(
                Transaction.plan_id == self.plan_id,
                Transaction.deleted == False,
                Transaction.memo.isnot(None),
                Transaction.provenance.in_(TRUSTED_PROVENANCE),
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

        if cat_ids:
            for c in self.db.query(Category).filter(Category.id.in_(cat_ids)).all():
                self._category_names[c.id] = c.name

        for txn in txns:
            payee_name = payee_map.get(txn.payee_id)
            cat_id = txn.category_id if txn.category_id in self._category_names else None
            if not payee_name and not cat_id:
                continue
            norm = normalize(txn.memo or "")
            memo_upper = norm.cleaned_memo.upper()

            # Currency units: prefer integer milliunits, else legacy milliunit column
            milli = txn.amount_milliunits if txn.amount_milliunits is not None else txn.amount
            amount_units = float(milli or 0) / 1000.0

            # Build exact index
            self._exact_index[memo_upper].append(
                _Entry(payee_name, cat_id, txn.account_id or "", _sign(amount_units), amount_units)
            )

            # Build fuzzy cache
            self._cache.append(_CachedTxn(
                cleaned_memo=norm.cleaned_memo,
                merchant_stem=norm.merchant_stem,
                amount=amount_units,
                account_id=txn.account_id or "",
                payee_name=payee_name,
                category_id=cat_id,
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
        result = self._exact_match(normalized_memo, amount, account_id)
        if result:
            return result
        return self._fuzzy_match(normalized_memo, merchant_stem, amount, account_id)

    # ------------------------------------------------------------------
    # FR-5.1  Exact historical lookup
    # ------------------------------------------------------------------

    def _exact_match(
        self, normalized_memo: str, amount: float = 0.0, account_id: str = "",
    ) -> Optional[HistoricalMatch]:
        """
        Find transactions whose memo normalizes to exactly the same string.
        Prefer same-account, same-sign evidence; broader evidence is a capped
        contextual fallback. Confidence is computed per target and reflects
        competing labels.
        """
        entries = self._exact_index.get(normalized_memo.upper())
        if not entries:
            return None

        sign = _sign(amount)
        same_sign = [e for e in entries if e.sign == sign]
        if account_id:
            scoped = [e for e in same_sign if e.account_id == account_id]
        else:
            scoped = same_sign  # no account context to compare against
        if scoped:
            chosen, context, cap = scoped, ("account+sign" if account_id else "sign"), 1.0
        elif same_sign:
            chosen, context, cap = same_sign, "sign", SAME_SIGN_FALLBACK_CAP
        else:
            chosen, context, cap = entries, "any", ANY_SIGN_FALLBACK_CAP

        payee, payee_conf, payee_conflict = _target_confidence([e.payee for e in chosen if e.payee])
        category, cat_conf, cat_conflict = _target_confidence(
            [e.category for e in chosen if e.category], sort_key=self._category_key)
        payee_conf = min(payee_conf, cap) if payee else 0.0
        cat_conf = min(cat_conf, cap) if category else 0.0
        present = [c for c, v in ((payee_conf, payee), (cat_conf, category)) if v]
        if not present:
            return None

        conflict = payee_conflict or cat_conflict
        similar = sum(
            1 for e in chosen
            if e.amount != 0 and amount != 0 and 0.5 <= abs(amount) / abs(e.amount) <= 2.0
        ) / len(chosen)
        explanation = f"Exact match in transaction history ({context} context, {len(chosen)} example(s))."
        if conflict:
            explanation += " Conflicting history: " + ", ".join(
                f"{label} x{n}" for label, n in Counter(
                    [self._category_names.get(e.category, e.category) if e.category else e.payee
                     for e in chosen]).most_common()) + "."
        if cap < 1.0:
            explanation += " Capped because no same-account evidence exists."
        return HistoricalMatch(
            payee=payee, category=self._category_names.get(category) if category else None,
            category_id=category, confidence=min(present), source_type="exact",
            payee_confidence=payee_conf, category_confidence=cat_conf, conflict=conflict,
            context=context, evidence_count=len(chosen), amount_similarity=round(similar, 3),
            explanation=explanation,
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
                scored.append((score, ct.payee_name, ct.category_id))

        if not scored:
            return None

        # Sort by score descending and pick best
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_payee, best_cat = scored[0]

        # Fuzzy evidence can suggest but never auto-accept on its own
        confidence = round(min(best_score, FUZZY_CAP), 3)
        return HistoricalMatch(
            payee=best_payee,
            category=self._category_names.get(best_cat) if best_cat else None,
            category_id=best_cat,
            confidence=confidence,
            source_type="fuzzy",
            payee_confidence=confidence if best_payee else 0.0,
            category_confidence=confidence if best_cat else 0.0,
            evidence_count=1,
            explanation=f"Fuzzy match (confidence: {confidence:.0%}).",
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

        # Account match (weight 0.1): missing account IDs never count as agreement
        account_match = 1.0 if (account1 and account2 and account1 == account2) else 0.0

        return (
            0.4 * memo_sim
            + 0.3 * stem_sim
            + 0.1 * sign_match
            + 0.1 * bucket_match
            + 0.1 * account_match
        )
