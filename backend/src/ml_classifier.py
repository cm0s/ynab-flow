"""
FR-6: ML Prediction Engine

Lightweight TF-IDF + LogisticRegression classifier for payee and category.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sqlalchemy.orm import Session

from src.models import Transaction, Payee, Category
from src.normalizer import normalize

logger = logging.getLogger(__name__)

# Where trained models are persisted (FR-6.5)
MODEL_DIR = Path(__file__).parent.parent / "models"
PAYEE_MODEL_PATH = MODEL_DIR / "payee_model.joblib"
CATEGORY_MODEL_PATH = MODEL_DIR / "category_model.joblib"


@dataclass
class MLPrediction:
    payee: Optional[str] = None
    category: Optional[str] = None
    payee_confidence: float = 0.0
    category_confidence: float = 0.0
    payee_alternatives: List[Tuple[str, float]] = field(default_factory=list)
    category_alternatives: List[Tuple[str, float]] = field(default_factory=list)


class MLClassifier:
    """
    Trains and predicts payee/category using TF-IDF + Logistic Regression.
    """

    def __init__(self):
        self.payee_pipeline: Optional[Pipeline] = None
        self.category_pipeline: Optional[Pipeline] = None
        self._load_models()

    # ------------------------------------------------------------------
    # Training (FR-6.1, FR-6.4)
    # ------------------------------------------------------------------

    def train(self, db: Session, plan_id: str) -> dict:
        """Train payee and category classifiers from historical YNAB transactions."""
        txns = (
            db.query(Transaction)
            .filter(
                Transaction.plan_id == plan_id,
                Transaction.deleted == False,
                Transaction.memo.isnot(None),
            )
            .all()
        )

        memos = []
        payee_labels = []
        category_labels = []

        for txn in txns:
            norm = normalize(txn.memo or "")
            text = norm.cleaned_memo
            if not text:
                continue

            payee_name = self._resolve_name(db, Payee, txn.payee_id)
            cat_name = self._resolve_name(db, Category, txn.category_id)

            if payee_name:
                memos.append(text)
                payee_labels.append(payee_name)

            if cat_name:
                if len(memos) > len(category_labels):
                    category_labels.append(cat_name)
                else:
                    # Separate entries for category-only
                    memos.append(text)
                    category_labels.append(cat_name)

        stats = {"payee_trained": False, "category_trained": False, "training_rows": len(txns)}

        # Need at least 2 distinct classes to train
        if len(set(payee_labels)) >= 2:
            self.payee_pipeline = self._build_pipeline()
            self.payee_pipeline.fit(memos[:len(payee_labels)], payee_labels)
            stats["payee_trained"] = True
            stats["payee_classes"] = len(set(payee_labels))

        if len(set(category_labels)) >= 2:
            self.category_pipeline = self._build_pipeline()
            # Use the corresponding memos for category
            cat_memos = memos[:len(category_labels)]
            self.category_pipeline.fit(cat_memos, category_labels)
            stats["category_trained"] = True
            stats["category_classes"] = len(set(category_labels))

        self._save_models()
        return stats

    # ------------------------------------------------------------------
    # Prediction (FR-6.2)
    # ------------------------------------------------------------------

    def predict(self, normalized_memo: str) -> MLPrediction:
        """Predict payee and category for a normalized memo."""
        result = MLPrediction()

        if self.payee_pipeline:
            proba = self.payee_pipeline.predict_proba([normalized_memo])[0]
            classes = self.payee_pipeline.classes_
            top_idx = proba.argsort()[::-1]

            result.payee = classes[top_idx[0]]
            result.payee_confidence = round(float(proba[top_idx[0]]), 4)
            result.payee_alternatives = [
                (classes[i], round(float(proba[i]), 4))
                for i in top_idx[:5]
            ]

        if self.category_pipeline:
            proba = self.category_pipeline.predict_proba([normalized_memo])[0]
            classes = self.category_pipeline.classes_
            top_idx = proba.argsort()[::-1]

            result.category = classes[top_idx[0]]
            result.category_confidence = round(float(proba[top_idx[0]]), 4)
            result.category_alternatives = [
                (classes[i], round(float(proba[i]), 4))
                for i in top_idx[:5]
            ]

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_pipeline() -> Pipeline:
        return Pipeline([
            ("tfidf", TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(2, 5),
                max_features=10000,
                sublinear_tf=True,
            )),
            ("clf", LogisticRegression(
                max_iter=1000,
                solver="lbfgs",
                C=1.0,
            )),
        ])

    def _save_models(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if self.payee_pipeline:
            joblib.dump(self.payee_pipeline, PAYEE_MODEL_PATH)
            logger.info(f"Payee model saved to {PAYEE_MODEL_PATH}")
        if self.category_pipeline:
            joblib.dump(self.category_pipeline, CATEGORY_MODEL_PATH)
            logger.info(f"Category model saved to {CATEGORY_MODEL_PATH}")

    def _load_models(self):
        if PAYEE_MODEL_PATH.exists():
            self.payee_pipeline = joblib.load(PAYEE_MODEL_PATH)
        if CATEGORY_MODEL_PATH.exists():
            self.category_pipeline = joblib.load(CATEGORY_MODEL_PATH)

    @staticmethod
    def _resolve_name(db: Session, model_class, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        entity = db.query(model_class).filter(model_class.id == entity_id).first()
        return entity.name if entity else None
