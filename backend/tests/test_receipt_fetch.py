"""Fetching receipts from a fake Bridge: cursors, deduplication across mailboxes, UIDVALIDITY, AI extraction."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.ai.client import AIClient
from src.ai.extract import ReceiptItemOut, ReceiptOut
from src.mail.fetch import fetch_message, fetch_receipts, mailbox_status
from src.mail.imap_source import MailError
from src.models import AIUsage, Base, MailboxCursor, Receipt, ReceiptSource
from src.settings import load_settings
from tests import mail_fixtures as fx
from tests.ai_fakes import FakeTransport
from tests.fake_imap import FakeAccount, FakeFolder, FakeImapServer


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def bridge(tmp_path):
    me, partner = FakeFolder(uidvalidity=5), FakeFolder(uidvalidity=9)
    me.add(fx.html_order(to="me@proton.me", message_id="<o-me@x>"))
    me.add(fx.newsletter())
    me.add(fx.pdf_invoice())
    partner.add(fx.html_order(to="partner@proton.me", message_id="<o-partner@x>"))
    accounts = {"me@proton.me": FakeAccount("p1", {"INBOX": me}), "partner@proton.me": FakeAccount("p2", {"INBOX": partner})}
    with FakeImapServer(tmp_path, accounts) as server:
        yield server


def settings_for(server, **extra):
    return load_settings({
        "BRIDGE_IMAP_PORT": str(server.port), "BRIDGE_MAILBOXES": "me,partner",
        "BRIDGE_ME_USER": "me@proton.me", "BRIDGE_ME_PASSWORD": "p1",
        "BRIDGE_PARTNER_USER": "partner@proton.me", "BRIDGE_PARTNER_PASSWORD": "p2",
        "ANTHROPIC_API_KEY": "sk-test", **extra})


def test_repeated_fetch_and_same_receipt_in_two_mailboxes(db, bridge):
    s = settings_for(bridge)
    first = fetch_receipts(db, s).as_dict()
    assert (first["messages"], first["new_receipts"], first["not_receipts"], first["duplicates"]) == (4, 2, 1, 1)
    order = db.query(Receipt).filter(Receipt.total_milliunits == 84900).one()
    assert sorted(src.mailbox for src in order.sources) == ["me", "partner"]
    assert order.merchant == "Migros Online" and order.status == "candidate"   # AI off: local fields only
    second = fetch_receipts(db, s).as_dict()
    assert second["messages"] == 0 and db.query(Receipt).count() == 3
    status = mailbox_status(db, s)
    assert [m["mailbox"] for m in status] == ["me", "partner"]
    assert status[0]["folders"][0]["last_uid"] == 3 and status[0]["folders"][0]["error"] == ""
    # read-only all the way through
    assert "SELECT" not in bridge.verbs() and not any(
        m.flags for a in bridge.accounts.values() for f in a.folders.values() for m in f.messages.values())


def test_uidvalidity_change_rescans_without_duplicates(db, bridge):
    s = settings_for(bridge)
    fetch_receipts(db, s)
    sources_before = db.query(ReceiptSource).count()
    old = bridge.accounts["me@proton.me"].folders["INBOX"]
    renumbered = FakeFolder(uidvalidity=6)
    for message in old.messages.values():
        renumbered.add(message.raw, uid=None)
    bridge.accounts["me@proton.me"].folders["INBOX"] = renumbered
    again = fetch_receipts(db, s).as_dict()
    assert again["messages"] == 3 and again["new_receipts"] == 0
    assert db.query(Receipt).count() == 3 and db.query(ReceiptSource).count() == sources_before
    cursor = db.query(MailboxCursor).filter_by(mailbox="me").one()
    assert (cursor.uidvalidity, cursor.last_uid) == (6, 3)


def ai_result(request, output_type):
    text = str(request["messages"][0]["content"])
    if "Migros" in text:
        return ReceiptOut(is_receipt=True, merchant="Migros Online", reference="MO-551203", kind="purchase",
                          purchase_date="2026-10-03", total="84.90", currency="CHF",
                          items=[ReceiptItemOut(description="Bio Milch 1l", amount="1.95")])
    if "document" in text:
        return ReceiptOut(is_receipt=True, merchant="Corner Shop", reference="", kind="purchase",
                          purchase_date="2026-10-02", total="12.30", currency="CHF", items=[])
    return ReceiptOut(is_receipt=True, merchant="Digitec Galaxus", reference="R-2026-88412", kind="purchase",
                      purchase_date="2026-10-03", total="119.00", currency="CHF", items=[])


def test_ai_extraction_text_pdf_and_scanned_pdf_with_usage(db, tmp_path):
    inbox = FakeFolder()
    for raw in (fx.html_order(), fx.newsletter(), fx.pdf_invoice(), fx.scanned_invoice()):
        inbox.add(raw)
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("p1", {"INBOX": inbox})}) as server:
        s = settings_for(server, BRIDGE_MAILBOXES="me")
        transport = FakeTransport(ai_result)
        fetch_receipts(db, s, ai=AIClient(db, s, transport))
    assert len(transport.requests) == 3                       # the newsletter cost nothing
    by_merchant = {r.merchant: r for r in db.query(Receipt).filter(Receipt.status != "not_receipt")}
    order = by_merchant["Migros Online"]
    assert order.status == "extracted" and order.extraction_method == "text" and "Bio Milch" in order.items_json
    invoice = by_merchant["Digitec Galaxus"]
    assert invoice.extraction_method == "pdf_text" and invoice.status == "needs_check"   # AI 119.00 vs PDF 120.00
    scanned = by_merchant["Corner Shop"]
    assert scanned.extraction_method == "ai_document" and scanned.total_milliunits == 12300
    document_requests = [r for r, _ in transport.requests
                         if any(b.get("type") == "document" for b in r["messages"][0]["content"])]
    assert len(document_requests) == 1
    assert db.query(AIUsage).filter_by(purpose="extract").count() == 3
    for request, _ in transport.requests:
        assert "p1" not in str(request) and "me@proton.me" not in str(request["messages"])


def test_budget_reached_keeps_candidates_for_later(db, bridge):
    s = settings_for(bridge, AI_MONTHLY_BUDGET_USD="0.01")
    db.add(AIUsage(created_at=datetime.utcnow(), purpose="extract", model="m", cost_usd=0.01, status="ok"))
    db.commit()
    transport = FakeTransport(ai_result)
    report = fetch_receipts(db, s, ai=AIClient(db, s, transport)).as_dict()
    assert transport.requests == [] and "budget" in report["ai_note"]
    assert {r.status for r in db.query(Receipt)} == {"candidate", "not_receipt"}


def test_unreachable_bridge_is_reported_per_folder(db):
    s = load_settings({"BRIDGE_IMAP_PORT": "1", "BRIDGE_MAILBOXES": "me", "BRIDGE_ME_USER": "u",
                       "BRIDGE_ME_PASSWORD": "p"})
    report = fetch_receipts(db, s).as_dict()
    assert "Is Bridge running" in report["folders"][0]["error"]
    assert "Is Bridge running" in mailbox_status(db, s)[0]["folders"][0]["error"]


def test_only_recent_messages_are_read(db, tmp_path):
    inbox = FakeFolder()
    old = inbox.add(fx.html_order(message_id="<old@x>"), received=date.today() - timedelta(days=90))
    inbox.add(fx.pdf_invoice(), received=date.today() - timedelta(days=5))
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("p1", {"INBOX": inbox})}) as server:
        s = settings_for(server, BRIDGE_MAILBOXES="me", ANTHROPIC_API_KEY="")
        assert s.fetch_since_days == 60                       # the default
        assert fetch_receipts(db, s).as_dict()["messages"] == 1
        assert any(" SINCE " in c for c in server.commands)
        # A later fetch keeps the window and the cursor: the old message is still not read
        assert fetch_receipts(db, s).as_dict()["messages"] == 0
        # 0 lifts the limit for a deliberate full read; the cursor is reset to reach older UIDs
        db.query(MailboxCursor).delete()
        everything = settings_for(server, BRIDGE_MAILBOXES="me", ANTHROPIC_API_KEY="", BRIDGE_FETCH_SINCE_DAYS="0")
        assert fetch_receipts(db, everything).as_dict()["messages"] == 2
    assert db.query(ReceiptSource).filter_by(uid=old).count() == 1


def test_one_message_is_read_again_read_only_with_message_id_fallback(db, tmp_path):
    inbox = FakeFolder(uidvalidity=5)
    inbox.add(fx.newsletter())
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("p1", {"INBOX": inbox})}) as server:
        s = settings_for(server, BRIDGE_MAILBOXES="me", ANTHROPIC_API_KEY="")
        fetch_receipts(db, s)
        skipped = db.query(Receipt).one()
        assert b"Autumn ideas" in fetch_message(s, skipped)                 # by UID
        inbox.uidvalidity = 6                                               # Bridge renumbered the folder
        inbox.messages = {41: inbox.messages[1]}
        assert b"Autumn ideas" in fetch_message(s, skipped)                 # by Message-ID
        assert (skipped.sources[0].uidvalidity, skipped.sources[0].uid) == (6, 41)
        inbox.messages = {}
        with pytest.raises(MailError, match="no longer in the mailbox"):
            fetch_message(s, skipped)
        assert set(server.verbs()) <= {"CAPABILITY", "STARTTLS", "LOGIN", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"}
    assert all(not m.flags for m in inbox.messages.values())
