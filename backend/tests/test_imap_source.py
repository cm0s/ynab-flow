"""Read-only IMAP adapter against a fake Bridge: allowlist, EXAMINE, BODY.PEEK, untouched flags."""
import socket

import pytest

from src.mail.imap_source import ForbiddenImapCommand, ImapConnection, MailError
from src.settings import load_settings
from tests.fake_imap import FakeAccount, FakeFolder, FakeImapServer

RAW = b"From: shop@example.com\r\nSubject: Order\r\nMessage-ID: <a@x>\r\n\r\nTotal CHF 10.00\r\n"


@pytest.fixture()
def bridge(tmp_path):
    inbox = FakeFolder(uidvalidity=7)
    inbox.add(RAW)
    inbox.add(RAW.replace(b"10.00", b"20.00"))
    with FakeImapServer(tmp_path, {"me@proton.me": FakeAccount("bridge-pass", {"INBOX": inbox})}) as server:
        yield server


def settings_for(server, password="bridge-pass", **extra):
    return load_settings({"BRIDGE_IMAP_PORT": str(server.port), "BRIDGE_MAILBOXES": "me",
                          "BRIDGE_ME_USER": "me@proton.me", "BRIDGE_ME_PASSWORD": password, **extra})


def test_fetch_is_read_only_and_leaves_flags_untouched(bridge):
    s = settings_for(bridge)
    conn = ImapConnection(s, s.mailboxes[0])
    assert conn.examine("INBOX") == 7
    uids = conn.uids_after(0)
    assert uids == [1, 2] and conn.uids_after(2) == []          # "3:*" returns UID 2 but it is filtered
    assert b"Total CHF 10.00" in conn.fetch(1)
    conn.logout()
    assert set(bridge.verbs()) <= {"CAPABILITY", "STARTTLS", "LOGIN", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"}
    assert "EXAMINE" in bridge.verbs() and "SELECT" not in bridge.verbs()
    assert all("BODY.PEEK[]" in c for c in bridge.commands if " UID FETCH " in c)
    assert all(not m.flags for m in bridge.accounts["me@proton.me"].folders["INBOX"].messages.values())
    assert not any("bridge-pass" in c for c in bridge.commands)


def test_write_commands_are_refused_before_reaching_the_server(bridge):
    s = settings_for(bridge)
    conn = ImapConnection(s, s.mailboxes[0])
    conn.examine("INBOX")
    imap = conn._imap
    for attempt in (
        lambda: imap.uid("STORE", "1", "+FLAGS", "(\\Seen)"),
        lambda: imap.uid("COPY", "1", "Archive"),
        lambda: imap.uid("MOVE", "1", "Trash"),
        lambda: imap.uid("FETCH", "1", "(BODY[])"),
        lambda: imap.store("1", "+FLAGS", "(\\Deleted)"),
        lambda: imap.expunge(),
        lambda: imap.select("INBOX"),
        lambda: imap.append("INBOX", None, None, b"x"),
    ):
        with pytest.raises(ForbiddenImapCommand):
            attempt()
    conn.logout()
    sent = " ".join(bridge.verbs())
    for verb in ("STORE", "COPY", "MOVE", "EXPUNGE", "SELECT", "APPEND"):
        assert verb not in sent
    assert all(not m.flags for m in bridge.accounts["me@proton.me"].folders["INBOX"].messages.values())


def test_pinned_certificate_is_verified(bridge):
    s = settings_for(bridge, BRIDGE_CERT_PATH=str(bridge.cert_path))
    conn = ImapConnection(s, s.mailboxes[0])
    assert conn.examine("INBOX") == 7
    conn.logout()


def test_wrong_password_is_explained_without_revealing_it(bridge):
    s = settings_for(bridge, password="my-proton-password")
    with pytest.raises(MailError) as err:
        ImapConnection(s, s.mailboxes[0])
    assert "Bridge-generated password" in str(err.value) and "my-proton-password" not in str(err.value)


def test_bridge_not_running():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    s = load_settings({"BRIDGE_IMAP_PORT": str(port), "BRIDGE_MAILBOXES": "me", "BRIDGE_ME_USER": "u",
                       "BRIDGE_ME_PASSWORD": "p"})
    with pytest.raises(MailError, match="Is Bridge running"):
        ImapConnection(s, s.mailboxes[0])


def test_unknown_folder(bridge):
    s = settings_for(bridge)
    conn = ImapConnection(s, s.mailboxes[0])
    with pytest.raises(MailError, match="BRIDGE_FOLDERS"):
        conn.examine("Receipts")
    conn.logout()
