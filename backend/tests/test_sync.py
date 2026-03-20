import pytest
import respx
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Plan, Account, CategoryGroup, Category, Payee, Transaction
from src.ynab_client import YnabClient
from src.sync_service import SyncService

# Test Database Setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
    return YnabClient(api_key="test-key")

@pytest.fixture
def service(db, client):
    return SyncService(db, client)

@respx.mock
def test_sync_budgets(service, db):
    # Mock YNAB API response
    respx.get("https://api.ynab.com/v1/budgets").mock(return_value=Response(
        200, 
        json={
            "data": {
                "budgets": [
                    {
                        "id": "budget-1",
                        "name": "My Budget",
                        "currency_format": {"iso_code": "USD"},
                        "accounts": [
                            {
                                "id": "account-1",
                                "name": "Checking",
                                "type": "checking",
                                "closed": False
                            }
                        ]
                    }
                ]
            }
        }
    ))

    # Run the sync process
    service.sync_budgets()

    # Verify data exists in DB
    plan = db.query(Plan).filter(Plan.ynab_plan_id == "budget-1").first()
    assert plan is not None
    assert plan.name == "My Budget"
    
    assert len(plan.accounts) == 1
    assert plan.accounts[0].name == "Checking"
    assert plan.accounts[0].currency == "USD"


@respx.mock
def test_sync_uses_separate_knowledge_per_entity(service, db):
    """Each entity type must track its own server_knowledge independently."""
    # 1) Sync budgets to create the plan
    respx.get("https://api.ynab.com/v1/budgets").mock(return_value=Response(
        200,
        json={"data": {"budgets": [
            {"id": "budget-1", "name": "B", "currency_format": {"iso_code": "CHF"},
             "accounts": [{"id": "acc-1", "name": "Checking", "type": "checking", "closed": False}]}
        ]}}
    ))
    service.sync_budgets()
    plan = db.query(Plan).filter(Plan.ynab_plan_id == "budget-1").first()

    # 2) Sync categories — returns knowledge=100
    respx.get("https://api.ynab.com/v1/budgets/budget-1/categories").mock(return_value=Response(
        200,
        json={"data": {"category_groups": [
            {"id": "grp-1", "name": "Bills", "deleted": False,
             "categories": [{"id": "cat-1", "name": "Rent", "deleted": False}]}
        ], "server_knowledge": 100}}
    ))
    service.sync_categories(plan)
    assert plan.last_server_knowledge == "100"

    # 3) Sync payees — should NOT send knowledge=100; returns knowledge=50
    payees_route = respx.get("https://api.ynab.com/v1/budgets/budget-1/payees").mock(
        return_value=Response(200, json={"data": {"payees": [
            {"id": "payee-1", "name": "Landlord", "deleted": False}
        ], "server_knowledge": 50}})
    )
    service.sync_payees(plan)
    # Payees knowledge should be stored separately
    assert plan.last_knowledge_payees == "50"
    assert plan.last_server_knowledge == "100"  # categories unchanged
    # First sync for payees should send knowledge=0, not the categories value of 100
    assert "last_knowledge_of_server=0" in str(payees_route.calls[0].request.url)

    # 4) Sync transactions — should NOT send knowledge=100 or 50; returns knowledge=75
    txn_route = respx.get("https://api.ynab.com/v1/budgets/budget-1/transactions").mock(
        return_value=Response(200, json={"data": {"transactions": [
            {"id": "txn-1", "account_id": "acc-1", "payee_id": "payee-1",
             "category_id": "cat-1", "date": "2026-01-15", "amount": -50000,
             "memo": "Rent January", "cleared": "cleared", "approved": True, "deleted": False}
        ], "server_knowledge": 75}})
    )
    service.sync_transactions(plan)
    assert plan.last_knowledge_transactions == "75"
    assert plan.last_server_knowledge == "100"  # still categories only
    assert plan.last_knowledge_payees == "50"   # still payees only
    # First sync for transactions should send knowledge=0, not 100 or 50
    assert "last_knowledge_of_server=0" in str(txn_route.calls[0].request.url)

    # Verify the transaction was actually stored
    txn = db.query(Transaction).filter(Transaction.ynab_transaction_id == "txn-1").first()
    assert txn is not None
    assert txn.memo == "Rent January"
