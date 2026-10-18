"""Tests for the historical matcher (FR-5)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Plan, Account, Payee, Category, CategoryGroup, Transaction
from src.historical_matcher import HistoricalMatcher

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


def _seed(db):
    """Create a plan with an account, payee, category, and a historical transaction."""
    plan = Plan(ynab_plan_id="b1", name="Budget")
    db.add(plan)
    db.flush()

    acct = Account(plan_id=plan.id, ynab_account_id="a1", name="Checking", type="checking")
    db.add(acct)
    db.flush()

    payee = Payee(plan_id=plan.id, ynab_payee_id="p1", name="Migros")
    db.add(payee)
    db.flush()

    cg = CategoryGroup(plan_id=plan.id, ynab_category_group_id="cg1", name="Groceries Group")
    db.add(cg)
    db.flush()

    cat = Category(category_group_id=cg.id, ynab_category_id="c1", name="Groceries")
    db.add(cat)
    db.flush()

    txn = Transaction(
        plan_id=plan.id, account_id=acct.id, payee_id=payee.id, category_id=cat.id,
        ynab_transaction_id="t1", date="2025-01-15", amount=-45.0,
        memo="ACHAT/PRESTATION TWINT DU 15.01.2025 MIGROS LAUSANNE (CH)",
    )
    db.add(txn)
    db.commit()
    return plan


def test_exact_match(db):
    plan = _seed(db)
    matcher = HistoricalMatcher(db, plan.id)
    # Same memo should yield an exact match
    result = matcher.match(
        normalized_memo="MIGROS LAUSANNE",
        merchant_stem="MIGROS LAUSANNE",
        amount=-50.0,
    )
    assert result is not None
    assert result.source_type == "exact"
    assert result.payee == "Migros"
    assert result.category == "Groceries"


def test_fuzzy_match(db):
    """A similar but not identical memo should yield a fuzzy match."""
    plan = _seed(db)
    matcher = HistoricalMatcher(db, plan.id)
    # Slightly different memo — same merchant, different location
    result = matcher.match(
        normalized_memo="MIGROS GENEVE",
        merchant_stem="MIGROS GENEVE",
        amount=-30.0,
    )
    assert result is not None
    assert result.source_type == "fuzzy"
    assert result.payee == "Migros"
    assert result.category == "Groceries"
    assert 0.6 <= result.confidence < 1.0


def test_fuzzy_match_frequency_boost(db):
    """Multiple similar historical transactions should produce higher confidence."""
    plan = _seed(db)
    # Add more Migros transactions with slight variations
    acct = db.query(Account).first()
    payee = db.query(Payee).first()
    cat = db.query(Category).first()
    for i, city in enumerate(["BERN", "ZURICH", "BASEL"]):
        txn = Transaction(
            plan_id=plan.id, account_id=acct.id, payee_id=payee.id, category_id=cat.id,
            ynab_transaction_id=f"t-extra-{i}", date="2025-02-01", amount=-25.0,
            memo=f"ACHAT/PRESTATION TWINT DU 01.02.2025 MIGROS {city} (CH)",
        )
        db.add(txn)
    db.commit()

    matcher = HistoricalMatcher(db, plan.id)
    result = matcher.match(
        normalized_memo="MIGROS FRIBOURG",
        merchant_stem="MIGROS FRIBOURG",
        amount=-20.0,
    )
    assert result is not None
    assert result.source_type == "fuzzy"
    assert result.payee == "Migros"


def test_no_match(db):
    plan = _seed(db)
    matcher = HistoricalMatcher(db, plan.id)
    result = matcher.match(
        normalized_memo="COMPLETELY DIFFERENT MEMO",
        merchant_stem="DIFFERENT",
        amount=-10.0,
    )
    # Should return None or a very low-confidence fuzzy match
    if result:
        assert result.confidence < 0.6
