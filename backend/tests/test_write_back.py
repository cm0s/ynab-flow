"""Row-reference write-back: payload safety, outcome correlation, claims, retries (7.1-7.4, 5.5)."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from src.models import ImportBatch, ImportRow, Plan, Account, SourceRecord
from src.write_back_service import recover_stale_claims
from tests.swisscard_fixture import build, row


def upload(env, *specs, account_id=None):
    out = env.upload(build([row(**s) for s in specs]), account_id=account_id).json()
    return out, out["rows"]


COFFEE = dict(desc="Coffee", merchant="Cafe", amount="4.50")
BREAD = dict(desc="Bread", merchant="Bakery", amount="3.00")
SHOP = dict(desc="Shop One", merchant="Shop One", amount="50.00")


def test_dry_run_changes_nothing_and_never_posts(env):
    out, rows = upload(env, COFFEE, BREAD)
    env.client.patch("/import-rows", json={"updates": [{"id": r["id"], "status": "accepted"} for r in rows]})
    res = env.write([env.row_ref(r) for r in rows], mode="dry_run", batch_id=out["batch_id"]).json()
    assert [r["status"] for r in res["results"]] == ["dry_run_ok", "dry_run_ok"]
    assert env.post_bodies == []
    after = env.client.get("/import-batches/plan-1/active").json()["rows"]
    assert len(after) == 2 and all(r["status"] == "accepted" and r["write_state"] == "unsent" for r in after)


def test_payload_uses_persisted_values_and_approved_false(env):
    _, rows = upload(env, SHOP)
    ref = env.row_ref(rows[0], payee_name="Shop", category_name="Groceries")
    ref.update({"amount": 999.0, "account_id": env.post.id, "date": "1999-01-01", "import_id": "forged"})  # ignored
    res = env.write([ref]).json()
    assert res["created"] == 1
    sent = env.post_bodies[0]["transactions"][0]
    assert sent["amount"] == -50000 and sent["account_id"] == "y-card" and sent["date"] == "2026-10-01"
    assert sent["approved"] is False and sent["import_id"] == rows[0]["import_id"]
    assert sent["payee_id"] == "y-p-shop" and sent["category_id"] == "y-c1"
    assert isinstance(sent["amount"], int)


def test_legacy_standalone_payload_gets_actionable_error(env):
    r = env.client.post("/write-back", json={"plan_id": "plan-1", "mode": "create", "transactions": [
        {"date": "2026-01-01", "amount": -5.0, "payee_name": "X", "account_id": env.card.id}]})
    assert r.status_code == 422 and "row_id" in r.json()["detail"]


def test_cross_plan_and_unknown_rows_cannot_enter_the_queue(env):
    env.db.add(Plan(id="plan-2", ynab_plan_id="b2", name="Other"))
    env.db.commit()
    _, rows = upload(env, COFFEE)
    r = env.client.post("/write-back", json={"plan_id": "plan-2", "mode": "create", "rows": [{"row_id": rows[0]["id"], "payee_name": "X"}]})
    assert r.status_code == 403
    assert env.write([{"row_id": "forged", "payee_name": "X"}]).status_code == 403
    assert env.post_bodies == []


def test_mixed_reordered_response_correlates_by_import_id(env):
    _, rows = upload(env, COFFEE, BREAD, SHOP)
    env.history.append(env.ynab_txn("old-bread", amount=-3000, import_id=rows[1]["import_id"]))  # BREAD already imported
    env.history_error = False

    def post(request):
        body = json.loads(request.content)
        env.post_bodies.append(body)
        sent = {t["import_id"]: t for t in body["transactions"]}
        # reorder: shop first, then coffee; bread reported duplicate
        ordered = [sent[rows[2]["import_id"]], sent[rows[0]["import_id"]]]
        txns = [env.ynab_txn(f"id-{i}", import_id=t["import_id"], amount=t["amount"]) for i, t in enumerate(ordered)]
        return httpx.Response(201, json={"data": {"transaction_ids": ["wrong-order-id"], "transactions": txns,
                                                   "duplicate_import_ids": [rows[1]["import_id"]]}})
    env.post_handler = post
    # bread is reconciled as existing before sending; send a blank-payee row as the locally invalid one
    refs = [env.row_ref(rows[0]), env.row_ref(rows[1]), env.row_ref(rows[2]), ]
    res = {r["row_id"]: r for r in env.write(refs).json()["results"]}
    assert res[rows[0]["id"]]["status"] == "created" and res[rows[0]["id"]]["ynab_transaction_id"] == "id-1"
    assert res[rows[2]["id"]]["status"] == "created" and res[rows[2]["id"]]["ynab_transaction_id"] == "id-0"
    assert res[rows[1]["id"]]["status"] == "existing"
    sent_ids = [t["import_id"] for t in env.post_bodies[0]["transactions"]]
    assert rows[1]["import_id"] not in sent_ids


def test_duplicate_response_and_invalid_row_in_one_request(env):
    _, rows = upload(env, COFFEE, BREAD, SHOP)
    # simulate YNAB knowing the coffee import id only after our reconciliation snapshot (race)
    def post(request):
        body = json.loads(request.content)
        env.post_bodies.append(body)
        sent = [t for t in body["transactions"]]
        dup = sent[0]["import_id"]
        made = [env.ynab_txn("n1", import_id=sent[1]["import_id"])]
        return httpx.Response(201, json={"data": {"transactions": list(reversed(made)), "transaction_ids": ["n1"],
                                                   "duplicate_import_ids": [dup]}})
    env.post_handler = post
    refs = [env.row_ref(rows[0]), env.row_ref(rows[1]), env.row_ref(rows[2], payee_name="  ")]
    out = {r["row_id"]: r for r in env.write(refs).json()["results"]}
    assert out[rows[0]["id"]]["status"] == "existing" and out[rows[0]["id"]]["ynab_transaction_id"] is None
    assert out[rows[1]["id"]]["status"] == "created" and out[rows[1]["id"]]["ynab_transaction_id"] == "n1"
    assert out[rows[2]["id"]]["status"] == "error" and "Payee" in out[rows[2]["id"]]["error"]


def test_id_only_response_resolved_by_reading_transactions(env):
    _, rows = upload(env, COFFEE, BREAD)
    def post(request):
        body = json.loads(request.content)
        env.post_bodies.append(body)
        created = [env.ynab_txn(f"t{i}", import_id=t["import_id"]) for i, t in enumerate(body["transactions"])]
        env.created.extend(created)
        return httpx.Response(201, json={"data": {"transaction_ids": ["t1", "t0"], "duplicate_import_ids": []}})  # reversed ids
    env.post_handler = post
    res = {r["row_id"]: r for r in env.write([env.row_ref(r) for r in rows]).json()["results"]}
    assert res[rows[0]["id"]]["ynab_transaction_id"] == "t0" and res[rows[1]["id"]]["ynab_transaction_id"] == "t1"


def test_missing_or_conflicting_mapping_is_unresolved_never_positional(env):
    _, rows = upload(env, COFFEE, BREAD)
    env.post_handler = lambda req: httpx.Response(201, json={"data": {
        "transaction_ids": ["only-one"], "duplicate_import_ids": []}})
    res = env.write([env.row_ref(r) for r in rows]).json()
    assert res["created"] == 0 and res["unresolved"] == 2
    assert all(r["ynab_transaction_id"] is None for r in res["results"])
    rec = env.db.query(SourceRecord).all()
    assert {r.write_state for r in rec} == {"reconcile_required"}


def test_timeout_then_retry_reconciles_and_reuses_import_ids(env):
    _, rows = upload(env, COFFEE)
    sent_ids = []

    def timeout(request):
        sent_ids.append(json.loads(request.content)["transactions"][0]["import_id"])
        raise httpx.ReadTimeout("slow", request=request)
    env.post_handler = timeout
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["unresolved"] == 1
    assert env.db.query(SourceRecord).one().write_state == "reconcile_required"

    # YNAB did NOT create it: the retry resends the same import id
    env.post_handler = env._default_post
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["created"] == 1 and env.post_bodies[-1]["transactions"][0]["import_id"] == sent_ids[0]

def test_timeout_where_ynab_did_create_is_linked_not_resent(env):
    _, rows = upload(env, COFFEE)
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)
    env.post_handler = timeout
    env.write([env.row_ref(rows[0])])
    env.history.append(env.ynab_txn("landed", amount=-4500, import_id=rows[0]["import_id"]))
    env.post_handler = lambda req: pytest.fail("must not resend")
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["results"][0]["status"] == "existing"
    assert env.db.query(SourceRecord).one().ynab_transaction_id == "landed"


def test_server_error_is_uncertain_but_validation_error_is_retryable(env):
    _, rows = upload(env, COFFEE)
    env.post_handler = lambda req: httpx.Response(500, json={"error": {"detail": "boom"}})
    assert env.write([env.row_ref(rows[0])]).json()["unresolved"] == 1
    env.post_handler = env._default_post
    assert env.write([env.row_ref(rows[0])]).json()["created"] == 1

    _, rows2 = upload(env, BREAD)
    env.post_handler = lambda req: httpx.Response(400, json={"error": {"detail": "Bad request"}})
    res = env.write([env.row_ref(rows2[0])]).json()
    assert res["errors"] == 1 and res["results"][0]["status"] == "error"
    assert env.db.query(SourceRecord).filter_by(import_id=rows2[0]["import_id"]).one().write_state == "error"


def test_in_flight_claim_blocks_simultaneous_push_and_stale_claims_reconcile(env):
    _, rows = upload(env, COFFEE)
    rec = env.db.query(SourceRecord).one()
    rec.write_state, rec.claimed_at = "claimed", datetime.utcnow()
    env.db.commit()
    env.post_handler = lambda req: pytest.fail("a claimed source must not be sent twice")
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["unresolved"] == 1 and "in progress" in res["results"][0]["error"]

    rec = env.db.query(SourceRecord).one()
    rec.claimed_at = datetime.utcnow() - timedelta(minutes=30)       # abandoned by a crash
    env.db.commit()
    env.post_handler = env._default_post
    assert env.write([env.row_ref(rows[0])]).json()["created"] == 1


def test_restart_marks_all_claims_for_reconciliation(env):
    upload(env, COFFEE)
    rec = env.db.query(SourceRecord).one()
    rec.write_state, rec.claimed_at = "claimed", datetime.utcnow()
    env.db.commit()
    assert recover_stale_claims(env.db, older_than=None) == 1
    assert env.db.query(SourceRecord).one().write_state == "reconcile_required"


def test_duplicate_row_submission_sends_once(env):
    _, rows = upload(env, COFFEE)
    res = env.write([env.row_ref(rows[0]), env.row_ref(rows[0])]).json()
    assert [r["status"] for r in res["results"]] == ["created", "error"]
    assert len(env.post_bodies[0]["transactions"]) == 1


def test_outcomes_persist_across_reload_and_failures_keep_edits(env):
    out, rows = upload(env, COFFEE, BREAD, SHOP)
    env.client.patch("/import-rows", json={"updates": [
        {"id": r["id"], "status": "accepted", "edited_payee": "Edited", "edited_memo": "kept"} for r in rows]})
    env.history.append(env.ynab_txn("old", amount=-3000, import_id=rows[1]["import_id"]))   # bread: duplicate
    def post(request):
        body = json.loads(request.content)
        env.post_bodies.append(body)
        txns = [env.ynab_txn("c1", import_id=body["transactions"][0]["import_id"])]
        return httpx.Response(201, json={"data": {"transactions": txns, "transaction_ids": ["c1"],
                                                   "duplicate_import_ids": []}})
    env.post_handler = post
    refs = [env.row_ref(rows[0], payee_name="Edited"), env.row_ref(rows[1], payee_name="Edited"),
            env.row_ref(rows[2], payee_name=" ")]
    res = env.write(refs).json()
    assert (res["created"], res["existing"], res["errors"]) == (1, 1, 1)
    # completion is rejected while the failed row remains
    assert env.client.post(f"/import-batches/{out['batch_id']}/complete").status_code == 409
    reload = env.client.get("/import-batches/plan-1/active").json()["rows"]
    assert [r["id"] for r in reload] == [rows[2]["id"]]
    assert reload[0]["edited_payee"] == "Edited" and reload[0]["edited_memo"] == "kept"
    assert reload[0]["write_state"] == "error"
    # a reclassify does not resurrect handled rows
    again = env.client.post("/reclassify", json={"plan_id": "plan-1"}).json()["rows"]
    assert [r["id"] for r in again] == [rows[2]["id"]]
    # fix and retry, then the batch completes
    res = env.write([env.row_ref(rows[2], payee_name="Edited")]).json()
    assert res["created"] == 1
    assert env.client.post(f"/import-batches/{out['batch_id']}/complete").status_code == 200


def test_duplicate_only_success_completes_rows(env):
    out, rows = upload(env, COFFEE)
    env.history.append(env.ynab_txn("o", amount=-4500, import_id=rows[0]["import_id"]))
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["existing"] == 1 and res["created"] == 0
    assert env.client.get("/import-batches/plan-1/active").json()["rows"] == []
    assert env.client.post(f"/import-batches/{out['batch_id']}/complete").status_code == 200


def test_ignored_rows_do_not_block_completion(env):
    out, rows = upload(env, COFFEE, BREAD)
    env.client.patch("/import-rows", json={"updates": [{"id": rows[1]["id"], "status": "ignored"}]})
    env.write([env.row_ref(rows[0])])
    assert env.client.post(f"/import-batches/{out['batch_id']}/complete").status_code == 200


def test_history_refresh_failure_blocks_writes(env):
    _, rows = upload(env, COFFEE)
    env.history_error = True
    env.post_handler = lambda req: pytest.fail("no write without verified history")
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["unresolved"] == 1 and "refreshed" in res["results"][0]["error"]
    dry = env.write([env.row_ref(rows[0])], mode="dry_run").json()
    assert dry["results"][0]["status"] == "dry_run_error"


def test_dry_run_does_not_persist_links_or_completion(env):
    _, rows = upload(env, COFFEE)
    env.history.append(env.ynab_txn("o", amount=-4500, import_id=rows[0]["import_id"]))
    res = env.write([env.row_ref(rows[0])], mode="dry_run").json()
    assert res["results"][0]["status"] == "existing"
    r = env.db.query(ImportRow).one()
    env.db.refresh(r)
    assert r.write_state == "unsent" and env.db.query(SourceRecord).one().write_state == "unsent"


def test_accepted_prediction_stays_on_the_record_after_creation(env):
    from src.models import Rule
    env.db.add(Rule(plan_id="plan-1", name="Shop", priority=1, match_type="contains", pattern="SHOP ONE",
                    assign_payee="Shop", assign_category="Groceries"))
    env.db.commit()
    _, rows = upload(env, SHOP)
    assert (rows[0]["payee"], rows[0]["category"]) == ("Shop", "Groceries")
    env.client.patch("/import-rows", json={"updates": [{"id": rows[0]["id"], "status": "accepted"}]})
    assert env.write([env.row_ref(rows[0], payee_name="Shop", category_name="Groceries")]).json()["created"] == 1
    sent = env.post_bodies[0]["transactions"][0]
    assert sent["approved"] is False and sent["category_id"] == "y-c1"
    stored = env.db.query(ImportRow).filter_by(id=rows[0]["id"]).one()
    env.db.refresh(stored)
    assert (stored.payee, stored.category, stored.status) == ("Shop", "Groceries", "accepted")
    assert stored.write_state == "created" and stored.ynab_transaction_id
    assert (stored.sent_payee, stored.sent_category) == ("Shop", "Groceries")      # what was actually sent


def test_sent_values_are_recorded_even_when_the_request_differs_from_the_stored_row(env):
    _, rows = upload(env, SHOP)
    assert env.write([env.row_ref(rows[0], payee_name="Shop", category_name="Groceries")]).json()["created"] == 1
    stored = env.db.query(ImportRow).filter_by(id=rows[0]["id"]).one()
    env.db.refresh(stored)
    assert stored.payee != "Shop" and (stored.sent_payee, stored.sent_category) == ("Shop", "Groceries")


def test_write_back_uses_the_stored_category_id_even_after_a_rename(env):
    from src.models import Rule
    env.db.add(Rule(plan_id="plan-1", name="Shop", priority=1, match_type="contains", pattern="SHOP ONE",
                    assign_payee="Shop", assign_category="Groceries"))
    env.db.commit()
    _, rows = upload(env, SHOP)
    assert (rows[0]["category"], rows[0]["category_id"], rows[0]["edited_category_id"]) == ("Groceries", "c1", "c1")
    env.groceries.name = "Food"                         # renamed in YNAB after the upload
    env.db.commit()
    assert env.write([env.row_ref(rows[0], payee_name="Shop", category_name="Groceries")]).json()["created"] == 1
    assert env.post_bodies[0]["transactions"][0]["category_id"] == "y-c1"
    stored = env.db.query(ImportRow).filter_by(id=rows[0]["id"]).one()
    env.db.refresh(stored)
    assert stored.sent_category_id == "c1"


def test_picker_edit_by_id_and_deleted_category_blocks_the_push(env):
    from src.models import Category
    env.db.add(Category(id="c2", category_group_id="g1", ynab_category_id="y-c2", name="Transport"))
    env.db.commit()
    _, rows = upload(env, SHOP)
    r = env.client.patch("/import-rows", json={"updates": [{"id": rows[0]["id"], "status": "accepted", "edited_category_id": "c2"}]})
    assert r.status_code == 200
    row = env.client.get("/import-batches/plan-1/active").json()["rows"][0]
    assert (row["edited_category"], row["edited_category_id"]) == ("Transport", "c2")
    bad = env.client.patch("/import-rows", json={"updates": [{"id": rows[0]["id"], "edited_category_id": "nope"}]})
    assert bad.status_code == 422
    env.db.get(Category, "c2").deleted = True
    env.db.commit()
    res = env.write([env.row_ref(row, payee_name="Shop", category_id="c2")]).json()
    assert res["errors"] == 1 and "deleted" in res["results"][0]["error"] and env.post_bodies == []
    # by-name edit from an older client resolves to the unique live category
    env.client.patch("/import-rows", json={"updates": [{"id": rows[0]["id"], "edited_category": "Groceries"}]})
    row = env.client.get("/import-batches/plan-1/active").json()["rows"][0]
    assert row["edited_category_id"] == "c1"
    assert env.write([env.row_ref(row, payee_name="Shop")]).json()["created"] == 1
    assert env.post_bodies[-1]["transactions"][0]["category_id"] == "y-c1"
