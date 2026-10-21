"""Upload / reload / reclassify / account-context contracts (tasks 3.3, 4.4, 4.5)."""
import json
from src.models import Rule, ImportBatch, ImportRow
from tests.swisscard_fixture import build, row, export_174


def test_upload_requires_valid_target_account(env):
    r = env.client.post("/upload-csv?plan_id=plan-1", files={"file": ("a.csv", b"x", "text/csv")})
    assert r.status_code == 422                                   # account_id missing
    assert env.upload(build([row()]), account_id="nope").status_code == 422


def test_cross_plan_account_rejected_and_active_batch_kept(env):
    from src.models import Plan, Account
    env.db.add(Plan(id="plan-2", ynab_plan_id="b2", name="Other"))
    env.db.add(Account(id="acc-other", plan_id="plan-2", ynab_account_id="y-o", name="Other", type="checking"))
    env.db.commit()
    first = env.upload(build([row()]))
    assert first.status_code == 200
    bad = env.upload(build([row(desc="Other")]), account_id="acc-other")
    assert bad.status_code == 422
    active = env.client.get("/import-batches/plan-1/active").json()
    assert active["batch_id"] == first.json()["batch_id"]


def test_unsupported_layout_and_bad_encoding_keep_active_batch(env):
    first = env.upload(build([row()])).json()
    bad = env.upload("Foo,Bar\n1,2\n")
    assert bad.status_code == 422 and "Supported formats" in bad.json()["detail"]
    latin = env.client.post(f"/upload-csv?plan_id=plan-1&account_id={env.card.id}",
                            files={"file": ("a.csv", "Description\xe9".encode("latin-1"), "text/csv")})
    assert latin.status_code == 422
    assert env.client.get("/import-batches/plan-1/active").json()["batch_id"] == first["batch_id"]


def test_upload_persists_account_counts_and_evidence(env):
    r = env.upload(export_174()).json()
    assert r["account_id"] == env.card.id and r["account_name"] == "Swiss Card"
    assert r["source_format"] == "swisscard" and r["count"] == 167
    report = r["report"]
    assert (report["detected"], report["eligible"], report["pending"], report["invalid"]) == (174, 167, 7, 0)
    assert len(report["pending_rows"]) == 7
    first = r["rows"][0]
    assert first["amount_milliunits"] == -10500 and first["evidence"]["Card number"]
    # reload returns the same persisted context
    again = env.client.get("/import-batches/plan-1/active").json()
    assert again["account_id"] == env.card.id and again["report"]["pending"] == 7 and len(again["rows"]) == 167
    assert ImportBatch  # sanity


def test_account_scoped_rule_applies_at_upload_and_reclassify(env):
    env.db.add(Rule(plan_id="plan-1", name="Card only", priority=1, match_type="contains", pattern="SHOP ONE",
                    account_filter="Swiss Card", assign_payee="Rule Payee", assign_category="Groceries"))
    env.db.commit()
    up = env.upload(build([row(desc="Shop One")])).json()
    assert up["rows"][0]["payee"] == "Rule Payee" and up["rows"][0]["payee_source"] == "rule"
    re = env.client.post("/reclassify", json={"plan_id": "plan-1", "batch_id": up["batch_id"]}).json()
    assert re["rows"][0]["payee"] == "Rule Payee"
    assert up["rows"][0]["category_id"] == re["rows"][0]["category_id"] == re["rows"][0]["edited_category_id"] == "c1"
    # the same row on the Post account does not match the card-only rule
    other = env.upload(build([row(desc="Shop One")]), account_id=env.post.id).json()
    assert other["rows"][0]["payee"] != "Rule Payee"


def test_reclassify_preserves_overrides_and_exposes_separate_suggestions(env):
    up = env.upload(build([row(desc="Mystery Shop", merchant="Mystery")])).json()
    r0 = up["rows"][0]
    env.client.patch("/import-rows", json={"updates": [
        {"id": r0["id"], "edited_payee": "My Payee", "edited_category": "Groceries", "edited_memo": "my note"}]})
    env.db.add(Rule(plan_id="plan-1", name="R", priority=1, match_type="contains", pattern="MYSTERY",
                    assign_payee="Suggested", assign_category="Groceries"))
    env.db.commit()
    out = env.client.post("/reclassify", json={"plan_id": "plan-1", "batch_id": up["batch_id"]}).json()["rows"][0]
    assert out["edited_payee"] == "My Payee" and out["edited_memo"] == "my note"
    assert out["payee"] == "Suggested" and out["original_memo"] == "Mystery Shop"
    assert out["payee_confidence"] == 1.0 and out["evidence"]["Merchant"] == "Mystery"


def test_invalid_review_status_rejected(env):
    r0 = env.upload(build([row()])).json()["rows"][0]
    assert env.client.patch("/import-rows", json={"updates": [{"id": r0["id"], "status": "bogus"}]}).status_code == 422


