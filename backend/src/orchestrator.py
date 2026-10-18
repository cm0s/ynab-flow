"""
FR-7: Prediction Orchestration

Chains all classification layers in the correct precedence:
  1. Deterministic rules
  2. Exact historical match
  3. Fuzzy historical match
  4. ML prediction
  5. Unclassified fallback
"""

from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session

from src.normalizer import normalize, NormalizationResult
from src.rule_engine import RuleEngine
from src.historical_matcher import HistoricalMatcher
from src.ml_classifier import MLClassifier


# Confidence thresholds (FR-6.3)
AUTO_APPROVE_THRESHOLD = 0.95
REVIEW_THRESHOLD = 0.75


@dataclass
class PredictionResult:
    original_memo: str
    cleaned_memo: str
    merchant_stem: str
    payee: Optional[str] = None
    category: Optional[str] = None
    confidence: float = 0.0
    source: str = "unclassified"     # rule | exact_history | fuzzy_history | ml | unclassified
    explanation: str = ""
    review_required: bool = True
    flag_ignore: bool = False


class Orchestrator:
    """
    Runs the full prediction pipeline (FR-7) for a single transaction.
    """

    def __init__(self, db: Session, plan_id: str, ml_classifier: Optional[MLClassifier] = None):
        self.db = db
        self.plan_id = plan_id
        self.rule_engine = RuleEngine(db, plan_id)
        self.history = HistoricalMatcher(db, plan_id)
        self.ml = ml_classifier or MLClassifier()

    def predict(
        self,
        memo: str,
        amount: float = 0.0,
        account_name: str = "",
        account_id: str = "",
        category_name: str = "",
    ) -> PredictionResult:
        """Execute the full FR-7 pipeline."""

        # Step 1 & 2: Normalize
        norm = normalize(memo)
        result = PredictionResult(
            original_memo=norm.original_memo,
            cleaned_memo=norm.cleaned_memo,
            merchant_stem=norm.merchant_stem,
        )

        # Step 3: Deterministic rules (FR-4)
        rule_match = self.rule_engine.evaluate(
            normalized_memo=norm.cleaned_memo,
            amount=amount,
            account_name=account_name,
            category_name=category_name,
        )
        if rule_match:
            result.payee = rule_match.assign_payee
            result.category = rule_match.assign_category
            result.confidence = 1.0
            result.source = "rule"
            result.explanation = f"Matched rule: {rule_match.rule_name}"
            result.review_required = rule_match.flag_review
            result.flag_ignore = rule_match.flag_ignore
            return result

        # Step 4: Exact historical match (FR-5.1)
        hist = self.history.match(
            normalized_memo=norm.cleaned_memo,
            merchant_stem=norm.merchant_stem,
            amount=amount,
            account_id=account_id,
        )
        if hist and hist.source_type == "exact":
            result.payee = hist.payee
            result.category = hist.category
            result.confidence = hist.confidence
            result.source = "exact_history"
            result.explanation = "Exact match found in transaction history."
            result.review_required = hist.confidence < AUTO_APPROVE_THRESHOLD
            return result

        # Step 5: Fuzzy historical match (FR-5.2)
        if hist and hist.source_type == "fuzzy" and hist.confidence >= REVIEW_THRESHOLD:
            result.payee = hist.payee
            result.category = hist.category
            result.confidence = hist.confidence
            result.source = "fuzzy_history"
            result.explanation = f"Fuzzy match (confidence: {hist.confidence:.0%})."
            result.review_required = hist.confidence < AUTO_APPROVE_THRESHOLD
            return result

        # Step 6: ML prediction (FR-6)
        ml_pred = self.ml.predict(norm.cleaned_memo)
        best_confidence = max(ml_pred.payee_confidence, ml_pred.category_confidence)

        if best_confidence > 0:
            result.payee = ml_pred.payee
            result.category = ml_pred.category
            result.confidence = best_confidence
            result.source = "ml"
            result.explanation = (
                f"ML prediction (payee: {ml_pred.payee_confidence:.0%}, "
                f"category: {ml_pred.category_confidence:.0%})."
            )
            result.review_required = best_confidence < AUTO_APPROVE_THRESHOLD
            return result

        # Step 7: Unclassified fallback
        result.source = "unclassified"
        result.explanation = "No rule, history, or ML prediction matched."
        result.review_required = True
        return result
