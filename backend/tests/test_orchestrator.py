"""Tests for the prediction orchestrator (FR-7)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Plan, Account, Payee, Category, CategoryGroup, Transaction, Rule
from src.orchestrator import Orchestrator

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _seed_with_rule(db):
    plan = Plan(ynab_plan_id="b1", name="Budget")
    db.add(plan)
    db.flush()

    rule = Rule(
        plan_id=plan.id, name="Migros Rule", priority=10,
        match_type="contains", pattern="MIGROS",
        assign_payee="Migros", assign_category="Groceries",
    )
    db.add(rule)
    db.commit()
    return plan


def test_rule_takes_precedence(db):
    """FR-7.1: Rules should win over everything else."""
    plan = _seed_with_rule(db)
    orch = Orchestrator(db, plan.id)
    result = orch.predict(memo="ACHAT/PRESTATION MIGROS LAUSANNE", amount=-50.0)

    assert result.source == "rule"
    assert result.payee == "Migros"
    assert result.category == "Groceries"
    assert result.confidence == 1.0


def test_unclassified_fallback(db, monkeypatch):
    """When nothing matches, source should be unclassified."""
    plan = Plan(ynab_plan_id="b2", name="Empty Budget")
    db.add(plan)
    db.commit()

    # Ensure the ML classifier doesn't interfere by returning an empty prediction
    from src.ml_classifier import MLClassifier, MLPrediction
    monkeypatch.setattr(MLClassifier, "predict", lambda self, memo: MLPrediction())

    orch = Orchestrator(db, plan.id)
    result = orch.predict(memo="UNKNOWN TRANSACTION XYZ", amount=-10.0)

    assert result.source == "unclassified"
    assert result.review_required is True


# --- Reliable import foundations: per-target merge and confidence ---

from src.ml_classifier import MLClassifier, MLPrediction
from src.orchestrator import AUTO_APPROVE_THRESHOLD


class StubML:
    def __init__(self, **kw):
        self.pred = MLPrediction(**kw)

    def predict(self, memo):
        return self.pred


def _world(db):
    plan = Plan(ynab_plan_id="w", name="W")
    db.add(plan)
    db.flush()
    acct = Account(plan_id=plan.id, ynab_account_id="a", name="Card", type="creditCard")
    group = CategoryGroup(plan_id=plan.id, ynab_category_group_id="g", name="G")
    payee = Payee(plan_id=plan.id, ynab_payee_id="p", name="Coop")
    db.add_all([acct, group, payee])
    db.flush()
    cat = Category(category_group_id=group.id, ynab_category_id="c", name="Groceries")
    db.add(cat)
    db.flush()
    return plan, acct, payee, cat


def _hist(db, plan, acct, payee, cat, n=3, memo="COOP CITY GENEVE"):
    for i in range(n):
        db.add(Transaction(plan_id=plan.id, account_id=acct.id, payee_id=payee.id if payee else None,
                           category_id=cat.id if cat else None, ynab_transaction_id=f"h{i}-{memo}",
                           date="2026-01-01", amount=-20000, amount_milliunits=-20000, memo=memo))


def _rule(db, plan, **kw):
    db.add(Rule(plan_id=plan.id, name=kw.pop("name", "R"), priority=1, match_type="contains",
                pattern=kw.pop("pattern", "COOP"), **kw))


def test_payee_only_rule_allows_category_inference(db):
    plan, acct, payee, cat = _world(db)
    _hist(db, plan, acct, payee, cat)
    _rule(db, plan, assign_payee="Coop Normalized")
    db.commit()
    r = Orchestrator(db, plan.id, StubML()).predict("COOP CITY GENEVE", -20.0, account_id=acct.id)
    assert (r.payee, r.payee_source, r.payee_confidence) == ("Coop Normalized", "rule", 1.0)
    assert (r.category, r.category_source) == ("Groceries", "exact_history")
    assert r.category_confidence == 1.0 and r.review_required is False


def test_category_only_rule_preserves_category_and_history_fills_payee(db):
    plan, acct, payee, cat = _world(db)
    _hist(db, plan, acct, payee, cat)
    _rule(db, plan, assign_category="Rule Category")
    db.commit()
    r = Orchestrator(db, plan.id, StubML()).predict("COOP CITY GENEVE", -20.0, account_id=acct.id)
    assert (r.category, r.category_source) == ("Rule Category", "rule")
    assert (r.payee, r.payee_source) == ("Coop", "exact_history")


def test_explicit_review_flag_cannot_be_cleared_by_high_confidence(db):
    plan, acct, payee, cat = _world(db)
    _hist(db, plan, acct, payee, cat)
    _rule(db, plan, flag_review=True)
    db.commit()
    r = Orchestrator(db, plan.id, StubML()).predict("COOP CITY GENEVE", -20.0, account_id=acct.id)
    assert r.confidence == 1.0 and r.review_flag and r.review_required


def test_explicit_ignore_terminates_and_stays_ignored(db):
    plan, acct, payee, cat = _world(db)
    _hist(db, plan, acct, payee, cat)
    _rule(db, plan, flag_ignore=True)
    db.commit()
    r = Orchestrator(db, plan.id, StubML(payee="Other", payee_confidence=0.99)).predict(
        "COOP CITY GENEVE", -20.0, account_id=acct.id)
    assert r.flag_ignore and r.payee is None and r.category is None


def test_strong_payee_weak_category_requires_review(db):
    plan = Plan(ynab_plan_id="m", name="M")
    db.add(plan)
    db.commit()
    ml = StubML(payee="Coop", payee_confidence=0.99, category="Groceries", category_confidence=0.20)
    r = Orchestrator(db, plan.id, ml).predict("SOMETHING NEW", -5.0)
    assert r.payee_confidence == 0.99 and r.category_confidence == 0.20
    assert r.confidence == 0.20 and r.review_required


def test_missing_category_blocks_auto_acceptance(db):
    plan = Plan(ynab_plan_id="n", name="N")
    db.add(plan)
    db.commit()
    r = Orchestrator(db, plan.id, StubML(payee="Coop", payee_confidence=0.99)).predict("NEW THING", -5.0)
    assert r.payee == "Coop" and r.category is None and r.confidence == 0.0 and r.review_required
    # a transfer (no category required) may be accepted on payee alone
    t = Orchestrator(db, plan.id, StubML(payee="Coop", payee_confidence=0.99)).predict(
        "NEW THING", -5.0, require_category=False)
    assert t.confidence == 0.99 and not t.review_required


def test_fuzzy_below_threshold_falls_through_to_ml(db):
    plan, acct, payee, cat = _world(db)
    _hist(db, plan, acct, payee, cat, n=1, memo="COOP CITY GENEVE")
    db.commit()
    ml = StubML(payee="ML Payee", payee_confidence=0.97, category="ML Cat", category_confidence=0.96)
    r = Orchestrator(db, plan.id, ml).predict("COMPLETELY UNRELATED TEXT HERE", -5.0, account_id=acct.id)
    assert r.source == "ml" and r.payee == "ML Payee" and r.category == "ML Cat"
    assert r.confidence >= AUTO_APPROVE_THRESHOLD and not r.review_required


def test_accepted_rows_meet_both_thresholds(db):
    plan, acct, payee, cat = _world(db)
    _hist(db, plan, acct, payee, cat, n=2)  # 0.95 each: just meets threshold
    db.commit()
    r = Orchestrator(db, plan.id, StubML()).predict("COOP CITY GENEVE", -20.0, account_id=acct.id)
    assert not r.review_required
    assert min(r.payee_confidence, r.category_confidence) >= AUTO_APPROVE_THRESHOLD
    _hist(db, plan, acct, payee, cat, n=1, memo="ONCE ONLY")
    db.commit()
    r1 = Orchestrator(db, plan.id, StubML()).predict("ONCE ONLY", -20.0, account_id=acct.id)
    assert r1.confidence == pytest.approx(0.9) and r1.review_required


def _seed_category_history(db, n=3):
    plan = Plan(ynab_plan_id="ids", name="Ids")
    db.add(plan)
    db.flush()
    acc = Account(plan_id=plan.id, ynab_account_id="ya", name="Post", type="checking")
    group = CategoryGroup(plan_id=plan.id, ynab_category_group_id="yg", name="Everyday")
    payee = Payee(plan_id=plan.id, ynab_payee_id="yp", name="Migros")
    db.add_all([acc, group, payee])
    db.flush()
    cat = Category(category_group_id=group.id, ynab_category_id="yc", name="Groceries")
    other = Category(category_group_id=group.id, ynab_category_id="yo", name="Transport")
    db.add_all([cat, other])
    db.flush()
    for i in range(n):
        db.add(Transaction(plan_id=plan.id, account_id=acc.id, payee_id=payee.id, category_id=cat.id,
                           ynab_transaction_id=f"t{i}", date="2026-01-0" + str(i + 1), amount=-45000,
                           amount_milliunits=-45000, memo="MIGROS LAUSANNE"))
    for i in range(n):
        db.add(Transaction(plan_id=plan.id, account_id=acc.id, payee_id=payee.id, category_id=other.id,
                           ynab_transaction_id=f"o{i}", date="2026-01-1" + str(i), amount=-3800,
                           amount_milliunits=-3800, memo="SBB MOBILE TICKET"))
    db.commit()
    return plan, acc, cat


def test_renamed_category_keeps_history_and_model_predictions(db, tmp_path):
    from src.ml_classifier import MLClassifier
    plan, acc, cat = _seed_category_history(db)
    ml = MLClassifier(model_dir=tmp_path)
    ml.train(db, plan.id)
    cat.name = "Food & groceries"                       # renamed in YNAB, then synced
    db.commit()
    orch = Orchestrator(db, plan.id, ml_classifier=ml)
    hist = orch.predict("MIGROS LAUSANNE", amount=-45.0, account_id=acc.id)
    assert (hist.category_id, hist.category, hist.category_source) == (cat.id, "Food & groceries", "exact_history")
    # the model was trained before the rename and still predicts the same category id
    assert ml.predict("MIGROS LAUSANNE").category == cat.id
    model_only = Orchestrator(db, plan.id, ml_classifier=ml)
    model_only.history._exact_index.clear()
    model_only.history._cache.clear()
    via_ml = model_only.predict("MIGROS LAUSANN", amount=-45.0, account_id=acc.id)
    assert via_ml.category_source == "ml" and via_ml.category_id == cat.id and via_ml.category == "Food & groceries"


def test_rule_category_name_resolves_to_id_and_deleted_category_is_not_suggested(db, monkeypatch):
    from src.ml_classifier import MLClassifier, MLPrediction
    plan, acc, cat = _seed_category_history(db)
    db.add(Rule(plan_id=plan.id, name="r", priority=1, match_type="contains", pattern="COOP", assign_category="Groceries"))
    db.commit()
    monkeypatch.setattr(MLClassifier, "predict", lambda self, memo: MLPrediction())
    ruled = Orchestrator(db, plan.id).predict("COOP PRONTO", amount=-5.0)
    assert (ruled.category, ruled.category_id) == ("Groceries", cat.id)
    cat.deleted = True
    db.commit()
    gone = Orchestrator(db, plan.id).predict("MIGROS LAUSANNE", amount=-45.0, account_id=acc.id)
    assert gone.category is None and gone.category_id is None and gone.review_required
