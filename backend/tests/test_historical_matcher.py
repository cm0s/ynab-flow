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
        ynab_transaction_id="t1", date="2025-01-15", amount=-45000,
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
            ynab_transaction_id=f"t-extra-{i}", date="2025-02-01", amount=-25000,
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


# --- Reliable import foundations: units, context and conflicts ---

def _add_txn(db, plan, n, memo, payee, category, account, amount_milli):
    db.add(Transaction(
        plan_id=plan.id, account_id=account.id, payee_id=payee.id if payee else None,
        category_id=category.id if category else None, ynab_transaction_id=f"x{n}",
        date="2026-01-01", amount=amount_milli, amount_milliunits=amount_milli, memo=memo,
    ))


def _world(db):
    plan = _seed(db)
    acct1 = db.query(Account).first()
    acct2 = Account(plan_id=plan.id, ynab_account_id="a2", name="Card", type="creditCard")
    groceries = db.query(Category).first()
    dining = Category(category_group_id=groceries.category_group_id, ynab_category_id="c2", name="Dining")
    db.add_all([acct2, dining])
    db.flush()
    return plan, acct1, acct2, db.query(Payee).first(), groceries, dining


def test_amount_units_equivalent_to_currency_amount(db):
    """CHF -50.00 from CSV and -50000 milliunits in history are the same amount."""
    plan = _seed(db)
    db.query(Transaction).update({"amount": -50000, "amount_milliunits": -50000})
    db.commit()
    m = HistoricalMatcher(db, plan.id)
    assert m._cache[0].amount == -50.0
    close = HistoricalMatcher._similarity_score("A", "A", -50.0, "", "A", "A", m._cache[0].amount, "")
    assert close == pytest.approx(0.4 + 0.3 + 0.1 + 0.1)  # sign and bucket match, no account bonus


def test_missing_account_ids_give_no_agreement_bonus():
    with_ids = HistoricalMatcher._similarity_score("A", "A", -5.0, "acc", "A", "A", -5.0, "acc")
    without = HistoricalMatcher._similarity_score("A", "A", -5.0, "", "A", "A", -5.0, "")
    assert with_ids - without == pytest.approx(0.1)


def test_three_to_two_category_conflict_stays_below_auto_acceptance(db):
    plan, acct1, _, payee, groceries, dining = _world(db)
    for i in range(3):
        _add_txn(db, plan, i, "SHOP X", payee, groceries, acct1, -10000)
    for i in range(2):
        _add_txn(db, plan, 10 + i, "SHOP X", payee, dining, acct1, -10000)
    db.commit()
    r = HistoricalMatcher(db, plan.id).match("SHOP X", "SHOP X", amount=-10.0, account_id=acct1.id)
    assert r.category == "Groceries" and r.category_confidence == pytest.approx(0.6)
    assert r.payee_confidence == 1.0           # payee is not in conflict
    assert r.conflict and r.confidence < 0.95 and "Conflicting" in r.explanation


def test_ties_are_deterministic_and_unconfident(db):
    plan, acct1, _, payee, groceries, dining = _world(db)
    _add_txn(db, plan, 1, "TIE", payee, dining, acct1, -1000)
    _add_txn(db, plan, 2, "TIE", payee, groceries, acct1, -1000)
    db.commit()
    r = HistoricalMatcher(db, plan.id).match("TIE", "TIE", amount=-1.0, account_id=acct1.id)
    assert r.category == "Dining" and r.category_confidence == pytest.approx(0.45)


def test_account_and_sign_context_select_evidence(db):
    plan, acct1, acct2, payee, groceries, dining = _world(db)
    _add_txn(db, plan, 1, "AMBIG", payee, groceries, acct1, -2000)
    _add_txn(db, plan, 2, "AMBIG", payee, dining, acct2, -2000)
    _add_txn(db, plan, 3, "AMBIG", payee, dining, acct1, 2000)  # refund on same account
    db.commit()
    m = HistoricalMatcher(db, plan.id)
    assert m.match("AMBIG", "AMBIG", -2.0, acct1.id).category == "Groceries"
    assert m.match("AMBIG", "AMBIG", -2.0, acct2.id).category == "Dining"
    assert m.match("AMBIG", "AMBIG", 2.0, acct1.id).category == "Dining"


def test_contextual_fallback_is_capped_below_auto_acceptance(db):
    plan, acct1, acct2, payee, groceries, _ = _world(db)
    for i in range(5):
        _add_txn(db, plan, i, "ONLYELSEWHERE", payee, groceries, acct1, -3000)
    db.commit()
    m = HistoricalMatcher(db, plan.id)
    same = m.match("ONLYELSEWHERE", "X", -3.0, acct1.id)
    other = m.match("ONLYELSEWHERE", "X", -3.0, acct2.id)
    refund = m.match("ONLYELSEWHERE", "X", 3.0, acct1.id)
    assert same.confidence == 1.0 and same.context == "account+sign"
    assert other.context == "sign" and other.confidence <= 0.90
    assert refund.context == "any" and refund.confidence <= 0.70


def test_fuzzy_matches_never_reach_auto_acceptance():
    from src.historical_matcher import FUZZY_CAP, HistoricalMatcher, _CachedTxn, _trigrams
    matcher = HistoricalMatcher.__new__(HistoricalMatcher)
    matcher._category_names = {"c": "Groceries"}
    matcher._cache = [_CachedTxn("MIGROS LAUSANNE GARE", "MIGROS", -50.0, "a", "Migros", "c",
                                 _trigrams("MIGROS LAUSANNE GARE"))]
    m = matcher._fuzzy_match("MIGROS LAUSANNE GARE.", "MIGROS", -50.0, "a")
    assert m is not None and m.confidence == FUZZY_CAP < 0.95


def test_fewer_than_three_agreeing_examples_stay_below_auto_acceptance():
    from src.historical_matcher import _target_confidence
    assert _target_confidence(["A"]) == ("A", 0.9, False)
    assert _target_confidence(["A", "A"]) == ("A", 0.94, False)        # was 0.95
    assert _target_confidence(["A", "A", "A"]) == ("A", 1.0, False)
