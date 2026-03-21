import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.main import app
from src.database import get_db
from src.models import Base, Plan, Rule

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_model_instantiation():
    plan = Plan(name="Test Plan", ynab_plan_id="123")
    assert plan.name == "Test Plan"


# ---- Rule CRUD API tests ---- #

from sqlalchemy.pool import StaticPool

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def test_client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def plan(db_session):
    p = Plan(ynab_plan_id="test-budget", name="Test Budget")
    db_session.add(p)
    db_session.commit()
    return p


def _create_rule(test_client, plan_id, **overrides):
    payload = {
        "plan_id": plan_id,
        "name": "Test Rule",
        "match_type": "contains",
        "pattern": "MIGROS",
        "priority": 0,
        "flag_review": False,
        "flag_ignore": False,
        **overrides,
    }
    resp = test_client.post("/rules", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_create_rule(test_client, plan):
    data = _create_rule(test_client, plan.id, name="Migros Rule", assign_payee="Migros")
    assert data["status"] == "success"
    assert data["rule_id"]


def test_list_rules(test_client, plan):
    _create_rule(test_client, plan.id, name="Rule A", priority=1)
    _create_rule(test_client, plan.id, name="Rule B", priority=10)
    resp = test_client.get(f"/rules?plan_id={plan.id}")
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) == 2
    # Ordered by priority desc
    assert rules[0]["name"] == "Rule B"
    assert rules[1]["name"] == "Rule A"


def test_update_rule(test_client, plan):
    created = _create_rule(test_client, plan.id, name="Old Name", pattern="OLD", assign_payee="OldPayee")
    rule_id = created["rule_id"]

    update_payload = {
        "plan_id": plan.id,
        "name": "New Name",
        "match_type": "exact",
        "pattern": "NEW PATTERN",
        "priority": 5,
        "assign_payee": "NewPayee",
        "assign_category": "NewCategory",
        "flag_review": False,
        "flag_ignore": False,
    }
    resp = test_client.put(f"/rules/{rule_id}", json=update_payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # Verify the rule was actually updated
    rules = test_client.get(f"/rules?plan_id={plan.id}").json()
    assert len(rules) == 1
    updated = rules[0]
    assert updated["name"] == "New Name"
    assert updated["match_type"] == "exact"
    assert updated["pattern"] == "NEW PATTERN"
    assert updated["assign_payee"] == "NewPayee"
    assert updated["assign_category"] == "NewCategory"


def test_update_rule_not_found(test_client, plan):
    update_payload = {
        "plan_id": plan.id,
        "name": "Ghost",
        "match_type": "contains",
        "pattern": "X",
        "priority": 0,
        "flag_review": False,
        "flag_ignore": False,
    }
    resp = test_client.put("/rules/nonexistent-id", json=update_payload)
    assert resp.status_code == 404


def test_delete_rule(test_client, plan):
    created = _create_rule(test_client, plan.id, name="To Delete")
    rule_id = created["rule_id"]

    resp = test_client.delete(f"/rules/{rule_id}")
    assert resp.status_code == 200

    rules = test_client.get(f"/rules?plan_id={plan.id}").json()
    assert len(rules) == 0


def test_delete_rule_not_found(test_client):
    resp = test_client.delete("/rules/nonexistent-id")
    assert resp.status_code == 404


def test_update_rule_then_classify(test_client, plan, db_session):
    """After updating a rule, reclassification should use the new values."""
    created = _create_rule(
        test_client, plan.id,
        name="Migros Rule", pattern="MIGROS",
        assign_payee="Migros", assign_category="Groceries",
    )
    rule_id = created["rule_id"]

    # Update the rule to assign a different payee and category
    test_client.put(f"/rules/{rule_id}", json={
        "plan_id": plan.id,
        "name": "Migros Updated",
        "match_type": "contains",
        "pattern": "MIGROS",
        "priority": 0,
        "assign_payee": "Migros SA",
        "assign_category": "Food",
        "flag_review": False,
        "flag_ignore": False,
    })

    # Verify the rule engine now uses the updated values
    from src.rule_engine import RuleEngine
    eng = RuleEngine(db_session, plan.id)
    match = eng.evaluate("PAIEMENT MIGROS LAUSANNE")
    assert match is not None
    assert match.assign_payee == "Migros SA"
    assert match.assign_category == "Food"
