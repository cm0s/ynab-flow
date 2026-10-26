"""Shared fixtures for end-to-end import tests (isolated DB, mocked YNAB)."""
import json
import os

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BUDGET = "https://api.ynab.com/v1/budgets/budget-1"


class Env:
    """A plan with a card account, a Post account, and a scriptable fake YNAB."""

    def __init__(self, db, client, router):
        from src.models import Plan, Account, Payee, CategoryGroup, Category
        self.db, self.client, self.router = db, client, router
        self.plan = Plan(id="plan-1", ynab_plan_id="budget-1", name="Budget")
        db.add(self.plan)
        db.flush()
        self.card = Account(id="acc-card", plan_id="plan-1", ynab_account_id="y-card", name="Swiss Card", type="creditCard")
        self.post = Account(id="acc-post", plan_id="plan-1", ynab_account_id="y-post", name="Post", type="checking")
        db.add_all([self.card, self.post])
        group = CategoryGroup(id="g1", plan_id="plan-1", ynab_category_group_id="y-g1", name="Expenses")
        db.add(group)
        db.flush()
        self.groceries = Category(id="c1", category_group_id="g1", ynab_category_id="y-c1", name="Groceries")
        db.add_all([
            self.groceries,
            Payee(id="p-shop", plan_id="plan-1", ynab_payee_id="y-p-shop", name="Shop"),
            Payee(id="p-xfer", plan_id="plan-1", ynab_payee_id="y-p-xfer", name="Transfer : Post",
                  transfer_account_id="y-post"),
        ])
        db.commit()
        self.history = []      # YNAB-side transactions returned by GET /transactions
        self.post_bodies = []  # request bodies received by POST /transactions
        self.post_handler = self._default_post
        self.history_error = False
        self.counter = 0
        router.get(f"{BUDGET}/transactions").mock(side_effect=self._get_transactions)
        router.post(f"{BUDGET}/transactions").mock(side_effect=lambda req: self.post_handler(req))
        router.get(url__regex=rf"{BUDGET}/transactions/[\w-]+$").mock(side_effect=self._get_one)

    # --- fake YNAB ---
    def _get_transactions(self, request):
        if self.history_error:
            return httpx.Response(503, json={"error": {"detail": "unavailable"}})
        return httpx.Response(200, json={"data": {"transactions": self.history, "server_knowledge": 1}})

    def _get_one(self, request):
        tid = request.url.path.rsplit("/", 1)[1]
        for t in self.history + self.created:
            if t["id"] == tid:
                return httpx.Response(200, json={"data": {"transaction": t}})
        return httpx.Response(404, json={"error": {"detail": "not found"}})

    created = []

    def ynab_txn(self, tid, account="y-card", date="2026-10-01", amount=-50000, memo="", import_id=None,
                 payee=None, deleted=False, transfer_account=None, transfer_txn=None):
        return {"id": tid, "account_id": account, "payee_id": payee, "category_id": None, "date": date,
                "amount": amount, "memo": memo, "cleared": "cleared", "approved": True, "deleted": deleted,
                "import_id": import_id, "transfer_account_id": transfer_account,
                "transfer_transaction_id": transfer_txn}

    def _default_post(self, request):
        body = json.loads(request.content)
        self.post_bodies.append(body)
        existing = {t.get("import_id") for t in self.history}
        txns, dups = [], []
        for t in body["transactions"]:
            if t["import_id"] in existing:
                dups.append(t["import_id"])
                continue
            self.counter += 1
            created = self.ynab_txn(f"new-{self.counter}", account=t["account_id"], date=t["date"],
                                    amount=t["amount"], memo=t["memo"], import_id=t["import_id"])
            self.created.append(created)
            txns.append(created)
        return httpx.Response(201, json={"data": {
            "transaction_ids": [t["id"] for t in txns], "transactions": txns,
            "duplicate_import_ids": dups, "server_knowledge": 2}})

    # --- API helpers ---
    def upload(self, content, account_id=None, filename="export.csv", plan_id="plan-1"):
        return self.client.post(
            f"/upload-csv?plan_id={plan_id}&account_id={account_id or self.card.id}",
            files={"file": (filename, content.encode("utf-8"), "text/csv")},
        )

    def write(self, rows, mode="create", batch_id=None):
        body = {"plan_id": "plan-1", "mode": mode, "rows": rows}
        if batch_id:
            body["batch_id"] = batch_id
        return self.client.post("/write-back", json=body)

    @staticmethod
    def row_ref(row, **kw):
        base = {"row_id": row["id"], "payee_name": row["edited_payee"] or "Shop",
                "category_name": row["edited_category"], "memo": row["edited_memo"] or row["original_memo"]}
        base.update(kw)
        return base


@pytest.fixture(autouse=True)
def no_local_mail_or_ai_settings(monkeypatch):
    """backend/.env is loaded on import; its real Bridge and AI settings must never reach a test."""
    for key in list(os.environ):
        if key.startswith(("BRIDGE_", "AI_", "ANTHROPIC_")):
            monkeypatch.delenv(key)


@pytest.fixture()
def env(monkeypatch):
    from src.main import app
    from src.database import get_db
    from src.models import Base
    monkeypatch.setenv("YNAB_API_KEY", "test-key")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()

    def override():
        yield db
    app.dependency_overrides[get_db] = override
    # keep ML models out of the picture: no joblib artifacts are loaded
    import src.ml_classifier as mlc
    monkeypatch.setattr(mlc.MLClassifier, "_load_models", lambda self: None)
    with respx.mock(assert_all_called=False) as router:
        e = Env(db, TestClient(app), router)
        e.created = []
        yield e
    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(engine)
