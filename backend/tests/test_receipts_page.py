"""Receipts page API: list and filters, detail, corrections with re-matching, candidates, manual links,
classification with rescue from the fake Bridge, and AI re-read with the fake transport."""
import json

import pytest

from src.ai.extract import ReceiptOut
from src.main import app
from src.models import AIUsage, Category, ImportRow, MailboxCursor, Receipt, ReceiptMatch, ReceiptSource
from src.receipts import review
from tests import mail_fixtures as fx
from tests.ai_fakes import FakeTransport, refusal
from tests.fake_imap import FakeAccount, FakeFolder, FakeImapServer
from tests.swisscard_fixture import build, row

DECATHLON = dict(date="18.10.2026", desc="Decathlon Online", merchant="Decathlon", amount="100.00")


def add_receipt(env, *, mailbox="me", **kw):
    values = dict(content_hash=f"h-{len(env.db.query(Receipt).all())}", status="extracted", kind="purchase",
                  merchant="Decathlon", reference="D-1", purchase_date="2026-10-16", total_milliunits=100000,
                  currency="CHF", subject="Your order", sender="shop@example.com",
                  items_json=json.dumps([{"description": "Tent", "quantity": 1, "amount_milliunits": 100000}]))
    values.update(kw)
    receipt = Receipt(**values)
    review.apply_extraction(receipt, {"merchant": receipt.merchant, "total_milliunits": receipt.total_milliunits,
                                      "purchase_date": receipt.purchase_date, "status": receipt.status})
    env.db.add(receipt)
    env.db.flush()
    env.db.add(ReceiptSource(receipt_id=receipt.id, mailbox=mailbox, folder="INBOX", uidvalidity=1,
                             uid=len(env.db.query(ReceiptSource).all()) + 1, message_id=f"<{receipt.content_hash}@x>"))
    env.db.commit()
    return receipt


def listing(env, **params):
    return env.client.get("/receipts", params=params).json()


def test_filters_needs_check_and_unlinked_purchases(env):
    add_receipt(env, status="needs_check", local_total_milliunits=120000, total_milliunits=119000)
    linked = add_receipt(env, merchant="Migros", total_milliunits=84900)
    add_receipt(env, kind="shipping", merchant="Post")
    add_receipt(env, status="not_receipt", merchant="", total_milliunits=None, kind="purchase")
    out = env.upload(build([row(date="05.10.2026", desc="Migros", merchant="Migros", amount="84.90")])).json()
    env.client.post(f"/receipts/{linked.id}/links", json={"row_id": out["rows"][0]["id"], "action": "link"})
    assert [r["status"] for r in listing(env, status="needs_check")["receipts"]] == ["needs_check"]
    unlinked = listing(env, kind="purchase", linked="false")["receipts"]
    assert sorted(r["merchant"] for r in unlinked) == ["", "Decathlon"]    # the linked Migros order is left out
    assert all(r["linked_rows"] == 0 and r["kind"] == "purchase" for r in unlinked)
    assert listing(env, linked="true")["receipts"][0]["merchant"] == "Migros"
    assert listing(env, q="post")["total"] == 1 and listing(env, mailbox="partner")["total"] == 0
    page = listing(env, page_size=2, page=2)
    assert page["total"] == 4 and len(page["receipts"]) == 2


