"""Receipt corrections: user values survive fetches and AI re-extraction, validation, reset, classification."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.ai.client import AIClient
from src.mail.fetch import fetch_receipts
from src.models import Base, MailboxCursor, Receipt
from src.receipts import review
from tests import mail_fixtures as fx
from tests.ai_fakes import FakeTransport
from tests.fake_imap import FakeAccount, FakeFolder, FakeImapServer
from tests.test_receipt_fetch import ai_result, settings_for


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def receipt(**kw):
    values = dict(content_hash="h", status="extracted", kind="purchase", merchant="Migros", reference="A1",
                  purchase_date="2026-10-03", total_milliunits=84900, currency="CHF",
                  items_json=json.dumps([{"description": "Milk", "quantity": 1, "amount_milliunits": 84900}]))
    values.update(kw)
    r = Receipt(**values)
    review.apply_extraction(r, {k: getattr(r, k) for k in ("merchant", "purchase_date", "total_milliunits",
                                                          "currency", "kind", "reference", "status")}
                            | {"items": review.items(r)})
    return r


def refetch_with_ai(db, server, mailboxes="me"):
    db.query(MailboxCursor).delete()                     # read the same messages again
    s = settings_for(server, BRIDGE_MAILBOXES=mailboxes)
    fetch_receipts(db, s, ai=AIClient(db, s, FakeTransport(ai_result)))


def test_fetch_and_ai_reextraction_keep_user_corrections(db, tmp_path):
    inbox = FakeFolder()
    inbox.add(fx.html_order())
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("p1", {"INBOX": inbox})}) as server:
        fetch_receipts(db, settings_for(server, BRIDGE_MAILBOXES="me", ANTHROPIC_API_KEY=""))
        order = db.query(Receipt).one()
        assert order.status == "candidate" and review.extracted(order)["merchant"] == "Migros Online"
        review.correct(order, {"merchant": "Migros (online shop)"})
        db.commit()
        refetch_with_ai(db, server)                      # the waiting candidate is read by AI now
    db.refresh(order)
    assert order.merchant == "Migros (online shop)"                  # the user's value wins
    assert review.extracted(order)["merchant"] == "Migros Online"    # the extraction is still recorded
    assert order.status == "extracted" and "Bio Milch" in order.items_json   # uncorrected fields are updated
    assert review.user_fields(order) == ["merchant"]


def test_user_classification_survives_reextraction(db, tmp_path):
    inbox = FakeFolder()
    inbox.add(fx.html_order())
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("p1", {"INBOX": inbox})}) as server:
        fetch_receipts(db, settings_for(server, BRIDGE_MAILBOXES="me", ANTHROPIC_API_KEY=""))
        order = db.query(Receipt).one()
        review.classify(order, "not_receipt")
        db.commit()
        refetch_with_ai(db, server)
    db.refresh(order)
    assert order.status == "not_receipt"


def test_receipt_classification_still_allows_reading():
    r = receipt(status="not_receipt")
    review.classify(r, "candidate")                       # "this is a receipt"
    review.apply_extraction(r, {"status": "not_receipt"})   # an extraction disagrees: the user's side is kept
    assert r.status == "candidate"
    review.apply_extraction(r, {"status": "extracted", "merchant": "Interio"})   # but it can still be read
    assert (r.status, r.merchant) == ("extracted", "Interio")


@pytest.mark.parametrize("field, value, stored", [
    ("merchant", "  Coop City ", "Coop City"),
    ("reference", "R-9", "R-9"),
    ("purchase_date", "2026-10-05", "2026-10-05"),
    ("total_milliunits", 51000, 51000),
    ("currency", "eur", "EUR"),
    ("kind", "refund", "refund"),
])
def test_each_field_can_be_corrected(field, value, stored):
    r = receipt()
    review.correct(r, {field: value})
    assert getattr(r, field) == stored and review.user_fields(r) == [field]


def test_items_can_be_corrected():
    r = receipt()
    review.correct(r, {"items": [{"description": "Javel", "quantity": 2, "amount_milliunits": 4500}]})
    assert review.items(r) == [{"description": "Javel", "quantity": 2, "amount_milliunits": 4500}]


@pytest.mark.parametrize("changes, reason", [
    ({"items": [{"description": "Soap", "amount_milliunits": -100}]}, "positive"),
    ({"items": [{"description": "Soap", "amount_milliunits": "abc"}]}, "milliunits"),
    ({"total_milliunits": 0}, "positive"),
    ({"purchase_date": "05.10.2026"}, "YYYY-MM-DD"),
    ({"kind": "gift"}, "kind"),
    ({"currency": "Swiss francs"}, "three-letter"),
    ({"status": "extracted"}, "cannot be corrected"),
])
def test_invalid_corrections_change_nothing(changes, reason):
    r = receipt()
    before = (r.merchant, r.total_milliunits, r.items_json, r.user_fields_json)
    with pytest.raises(review.CorrectionError, match=reason):
        review.correct(r, {"merchant": "Changed", **changes})
    assert (r.merchant, r.total_milliunits, r.items_json, r.user_fields_json) == before


def test_reset_restores_the_extracted_value():
    r = receipt(total_milliunits=None)
    review.correct(r, {"total_milliunits": 51000})
    review.reset(r, ["total_milliunits"])
    assert r.total_milliunits is None and review.user_fields(r) == []
    review.classify(r, "not_receipt")
    review.reset(r, ["classification"])
    assert r.status == "extracted" and review.user_fields(r) == []


def test_exclusion_flag_is_kept_on_the_receipt():
    r = receipt()
    r.excluded = True
    review.apply_extraction(r, {"merchant": "Other"})
    assert r.excluded
