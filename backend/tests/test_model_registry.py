"""Model versions: legacy registration, promotion, rejection, rollback, recovery, retention (tasks 5.1-5.3)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import ml_classifier, model_registry
from src.ml_classifier import MLClassifier
from src.models import Account, Base, Category, CategoryGroup, ModelVersion, Payee, Plan, Transaction


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    monkeypatch.setattr(ml_classifier, "MODEL_DIR", d)
    monkeypatch.setattr(ml_classifier, "PAYEE_MODEL_PATH", d / "payee_model.joblib")
    monkeypatch.setattr(ml_classifier, "CATEGORY_MODEL_PATH", d / "category_model.joblib")
    return d


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(Plan(id="p", ynab_plan_id="p", name="P"))
        session.add(Account(id="a", plan_id="p", ynab_account_id="a", name="Post", type="checking"))
        session.add(CategoryGroup(id="g", plan_id="p", ynab_category_group_id="g", name="G"))
        session.add_all([Payee(id="p1", plan_id="p", ynab_payee_id="p1", name="Migros"),
                         Payee(id="p2", plan_id="p", ynab_payee_id="p2", name="SBB")])
        session.flush()
        session.add_all([Category(id="c1", category_group_id="g", ynab_category_id="c1", name="Groceries"),
                         Category(id="c2", category_group_id="g", ynab_category_id="c2", name="Transport")])
        start = date(2026, 1, 1)
        for i in range(60):
            migros = i % 2 == 0
            session.add(Transaction(
                id=f"t{i}", plan_id="p", account_id="a", ynab_transaction_id=f"t{i}",
                date=(start + timedelta(days=i)).isoformat(), amount=-1000.0, amount_milliunits=-1000,
                memo=f"MIGROS LAUSANNE {i}" if migros else f"SBB MOBILE TICKET {i}",
                payee_id="p1" if migros else "p2", category_id="c1" if migros else "c2"))
        session.commit()
        yield session
    engine.dispose()


def test_legacy_files_are_registered_without_retraining(db, models_dir):
    MLClassifier().train(db, "p")                      # a pre-versioning install: files in MODEL_DIR itself
    before = ml_classifier.PAYEE_MODEL_PATH.stat().st_mtime_ns
    assert model_registry.ensure_active_model(db, "p") == "legacy"
    assert model_registry.active_version(db).id == "legacy"
    assert ml_classifier.PAYEE_MODEL_PATH.stat().st_mtime_ns == before
    assert not (models_dir / ml_classifier.ACTIVE_POINTER).exists()
    assert MLClassifier().predict("MIGROS LAUSANNE").payee == "Migros"


def test_first_model_is_promoted_and_served(db, models_dir):
    result = model_registry.train_candidate(db, "p")
    assert result["promoted"] and result["version"]["status"] == "active"
    vid = result["version"]["id"]
    assert (models_dir / ml_classifier.ACTIVE_POINTER).read_text() == vid
    assert result["version"]["comparison"]["holdout_examples"] == 6
    assert ml_classifier.active_model_dir() == models_dir / vid
    assert MLClassifier().predict("SBB MOBILE TICKET").category == "c2"


def test_worse_candidate_is_rejected_and_active_keeps_serving(db, models_dir, monkeypatch):
    first = model_registry.train_candidate(db, "p")["version"]["id"]
    scores = iter([{"payee": {"examples": 6, "correct": 6, "accuracy": 1.0}, "category": {"examples": 6, "correct": 6, "accuracy": 1.0}},
                   {"payee": {"examples": 6, "correct": 6, "accuracy": 1.0}, "category": {"examples": 6, "correct": 3, "accuracy": 0.5}}])
    monkeypatch.setattr(model_registry, "score", lambda *a: next(scores))
    result = model_registry.train_candidate(db, "p")
    assert not result["promoted"] and "category" in result["note"]
    assert result["version"]["status"] == "rejected" and not result["version"]["files_present"]
    assert model_registry.active_version(db).id == first
    assert (models_dir / ml_classifier.ACTIVE_POINTER).read_text() == first


def test_decide_rules():
    good = {"payee": {"accuracy": 0.8}, "category": {"accuracy": 0.7}}
    assert model_registry.decide(good, None)[0]
    assert model_registry.decide(good, {"payee": {"accuracy": 0.8}, "category": {"accuracy": 0.7}})[0]  # ties promote
    assert not model_registry.decide(good, {"payee": {"accuracy": 0.81}, "category": None})[0]
    assert not model_registry.decide({"payee": None, "category": {"accuracy": 1.0}}, {"payee": {"accuracy": 0.1}})[0]


def test_rollback_restores_the_previous_version(db, models_dir):
    first = model_registry.train_candidate(db, "p")["version"]["id"]
    second = model_registry.train_candidate(db, "p")["version"]["id"]   # equal accuracy: promoted
    assert model_registry.active_version(db).id == second
    restored = model_registry.restore(db, first)
    assert restored["status"] == "active" and "Restored" in restored["note"]
    assert db.get(ModelVersion, second).status == "retired"
    assert (models_dir / ml_classifier.ACTIVE_POINTER).read_text() == first
    with pytest.raises(LookupError):
        model_registry.restore(db, "missing")


def test_missing_active_files_are_rebuilt_under_the_same_version(db, models_dir):
    vid = model_registry.train_candidate(db, "p")["version"]["id"]
    import shutil
    shutil.rmtree(models_dir / vid)
    (models_dir / ml_classifier.ACTIVE_POINTER).unlink()
    assert model_registry.ensure_active_model(db, "p") == vid
    assert (models_dir / vid / "payee_model.joblib").exists()
    assert "rebuilt" in db.get(ModelVersion, vid).note
    assert (models_dir / ml_classifier.ACTIVE_POINTER).read_text() == vid


def test_retention_keeps_active_and_two_retired(db, models_dir):
    ids = [model_registry.train_candidate(db, "p")["version"]["id"] for _ in range(4)]
    present = [model_registry._has_files(v) for v in ids]
    assert present == [False, True, True, True]
    assert "removed by retention" in db.get(ModelVersion, ids[0]).note


def test_model_endpoints(env, tmp_path, monkeypatch):
    d = tmp_path / "api-models"
    monkeypatch.setattr(ml_classifier, "MODEL_DIR", d)
    monkeypatch.setattr(ml_classifier, "PAYEE_MODEL_PATH", d / "payee_model.joblib")
    monkeypatch.setattr(ml_classifier, "CATEGORY_MODEL_PATH", d / "category_model.joblib")
    assert env.client.get("/models").json() == {"versions": []}
    assert env.client.post("/models/nope/activate").status_code == 404
    env.db.add(ModelVersion(id="r", status="rejected", note=""))
    env.db.commit()
    assert env.client.post("/models/r/activate").status_code == 409
    assert env.client.get("/models").json()["versions"][0]["id"] == "r"
