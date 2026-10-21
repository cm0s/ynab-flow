"""Offline, reproducible evaluation of payee/category predictions on historical data.

Nothing here writes to YNAB, the production database or the active models.
"""

from pathlib import Path
from typing import Callable, List, Optional, Sequence

from src.evaluation import metrics, report
from src.evaluation.data import EvalRow, EvaluationInputError, load_export, load_synced  # noqa: F401
from src.evaluation.runner import add_rules, build_world, predict_all, temporary_model_dir
from src.evaluation.split import split
from src.evaluation.treatment import duplicate_count, treat_all, treatment_counts


def evaluate(
    rows: Sequence[EvalRow],
    *,
    input_name: str,
    input_sha256: str,
    cutoff: Optional[str] = None,
    rules: Optional[List[dict]] = None,
    max_rows: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """Evaluate ``rows``; ``rules`` (when given) adds a separately labelled hindsight variant."""
    treated = treat_all(rows)
    parts = split(treated, cutoff)
    evaluation = parts.evaluation[:max_rows] if max_rows else parts.evaluation
    with temporary_model_dir() as tmp:
        world = build_world(parts.training, parts.evaluation, Path(tmp))
        try:
            variants = {"learned_only": {"note": "", "metrics": metrics.compute(predict_all(world, evaluation, progress))}}
            if rules is not None:
                count = add_rules(world, rules)
                variants["with_rules"] = {
                    "note": f"{report.HINDSIGHT} ({count} rules)",
                    "metrics": metrics.compute(predict_all(world, evaluation, progress)),
                }
            training_stats = dict(world.training_stats)
        finally:
            world.close()
    return {
        "config": {"input": input_name, "input_sha256": input_sha256, "cutoff": parts.cutoff,
                   "options": {"with_rules": rules is not None, "max_rows": max_rows},
                   "git_commit": report.git_commit()},
        "split": {**parts.ranges(), "evaluated_rows": len(evaluation)},
        "treatments": treatment_counts(treated),
        "input_rows": len(rows),
        "duplicates": duplicate_count(treated),
        "training": training_stats,
        "limitations": list(report.LIMITATIONS),
        "variants": variants,
    }
