"""Legacy-import matching, conflict resolution and repayment routing (5.3, 5.4, 6.1, 6.2)."""
import pytest

from src.models import SourceRecord, ImportRow, ImportRow
from tests.swisscard_fixture import build, row

COFFEE = dict(desc="Coffee", merchant="Cafe", amount="4.50")
REPAY = dict(desc="Payment Thank You", merchant="", amount="-6000.00", dc="Credit")
REFUND = dict(desc="Refund Cafe", merchant="Cafe", amount="-4.50", dc="Credit")


def upload(env, *specs, account_id=None):
    out = env.upload(build([row(**s) for s in specs]), account_id=account_id).json()
    return out, out["rows"]


def reconcile(env, batch_id):
    r = env.client.post(f"/import-batches/{batch_id}/reconcile")
    assert r.status_code == 200
    return {x["original_memo"]: x for x in r.json()["rows"]}, r.json()


def test_unambiguous_legacy_match_is_linked_without_writing(env):
    out, rows = upload(env, COFFEE)
    env.history.append(env.ynab_txn("legacy-1", amount=-4500, memo="CAFE COFFEE", import_id="YNAB:-4500:2026-10-01:1"))
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["existing"] == 1 and res["results"][0]["ynab_transaction_id"] == "legacy-1"
    assert env.post_bodies == []
    assert env.db.query(SourceRecord).one().ynab_transaction_id == "legacy-1"


def test_equal_date_and_amount_alone_is_only_potential_and_blocks(env):
    out, rows = upload(env, COFFEE)
    env.history.append(env.ynab_txn("other", amount=-4500, memo="Totally different thing"))
    by, _ = reconcile(env, out["batch_id"])
    assert by["Coffee"]["reconciliation_state"] == "potential"
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["unresolved"] == 1 and env.post_bodies == []
    # user says it is a distinct purchase: immutable bank values untouched, write proceeds
    r = env.client.post(f"/import-rows/{rows[0]['id']}/resolve", json={"action": "confirm_new"})
    assert r.status_code == 200 and r.json()["amount_milliunits"] == -4500
    assert env.write([env.row_ref(rows[0])]).json()["created"] == 1


def test_user_can_link_an_existing_record(env):
    out, rows = upload(env, COFFEE)
    env.history.append(env.ynab_txn("other", amount=-4500, memo="Totally different thing"))
    reconcile(env, out["batch_id"])
    bad = env.client.post(f"/import-rows/{rows[0]['id']}/resolve", json={"action": "link_existing", "ynab_transaction_id": "nope"})
    assert bad.status_code == 422
    ok = env.client.post(f"/import-rows/{rows[0]['id']}/resolve", json={"action": "link_existing", "ynab_transaction_id": "other"})
    assert ok.status_code == 200 and ok.json()["write_state"] == "existing"
    assert env.db.query(SourceRecord).one().ynab_transaction_id == "other"
    assert env.client.get("/import-batches/plan-1/active").json()["rows"] == []


def test_multiple_candidates_and_multiplicity_conflicts_are_not_auto_linked(env):
    out, rows = upload(env, COFFEE)
    env.history += [env.ynab_txn("a", amount=-4500, memo="COFFEE CAFE"), env.ynab_txn("b", amount=-4500, memo="COFFEE CAFE")]
    assert reconcile(env, out["batch_id"])[0]["Coffee"]["reconciliation_state"] == "potential"

    env.history.clear()
    out2, rows2 = upload(env, COFFEE, COFFEE)          # two purchases, one prior record
    env.history.append(env.ynab_txn("a", amount=-4500, memo="COFFEE CAFE"))
    states = [r["reconciliation_state"] for r in reconcile(env, out2["batch_id"])[1]["rows"]]
    assert states == ["potential", "potential"]


def test_our_own_ids_never_count_as_legacy_matches(env):
    out, rows = upload(env, COFFEE)
    env.history.append(env.ynab_txn("ours", amount=-4500, memo="COFFEE CAFE", import_id="YF1:somebodyelse:1"))
    assert reconcile(env, out["batch_id"])[0]["Coffee"]["reconciliation_state"] == "new"


def test_deleted_link_needs_explicit_resolution_and_gets_a_fresh_import_id(env):
    out, rows = upload(env, COFFEE)
    rec = env.db.query(SourceRecord).one()
    rec.ynab_transaction_id = "gone"
    env.db.commit()
    env.history.append(env.ynab_txn("gone", amount=-4500, deleted=True))
    by, _ = reconcile(env, out["batch_id"])
    assert by["Coffee"]["reconciliation_state"] == "deleted_link"
    assert env.write([env.row_ref(rows[0])]).json()["unresolved"] == 1 and env.post_bodies == []
    r = env.client.post(f"/import-rows/{rows[0]['id']}/resolve", json={"action": "confirm_new"}).json()
    assert r["import_id"] != rows[0]["import_id"]
    assert env.write([env.row_ref(rows[0])]).json()["created"] == 1


def test_confirm_new_refused_while_history_unverified(env):
    out, rows = upload(env, COFFEE)
    env.history_error = True
    env.write([env.row_ref(rows[0])])
    r = env.client.post(f"/import-rows/{rows[0]['id']}/resolve", json={"action": "confirm_new"})
    assert r.status_code == 409


