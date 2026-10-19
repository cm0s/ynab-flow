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


# Confidence thresholds (FR-6.3). Operational scores, not measured accuracy.
AUTO_APPROVE_THRESHOLD = 0.95
REVIEW_THRESHOLD = 0.75

_SOURCE_RANK = {"rule": 0, "exact_history": 1, "fuzzy_history": 2, "ml": 3, "unclassified": 4}


@dataclass
class PredictionResult:
    original_memo: str
    cleaned_memo: str
    merchant_stem: str
    payee: Optional[str] = None
    category: Optional[str] = None
    confidence: float = 0.0          # aggregate: minimum over required targets (missing = 0)
    source: str = "unclassified"     # strongest evidence source across targets
    explanation: str = ""
    review_required: bool = True
    flag_ignore: bool = False
    # Per-target confidence and provenance
    payee_confidence: float = 0.0
    category_confidence: float = 0.0
    payee_source: str = "unclassified"
    category_source: str = "unclassified"
    payee_explanation: str = ""
    category_explanation: str = ""
    review_flag: bool = False        # explicit review request (rule); never cleared by later evidence


class Orchestrator:
    """
    Runs the full prediction pipeline (FR-7) for a single transaction.

    Each stage fills only the targets (payee, category) that are still
    missing, so a payee-only rule still lets history/ML infer the category.
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
        require_category: bool = True,
    ) -> PredictionResult:
        """Execute the full FR-7 pipeline. ``amount`` is in currency units."""
        norm = normalize(memo)
        result = PredictionResult(
            original_memo=norm.original_memo,
            cleaned_memo=norm.cleaned_memo,
            merchant_stem=norm.merchant_stem,
        )

        # Deterministic rules (FR-4): lock only the targets they supply
        rule_match = self.rule_engine.evaluate(
            normalized_memo=norm.cleaned_memo,
            amount=amount,
            account_name=account_name,
            category_name=category_name,
        )
        if rule_match:
            note = f"Matched rule: {rule_match.rule_name}"
            if rule_match.assign_payee:
                self._set(result, "payee", rule_match.assign_payee, 1.0, "rule", note)
            if rule_match.assign_category:
                self._set(result, "category", rule_match.assign_category, 1.0, "rule", note)
            result.review_flag = bool(rule_match.flag_review)
            result.flag_ignore = bool(rule_match.flag_ignore)
            if result.flag_ignore:
                # Ignore terminates classification
                return self._finish(result, require_category)

        # Historical matching (FR-5): exact first, then fuzzy at/above the review threshold
        if self._missing(result, require_category):
            hist = self.history.match(
                normalized_memo=norm.cleaned_memo,
                merchant_stem=norm.merchant_stem,
                amount=amount,
                account_id=account_id,
            )
            if hist and (hist.source_type == "exact" or hist.confidence >= REVIEW_THRESHOLD):
                source = "exact_history" if hist.source_type == "exact" else "fuzzy_history"
                if hist.payee:
                    self._set(result, "payee", hist.payee, hist.payee_confidence, source, hist.explanation, fill=True)
                if hist.category:
                    self._set(result, "category", hist.category, hist.category_confidence, source, hist.explanation, fill=True)

        # ML (FR-6) fills anything still missing
        if self._missing(result, require_category):
            ml_pred = self.ml.predict(norm.cleaned_memo)
            if ml_pred.payee and ml_pred.payee_confidence > 0:
                self._set(result, "payee", ml_pred.payee, ml_pred.payee_confidence, "ml",
                          f"ML prediction ({ml_pred.payee_confidence:.0%}).", fill=True)
            if ml_pred.category and ml_pred.category_confidence > 0:
                self._set(result, "category", ml_pred.category, ml_pred.category_confidence, "ml",
                          f"ML prediction ({ml_pred.category_confidence:.0%}).", fill=True)

        return self._finish(result, require_category)

    # ------------------------------------------------------------------

    @staticmethod
    def _set(result, target, value, confidence, source, explanation, fill=False):
        if fill and getattr(result, target):
            return  # an earlier stage already supplied this target
        setattr(result, target, value)
        setattr(result, f"{target}_confidence", round(float(confidence), 4))
        setattr(result, f"{target}_source", source)
        setattr(result, f"{target}_explanation", explanation)

    @staticmethod
    def _missing(result, require_category):
        return not result.payee or (require_category and not result.category)

    @staticmethod
    def _finish(result, require_category):
        sources = [s for s, v in ((result.payee_source, result.payee), (result.category_source, result.category)) if v]
        result.source = min(sources, key=lambda s: _SOURCE_RANK.get(s, 9)) if sources else "unclassified"
        explanations = [e for e in (result.payee_explanation, result.category_explanation) if e]
        result.explanation = " ".join(dict.fromkeys(explanations)) or "No rule, history, or ML prediction matched."

        if result.flag_ignore:
            # Ignored rows leave the write queue; keep legacy rule semantics
            result.confidence = 1.0
            result.review_required = result.review_flag
            return result

        required = [result.payee_confidence if result.payee else 0.0]
        if require_category:
            required.append(result.category_confidence if result.category else 0.0)
        result.confidence = round(min(required), 4)
        result.review_required = (
            result.review_flag
            or result.confidence < AUTO_APPROVE_THRESHOLD
        )
        return result
