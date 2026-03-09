import pytest
import respx
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Plan
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
