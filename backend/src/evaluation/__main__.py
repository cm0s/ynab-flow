"""CLI: ``python -m src.evaluation --export PATH`` or ``python -m src.evaluation --synced PLAN_ID``."""

import argparse
import sys
from pathlib import Path

from src.evaluation import EvaluationInputError, evaluate, load_export, load_synced, report
from src.evaluation.runner import RULE_FIELDS, stderr_progress


def _production_rules(plan_id=None):
    """Read the user's current rules (read-only) from the configured database."""
    from src.database import SessionLocal
    from src.models import Plan, Rule
    with SessionLocal() as db:
        plan_id = plan_id or (db.query(Plan.id).order_by(Plan.id).first() or [None])[0]
        rules = db.query(Rule).filter(Rule.plan_id == plan_id).order_by(Rule.priority.desc(), Rule.id).all()
        return [{k: getattr(r, k) for k in RULE_FIELDS} for r in rules]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.evaluation", description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--export", type=Path, help="YNAB plan export (.zip) or its Register.csv")
    source.add_argument("--synced", metavar="PLAN_ID", help="evaluate the synced local history of this plan")
    parser.add_argument("--cutoff", help="first evaluation date, YYYY-MM-DD (default: 80%% of rows train)")
    parser.add_argument("--with-rules", action="store_true", help="also report a variant using your current rules")
    parser.add_argument("--max-rows", type=int, help="evaluate only the first N evaluation rows (quick check)")
    parser.add_argument("--out-dir", type=Path, default=report.DEFAULT_OUT_DIR)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.export:
            rows, sha = load_export(args.export)
            name = args.export.name
        else:
            from src.database import SessionLocal
            with SessionLocal() as db:
                rows, sha = load_synced(db, args.synced)
            name = f"synced:{args.synced}"
        rules = _production_rules(args.synced) if args.with_rules else None
        result = evaluate(rows, input_name=name, input_sha256=sha, cutoff=args.cutoff, rules=rules,
                          max_rows=args.max_rows, progress=None if args.quiet else stderr_progress)
    except (EvaluationInputError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    path = report.write(result, args.out_dir)
    for variant, data in result["variants"].items():
        m = data["metrics"]
        print(f"{variant}: payee {report._pct(m['payee']['accuracy'])}, category {report._pct(m['category']['accuracy'])}, "
              f"auto-accept precision {report._pct(m['rows']['auto_accept_precision'])}")
    print(f"Report: {path} (and {path.with_suffix('.md').name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
