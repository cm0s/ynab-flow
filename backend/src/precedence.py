"""Evidence precedence per target (D7), shared by reclassify and AI suggestion.

user edit > rule > history ≥ 0.95 > AI with receipt evidence > other history
> AI without receipt > ML > unclassified

The pipeline prediction and the stored AI suggestion are merged per target
(payee, category). Suggestions that lose are kept in ``alternatives_json`` so
the review table can show them; a user edit is never replaced. Because the
merge reads the stored AI suggestion and the fresh (or recovered) pipeline
prediction, reclassify and AI suggestion can run in either order.
"""

import json
from typing import Dict, List, Optional

from src.ai.categorize import UNCERTAINTY_CONFIDENCE
from src.categories import CategoryDirectory
from src.models import ImportRow
from src.orchestrator import AUTO_APPROVE_THRESHOLD

TARGETS = ("payee", "category")
_RANK = {"rule": 1, "confident_history": 2, "ai_receipt": 3, "history": 4, "ai": 5, "ml": 6, "unclassified": 7}
_STRENGTH = {"rule": 0, "exact_history": 1, "fuzzy_history": 2, "ai": 3, "ml": 4, "unclassified": 5}


def rank(source: str, confidence: float, receipt_backed: bool = False) -> int:
    if source == "rule":
        return _RANK["rule"]
    if source in ("exact_history", "fuzzy_history"):
        return _RANK["confident_history"] if confidence >= AUTO_APPROVE_THRESHOLD else _RANK["history"]
    if source == "ai":
        return _RANK["ai_receipt"] if receipt_backed else _RANK["ai"]
    return _RANK.get(source, _RANK["unclassified"])


def alternatives(row: ImportRow) -> Dict[str, List[dict]]:
    try:
        data = json.loads(row.alternatives_json or "{}")
    except ValueError:
        data = {}
    return {t: list(data.get(t, [])) for t in TARGETS}


def _current(row: ImportRow, target: str) -> dict:
    return {
        "source": getattr(row, f"{target}_source") or "unclassified",
        "value": getattr(row, target),
        "category_id": row.category_id if target == "category" else None,
        "confidence": getattr(row, f"{target}_confidence") or 0.0,
        "explanation": getattr(row, f"{target}_explanation") or "",
    }


def pipeline_candidate(row: ImportRow, target: str) -> dict:
    """The rule/history/ML suggestion: the row's own values, or the one an AI suggestion displaced."""
    current = _current(row, target)
    if current["source"] != "ai":
        return current
    for alt in alternatives(row)[target]:
        if alt.get("source") != "ai":
            return alt
    return {"source": "unclassified", "value": None, "category_id": None, "confidence": 0.0, "explanation": ""}


def ai_candidate(row: ImportRow, target: str, directory: CategoryDirectory) -> Optional[dict]:
    if row.ai_status != "suggested":
        return None
    confidence = UNCERTAINTY_CONFIDENCE.get(row.ai_uncertainty or "", 0.5)
    try:
        receipt_backed = bool(json.loads(row.ai_evidence_json or "{}").get("receipt_backed"))
    except ValueError:
        receipt_backed = False
    explanation = f"AI ({row.ai_uncertainty or 'unknown'} uncertainty): {row.ai_explanation or ''}".strip()
    if target == "payee":
        if not row.ai_payee:
            return None
        value, category_id = row.ai_payee, None
    else:
        if not row.ai_category_id or directory.is_deleted(row.ai_category_id) or row.ai_category_id not in directory:
            return None
        value, category_id = directory.name(row.ai_category_id), row.ai_category_id
    return {"source": "ai", "value": value, "category_id": category_id, "confidence": confidence,
            "explanation": explanation, "receipt_backed": receipt_backed}


def user_edited(row: ImportRow, target: str) -> bool:
    if target == "payee":
        return (row.edited_payee or "") != (row.payee or "")
    return (row.edited_category_id or None) != (row.category_id or None) \
        or (row.edited_category or "") != (row.category or "")


def apply_precedence(row: ImportRow, directory: CategoryDirectory) -> None:
    """Merge pipeline and AI suggestions into the row's per-target values; keep the losers as alternatives."""
    alts = {}
    ai_won = False
    for target in TARGETS:
        edited = user_edited(row, target)
        candidates = [c for c in (pipeline_candidate(row, target), ai_candidate(row, target, directory))
                      if c and c.get("value")]
        if not candidates:
            alts[target] = []
            continue
        winner = min(candidates, key=lambda c: rank(c["source"], c["confidence"], c.get("receipt_backed", False)))
        _set(row, target, winner)
        if not edited:
            if target == "payee":
                row.edited_payee = winner["value"] or ""
            else:
                row.edited_category, row.edited_category_id = winner["value"] or "", winner["category_id"]
        ai_won = ai_won or (winner["source"] == "ai" and not edited)
        alts[target] = [{k: c.get(k) for k in ("source", "value", "category_id", "confidence", "explanation")}
                        for c in candidates if c is not winner and c["value"] != winner["value"]]
        if edited and winner["value"] != _edited_value(row, target):
            alts[target].insert(0, {k: winner.get(k) for k in ("source", "value", "category_id", "confidence",
                                                               "explanation")})
    row.alternatives_json = json.dumps(alts) if any(alts.values()) else None
    _finish(row)
    if ai_won and row.status == "accepted":
        row.status = "pending"          # AI suggestions always need review


def _edited_value(row: ImportRow, target: str):
    return row.edited_payee if target == "payee" else row.edited_category


def _set(row: ImportRow, target: str, candidate: dict) -> None:
    setattr(row, target, candidate["value"])
    setattr(row, f"{target}_source", candidate["source"])
    setattr(row, f"{target}_confidence", round(float(candidate["confidence"] or 0.0), 4))
    setattr(row, f"{target}_explanation", candidate.get("explanation") or "")
    if target == "category":
        row.category_id = candidate.get("category_id")


def _finish(row: ImportRow) -> None:
    """Aggregate confidence, source and review flag like the orchestrator does."""
    if row.flag_ignore:
        return
    present = [(row.payee_source, row.payee_confidence) if row.payee else ("unclassified", 0.0),
               (row.category_source, row.category_confidence) if row.category else ("unclassified", 0.0)]
    row.confidence = round(min(c for _, c in present), 4)
    sources = [s for s, _ in present if s != "unclassified"]
    row.source = min(sources, key=lambda s: _STRENGTH.get(s, 9)) if sources else "unclassified"
    row.explanation = " ".join(dict.fromkeys(e for e in (row.payee_explanation, row.category_explanation) if e)) \
        or row.explanation
    row.review_required = bool(row.review_flag) or row.confidence < AUTO_APPROVE_THRESHOLD \
        or "ai" in (row.payee_source, row.category_source)
