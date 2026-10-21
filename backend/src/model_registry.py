"""Model versions: candidate training, held-out comparison, promotion, rollback and recovery.

A candidate never replaces the active model unless it is at least as accurate on
a recent held-out slice of trusted examples it was not trained on. Versions live in
``MODEL_DIR/<version id>/``; ``MODEL_DIR/ACTIVE`` names the active one. Files of a
pre-registry install stay in ``MODEL_DIR`` itself and are registered as ``legacy``.
"""

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src import ml_classifier
from src.categories import CategoryDirectory
from src.ml_classifier import MLClassifier
from src.models import TRUSTED_PROVENANCE, ModelVersion, Payee, Transaction
from src.normalizer import normalize

logger = logging.getLogger(__name__)

HOLDOUT_SHARE = 0.10
SMALL_HOLDOUT = 50
KEEP_RETIRED = 2
LEGACY_ID = "legacy"
TARGETS = ("payee", "category")


def _dir(version_id: str) -> Path:
    return ml_classifier.MODEL_DIR if version_id == LEGACY_ID else ml_classifier.MODEL_DIR / version_id


def _has_files(version_id: str) -> bool:
    d = _dir(version_id)
    return any((d / p.name).exists() for p in (ml_classifier.PAYEE_MODEL_PATH, ml_classifier.CATEGORY_MODEL_PATH))


def _point_to(version_id: Optional[str]) -> None:
    root = ml_classifier.MODEL_DIR
    root.mkdir(parents=True, exist_ok=True)
    pointer = root / ml_classifier.ACTIVE_POINTER
    if version_id and version_id != LEGACY_ID:
        pointer.write_text(version_id)
    elif pointer.exists():
        pointer.unlink()


def active_version(db: Session) -> Optional[ModelVersion]:
    return db.query(ModelVersion).filter(ModelVersion.status == "active").first()


def describe(v: ModelVersion) -> dict:
    return {
        "id": v.id, "status": v.status, "plan_id": v.plan_id, "note": v.note,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "activated_at": v.activated_at.isoformat() if v.activated_at else None,
        "training": json.loads(v.training_json or "{}"), "excluded": json.loads(v.exclusions_json or "{}"),
        "comparison": json.loads(v.comparison_json or "{}"), "files_present": _has_files(v.id),
    }


def list_versions(db: Session) -> List[dict]:
    return [describe(v) for v in db.query(ModelVersion).order_by(ModelVersion.created_at.desc()).all()]


# ---------------------------------------------------------------------------
# Held-out comparison
# ---------------------------------------------------------------------------

