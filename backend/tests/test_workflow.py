"""Task 8.1: upload -> prediction -> edit -> dry run -> mixed write -> reload -> retry -> overlapping re-upload."""
import json

import httpx

from src.models import SourceRecord
from tests.swisscard_fixture import HEADER, build, row, export_174


def test_end_to_end_workflow(env):
    # 1. upload the 174-entry synthetic export
    up = env.upload(export_174()).json()
    rows = up["rows"]
    assert up["count"] == 167 and up["report"]["pending"] == 7
    repayments = [r for r in rows if r["transfer_state"] == "candidate"]
    assert len(repayments) == 2 and all(r["amount_milliunits"] > 0 and r["category"] is None for r in repayments)
    ordinary = [r for r in rows if r["transfer_state"] == "none"]
    assert len(ordinary) == 165
    ids_before = {r["id"]: r["import_id"] for r in rows}
    assert len(set(ids_before.values())) == 167

    # 2. edit a row, accept everything
    edited = ordinary[0]
    env.client.patch("/import-rows", json={"updates": [
        {"id": r["id"], "status": "accepted", **({"edited_payee": "Edited Payee", "edited_memo": "my note"} if r is edited else {})}
        for r in rows]})

    # 3. dry run: nothing sent, nothing changes, repayments held
    refs = [env.row_ref(r, **({"payee_name": "Edited Payee", "memo": "my note"} if r is edited else {})) for r in rows]
    dry = env.write(refs, mode="dry_run", batch_id=up["batch_id"]).json()
    assert env.post_bodies == []
    assert sum(1 for r in dry["results"] if r["status"] == "dry_run_ok") == 165
    assert sum(1 for r in dry["results"] if r["status"] == "dry_run_error") == 2        # repayment holds
    assert len(env.client.get("/import-batches/plan-1/active").json()["rows"]) == 167

    # 4. mixed write: YNAB creates most rows (reordered), flags 2 as duplicates, silently drops 1
    victims = {ordinary[1]["import_id"], ordinary[2]["import_id"]}
    dropped = ordinary[3]["import_id"]

    def post(request):
        body = json.loads(request.content)
        env.post_bodies.append(body)
        sent = body["transactions"]
        dups = [t["import_id"] for t in sent if t["import_id"] in victims]
        made = [env.ynab_txn(f"id-{i}", import_id=t["import_id"], amount=t["amount"])
                for i, t in enumerate(sent) if t["import_id"] not in victims | {dropped}]
        made.reverse()
        env.created.extend(made)
        return httpx.Response(201, json={"data": {"transactions": made, "transaction_ids": [m["id"] for m in made],
                                                   "duplicate_import_ids": dups, "server_knowledge": 9}})
    env.post_handler = post
    res = env.write(refs, mode="create", batch_id=up["batch_id"]).json()
    assert (res["created"], res["existing"], res["unresolved"], res["errors"]) == (162, 2, 3, 0)  # 3 = dropped + 2 repayments
    body = env.post_bodies[0]["transactions"]
    assert len(body) == 165 and all(t["approved"] is False for t in body)
    assert all(isinstance(t["amount"], int) for t in body)
    sent_amounts = {t["import_id"]: t["amount"] for t in body}
    for r in ordinary:                                                   # exact money, immutable evidence
        assert sent_amounts[r["import_id"]] == r["amount_milliunits"]
    edited_sent = next(t for t in body if t["import_id"] == edited["import_id"])
    assert edited_sent["memo"] == "my note" and edited_sent["payee_name"] == "Edited Payee"

    # 5. reload: only the unresolved ordinary row and the two repayments remain, with identities intact
    left = env.client.get("/import-batches/plan-1/active").json()["rows"]
    assert {r["import_id"] for r in left} == {dropped} | {r["import_id"] for r in repayments}
    assert {r["id"]: r["import_id"] for r in left}.items() <= ids_before.items()
    assert [r for r in left if r["import_id"] == dropped][0]["write_state"] == "unresolved"
    assert all(r["transfer_state"] == "candidate" for r in left if r["import_id"] != dropped)

    # 6. retry the dropped row: same import id is resent and now created
    env.post_handler = env._default_post
    dropped_row = [r for r in left if r["import_id"] == dropped][0]
    retry = env.write([env.row_ref(dropped_row)], mode="create", batch_id=up["batch_id"]).json()
    assert retry["created"] == 1
    assert env.post_bodies[-1]["transactions"][0]["import_id"] == dropped
    assert env.client.post(f"/import-batches/{up['batch_id']}/complete").status_code == 409   # repayments still held

    # 7. overlapping re-upload: previous 167 are recognised, only 3 new rows queue, repayments still held
    newer = export_174().rstrip("\n").split("\n")
    extra = [row(desc=f"Brand new {i}", merchant=f"New {i}", amount="7.25") for i in range(3)]
    second = env.upload("\n".join(newer + extra) + "\n").json()
    assert second["report"]["already_imported"] == 165
    got = {r["original_memo"] for r in second["rows"]}
    assert got >= {"Brand new 0", "Brand new 1", "Brand new 2", "Payment Thank You"}
    assert len(second["rows"]) == 3 + 2
    total_ledger = env.db.query(SourceRecord).count()
    assert total_ledger == 167 + 3                       # no identity was invented for the overlap
    for r in second["rows"]:
        if r["original_memo"] in ("Payment Thank You", "Payment Thank You 2"):
            assert r["transfer_state"] == "candidate" and r["import_id"] in ids_before.values()
