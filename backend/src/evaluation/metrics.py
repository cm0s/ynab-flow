"""Quality metrics per target, with breakdowns, calibration bands and failures."""

import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from src.evaluation.runner import Outcome
from src.evaluation.treatment import EVALUATED, PAYEE_ONLY, TAG_HIDDEN, TAG_REFUND
from src.orchestrator import AUTO_APPROVE_THRESHOLD, REVIEW_THRESHOLD

BANDS = (("none", None, None), ("low", 0.0, REVIEW_THRESHOLD),
         ("medium", REVIEW_THRESHOLD, AUTO_APPROVE_THRESHOLD), ("high", AUTO_APPROVE_THRESHOLD, 1.01))
TOP_FAILURES = 25
TOP_CONFUSIONS = 15


def normalize_payee(name: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip().casefold()


def band(predicted, confidence: float) -> str:
    if not predicted:
        return "none"
    for name, low, high in BANDS[1:]:
        if low <= confidence < high:
            return name
    return "high"


def _target_view(o: Outcome, target: str) -> dict:
    r = o.row.row
    if target == "payee":
        predicted, actual = o.payee, r.payee
        correct = bool(predicted) and normalize_payee(predicted) == normalize_payee(actual)
        return {"predicted": predicted, "actual": actual, "correct": correct,
                "confidence": o.payee_confidence, "source": o.payee_source}
    return {"predicted": o.category_label, "actual": r.category_label,
            "correct": bool(o.category_label) and o.category_label == r.category_label,
            "confidence": o.category_confidence, "source": o.category_source}


def _rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def _summary(views: List[dict]) -> dict:
    n = len(views)
    correct = sum(v["correct"] for v in views)
    covered = sum(bool(v["predicted"]) for v in views)
    return {"rows": n, "correct": correct, "accuracy": _rate(correct, n),
            "coverage": _rate(covered, n), "unclassified": n - covered}


def _grouped(pairs) -> Dict[str, dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for key, view in pairs:
        groups[key].append(view)
    return {k: _summary(v) for k, v in sorted(groups.items())}


def target_metrics(outcomes: List[Outcome], target: str) -> dict:
    eligible = [o for o in outcomes if target == "payee" or o.row.treatment == EVALUATED]
    views = [(o, _target_view(o, target)) for o in eligible]
    plain = [v for _, v in views]
    wrong = [(o, v) for o, v in views if not v["correct"]]
    failures = sorted((x for x in wrong if x[1]["predicted"]), key=lambda x: (-x[1]["confidence"], x[0].row.row.line))
    confusions = Counter((v["actual"], v["predicted"]) for _, v in wrong if v["predicted"])
    return {
        **_summary(plain),
        "by_account": _grouped((o.row.row.account, v) for o, v in views),
        "by_source": _grouped((v["source"], v) for _, v in views),
        "by_band": _grouped((band(v["predicted"], v["confidence"]), v) for _, v in views),
        "subsets": {
            tag: _summary([v for o, v in views if tag in o.row.tags]) for tag in (TAG_REFUND, TAG_HIDDEN)
        },
        "confident_failures": [
            {"date": o.row.row.date, "account": o.row.row.account, "description": o.row.row.memo,
             "predicted": v["predicted"], "actual": v["actual"], "source": v["source"],
             "confidence": v["confidence"]}
            for o, v in failures[:TOP_FAILURES]
        ],
        "frequent_confusions": [
            {"actual": a, "predicted": p, "count": c} for (a, p), c in confusions.most_common(TOP_CONFUSIONS)
        ],
    }


def row_metrics(outcomes: List[Outcome]) -> dict:
    """Row-level workload: auto-accept precision and corrections a user would make."""
    def all_correct(o: Outcome) -> bool:
        ok = _target_view(o, "payee")["correct"]
        if o.row.treatment == EVALUATED:
            ok = ok and _target_view(o, "category")["correct"]
        return ok
    auto = [o for o in outcomes if not o.review_required]
    return {
        "rows": len(outcomes),
        "would_auto_accept": len(auto),
        "auto_accept_precision": _rate(sum(all_correct(o) for o in auto), len(auto)),
        "corrections_required": sum(not all_correct(o) for o in outcomes),
        "payee_only_rows": sum(o.row.treatment == PAYEE_ONLY for o in outcomes),
    }


def compute(outcomes: List[Outcome]) -> dict:
    return {"payee": target_metrics(outcomes, "payee"), "category": target_metrics(outcomes, "category"),
            "rows": row_metrics(outcomes)}
