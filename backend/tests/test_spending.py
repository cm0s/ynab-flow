"""Spending analysis: AI item categories on request, counting and exact scaling, and the report endpoint."""
import json
import re

import pytest

from src.main import app
from src.models import AIUsage, Category, Receipt
from src.receipts.spending import scale
from tests.test_receipts_page import add_receipt, with_ai
from tests.swisscard_fixture import build, row


@pytest.fixture()
def plan(env, monkeypatch):
    env.monkeypatch = monkeypatch
    env.card.currency = "CHF"
    env.db.add(Category(id="c2", category_group_id="g1", ynab_category_id="y-c2", name="Household"))
    env.db.commit()
    yield env
    app.state.ai_transport = None


def items(*pairs):
    return json.dumps([{"description": d, "quantity": 1, "amount_milliunits": a} for d, a in pairs])


def link(env, receipt, date, desc, amount):
    out = env.upload(build([row(date=date, desc=desc, merchant=desc, amount=amount)])).json()
    row_id = out["rows"][-1]["id"]
    env.client.post(f"/receipts/{receipt.id}/links", json={"row_id": row_id, "action": "link"})
    return row_id


def spending(env, **params):
    return env.client.get("/spending", params={"plan_id": "plan-1", **params}).json()


def lines_of(report, receipt_id):
    return [l for g in report["groups"] for l in g["lines"] if l["receipt_id"] == receipt_id]


# --- AI item categories (fake transport) ---

def answer_with(category_for):
    """A fake AI answer: category_for(description) for each requested item."""
    def make(request, output_type):
        data = json.loads(re.search(r"<receipt>\n(.*)\n</receipt>", request["messages"][0]["content"][0]["text"],
                                    re.S).group(1))
        return output_type(items=[{"index": i["index"], "category_id": category_for(i["description"])}
                                  for i in data["items"]])
    return make


def test_categorize_items_fills_only_empty_items_after_an_estimate(plan):
    env = plan
    first = add_receipt(env, merchant="Migros", total_milliunits=84900, subject="SECRET SUBJECT",
                        items_json=items(("Bio Milch", 62400), ("Javel", 22500)))
    second = add_receipt(env, merchant="Coop", total_milliunits=1000, items_json=items(("Brot", 1000)))
    env.client.put(f"/receipts/{first.id}/items/1/category", json={"category_id": "c1"})   # the user's choice
    body = {"receipt_ids": [first.id, second.id], "plan_id": "plan-1"}
    estimate = env.client.post("/receipts/categorize-items", json={**body, "estimate_only": True}).json()
    assert estimate["requests"] == 2 and estimate["estimate_usd"] > 0
    transport = with_ai(env.monkeypatch, answer_with(lambda d: "c2" if d == "Javel" else "c1"))
    result = env.client.post("/receipts/categorize-items", json=body).json()
    assert (result["receipts"], result["items_filled"], result["failed"]) == (2, 2, 0)
    detail = env.client.get(f"/receipts/{first.id}").json()
    assert [(i["category_name"], i["category_source"]) for i in detail["items"]] == \
        [("Groceries", "ai"), ("Groceries", "user")]                    # the user's Javel stays Groceries
    sent = [r for r, _ in transport.requests]
    text = sent[0]["messages"][0]["content"][0]["text"]
    assert "Javel" not in text and "SECRET SUBJECT" not in text and "untrusted data" in sent[0]["system"][0]["text"]
    assert sent[0]["system"][-1]["cache_control"] == {"type": "ephemeral"} and '"id": "c2"' in sent[0]["system"][-1]["text"]
    assert env.db.query(AIUsage).filter_by(purpose="categorize_items").count() == 2
    again = env.client.post("/receipts/categorize-items", json={**body, "estimate_only": True}).json()
    assert again["requests"] == 0                                        # nothing left to categorize


def test_unknown_category_from_the_ai_stays_uncategorized(plan):
    env = plan
    receipt = add_receipt(env, items_json=items(("Tent", 100000)))
    with_ai(env.monkeypatch, answer_with(lambda d: "not-a-category"))
    result = env.client.post("/receipts/categorize-items", json={"receipt_ids": [receipt.id]}).json()
    assert result["items_filled"] == 0
    item = env.client.get(f"/receipts/{receipt.id}").json()["items"][0]
    assert item["category_source"] == "uncategorized" and "not in this budget" in item["category_note"]


# --- counting and scaling ---

def test_scale_is_exact_with_discounts():
    assert scale([10000, 16970], 25240) == [9359, 15881] and sum(scale([10000, 16970], 25240)) == 25240
    assert sum(scale([5000, 5000, -1000], 9001)) == 9001
    assert scale([0, 0], 500) == [250, 250] and scale([0, 0, 0], 100) == [34, 33, 33]