def test_repeat_upload_creates_no_extra_identities(env):
    content = build([row(desc="Coffee", merchant="Cafe", amount="4.50")] * 2)
    first = env.upload(content).json()
    ids = [r["import_id"] for r in first["rows"]]
    second = env.upload(content).json()
    assert [r["import_id"] for r in second["rows"]] == ids and len(set(ids)) == 2
    assert second["report"]["repeated_export"] is True


def test_legacy_batch_requires_account_confirmation_before_write(env):
    b = ImportBatch(id="legacy", plan_id="plan-1", account_confirmed=False)
    env.db.add(b)
    env.db.add(ImportRow(id="lr", batch_id="legacy", row_index=0, date="2026-01-01", original_memo="old",
                         amount=-5.0, amount_milliunits=-5000, reconciliation_state="legacy_unresolved"))
    env.db.commit()
    r = env.write([{"row_id": "lr", "payee_name": "Shop"}])
    assert r.status_code == 409 and "account" in r.json()["detail"].lower()
    # reassigning confirms the account and creates a ledger identity
    out = env.client.post("/import-batches/legacy/reassign-account", json={"account_id": env.card.id})
    assert out.status_code == 200 and out.json()["account_confirmed"] is True
    assert out.json()["rows"][0]["import_id"].startswith("YF1:")


def test_reassign_account_rescopes_identity_and_prediction(env):
    up = env.upload(build([row(desc="Coffee", merchant="Cafe", amount="4.50")])).json()
    before = up["rows"][0]["import_id"]
    out = env.client.post(f"/import-batches/{up['batch_id']}/reassign-account", json={"account_id": env.post.id}).json()
    assert out["account_id"] == env.post.id and out["rows"][0]["import_id"] != before
    assert env.client.post(f"/import-batches/{up['batch_id']}/reassign-account",
                           json={"account_id": "missing"}).status_code == 422


def test_pending_evidence_survives_a_later_posted_export_without_creating_records(env):
    from src.models import SourceRecord
    first = env.upload(build([row(desc="Hotel", merchant="Hotel X", amount="80.00", status="Pending")])).json()
    assert first["count"] == 0 and first["report"]["pending"] == 1
    pending = first["report"]["pending_rows"][0]
    later = env.upload(build([row(desc="Hotel", merchant="Hotel X", amount="80.00", status="Posted")])).json()
    assert later["count"] == 1 and later["report"]["pending"] == 0
    # the earlier Pending evidence is still stored, and never became an identity or a YNAB write
    old = env.db.get(ImportBatch, first["batch_id"])
    stored = json.loads(old.report_json)["pending_rows"][0]
    assert stored == pending and stored["amount_milliunits"] == later["rows"][0]["amount_milliunits"]
    assert env.db.query(SourceRecord).count() == 1 and env.post_bodies == []


def test_discard_removes_unpushed_import_and_allows_a_clean_reupload(env):
    from src.models import SourceRecord, SourceExport
    first = env.upload(build([row(desc="Coffee", merchant="Cafe", amount="4.50")])).json()
    assert env.db.query(SourceRecord).count() == 1
    r = env.client.post(f"/import-batches/{first['batch_id']}/discard")
    assert r.status_code == 200 and r.json() == {"removed_rows": 1, "kept_rows": 0}
    env.db.expire_all()
    assert env.db.query(ImportRow).count() == 0 and env.db.query(SourceRecord).count() == 0
    assert env.db.query(SourceExport).count() == 0 and env.db.query(ImportBatch).count() == 0
    assert env.client.get("/import-batches/plan-1/active").status_code == 404
    again = env.upload(build([row(desc="Coffee", merchant="Cafe", amount="4.50")])).json()
    assert again["count"] == 1 and again["report"]["repeated_export"] is False


def test_discard_keeps_rows_already_in_ynab_and_refuses_uncertain_ones(env):
    from src.models import SourceRecord
    out = env.upload(build([row(desc="Coffee", merchant="Cafe", amount="4.50"),
                            row(desc="Bread", merchant="Bakery", amount="3.00")])).json()
    rows = out["rows"]
    env.client.patch("/import-rows", json={"updates": [{"id": r["id"], "status": "accepted"} for r in rows]})
    assert env.write([env.row_ref(rows[0], payee_name="Cafe")], batch_id=out["batch_id"]).json()["created"] == 1
    # a row that may be in YNAB blocks removal
    env.db.expire_all()
    bread = env.db.query(ImportRow).filter_by(id=rows[1]["id"]).one()
    bread.source_record.write_state = "reconcile_required"
    env.db.commit()
    assert env.client.post(f"/import-batches/{out['batch_id']}/discard").status_code == 409
    bread.source_record.write_state = "unsent"
    env.db.commit()
    r = env.client.post(f"/import-batches/{out['batch_id']}/discard")
    assert r.status_code == 200 and r.json() == {"removed_rows": 1, "kept_rows": 1}
    env.db.expire_all()
    left = env.db.query(SourceRecord).all()
    assert len(left) == 1 and left[0].write_state == "created"       # the pushed one keeps its identity
    assert env.client.post(f"/import-batches/{out['batch_id']}/discard").status_code == 409   # no longer active
    assert env.client.post("/import-batches/nope/discard").status_code == 404
