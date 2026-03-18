"""Tests for the deterministic rule engine (FR-4)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Plan, Rule
from src.rule_engine import RuleEngine

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


@pytest.fixture()
def plan(db):
    p = Plan(ynab_plan_id="test-budget", name="Test Budget")
    db.add(p)
    db.commit()
    return p


def _add_rule(db, plan, **kwargs):
    defaults = dict(
        plan_id=plan.id,
        name="test-rule",
        priority=0,
        match_type="contains",
        pattern="MIGROS",
    )
    defaults.update(kwargs)
    rule = Rule(**defaults)
    db.add(rule)
    db.commit()
    return rule


def test_contains_match(db, plan):
    _add_rule(db, plan, pattern="MIGROS", assign_payee="Migros", assign_category="Groceries")
    eng = RuleEngine(db, plan.id)
    match = eng.evaluate("PAIEMENT MIGROS LAUSANNE")
    assert match is not None
    assert match.assign_payee == "Migros"
    assert match.assign_category == "Groceries"


def test_exact_match(db, plan):
    _add_rule(db, plan, match_type="exact", pattern="SBB MOBILE", assign_payee="SBB")
    eng = RuleEngine(db, plan.id)
    # Should NOT match a longer string
    assert eng.evaluate("SBB MOBILE BERN") is None
    # Should match exact
    assert eng.evaluate("SBB MOBILE") is not None


def test_regex_match(db, plan):
    _add_rule(db, plan, match_type="regex", pattern=r"COOP\s+(CITY|PRONTO)", assign_payee="Coop")
    eng = RuleEngine(db, plan.id)
    assert eng.evaluate("COOP CITY GENEVE") is not None
    assert eng.evaluate("COOP PRONTO ZH") is not None
    assert eng.evaluate("COOP SUPERMARKET") is None


def test_priority_ordering(db, plan):
    """Higher priority rule should win (FR-4.3)."""
    _add_rule(db, plan, name="low", priority=1, pattern="SUNRISE", assign_payee="Sunrise Low")
    _add_rule(db, plan, name="high", priority=10, pattern="SUNRISE", assign_payee="Sunrise High")
    eng = RuleEngine(db, plan.id)
    match = eng.evaluate("SUNRISE GMBH")
    assert match is not None
    assert match.rule_name == "high"
    assert match.assign_payee == "Sunrise High"


def test_amount_sign_filter(db, plan):
    _add_rule(db, plan, pattern="SALARY", amount_sign="positive", assign_payee="Employer")
    eng = RuleEngine(db, plan.id)
    assert eng.evaluate("SALARY DEPOSIT", amount=5000.0) is not None
    assert eng.evaluate("SALARY DEPOSIT", amount=-100.0) is None


def test_amount_range_filter(db, plan):
    _add_rule(db, plan, pattern="TRANSFER", amount_min=100.0, amount_max=500.0, assign_payee="Medium Transfer")
    eng = RuleEngine(db, plan.id)
    assert eng.evaluate("TRANSFER", amount=-200.0) is not None   # abs(200) in range
    assert eng.evaluate("TRANSFER", amount=-50.0) is None        # abs(50) < 100


def test_no_match_returns_none(db, plan):
    _add_rule(db, plan, pattern="MIGROS")
    eng = RuleEngine(db, plan.id)
    assert eng.evaluate("COOP LAUSANNE") is None


def test_flag_review(db, plan):
    _add_rule(db, plan, pattern="UNKNOWN", flag_review=True)
    eng = RuleEngine(db, plan.id)
    match = eng.evaluate("UNKNOWN MERCHANT")
    assert match is not None
    assert match.flag_review is True
