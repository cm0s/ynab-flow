import pytest
import respx
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Plan, Account, CategoryGroup, Category, Payee
from src.ynab_client import YnabClient
from src.write_back_service import WriteBackService, WriteTransaction

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
def plan(db):
    p = Plan(id="plan-1", ynab_plan_id="budget-1", name="Test Budget")
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def account(db, plan):
    a = Account(
        id="acc-1", plan_id=plan.id,
        ynab_account_id="ynab-acc-1", name="Checking", type="checking",
    )
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def payee_regular(db, plan):
    p = Payee(
        id="payee-1", plan_id=plan.id,
        ynab_payee_id="ynab-payee-1", name="Landlord",
    )
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def payee_transfer(db, plan):
    p = Payee(
        id="payee-2", plan_id=plan.id,
        ynab_payee_id="ynab-payee-transfer", name="Transfer : Wise",
        transfer_account_id="ynab-acc-wise",
    )
    db.add(p)
    db.commit()
    return p


def _make_txn(account_id="acc-1", payee_name="Landlord", **kwargs):
    defaults = dict(
        date="2026-03-01", amount=-1200.0, payee_name=payee_name,
        category_name="", memo="Rent", account_id=account_id,
    )
    defaults.update(kwargs)
    return WriteTransaction(**defaults)


@respx.mock
def test_known_payee_uses_payee_id(db, client, plan, account, payee_regular):
    """When the payee exists in the DB, the API call should use payee_id."""
    route = respx.post("https://api.ynab.com/v1/budgets/budget-1/transactions").mock(
        return_value=Response(200, json={"data": {
            "transaction_ids": ["txn-1"], "duplicate_import_ids": [],
        }})
    )

    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn()], mode="create")

    assert result.created == 1
    body = route.calls[0].request.content
    import json
    payload = json.loads(body)
    txn = payload["transactions"][0]
    assert txn["payee_id"] == "ynab-payee-1"
    assert "payee_name" not in txn


@respx.mock
def test_unknown_payee_falls_back_to_payee_name(db, client, plan, account):
    """When the payee is not in the DB, the API call should use payee_name."""
    route = respx.post("https://api.ynab.com/v1/budgets/budget-1/transactions").mock(
        return_value=Response(200, json={"data": {
            "transaction_ids": ["txn-1"], "duplicate_import_ids": [],
        }})
    )

    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn(payee_name="New Shop")], mode="create")

    assert result.created == 1
    import json
    payload = json.loads(route.calls[0].request.content)
    txn = payload["transactions"][0]
    assert txn["payee_name"] == "New Shop"
    assert "payee_id" not in txn


@respx.mock
def test_transfer_payee_uses_payee_id(db, client, plan, account, payee_transfer):
    """Transfer payees (e.g. 'Transfer : Wise') should resolve to payee_id."""
    route = respx.post("https://api.ynab.com/v1/budgets/budget-1/transactions").mock(
        return_value=Response(200, json={"data": {
            "transaction_ids": ["txn-1"], "duplicate_import_ids": [],
        }})
    )

    service = WriteBackService(db, client)
    result = service.execute(
        "plan-1", [_make_txn(payee_name="Transfer : Wise")], mode="create",
    )

    assert result.created == 1
    assert result.errors == 0
    import json
    payload = json.loads(route.calls[0].request.content)
    txn = payload["transactions"][0]
    assert txn["payee_id"] == "ynab-payee-transfer"
    assert "payee_name" not in txn


def test_dry_run_validates_without_api_call(db, client, plan, account, payee_regular):
    """Dry run should validate but never call the YNAB API."""
    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn()], mode="dry_run")

    assert result.mode == "dry_run"
    assert result.total == 1
    assert result.results[0].status == "dry_run_ok"


def test_validation_rejects_missing_account(db, client, plan):
    """Transaction with unknown account_id should fail validation."""
    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn(account_id="bad-id")], mode="dry_run")

    assert result.errors == 1
    assert "Account not found" in result.results[0].error


def test_validation_rejects_empty_payee(db, client, plan, account):
    """Transaction with blank payee should fail validation."""
    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn(payee_name="  ")], mode="dry_run")

    assert result.errors == 1
    assert "Payee name is required" in result.results[0].error


def test_validation_rejects_bad_date(db, client, plan, account):
    """Transaction with invalid date format should fail validation."""
    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn(date="03/01/2026")], mode="dry_run")

    assert result.errors == 1
    assert "Invalid date" in result.results[0].error


@respx.mock
def test_ynab_api_error_marks_all_as_error(db, client, plan, account, payee_regular):
    """When YNAB API returns an error, all transactions should be marked as error."""
    respx.post("https://api.ynab.com/v1/budgets/budget-1/transactions").mock(
        return_value=Response(400, json={"error": {"detail": "Bad request"}})
    )

    service = WriteBackService(db, client)
    result = service.execute("plan-1", [_make_txn()], mode="create")

    assert result.errors == 1
    assert result.results[0].status == "error"