def test_ambiguous_partial_overlap_resolution(env):
    out, rows = upload(env, COFFEE, COFFEE)
    env.write([env.row_ref(rows[0])])                       # first coffee is now in YNAB
    out2, rows2 = upload(env, COFFEE, dict(desc="Other", merchant="O", amount="9.00"))
    coffee = [r for r in rows2 if r["original_memo"] == "Coffee"][0]
    assert coffee["reconciliation_state"] == "ambiguous" and "previously seen 2" in coffee["reconciliation_note"]
    assert env.write([env.row_ref(coffee)]).json()["unresolved"] == 1
    r = env.client.post(f"/import-rows/{coffee['id']}/resolve", json={"action": "confirm_new"}).json()
    assert r["import_id"] not in {x["import_id"] for x in rows}


# --- repayments ---

def test_blank_merchant_repayment_stays_visible_and_blocked_refund_is_ordinary(env):
    out, rows = upload(env, REPAY, REFUND)
    by = {r["original_memo"]: r for r in rows}
    repay, refund = by["Payment Thank You"], by["Refund Cafe"]
    assert repay["transfer_state"] == "candidate" and repay["amount_milliunits"] == 6000000
    assert repay["category"] is None and repay["payee"] is None and repay["status"] == "pending"
    assert repay["evidence"]["Merchant"] == ""
    assert refund["transfer_state"] == "none" and refund["amount_milliunits"] == 4500
    res = env.write([env.row_ref(repay, payee_name="Anything")]).json()
    assert res["unresolved"] == 1 and env.post_bodies == []


def test_existing_post_side_transfer_links_card_side_without_creating(env):
    out, rows = upload(env, REPAY)
    env.history += [
        env.ynab_txn("post-side", account="y-post", amount=-6000000, transfer_account="y-card", transfer_txn="card-side", payee="y-p-xfer"),
        env.ynab_txn("card-side", account="y-card", amount=6000000, transfer_account="y-post", transfer_txn="post-side"),
    ]
    _, body = reconcile(env, out["batch_id"])
    assert body["linked_count"] == 1 and body["rows"] == []          # linked rows leave the queue
    row_ = env.db.query(ImportRow).one()
    assert row_.transfer_state == "linked" and row_.reconciliation_state == "linked"
    assert row_.transfer_account_id == env.post.id
    res = env.write([env.row_ref(rows[0])]).json()
    assert res["existing"] == 1 and res["results"][0]["ynab_transaction_id"] == "card-side" and env.post_bodies == []


def test_multiple_counterparts_block_the_write(env):
    out, rows = upload(env, REPAY)
    for n in (1, 2):
        env.history.append(env.ynab_txn(f"post-{n}", account="y-post", amount=-6000000, transfer_account="y-card", transfer_txn=f"card-{n}"))
    by, _ = reconcile(env, out["batch_id"])
    assert by["Payment Thank You"]["transfer_state"] == "ambiguous"
    assert env.write([env.row_ref(rows[0])]).json()["unresolved"] == 1 and env.post_bodies == []


def test_confirmed_new_transfer_writes_one_side_with_null_category(env):
    out, rows = upload(env, REPAY)
    env.history.append(env.ynab_txn("hist", account="y-post", amount=-100, transfer_account="y-card", transfer_txn="x", date="2026-01-01"))
    r = env.client.post(f"/import-rows/{rows[0]['id']}/transfer", json={"account_id": env.post.id}).json()
    assert r["transfer_state"] == "resolved" and r["transfer_account_id"] == env.post.id
    res = env.write([env.row_ref(rows[0], payee_name="", category_name="Groceries")]).json()
    assert res["created"] == 1 and len(env.post_bodies[0]["transactions"]) == 1
    sent = env.post_bodies[0]["transactions"][0]
    assert sent["payee_id"] == "y-p-xfer" and sent["category_id"] is None
    assert sent["amount"] == 6000000 and sent["account_id"] == "y-card" and sent["approved"] is False


def test_transfer_routing_validation_and_keep_for_later(env):
    out, rows = upload(env, REPAY)
    rid = rows[0]["id"]
    assert env.client.post(f"/import-rows/{rid}/transfer", json={"account_id": env.card.id}).status_code == 422   # itself
    assert env.client.post(f"/import-rows/{rid}/transfer", json={"account_id": "nope"}).status_code == 422
    env.card.closed = False
    kept = env.client.post(f"/import-rows/{rid}/transfer", json={"account_id": None}).json()
    assert kept["transfer_state"] == "candidate" and kept["transfer_account_id"] is None
    ordinary = upload(env, COFFEE)[1][0]
    assert env.client.post(f"/import-rows/{ordinary['id']}/transfer", json={"account_id": env.post.id}).status_code == 409


def test_reconcile_endpoint_reports_warning_and_suggestions(env):
    out, rows = upload(env, REPAY)
    env.history.append(env.ynab_txn("h", account="y-post", amount=-5, transfer_account="y-card", transfer_txn="z", date="2026-01-01"))
    _, body = reconcile(env, out["batch_id"])
    assert body["warning"] is None and body["transfer_account_suggestions"] == [env.post.id]
    env.history_error = True
    _, body = reconcile(env, out["batch_id"])
    assert body["warning"] and body["rows"][0]["reconciliation_state"] == "unverified"
