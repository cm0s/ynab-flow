"""
FR-6: ML Prediction Engine

Memory-efficient TF-IDF + stochastic logistic regression classifier for payee and category.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.pipeline import Pipeline
from sqlalchemy.orm import Session

from collections import Counter

from src.models import TRUSTED_PROVENANCE, Transaction, Payee, Category
from src.normalizer import normalize

logger = logging.getLogger(__name__)

# Where trained models are persisted (FR-6.5)
MODEL_DIR = Path(__file__).parent.parent / "models"
PAYEE_MODEL_PATH = MODEL_DIR / "payee_model.joblib"
CATEGORY_MODEL_PATH = MODEL_DIR / "category_model.joblib"
# Name of the file in MODEL_DIR naming the active version's subdirectory (absent: legacy files in MODEL_DIR)
ACTIVE_POINTER = "ACTIVE"


def active_model_dir() -> Path:
    """Directory of the active model version (see model_registry)."""
    pointer = MODEL_DIR / ACTIVE_POINTER
    try:
        name = pointer.read_text().strip()
    except OSError:
        name = ""
    return MODEL_DIR / name if name else MODEL_DIR


@dataclass
class MLPrediction:
    payee: Optional[str] = None
    category: Optional[str] = None     # category label: a local category id (legacy models: a name)
    payee_confidence: float = 0.0
    category_confidence: float = 0.0
    payee_alternatives: List[Tuple[str, float]] = field(default_factory=list)
    category_alternatives: List[Tuple[str, float]] = field(default_factory=list)


class MLClassifier:
    """
    Trains and predicts payee/category using TF-IDF + stochastic logistic regression.
    """

    def __init__(self, model_dir: Optional[Path] = None):
        """``model_dir`` overrides where models are loaded from and saved to (default: ``MODEL_DIR``)."""
        self.model_dir = Path(model_dir) if model_dir is not None else None
        self.payee_pipeline: Optional[Pipeline] = None
        self.category_pipeline: Optional[Pipeline] = None
        self._load_models()

    # ------------------------------------------------------------------
    # Training (FR-6.1, FR-6.4)
    # ------------------------------------------------------------------

    def train(self, db: Session, plan_id: str, before_date: Optional[str] = None) -> dict:
        """Train payee and category classifiers from trusted historical YNAB transactions.

        ``before_date`` (YYYY-MM-DD) limits training to earlier transactions, e.g. to hold out recent ones.
        """
        # Fetch only training columns; resolve labels in SQL rather than two
        # remote queries per transaction or retaining full ORM objects.
        rows = (
            db.query(Transaction.memo, Payee.name, Category.id, Transaction.provenance, Transaction.deleted)
            .outerjoin(Payee, Transaction.payee_id == Payee.id)
            .outerjoin(Category, Transaction.category_id == Category.id)
            .filter(Transaction.plan_id == plan_id, *([Transaction.date < before_date] if before_date else []))
            .yield_per(1000)
        )
        payee_memos, payee_labels = [], []
        category_memos, category_labels = [], []
        training_rows = 0
        excluded: Counter = Counter()
        for memo, payee_name, category_id, provenance, deleted in rows:
            # Only trusted examples are learned: never unreviewed machine guesses or split parents
            if deleted:
                excluded["deleted"] += 1
                continue
            if provenance not in TRUSTED_PROVENANCE:
                excluded[provenance or "unknown"] += 1
                continue
            text = normalize(memo or "").cleaned_memo
            if not text:
                excluded["no_description"] += 1
                continue
            training_rows += 1
            if payee_name:
                payee_memos.append(text)
                payee_labels.append(payee_name)
            if category_id:
                # Stable local category ids: renames do not split a category's examples
                category_memos.append(text)
                category_labels.append(category_id)

        stats = {"payee_trained": False, "category_trained": False, "training_rows": training_rows,
                 "payee_examples": len(payee_labels), "category_examples": len(category_labels),
                 "excluded": dict(sorted(excluded.items()))}
        if len(set(payee_labels)) >= 2:
            self.payee_pipeline = self._build_pipeline()
            self.payee_pipeline.fit(payee_memos, payee_labels)
            stats["payee_trained"] = True
            stats["payee_classes"] = len(set(payee_labels))

        if len(set(category_labels)) >= 2:
            self.category_pipeline = self._build_pipeline()
            self.category_pipeline.fit(category_memos, category_labels)
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
                dtype=np.float32,
            )),
            # Stochastic logistic regression keeps sparse float32 inputs and
            # avoids L-BFGS optimizer history scaling with classes × features.
            ("clf", SGDClassifier(
                loss="log_loss",
                max_iter=200,
                tol=1e-3,
                random_state=42,
                n_jobs=1,
            )),
        ])

    def _paths(self) -> Tuple[Path, Path, Path]:
        if self.model_dir is None:
            directory = active_model_dir()
            if directory == MODEL_DIR:
                return MODEL_DIR, PAYEE_MODEL_PATH, CATEGORY_MODEL_PATH
            return directory, directory / PAYEE_MODEL_PATH.name, directory / CATEGORY_MODEL_PATH.name
        return self.model_dir, self.model_dir / PAYEE_MODEL_PATH.name, self.model_dir / CATEGORY_MODEL_PATH.name

    def _save_models(self):
        directory, payee_path, category_path = self._paths()
        directory.mkdir(parents=True, exist_ok=True)
        if self.payee_pipeline:
            joblib.dump(self.payee_pipeline, payee_path)
            logger.info(f"Payee model saved to {payee_path}")
        if self.category_pipeline:
            joblib.dump(self.category_pipeline, category_path)
            logger.info(f"Category model saved to {category_path}")

    def _load_models(self):
        _, payee_path, category_path = self._paths()
        if payee_path.exists():
            self.payee_pipeline = joblib.load(payee_path)
        if category_path.exists():
            self.category_pipeline = joblib.load(category_path)

    @staticmethod
    def _resolve_name(db: Session, model_class, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        entity = db.query(model_class).filter(model_class.id == entity_id).first()
        return entity.name if entity else None