def test_list_and_detail_never_expose_credentials(env, monkeypatch):
    monkeypatch.setenv("BRIDGE_MAILBOXES", "me")
    monkeypatch.setenv("BRIDGE_ME_USER", "me@proton.me")
    monkeypatch.setenv("BRIDGE_ME_PASSWORD", "bridge-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    receipt = add_receipt(env)
    for body in (env.client.get("/receipts").text, env.client.get(f"/receipts/{receipt.id}").text):
        for secret in ("bridge-secret", "sk-ant-secret", "me@proton.me"):
            assert secret not in body
    detail = env.client.get(f"/receipts/{receipt.id}").json()
    assert detail["sources"] == [{"mailbox": "me", "folder": "INBOX"}]


def test_detail_shows_both_totals_of_a_doubtful_receipt(env):
    receipt = add_receipt(env, status="needs_check", total_milliunits=119000, local_total_milliunits=120000)
    detail = env.client.get(f"/receipts/{receipt.id}").json()
    assert (detail["total_milliunits"], detail["local_total_milliunits"]) == (119000, 120000)
    assert detail["items"][0]["description"] == "Tent" and detail["extracted"]["merchant"] == "Decathlon"
    assert detail["text_excerpt"] == "" and detail["subject"] == "Your order"


def test_possible_duplicates_are_marked_not_excluded(env):
    first = add_receipt(env)
    add_receipt(env, purchase_date="2026-10-18", subject="Your invoice")
    rows = {r["id"]: r for r in listing(env)["receipts"]}
    assert len(rows[first.id]["possible_duplicates"]) == 1 and not rows[first.id]["excluded"]


def test_corrected_total_now_matches_an_active_row(env):
    out = env.upload(build([row(**{**DECATHLON, "amount": "51.00", "desc": "Gravity Park", "merchant": "Gravity Park"})])).json()
    receipt = add_receipt(env, merchant="Gravity Park", total_milliunits=0, purchase_date="2026-10-16")
    assert env.db.query(ReceiptMatch).count() == 0
    res = env.client.patch(f"/receipts/{receipt.id}", json={"fields": {"total_milliunits": 51000}})
    assert res.status_code == 200 and res.json()["user_fields"] == ["total_milliunits"]
    match = env.db.query(ReceiptMatch).filter_by(receipt_id=receipt.id).one()
    assert match.import_row_id == out["rows"][0]["id"] and match.state == "linked"
    stored = env.db.get(ImportRow, out["rows"][0]["id"])
    assert stored.amount_milliunits == -51000                 # bank values unchanged


def test_rejected_pairing_stays_rejected_after_a_correction(env):
    out = env.upload(build([row(**DECATHLON)])).json()
    receipt = add_receipt(env)
    row_id = out["rows"][0]["id"]
    env.client.post(f"/receipts/{receipt.id}/links", json={"row_id": row_id, "action": "reject"})
    env.client.patch(f"/receipts/{receipt.id}", json={"fields": {"purchase_date": "2026-10-17"}})
    assert env.db.query(ReceiptMatch).filter_by(receipt_id=receipt.id, import_row_id=row_id).one().state == "rejected"


def test_invalid_correction_and_reset_and_exclusion(env):
    receipt = add_receipt(env)
    bad = env.client.patch(f"/receipts/{receipt.id}", json={"fields": {"merchant": "X", "total_milliunits": -5}})
    assert bad.status_code == 422 and "positive" in bad.json()["detail"]
    assert env.client.get(f"/receipts/{receipt.id}").json()["merchant"] == "Decathlon"
    env.client.patch(f"/receipts/{receipt.id}", json={"fields": {"merchant": "Decathlon Lausanne"}, "excluded": True})
    detail = env.client.patch(f"/receipts/{receipt.id}", json={"reset": ["merchant"]}).json()
    assert detail["merchant"] == "Decathlon" and detail["user_fields"] == [] and detail["excluded"] is True


def test_link_a_pushed_row_as_evidence_only(env):
    out = env.upload(build([row(**DECATHLON)])).json()
    r = out["rows"][0]
    assert env.write([env.row_ref(r, payee_name="Decathlon", category_name="Groceries")]).json()["created"] == 1
    posts = len(env.post_bodies)
    pushed = env.db.get(ImportRow, r["id"])
    before = (pushed.write_state, pushed.amount_milliunits, pushed.ynab_transaction_id, pushed.date)
    receipt = add_receipt(env)
    cands = env.client.get(f"/receipts/{receipt.id}/candidates").json()["candidates"]
    assert cands[0]["row"]["id"] == r["id"] and cands[0]["row"]["in_ynab"] and cands[0]["score"] >= 0.45
    detail = env.client.post(f"/receipts/{receipt.id}/links", json={"row_id": r["id"], "action": "link"}).json()
    assert detail["matches"][0]["state"] == "linked" and detail["matches"][0]["row"]["category"] == "Groceries"
    env.db.refresh(pushed)
    assert (pushed.write_state, pushed.amount_milliunits, pushed.ynab_transaction_id, pushed.date) == before
    assert len(env.post_bodies) == posts                       # nothing re-sent to YNAB


def test_automatic_matching_skips_pushed_rows(env):
    out = env.upload(build([row(**DECATHLON)])).json()
    env.write([env.row_ref(out["rows"][0], payee_name="Decathlon", category_name="Groceries")])
    receipt = add_receipt(env)
    env.client.patch(f"/receipts/{receipt.id}", json={"fields": {"merchant": "Decathlon"}})
    assert env.db.query(ReceiptMatch).filter_by(receipt_id=receipt.id).count() == 0


# --- classification, rescue and AI re-read (fake Bridge, fake Claude) ---

INTERIO = ReceiptOut(is_receipt=True, merchant="Interio", reference="IN-7", kind="purchase",
                     purchase_date="2026-10-03", total="59.00", currency="CHF", items=[])


@pytest.fixture()
def bridge(env, monkeypatch, tmp_path):
    inbox = FakeFolder()
    inbox.add(fx.newsletter())
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("bridge-pw", {"INBOX": inbox})}) as server:
        for key, value in {"BRIDGE_IMAP_PORT": str(server.port), "BRIDGE_MAILBOXES": "me",
                           "BRIDGE_ME_USER": "me@proton.me", "BRIDGE_ME_PASSWORD": "bridge-pw"}.items():
            monkeypatch.setenv(key, value)
        env.client.post("/receipts/fetch")
        server.inbox = inbox
        yield server
    app.state.ai_transport = None