def test_foreign_receipt_linked_to_a_card_row_is_counted_in_chf(plan):
    env = plan
    receipt = add_receipt(env, merchant="Amazon.de", currency="EUR", total_milliunits=26970, purchase_date="2026-10-20",
                          items_json=items(("Cable", 10000), ("Book", 16970)))
    link(env, receipt, "22.10.2026", "AMAZON.DE", "25.24")
    lines = lines_of(spending(env), receipt.id)
    assert [l["description"] for l in lines] == ["Cable", "Book"]
    assert sum(l["amount_milliunits"] for l in lines) == 25240


def test_notices_excluded_and_not_a_receipt_are_not_counted(plan):
    env = plan
    purchase = add_receipt(env, reference="O-1", total_milliunits=50000, items_json="[]")
    for kind in ("payment", "shipping"):
        add_receipt(env, kind=kind, reference="O-1", total_milliunits=50000, items_json="[]")
    excluded = add_receipt(env, total_milliunits=50000, purchase_date="2026-10-17", items_json="[]")
    env.client.patch(f"/receipts/{excluded.id}", json={"excluded": True})
    add_receipt(env, status="not_receipt", total_milliunits=50000)
    report = spending(env)
    assert report["total_milliunits"] == 50000 and report["receipts_counted"] == 1
    assert [l["receipt_id"] for g in report["groups"] for l in g["lines"]] == [purchase.id]


def test_unlinked_foreign_receipt_is_listed_separately(plan):
    env = plan
    usd = add_receipt(env, merchant="Anthropic", currency="USD", total_milliunits=21620, items_json="[]")
    report = spending(env)
    assert report["total_milliunits"] == 0
    assert report["uncounted"][0]["receipt_id"] == usd.id and "USD" in report["uncounted"][0]["reason"]


def test_refunds_subtract_and_fees_go_to_other(plan):
    env = plan
    add_receipt(env, total_milliunits=10500, items_json=items(("Shirt", 10000)))
    add_receipt(env, kind="refund", total_milliunits=10000, purchase_date="2026-10-20", items_json=items(("Shirt", 10000)))
    report = spending(env, group="item")
    by_label = {g["label"]: g["total_milliunits"] for g in report["groups"]}
    assert by_label == {"Shirt": 0, "Other (fees, discounts)": 500}


# --- report ---

def test_category_over_months_with_drill_down(plan):
    env = plan
    for month, amount in (("08", 1000), ("09", 2000), ("10", 3000)):
        r = add_receipt(env, purchase_date=f"2026-{month}-10", total_milliunits=amount,
                        items_json=items((f"Item {month}", amount)))
        env.client.put(f"/receipts/{r.id}/items/0/category", json={"category_id": "c2"})
    add_receipt(env, purchase_date="2026-07-31", total_milliunits=999, items_json=items(("Old", 999)))
    report = spending(env, **{"from": "2026-08-01", "to": "2026-10-31"})
    assert report["months"] == ["2026-08", "2026-09", "2026-10"]
    household = report["groups"][0]
    assert household["label"] == "Household" and household["months"] == {"2026-08": 1000, "2026-09": 2000, "2026-10": 3000}
    assert [l["description"] for l in household["lines"]] == ["Item 10", "Item 09", "Item 08"]
    assert report["uncategorized_share"] == 0.0


def test_item_search_across_merchants_and_uncategorized_share(plan):
    env = plan
    add_receipt(env, merchant="Migros", total_milliunits=3000, items_json=items(("Javel 1l", 2000), ("Brot", 1000)))
    add_receipt(env, merchant="Coop", total_milliunits=2500, purchase_date="2026-10-18", items_json=items(("JAVEL", 2500)))
    report = spending(env, group="item", q="javel")
    assert sorted((l["merchant"], l["description"]) for g in report["groups"] for l in g["lines"]) == \
        [("Coop", "JAVEL"), ("Migros", "Javel 1l")]
    assert report["uncategorized_share"] == 1.0 and len(report["uncategorized_receipt_ids"]) == 2


def test_reports_make_no_ai_or_external_call(plan):
    env = plan
    add_receipt(env)
    transport = with_ai(env.monkeypatch, answer_with(lambda d: "c1"))
    for group in ("category", "merchant", "item"):
        assert env.client.get("/spending", params={"group": group}).status_code == 200
    assert transport.requests == [] and env.db.query(AIUsage).count() == 0
    assert env.client.get("/spending", params={"group": "colour"}).status_code == 422


def test_items_without_prices_share_the_amount_and_keep_their_category(plan):
    env = plan
    receipt = add_receipt(env, merchant="Gravity Park", total_milliunits=51000, items_json=json.dumps([
        {"description": "Entry", "quantity": 2, "amount_milliunits": 0, "category_id": "c2", "category_source": "ai"}]))
    lines = lines_of(spending(env), receipt.id)
    assert [(l["description"], l["amount_milliunits"], l["category_name"]) for l in lines] == [("Entry", 51000, "Household")]
