"""Split endpoints and split write-back (subtransactions, re-validation at push, dry runs)."""
from src.models import Category, ImportRow
from tests.swisscard_fixture import build, row

SUPERMARKET = dict(desc="Migros Online", merchant="Migros", amount="84.90")


def setup(env):
    env.db.add(Category(id="c2", category_group_id="g1", ynab_category_id="y-c2", name="Household"))
    env.db.commit()
    out = env.upload(build([row(**SUPERMARKET)])).json()
    return out, out["rows"][0]


def put(env, row_id, *lines):
    return env.client.put(f"/import-rows/{row_id}/splits", json={"lines": [
        {"amount_milliunits": a, "category_id": c, "memo": m} for a, c, m in lines]})


GOOD = ((-62400, "c1", "Food"), (-22500, "c2", "Cleaning"))


def test_splits_persist_across_reload_and_can_be_cleared(env):
    _, r = setup(env)
    saved = put(env, r["id"], *GOOD)
    assert saved.status_code == 200 and [s["amount_milliunits"] for s in saved.json()["splits"]] == [-62400, -22500]
    reloaded = env.client.get("/import-batches/plan-1/active").json()["rows"][0]
    assert [(s["category_id"], s["memo"]) for s in reloaded["splits"]] == [("c1", "Food"), ("c2", "Cleaning")]
    assert reloaded["amount_milliunits"] == -84900          # bank amount unchanged
    assert env.client.delete(f"/import-rows/{r['id']}/splits").json()["splits"] == []
    assert env.client.get("/import-batches/plan-1/active").json()["rows"][0]["splits"] == []


def test_invalid_split_returns_422_with_reason_and_keeps_previous_lines(env):
    _, r = setup(env)
    put(env, r["id"], *GOOD)
    bad = put(env, r["id"], (-62400, "c1", ""), (-22450, "c2", ""))
    assert bad.status_code == 422 and "0.05 is unassigned" in bad.json()["detail"]
    assert len(env.client.get("/import-batches/plan-1/active").json()["rows"][0]["splits"]) == 2
    assert put(env, r["id"], (-84900, "c1", "")).json()["detail"] == "A split needs at least two lines."


def test_split_push_sends_subtransactions_with_null_parent_category(env):
    out, r = setup(env)
    put(env, r["id"], *GOOD)
    res = env.write([env.row_ref(r, payee_name="Migros", category_name="Groceries")]).json()
    assert res["created"] == 1
    sent = env.post_bodies[0]["transactions"][0]
    assert sent["amount"] == -84900 and sent["approved"] is False and sent["import_id"].startswith("YF1:")
    assert sent["category_id"] is None
    assert sent["subtransactions"] == [
        {"amount": -62400, "category_id": "y-c1", "memo": "Food"},
        {"amount": -22500, "category_id": "y-c2", "memo": "Cleaning"},
    ]
    stored = env.db.get(ImportRow, r["id"])
    assert stored.sent_category_id is None and '"y-c1"' not in stored.sent_splits_json
    assert '"c2"' in stored.sent_splits_json


def test_deleted_category_blocks_the_split_row_at_push(env):
    _, r = setup(env)
    put(env, r["id"], *GOOD)
    env.db.get(Category, "c2").deleted = True
    env.db.commit()
    res = env.write([env.row_ref(r, payee_name="Migros")]).json()
    assert res["errors"] == 1 and "Household" in res["results"][0]["error"] and "deleted" in res["results"][0]["error"]
    assert env.post_bodies == []


def test_split_dry_run_validates_and_sends_nothing(env):
    _, r = setup(env)
    put(env, r["id"], *GOOD)
    ok = env.write([env.row_ref(r, payee_name="Migros")], mode="dry_run").json()
    assert ok["results"][0]["status"] == "dry_run_ok" and env.post_bodies == []
    env.db.get(Category, "c1").deleted = True
    env.db.commit()
    bad = env.write([env.row_ref(r, payee_name="Migros")], mode="dry_run").json()
    assert bad["results"][0]["status"] == "dry_run_error" and env.post_bodies == []
    assert env.db.get(ImportRow, r["id"]).write_state == "unsent"