def with_ai(monkeypatch, *results):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app.state.ai_transport = FakeTransport(*results)
    return app.state.ai_transport


def skipped(env):
    return env.db.query(Receipt).filter_by(status="not_receipt").one()


def test_rescue_a_wrongly_skipped_message(env, bridge, monkeypatch):
    with_ai(monkeypatch, INTERIO)
    message = skipped(env)
    detail = env.client.post(f"/receipts/{message.id}/classify", json={"as": "receipt"}).json()
    assert detail["status"] == "extracted" and detail["merchant"] == "Interio" and detail["note"] == ""
    assert detail["total_milliunits"] == 59000 and "classification" in detail["user_fields"]
    assert "Discover our new collection" in detail["text_excerpt"]
    assert not any(m.flags for m in bridge.inbox.messages.values())        # read-only all the way
    env.db.query(MailboxCursor).delete()
    env.db.commit()
    env.client.post("/receipts/fetch")                                    # a later fetch keeps the decision
    env.db.expire_all()
    assert env.db.get(Receipt, message.id).status == "extracted"


def test_rescue_waits_when_bridge_is_not_running(env, bridge, monkeypatch):
    message = skipped(env)
    monkeypatch.setenv("BRIDGE_IMAP_PORT", "1")
    detail = env.client.post(f"/receipts/{message.id}/classify", json={"as": "receipt"}).json()
    assert detail["status"] == "candidate" and detail["note"] and detail["error"] == detail["note"]
    monkeypatch.setenv("BRIDGE_IMAP_PORT", str(bridge.port))           # retry once Bridge is back
    assert env.client.post(f"/receipts/{message.id}/classify", json={"as": "receipt"}).json()["error"] == ""


def test_not_a_receipt_is_kept(env, bridge):
    receipt = add_receipt(env)
    detail = env.client.post(f"/receipts/{receipt.id}/classify", json={"as": "not_receipt"}).json()
    assert detail["status"] == "not_receipt" and "classification" in detail["user_fields"]
    assert env.client.post(f"/receipts/{receipt.id}/classify", json={"as": "maybe"}).status_code == 422


def test_reread_keeps_user_fields_and_records_usage(env, bridge, monkeypatch):
    with_ai(monkeypatch, INTERIO)
    message = skipped(env)
    env.client.post(f"/receipts/{message.id}/classify", json={"as": "receipt"})
    env.client.patch(f"/receipts/{message.id}", json={"fields": {"merchant": "Interio Lausanne"}})
    estimate = env.client.post(f"/receipts/{message.id}/reread", json={"estimate_only": True}).json()
    assert estimate["requests"] == 1 and estimate["estimate_usd"] > 0
    app.state.ai_transport.results = [ReceiptOut(**{**INTERIO.model_dump(), "merchant": "INTERIO AG", "total": "61.00"})]
    detail = env.client.post(f"/receipts/{message.id}/reread", json={}).json()
    assert detail["merchant"] == "Interio Lausanne" and detail["total_milliunits"] == 61000
    assert detail["extracted"]["merchant"] == "INTERIO AG"
    assert env.db.query(AIUsage).filter_by(purpose="extract").count() == 2


@pytest.mark.parametrize("budget, answer, status", [("0", INTERIO, 409), ("10", refusal(), 502)])
def test_failed_reread_changes_nothing(env, bridge, monkeypatch, budget, answer, status):
    receipt = add_receipt(env, merchant="Decathlon", text_excerpt="Order total CHF 100.00")
    with_ai(monkeypatch, answer)
    monkeypatch.setenv("AI_MONTHLY_BUDGET_USD", budget)
    before = env.client.get(f"/receipts/{receipt.id}").json()
    res = env.client.post(f"/receipts/{receipt.id}/reread", json={})
    assert res.status_code == status and res.json()["detail"]
    after = env.client.get(f"/receipts/{receipt.id}").json()
    assert {k: after[k] for k in ("merchant", "total_milliunits", "items", "status")} == \
        {k: before[k] for k in ("merchant", "total_milliunits", "items", "status")}


