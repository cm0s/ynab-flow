import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.csv_service import parse_import
from src.identity import (
    allocate_occurrences, add_confirmed_occurrence, canonical_evidence, fingerprint_of,
    make_import_id, IdentityCollision,
)
from src.models import Base, Plan, Account, SourceRecord, SourceExport
from tests.swisscard_fixture import build, row


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    plan = Plan(id="p1", ynab_plan_id="y", name="P")
    s.add(plan)
    s.add_all([Account(id="a1", plan_id="p1", ynab_account_id="ya1", name="Card", type="creditCard"),
               Account(id="a2", plan_id="p1", ynab_account_id="ya2", name="Card2", type="creditCard")])
    s.commit()
    yield s
    s.close()


def rows_of(*specs):
    return parse_import(build([row(**s) for s in specs])).rows


COFFEE = dict(desc="Coffee", merchant="Cafe", amount="4.50")


def test_two_identical_purchases_are_distinct_and_repeat_adds_nothing(db):
    first = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, COFFEE))
    db.commit()
    ids = [a.record.import_id for a in first.allocations]
    assert len(set(ids)) == 2 and [a.record.occurrence for a in first.allocations] == [1, 2]
    again = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, COFFEE))
    db.commit()
    assert [a.record.import_id for a in again.allocations] == ids
    assert again.repeated_export and db.query(SourceRecord).count() == 2
    assert db.query(SourceExport).count() == 1


def test_identity_ignores_edits_batches_status_and_source_category(db):
    base = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE)).allocations[0].record.import_id
    changed = rows_of(dict(COFFEE, mcat="Other", rcat="Else"))
    assert allocate_occurrences(db, "p1", "a1", changed).allocations[0].record.import_id == base
    # reordering the file does not change identity either
    a = rows_of(COFFEE, dict(desc="Bread", merchant="Bakery", amount="3.00"))
    b = list(reversed(a))
    ids_a = {x.txn.memo: x.record.import_id for x in allocate_occurrences(db, "p1", "a1", a).allocations}
    ids_b = {x.txn.memo: x.record.import_id for x in allocate_occurrences(db, "p1", "a1", b).allocations}
    assert ids_a == ids_b


def test_account_scopes_identity(db):
    r1 = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE)).allocations[0].record
    r2 = allocate_occurrences(db, "p1", "a2", rows_of(COFFEE)).allocations[0].record
    assert r1.fingerprint == r2.fingerprint and r1.import_id != r2.import_id and r1.id != r2.id


def test_import_id_length_and_determinism():
    iid = make_import_id("p", "a", "f" * 64, 12345)
    assert len(iid) <= 36 and iid == make_import_id("p", "a", "f" * 64, 12345)


def test_overlap_adds_only_demonstrated_extra_occurrences(db):
    allocate_occurrences(db, "p1", "a1", rows_of(COFFEE))
    db.commit()
    res = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, COFFEE, dict(desc="New", merchant="N", amount="1.00")))
    db.commit()
    assert [a.record.occurrence for a in res.allocations[:2]] == [1, 2]
    assert db.query(SourceRecord).filter_by(fingerprint=res.allocations[0].record.fingerprint).count() == 2
    assert db.query(SourceRecord).count() == 3


def test_ambiguous_partial_subset_requires_resolution(db):
    first = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, COFFEE))
    first.allocations[0].record.write_state = "created"   # one of the two is already in YNAB
    db.commit()
    res = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, dict(desc="Other", merchant="O", amount="9.00")))
    coffee = res.allocations[0]
    assert coffee.state == "ambiguous" and "previously seen 2" in coffee.note
    assert db.query(SourceRecord).count() == 3        # no third coffee was invented
    extra = add_confirmed_occurrence(db, coffee.record)  # explicit user confirmation
    assert extra.occurrence == 3 and extra.import_id != coffee.record.import_id


def test_partial_subset_with_uniform_state_is_not_ambiguous(db):
    allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, COFFEE))
    db.commit()
    res = allocate_occurrences(db, "p1", "a1", rows_of(COFFEE, dict(desc="Other", merchant="O", amount="9.00")))
    assert res.allocations[0].state == "pending"


def test_raw_evidence_collision_is_detected(db):
    allocate_occurrences(db, "p1", "a1", rows_of(COFFEE))
    rec = db.query(SourceRecord).one()
    stored = json.loads(rec.evidence_json)
    stored["canonical"]["merchant"] = "Tampered"
    rec.evidence_json = json.dumps(stored)
    db.commit()
    with pytest.raises(IdentityCollision):
        allocate_occurrences(db, "p1", "a1", rows_of(COFFEE))


def test_concurrent_allocation_reuses_the_winner(db, monkeypatch):
    """A second uploader that loses the unique-constraint race reuses the winner's row."""
    import src.identity as identity
    winner_session = sessionmaker(bind=db.get_bind())()
    txn = rows_of(COFFEE)[0]
    canon = canonical_evidence(txn)
    fp = fingerprint_of(canon)
    original = identity._insert_record
    state = {"raced": False}

    def racing(db_, plan_id, account_id, fp_, occ, canon_, txn_):
        if not state["raced"]:
            state["raced"] = True
            original(winner_session, plan_id, account_id, fp_, occ, canon_, txn_)
            winner_session.commit()
        return original(db_, plan_id, account_id, fp_, occ, canon_, txn_)

    monkeypatch.setattr(identity, "_insert_record", racing)
    res = allocate_occurrences(db, "p1", "a1", [txn])
    db.commit()
    assert db.query(SourceRecord).count() == 1
    assert res.allocations[0].record.fingerprint == fp