def _trusted_examples(db: Session, plan_id: str) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
    """(date, normalized memo, payee name, category id) of learnable transactions, oldest first."""
    rows = (
        db.query(Transaction.date, Transaction.memo, Payee.name, Transaction.category_id)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .filter(Transaction.plan_id == plan_id, Transaction.deleted == False,  # noqa: E712
                Transaction.provenance.in_(TRUSTED_PROVENANCE), Transaction.memo.isnot(None))
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    out = []
    for date, memo, payee, category_id in rows:
        text = normalize(memo or "").cleaned_memo
        if text and (payee or category_id):
            out.append((date, text, payee, category_id))
    return out


def holdout_cutoff(examples) -> Optional[str]:
    """First date of the most recent ~10% of examples, or None when history is too short to split."""
    if len(examples) < 10:
        return None
    dates = [e[0] for e in examples]
    cut = dates[int(len(dates) * (1 - HOLDOUT_SHARE))]
    return cut if cut > dates[0] else None


def score(model: MLClassifier, holdout, directory: CategoryDirectory) -> Dict[str, Optional[dict]]:
    """ML-layer accuracy per target on held-out examples (None when the model lacks that target)."""
    result: Dict[str, Optional[dict]] = {}
    for target in TARGETS:
        pipeline = model.payee_pipeline if target == "payee" else model.category_pipeline
        examples = [e for e in holdout if (e[2] if target == "payee" else e[3])]
        if pipeline is None or not examples:
            result[target] = None
            continue
        predicted = pipeline.predict([e[1] for e in examples])
        if target == "payee":
            correct = sum(p == e[2] for p, e in zip(predicted, examples))
        else:  # legacy models predict names; compare by resolved id
            correct = sum(directory.identify(p)[0] == e[3] for p, e in zip(predicted, examples))
        result[target] = {"examples": len(examples), "correct": int(correct),
                          "accuracy": round(correct / len(examples), 4)}
    return result


def decide(candidate: dict, active: Optional[dict]) -> Tuple[bool, str]:
    """Promote only if the candidate is not worse on any target the active model serves."""
    if active is None:
        return True, "No active model; the first successfully trained model is promoted."
    worse = []
    for target in TARGETS:
        a, c = active.get(target), candidate.get(target)
        if a is None:
            continue
        if c is None:
            worse.append(f"{target}: candidate has no model")
        elif c["accuracy"] < a["accuracy"]:
            worse.append(f"{target}: {c['accuracy']:.1%} < active {a['accuracy']:.1%}")
    if worse:
        return False, "Not promoted; worse on held-out examples (" + "; ".join(worse) + ")."
    return True, "Promoted; not worse than the active model on held-out examples."


# ---------------------------------------------------------------------------
# Training, promotion, rollback, recovery
# ---------------------------------------------------------------------------

def _activate(db: Session, version: ModelVersion) -> None:
    for v in db.query(ModelVersion).filter(ModelVersion.status == "active", ModelVersion.id != version.id).all():
        v.status = "retired"
    version.status, version.activated_at = "active", datetime.utcnow()
    _point_to(version.id)
    _prune(db, version.id)


def _prune(db: Session, active_id: str) -> None:
    """Keep the active version and the last KEEP_RETIRED retired versions on disk."""
    retired = (db.query(ModelVersion).filter(ModelVersion.status == "retired")
               .order_by(ModelVersion.activated_at.desc().nullslast(), ModelVersion.created_at.desc()).all())
    for v in retired[KEEP_RETIRED:]:
        if v.id != LEGACY_ID and v.id != active_id and _dir(v.id).exists():
            shutil.rmtree(_dir(v.id), ignore_errors=True)
            v.note = (v.note + " Files removed by retention.").strip()


def train_candidate(db: Session, plan_id: str) -> dict:
    """Train a candidate, compare with the active model on a recent holdout, then promote or reject."""
    examples = _trusted_examples(db, plan_id)
    cut = holdout_cutoff(examples)
    holdout = [e for e in examples if cut and e[0] >= cut]
    version = ModelVersion(id=uuid.uuid4().hex[:12], status="candidate", plan_id=plan_id)
    candidate = MLClassifier(model_dir=_dir(version.id))
    stats = candidate.train(db, plan_id, before_date=cut)
    if not stats["payee_trained"] and not stats["category_trained"]:
        shutil.rmtree(_dir(version.id), ignore_errors=True)
        return {"promoted": False, "version": None, "training": stats,
                "note": "Not enough labelled history to train."}

    directory = CategoryDirectory(db, plan_id)
    current = active_version(db)
    current_scores = None
    if current is not None and _has_files(current.id):
        current_scores = score(MLClassifier(model_dir=_dir(current.id)), holdout, directory)
    candidate_scores = score(candidate, holdout, directory)
    if current_scores is not None and not holdout:
        promoted, note = False, "Not promoted; history is too short to hold out recent examples for comparison."
    else:
        promoted, note = decide(candidate_scores, current_scores)
    if holdout and len(holdout) < SMALL_HOLDOUT:
        note += f" Warning: only {len(holdout)} held-out examples."
    comparison = {"holdout_from": cut, "holdout_examples": len(holdout),
                  "candidate": candidate_scores, "active": current_scores,
                  "active_version": current.id if current else None}

    if promoted:
        # Deploy a model trained on all trusted examples, not only those before the holdout
        stats = MLClassifier(model_dir=_dir(version.id)).train(db, plan_id)
        version.note = note + " Retrained on all trusted examples after promotion."
    else:
        shutil.rmtree(_dir(version.id), ignore_errors=True)
        version.status, version.note = "rejected", note
    version.training_json = json.dumps({k: v for k, v in stats.items() if k != "excluded"})
    version.exclusions_json = json.dumps(stats.get("excluded", {}))
    version.comparison_json = json.dumps(comparison)
    db.add(version)
    db.flush()
    if promoted:
        _activate(db, version)
    db.commit()
    return {"promoted": promoted, "version": describe(version), "training": stats, "note": version.note}


def restore(db: Session, version_id: str) -> dict:
    """Make a retired version active again (rollback)."""
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise LookupError("Model version not found.")
    if version.status == "active":
        return describe(version)
    if version.status != "retired" or not _has_files(version.id):
        raise ValueError("Only a retired version whose files still exist can be restored.")
    _activate(db, version)
    version.note = (version.note + f" Restored {datetime.utcnow():%Y-%m-%d %H:%M} UTC.").strip()
    db.commit()
    return describe(version)


def ensure_active_model(db: Session, plan_id: Optional[str]) -> Optional[str]:
    """Startup: register legacy files, recover a missing active model, or train a first one."""
    current = active_version(db)
    if current is None:
        if _has_files(LEGACY_ID):
            legacy = db.get(ModelVersion, LEGACY_ID) or ModelVersion(id=LEGACY_ID, plan_id=plan_id)
            legacy.note = "Model files from before versioning; held-out metrics unknown."
            db.add(legacy)
            db.flush()
            _activate(db, legacy)
            db.commit()
            return LEGACY_ID
        if plan_id:
            result = train_candidate(db, plan_id)
            return result["version"]["id"] if result["promoted"] else None
        return None
    if not _has_files(current.id) and (current.plan_id or plan_id):
        # Ephemeral disks (e.g. Render) lose model files: rebuild the recorded version, never swap silently
        stats = MLClassifier(model_dir=_dir(current.id)).train(db, current.plan_id or plan_id)
        current.training_json = json.dumps({k: v for k, v in stats.items() if k != "excluded"})
        current.exclusions_json = json.dumps(stats.get("excluded", {}))
        current.note = (current.note + f" Files rebuilt from trusted examples {datetime.utcnow():%Y-%m-%d %H:%M} UTC.").strip()
        db.commit()
    _point_to(current.id)
    return current.id