# --- item categories ---


def household(env):
    env.db.add(Category(id="c2", category_group_id="g1", ynab_category_id="y-c2", name="Household"))
    env.db.commit()


def migros_receipt(env):
    return add_receipt(env, merchant="Migros", total_milliunits=84900, purchase_date="2026-10-03", items_json=json.dumps([
        {"description": "Bio Milch", "quantity": 1, "amount_milliunits": 62400},
        {"description": "Javel", "quantity": 1, "amount_milliunits": 22500}]))


def migros_row(env, receipt):
    out = env.upload(build([row(date="05.10.2026", desc="Migros Online", merchant="Migros", amount="84.90")])).json()
    row_id = out["rows"][0]["id"]
    env.client.post(f"/receipts/{receipt.id}/links", json={"row_id": row_id, "action": "link"})
    return row_id


def categories(env, receipt):
    return [(i["description"], i["category_name"], i["category_source"])
            for i in env.client.get(f"/receipts/{receipt.id}").json()["items"]]


def test_items_take_split_lines_then_row_category(env):
    household(env)
    receipt = migros_receipt(env)
    assert categories(env, receipt) == [("Bio Milch", None, "uncategorized"), ("Javel", None, "uncategorized")]
    row_id = migros_row(env, receipt)
    env.client.put(f"/import-rows/{row_id}/splits", json={"lines": [
        {"amount_milliunits": -62400, "category_id": "c1", "memo": "bio milch"},
        {"amount_milliunits": -22500, "category_id": "c2", "memo": "Javel"}]})
    assert categories(env, receipt) == [("Bio Milch", "Groceries", "split"), ("Javel", "Household", "split")]
    env.client.delete(f"/import-rows/{row_id}/splits")
    env.db.get(ImportRow, row_id).edited_category_id = "c2"
    env.db.commit()
    assert categories(env, receipt) == [("Bio Milch", "Household", "row"), ("Javel", "Household", "row")]


def test_split_lines_by_position_when_descriptions_differ(env):
    household(env)
    receipt = migros_receipt(env)
    row_id = migros_row(env, receipt)
    env.client.put(f"/import-rows/{row_id}/splits", json={"lines": [
        {"amount_milliunits": -62400, "category_id": "c1", "memo": "Food"},
        {"amount_milliunits": -22500, "category_id": "c2", "memo": "Cleaning"}]})
    assert [c[1] for c in categories(env, receipt)] == ["Groceries", "Household"]


def test_deleted_category_is_reported_and_left_uncategorized(env):
    household(env)
    receipt = migros_receipt(env)
    env.client.put(f"/receipts/{receipt.id}/items/1/category", json={"category_id": "c2"})
    env.db.get(Category, "c2").deleted = True
    env.db.commit()
    item = env.client.get(f"/receipts/{receipt.id}").json()["items"][1]
    assert item["category_source"] == "uncategorized" and "Household" in item["note"] and "deleted" in item["note"]


def test_user_category_wins_and_freezes_the_items(env, bridge, monkeypatch):
    household(env)
    receipt = migros_receipt(env)
    row_id = migros_row(env, receipt)
    env.db.get(ImportRow, row_id).edited_category_id = "c1"
    env.db.commit()
    detail = env.client.put(f"/receipts/{receipt.id}/items/1/category", json={"category_id": "c2"}).json()
    assert [(i["category_name"], i["category_source"]) for i in detail["items"]] == \
        [("Groceries", "row"), ("Household", "user")]
    assert "items" in detail["user_fields"]
    assert env.client.put(f"/receipts/{receipt.id}/items/1/category", json={"category_id": "nope"}).status_code == 422
    assert env.client.put(f"/receipts/{receipt.id}/items/9/category", json={"category_id": "c2"}).status_code == 404
    with_ai(monkeypatch, ReceiptOut(is_receipt=True, merchant="Migros", reference="", kind="purchase",
                                    purchase_date="2026-10-03", total="84.90", currency="CHF", items=[]))
    env.db.get(Receipt, receipt.id).text_excerpt = "Migros total CHF 84.90"
    env.db.commit()
    reread = env.client.post(f"/receipts/{receipt.id}/reread", json={}).json()   # the AI saw no items
    assert [i["description"] for i in reread["items"]] == ["Bio Milch", "Javel"]
    assert reread["items"][1]["category_source"] == "user"
    cleared = env.client.put(f"/receipts/{receipt.id}/items/1/category", json={"category_id": None}).json()
    assert cleared["items"][1]["category_source"] == "row"
