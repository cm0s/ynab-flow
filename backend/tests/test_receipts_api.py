"""Receipt endpoints: status with no mailbox, fetch through a fake Bridge, unreachable Bridge."""
import pytest

from src.ai.extract import ReceiptOut
from src.main import app
from tests import mail_fixtures as fx
from tests.ai_fakes import FakeTransport
from tests.fake_imap import FakeAccount, FakeFolder, FakeImapServer


@pytest.fixture()
def no_ai(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "BRIDGE_MAILBOXES"):
        monkeypatch.setenv(key, "")
    app.state.ai_transport = None
    yield
    app.state.ai_transport = None


def test_status_and_fetch_without_mailbox(env, no_ai):
    status = env.client.get("/receipts/status").json()
    assert status["feature"]["enabled"] is False and status["mailboxes"] == [] and status["receipts"] == {}
    response = env.client.post("/receipts/fetch")
    assert response.status_code == 409 and "BRIDGE_MAILBOXES" in response.json()["detail"]


def test_fetch_through_fake_bridge_with_fake_ai(env, no_ai, monkeypatch, tmp_path):
    inbox = FakeFolder()
    inbox.add(fx.html_order())
    inbox.add(fx.newsletter())
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("bridge-pw", {"INBOX": inbox})}) as server:
        for key, value in {"BRIDGE_IMAP_PORT": str(server.port), "BRIDGE_MAILBOXES": "me",
                           "BRIDGE_ME_USER": "me@proton.me", "BRIDGE_ME_PASSWORD": "bridge-pw"}.items():
            monkeypatch.setenv(key, value)
        app.state.ai_transport = FakeTransport(ReceiptOut(
            is_receipt=True, merchant="Migros Online", reference="MO-551203", kind="purchase",
            purchase_date="2026-10-03", total="84.90", currency="CHF", items=[]))
        body = env.client.post("/receipts/fetch").json()
        assert (body["messages"], body["new_receipts"], body["not_receipts"]) == (2, 1, 1)
        assert body["receipts"] == {"extracted": 1, "not_receipt": 1}
        assert body["mailboxes"][0]["folders"][0]["last_fetch_at"]
        again = env.client.post("/receipts/fetch").json()
        assert again["messages"] == 0
    assert "bridge-pw" not in str(body) and "bridge-pw" not in env.client.get("/receipts/status").text


def test_unreachable_bridge_reports_plain_error(env, no_ai, monkeypatch):
    for key, value in {"BRIDGE_IMAP_PORT": "1", "BRIDGE_MAILBOXES": "me", "BRIDGE_ME_USER": "u",
                       "BRIDGE_ME_PASSWORD": "p"}.items():
        monkeypatch.setenv(key, value)
    body = env.client.post("/receipts/fetch").json()
    assert "Is Bridge running" in body["folders"][0]["error"]
    status = env.client.get("/receipts/status").json()
    assert "Is Bridge running" in status["mailboxes"][0]["folders"][0]["error"]


def test_fetch_links_receipts_to_the_active_import(env, no_ai, monkeypatch, tmp_path):
    from tests.swisscard_fixture import build, row
    uploaded = env.upload(build([row(date="06.10.2026", desc="Migros Online Zuerich", merchant="Migros Online",
                                     amount="84.90")])).json()["rows"][0]
    assert uploaded["receipts"] == []
    inbox = FakeFolder()
    inbox.add(fx.html_order())
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("pw", {"INBOX": inbox})}) as server:
        for key, value in {"BRIDGE_IMAP_PORT": str(server.port), "BRIDGE_MAILBOXES": "me",
                           "BRIDGE_ME_USER": "me@proton.me", "BRIDGE_ME_PASSWORD": "pw"}.items():
            monkeypatch.setenv(key, value)
        body = env.client.post("/receipts/fetch").json()
    assert body["matches"] == {"proposed": 0, "linked": 1}
    view = env.client.get("/import-batches/plan-1/active").json()["rows"][0]["receipts"][0]
    assert view["state"] == "linked" and view["merchant"] == "Migros Online" and view["mailboxes"] == ["me"]
