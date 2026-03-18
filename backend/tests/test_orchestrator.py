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


def test_unclassified_fallback(db):
    """When nothing matches, source should be unclassified."""
    plan = Plan(ynab_plan_id="b2", name="Empty Budget")
    db.add(plan)
    db.commit()

    orch = Orchestrator(db, plan.id)
    result = orch.predict(memo="UNKNOWN TRANSACTION XYZ", amount=-10.0)

    assert result.source == "unclassified"
    assert result.review_required is True
