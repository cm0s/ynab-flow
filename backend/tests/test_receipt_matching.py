"""Receipt matching: scoring scenarios, ambiguity, refunds, partial shipments, user decisions, bank values."""
import json

from src.models import ImportRow, Receipt, ReceiptMatch
from src.receipts.matching import match_active_batches
from tests.swisscard_fixture import build, row


def receipt(env, merchant, total, day="2026-10-03", kind="purchase", items=None, reference="", card=""):
    r = Receipt(content_hash=f"{merchant}-{total}-{day}-{kind}-{reference}-{len(env.db.query(Receipt).all())}",
                status="extracted", kind=kind, merchant=merchant, purchase_date=day, total_milliunits=total,
                currency="CHF", items_json=json.dumps(items or []), reference=reference, card_hint=card)
    env.db.add(r)
    env.db.commit()
    return r


def upload(env, *specs):
    return env.upload(build([row(**s) for s in specs])).json()["rows"]


def matches(env, row_id):
    return {(m.receipt_id, m.state) for m in env.db.query(ReceiptMatch).filter_by(import_row_id=row_id)}


def test_card_purchase_posted_later_is_linked_with_its_signals(env):
    r = receipt(env, "Migros Online", 84900, card="7890")
    rows = upload(env, dict(date="06.10.2026", desc="Migros Online Zuerich", merchant="Migros Online", amount="84.90"))
    view = rows[0]["receipts"][0]                          # matched at upload
    assert view["receipt_id"] == r.id and view["state"] == "linked" and view["origin"] == "auto"
    assert set(view["signals"]) == {"amount", "date", "merchant", "card"} and view["score"] == 1.0


def test_amount_alone_is_only_a_candidate(env):
    r = receipt(env, "Some Other Shop", 84900, day="2026-09-10")
    rows = upload(env, dict(date="06.10.2026", desc="Migros Online Zuerich", merchant="Migros Online", amount="84.90"))
    assert matches(env, rows[0]["id"]) == {(r.id, "proposed")}
    assert rows[0]["receipts"][0]["signals"] == {"amount": 0.45}


def test_two_identical_orders_need_the_user(env):
    a = receipt(env, "Galaxus", 50000)
    b = receipt(env, "Galaxus", 50000)
    rows = upload(env, dict(date="05.10.2026", desc="Galaxus Order", merchant="Galaxus", amount="50.00"),
                  dict(date="05.10.2026", desc="Galaxus Order 2", merchant="Galaxus", amount="50.00"))
    for r in rows:
        assert matches(env, r["id"]) == {(a.id, "proposed"), (b.id, "proposed")}
    resp = env.client.post(f"/import-rows/{rows[0]['id']}/receipts", json={"receipt_id": b.id, "action": "link"})
    assert resp.status_code == 200
    assert [v["state"] for v in resp.json()["receipts"] if v["receipt_id"] == b.id] == ["linked"]


def test_refund_matches_only_the_inflow(env):
    refund = receipt(env, "Zalando", 25000, day="2026-10-08", kind="refund")
    rows = upload(env, dict(date="09.10.2026", desc="Zalando Refund", merchant="Zalando", amount="-25.00", dc="Credit"),
                  dict(date="09.10.2026", desc="Zalando", merchant="Zalando", amount="25.00"))
    inflow, outflow = sorted(rows, key=lambda r: -r["amount_milliunits"])
    assert matches(env, inflow["id"]) == {(refund.id, "linked")}
    assert inflow["receipts"][0]["kind"] == "refund"
    assert matches(env, outflow["id"]) == set()


