"""Run the production classification pipeline against a disposable training database.

Only training-period rows are loaded into the temporary database and model
directory, so evaluation rows cannot reach history indexes or ML training.
"""

import hashlib
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.evaluation.treatment import EVALUATED, PAYEE_ONLY, Treated
from src.ml_classifier import MLClassifier
from src.models import Account, Base, Category, CategoryGroup, Payee, Plan, Rule, Transaction
from src.money import milliunits_to_units
from src.orchestrator import Orchestrator

PLAN_ID = "evaluation-plan"
RULE_FIELDS = ("name", "priority", "match_type", "pattern", "amount_sign", "amount_min", "amount_max",
               "account_filter", "category_filter", "assign_payee", "assign_category", "flag_review", "flag_ignore")


def _sid(prefix: str, text: str) -> str:
    return f"{prefix}-{hashlib.sha256(text.encode()).hexdigest()[:16]}"


@dataclass
class Outcome:
    row: Treated
    payee: Optional[str]
    payee_confidence: float
    payee_source: str
    category_label: Optional[str]
    category: Optional[str]
    category_confidence: float
    category_source: str
    review_required: bool


@dataclass
class TrainingWorld:
    """The disposable database holding only the training period."""
    db: Session
    model_dir: Path
    account_ids: Dict[str, str]
    label_by_category_id: Dict[str, str] = field(default_factory=dict)
    labels_by_name: Dict[str, List[str]] = field(default_factory=dict)
    ml: Optional[MLClassifier] = None
    training_stats: dict = field(default_factory=dict)

    def close(self):
        bind = self.db.get_bind()
        self.db.close()
        bind.dispose()


def build_world(training: Iterable[Treated], evaluation: Iterable[Treated], model_dir: Path) -> TrainingWorld:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Plan(id=PLAN_ID, ynab_plan_id=PLAN_ID, name="Evaluation"))

    training, evaluation = list(training), list(evaluation)
    # Accounts and categories are structural (known before any transaction); payees come from training only
    account_ids: Dict[str, str] = {}
    for t in training + evaluation:
        name = t.row.account
        if name not in account_ids:
            account_ids[name] = _sid("acc", name)
            db.add(Account(id=account_ids[name], plan_id=PLAN_ID, ynab_account_id=account_ids[name],
                           name=name, type="checking"))
    groups: Dict[str, str] = {}
    world = TrainingWorld(db=db, model_dir=model_dir, account_ids=account_ids)
    for t in training + evaluation:
        r = t.row
        if not r.category_label or _sid("cat", r.category_label) in world.label_by_category_id:
            continue
        gid = groups.get(r.category_group)
        if gid is None:
            gid = groups[r.category_group] = _sid("grp", r.category_group)
            db.add(CategoryGroup(id=gid, plan_id=PLAN_ID, ynab_category_group_id=gid, name=r.category_group))
        cid = _sid("cat", r.category_label)
        db.add(Category(id=cid, category_group_id=gid, ynab_category_id=cid, name=r.category))
        world.label_by_category_id[cid] = r.category_label
        world.labels_by_name.setdefault(r.category, []).append(r.category_label)

    payees: Dict[str, str] = {}
    for t in training:
        r = t.row
        pid = None
        if r.payee:
            pid = payees.get(r.payee)
            if pid is None:
                pid = payees[r.payee] = _sid("pay", r.payee)
                db.add(Payee(id=pid, plan_id=PLAN_ID, ynab_payee_id=pid, name=r.payee))
        cid = _sid("cat", r.category_label) if (t.treatment == EVALUATED and r.category_label) else None
        db.add(Transaction(
            id=f"txn-{r.line}", plan_id=PLAN_ID, account_id=account_ids[r.account], payee_id=pid,
            category_id=cid, ynab_transaction_id=f"txn-{r.line}", date=r.date, amount=float(r.milliunits),
            amount_milliunits=r.milliunits, memo=r.memo, cleared="reconciled", approved=True,
        ))
    db.commit()
    world.ml = MLClassifier(model_dir=model_dir)
    world.training_stats = world.ml.train(db, PLAN_ID)
    return world


def add_rules(world: TrainingWorld, rules: Iterable[dict]) -> int:
    count = 0
    for i, rule in enumerate(rules):
        world.db.add(Rule(id=f"rule-{i}", plan_id=PLAN_ID, **{k: rule.get(k) for k in RULE_FIELDS}))
        count += 1
    world.db.commit()
    return count


def _category_label(world: TrainingWorld, result) -> Optional[str]:
    category_id = getattr(result, "category_id", None)
    if category_id:
        return world.label_by_category_id.get(category_id, category_id)
    if not result.category:
        return None
    labels = world.labels_by_name.get(result.category, [])
    # A name shared by several labels cannot be attributed: keep the name so it never counts as correct
    return labels[0] if len(labels) == 1 else f"ambiguous name: {result.category}"


def predict_all(world: TrainingWorld, evaluation: List[Treated],
                progress: Optional[Callable[[int, int], None]] = None) -> List[Outcome]:
    orchestrator = Orchestrator(world.db, PLAN_ID, ml_classifier=world.ml)
    outcomes: List[Outcome] = []
    for i, t in enumerate(evaluation, start=1):
        r = t.row
        result = orchestrator.predict(
            r.memo, amount=milliunits_to_units(r.milliunits), account_name=r.account,
            account_id=world.account_ids[r.account], require_category=t.treatment != PAYEE_ONLY,
        )
        outcomes.append(Outcome(
            row=t, payee=result.payee, payee_confidence=result.payee_confidence if result.payee else 0.0,
            payee_source=result.payee_source if result.payee else "unclassified",
            category_label=_category_label(world, result), category=result.category,
            category_confidence=result.category_confidence if result.category else 0.0,
            category_source=result.category_source if result.category else "unclassified",
            review_required=result.review_required,
        ))
        if progress:
            progress(i, len(evaluation))
    return outcomes


def stderr_progress(done: int, total: int) -> None:
    if done == total or done % 250 == 0:
        print(f"  evaluated {done}/{total}", file=sys.stderr, flush=True)


def temporary_model_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="ynab-flow-eval-models-")
