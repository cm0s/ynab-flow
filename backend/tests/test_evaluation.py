"""Offline evaluation: adapters, treatments, split, isolation, metrics, reports and CLI (tasks 1.2-1.10)."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import ml_classifier
from src.evaluation import evaluate, load_export, load_synced, metrics
from src.evaluation.__main__ import main
from src.evaluation.data import EvalRow, EvaluationInputError
from src.evaluation.runner import Outcome
from src.evaluation.split import split
from src.evaluation.treatment import (EVALUATED, PAYEE_ONLY, TAG_HIDDEN, TAG_REFUND, TREATMENTS, Treated,
                                      duplicate_count, treat_all, treatment_counts)
from src.models import Account, Base, Category, CategoryGroup, Payee, Plan, Transaction
from tests.evaluation_fixture import history, line, write_export


@pytest.fixture(autouse=True)
def isolated_default_models(tmp_path_factory, monkeypatch):
    """Point production model paths at an empty directory and prove evaluation never writes there."""
    d = tmp_path_factory.mktemp("prod-models")
    monkeypatch.setattr(ml_classifier, "MODEL_DIR", d)
    monkeypatch.setattr(ml_classifier, "PAYEE_MODEL_PATH", d / "payee_model.joblib")
    monkeypatch.setattr(ml_classifier, "CATEGORY_MODEL_PATH", d / "category_model.joblib")
    yield
    assert list(d.iterdir()) == []


@pytest.fixture
def export(tmp_path):
    return write_export(tmp_path / "export.zip")


def test_export_zip_and_csv_parse_to_the_same_rows(tmp_path, export):
    rows, sha = load_export(export)
    csv_rows, csv_sha = load_export(write_export(tmp_path / "Register.csv", as_zip=False))
    assert rows == csv_rows and sha == csv_sha and len(rows) == len(history())
    salary = next(r for r in rows if r.memo == "SALAIRE ACME SA")
    assert salary.date == "2024-01-09" and salary.milliunits == 5_000_000
    migros = rows[1]
    assert (migros.milliunits, migros.category_label, migros.category) == (-45_100, "Everyday expenses: Groceries", "Groceries")


def test_missing_or_unrecognised_input_is_an_actionable_error(tmp_path):
    with pytest.raises(EvaluationInputError, match="not found"):
        load_export(tmp_path / "nope.zip")
    bad = tmp_path / "other.csv"
    bad.write_text("Foo,Bar\n1,2\n")
    with pytest.raises(EvaluationInputError, match="missing columns"):
        load_export(bad)
    assert main(["--export", str(tmp_path / "nope.zip"), "--out-dir", str(tmp_path / "r"), "--quiet"]) == 2
    assert not (tmp_path / "r").exists()


def test_synced_adapter_yields_equivalent_rows(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Plan(id="p", ynab_plan_id="p", name="P"))
        db.add(Account(id="a", plan_id="p", ynab_account_id="a", name="Post", type="checking"))
        db.add(CategoryGroup(id="g", plan_id="p", ynab_category_group_id="g", name="Everyday expenses"))
        db.add(Category(id="c", category_group_id="g", ynab_category_id="c", name="Groceries"))
        db.add(Payee(id="py", plan_id="p", ynab_payee_id="py", name="Migros"))
        db.add(Transaction(id="t", plan_id="p", account_id="a", payee_id="py", category_id="c", ynab_transaction_id="t",
                           date="2024-01-03", amount=-45100.0, amount_milliunits=-45100, memo="MIGROS LAUSANNE 1"))
        db.add(Transaction(id="d", plan_id="p", account_id="a", ynab_transaction_id="d", date="2024-01-04",
                           amount=-1.0, memo="DELETED", deleted=True))
        db.commit()
        rows, sha = load_synced(db, "p")
    exported, _ = load_export(write_export(tmp_path / "e.zip", [
        line("03.01.2024", "Migros", "Everyday expenses", "Groceries", "MIGROS LAUSANNE 1", "45.10")]))
    assert len(rows) == 1 and len(sha) == 64
    strip = lambda r: (r.date, r.account, r.payee, r.category_group, r.category, r.memo, r.milliunits)
    assert strip(rows[0]) == strip(exported[0])
    assert rows[0].category_label == "c"          # synced labels are stable category ids


def test_every_row_gets_exactly_one_treatment(export):
    rows, _ = load_export(export)
    treated = treat_all(rows)
    counts = treatment_counts(treated)
    assert set(counts) == set(TREATMENTS) and sum(counts.values()) == len(rows)
    assert counts["excluded_starting_balance"] == 1 and counts["excluded_split_line"] == 2
    assert counts["excluded_blank_description"] == 1 and counts["excluded_uncategorized"] == 1
    assert counts[PAYEE_ONLY] == 12                                # transfers are payee-only
    tags = {t.row.memo: t.tags for t in treated}
    assert TAG_REFUND in tags["MIGROS LAUSANNE RETOUR"] and TAG_HIDDEN in tags["OLD SHOP BERN"]
    assert tags["SALAIRE ACME SA"] == set()                        # income is not a refund
    assert duplicate_count(treated) == 1


def test_chronological_split_default_and_override(export):
    treated = treat_all(load_export(export)[0])
    parts = split(treated)
    assert max(t.row.date for t in parts.training) < parts.cutoff <= min(t.row.date for t in parts.evaluation)
    ranges = parts.ranges()
    assert ranges["training"]["rows"] + ranges["evaluation"]["rows"] == sum(
        1 for t in treated if t.treatment in (EVALUATED, PAYEE_ONLY))
    explicit = split(treated, "2024-11-01")
    assert explicit.cutoff == "2024-11-01" and min(t.row.date for t in explicit.evaluation) >= "2024-11-01"
    with pytest.raises(ValueError):
        split(treated, "2030-01-01")


def test_evaluation_rows_never_reach_learned_state(export):
    rows, sha = load_export(export)
    report = evaluate(rows, input_name="x", input_sha256=sha, cutoff="2024-11-01")
    m = report["variants"]["learned_only"]["metrics"]
    failures = {f["description"]: f for f in m["payee"]["confident_failures"]}
    # The December-only merchant never appears in training: no stage can return its own label
    for target in ("payee", "category"):
        assert all(f["predicted"] not in ("Unique Shop", "Fun + learn: Gifts")
                   for f in m[target]["confident_failures"] if f["description"] == "ONLY IN DECEMBER SHOP")
    assert m["payee"]["rows"] == report["split"]["evaluation"]["rows"]
    # recurring merchants are learned from training history
    assert m["payee"]["by_source"].get("exact_history", {}).get("accuracy") == 1.0
    assert "ONLY IN DECEMBER SHOP" not in failures or failures["ONLY IN DECEMBER SHOP"]["predicted"] != "Unique Shop"


def test_rules_variant_is_separate_and_marked_as_hindsight(export):
    rows, sha = load_export(export)
    rule = {"name": "Unique", "priority": 1, "match_type": "contains", "pattern": "ONLY IN DECEMBER",
            "assign_payee": "Unique Shop", "assign_category": "Gifts", "flag_review": False, "flag_ignore": False}
    report = evaluate(rows, input_name="x", input_sha256=sha, cutoff="2024-11-01", rules=[rule])
    assert set(report["variants"]) == {"learned_only", "with_rules"}
    assert "hindsight" in report["variants"]["with_rules"]["note"]
    learned = report["variants"]["learned_only"]["metrics"]["payee"]["correct"]
    assert report["variants"]["with_rules"]["metrics"]["payee"]["correct"] == learned + 2
    assert report["variants"]["learned_only"]["metrics"]["payee"]["by_source"].get("rule") is None


def _outcome(memo, payee, actual_payee, label, actual_label, conf, review, treatment=EVALUATED, tags=()):
    row = EvalRow(line=1, date="2024-12-01", account="Post", payee=actual_payee, category_group="G",
                  category="C", category_label=actual_label, memo=memo, milliunits=-1000)
    return Outcome(row=Treated(row, treatment, set(tags)), payee=payee, payee_confidence=conf if payee else 0.0,
                   payee_source="exact_history" if payee else "unclassified", category_label=label, category=label,
                   category_confidence=conf if label else 0.0,
                   category_source="exact_history" if label else "unclassified", review_required=review)


def test_metrics_match_hand_computed_values():
    outcomes = [
        _outcome("A", "Migros", "migros ", "G: C", "G: C", 0.99, False),       # both right, auto-accepted
        _outcome("B", "Coop", "Migros", "G: C", "G: C", 0.97, False),          # payee wrong, auto-accepted
        _outcome("C", "SBB", "SBB", "G: X", "G: C", 0.80, True),               # category wrong, medium
        _outcome("D", None, "Lidl", None, "G: C", 0.0, True),                  # unclassified
        _outcome("E", "Transfer : Post", "Transfer : Post", None, "", 0.60, True, treatment=PAYEE_ONLY),
    ]
    m = metrics.compute(outcomes)
    assert (m["payee"]["rows"], m["payee"]["correct"], m["payee"]["accuracy"]) == (5, 3, 0.6)
    assert m["payee"]["coverage"] == 0.8 and m["payee"]["unclassified"] == 1
    assert (m["category"]["rows"], m["category"]["correct"], m["category"]["accuracy"]) == (4, 2, 0.5)
    assert m["category"]["by_band"]["high"] == {"rows": 2, "correct": 2, "accuracy": 1.0, "coverage": 1.0, "unclassified": 0}
    assert m["category"]["by_band"]["none"]["rows"] == 1
    assert m["rows"]["would_auto_accept"] == 2 and m["rows"]["auto_accept_precision"] == 0.5
    assert m["rows"]["corrections_required"] == 3
    assert m["payee"]["confident_failures"][0]["predicted"] == "Coop"
    assert m["category"]["frequent_confusions"] == [{"actual": "G: C", "predicted": "G: X", "count": 1}]


def test_repeat_runs_are_identical_and_reports_are_written(tmp_path, export):
    rows, sha = load_export(export)
    first = evaluate(rows, input_name="x", input_sha256=sha)
    second = evaluate(rows, input_name="x", input_sha256=sha)
    assert first["variants"] == second["variants"] and first["config"]["input_sha256"] == sha
    assert any("present-day" in x for x in first["limitations"])
    out = tmp_path / "reports"
    assert main(["--export", str(export), "--out-dir", str(out), "--quiet"]) == 0
    written = sorted(p.suffix for p in out.iterdir())
    assert written == [".json", ".md"]
    data = json.loads(next(out.glob("*.json")).read_text())
    assert data["config"]["cutoff"] and data["input_rows"] == sum(data["treatments"].values())
    assert "present-day" in next(out.glob("*.md")).read_text()


def test_category_labels_are_compared_by_id_even_when_names_collide(tmp_path):
    rows = []
    for month in range(1, 13):
        rows += [line(f"02.{month:02d}.2024", "Shop A", "Home", "Misc", "SHOP A PURCHASE", "10.00"),
                 line(f"03.{month:02d}.2024", "Shop B", "Work", "Misc", "SHOP B PURCHASE", "20.00")]
    data, sha = load_export(write_export(tmp_path / "e.zip", rows))
    report = evaluate(data, input_name="x", input_sha256=sha, cutoff="2024-10-01")
    cat = report["variants"]["learned_only"]["metrics"]["category"]
    # Two "Misc" categories in different groups: names are ambiguous, ids are not
    assert cat["rows"] == 6 and cat["accuracy"] == 1.0