def test_partial_shipment_offers_the_receipt_to_both_rows_with_item_splits(env):
    items = [{"description": "Monitor arm", "quantity": 1, "amount_milliunits": 50000},
             {"description": "Cable", "quantity": 1, "amount_milliunits": 30000},
             {"description": "Dock", "quantity": 1, "amount_milliunits": 40000}]
    order = receipt(env, "Digitec Galaxus", 120000, items=items)
    rows = upload(env, dict(date="05.10.2026", desc="Digitec Galaxus", merchant="Digitec Galaxus", amount="80.00"),
                  dict(date="07.10.2026", desc="Digitec Galaxus", merchant="Digitec Galaxus", amount="40.00"))
    big, small = sorted(rows, key=lambda r: r["amount_milliunits"])
    assert matches(env, big["id"]) == {(order.id, "proposed")} == {(order.id, s) for _, s in matches(env, small["id"])}
    for r in rows:
        env.client.post(f"/import-rows/{r['id']}/receipts", json={"receipt_id": order.id, "action": "link"})
    active = {r["id"]: r for r in env.client.get("/import-batches/plan-1/active").json()["rows"]}
    assert active[big["id"]]["receipts"][0]["state"] == active[small["id"]]["receipts"][0]["state"] == "linked"
    suggestion = active[big["id"]]["receipts"][0]["split_suggestion"]
    assert [(l["amount_milliunits"], l["memo"], l["origin"]) for l in suggestion] == \
        [(-50000, "Monitor arm", "receipt"), (-30000, "Cable", "receipt")]
    assert active[small["id"]]["receipts"][0]["split_suggestion"] == []      # one item: no split


def test_receipt_total_differing_from_bank_amount_keeps_the_bank_amount(env):
    items = [{"description": "Food", "quantity": 1, "amount_milliunits": 60000},
             {"description": "Soap", "quantity": 1, "amount_milliunits": 26000}]   # 86.00 before a 1.10 discount
    receipt(env, "Migros Online", 84900, items=items)
    rows = upload(env, dict(date="06.10.2026", desc="Migros Online", merchant="Migros Online", amount="84.90"))
    lines = rows[0]["receipts"][0]["split_suggestion"]
    assert sum(l["amount_milliunits"] for l in lines) == rows[0]["amount_milliunits"] == -84900
    assert "difference to bank amount" in lines[0]["memo"]


def test_decisions_never_change_bank_values_and_survive_rematching(env):
    r = receipt(env, "Migros Online", 84900)
    rows = upload(env, dict(date="06.10.2026", desc="Migros Online Zuerich", merchant="Migros Online", amount="84.90"))
    fields = ("date", "amount", "amount_milliunits", "account_id", "source_record_id", "reconciliation_state",
              "transfer_state", "write_state", "evidence_json", "original_memo")
    stored = env.db.get(ImportRow, rows[0]["id"])
    before = {f: getattr(stored, f) for f in fields}
    import_id = stored.source_record.import_id
    for action in ("unlink", "link", "reject"):
        assert env.client.post(f"/import-rows/{stored.id}/receipts", json={"receipt_id": r.id, "action": action}).status_code == 200
    match_active_batches(env.db)
    env.db.commit()
    env.db.refresh(stored)
    assert {f: getattr(stored, f) for f in fields} == before and stored.source_record.import_id == import_id
    assert matches(env, stored.id) == {(r.id, "rejected")}
    assert env.client.get("/import-batches/plan-1/active").json()["rows"][0]["receipts"] == []
    bad = env.client.post(f"/import-rows/{stored.id}/receipts", json={"receipt_id": r.id, "action": "delete"})
    assert bad.status_code == 422


def test_shipping_notice_supports_the_linked_purchase(env):
    order = receipt(env, "Digitec Galaxus", 120000, reference="R-2026-88412")
    notice = receipt(env, "Galaxus", None, day="2026-10-04", kind="shipping", reference="R-2026-88412")
    rows = upload(env, dict(date="05.10.2026", desc="Digitec Galaxus", merchant="Digitec Galaxus", amount="120.00"))
    views = {v["receipt_id"]: v for v in rows[0]["receipts"]}
    assert views[order.id]["role"] == "primary" and views[order.id]["state"] == "linked"
    assert views[notice.id]["role"] == "supporting"
