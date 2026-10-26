"""Read-only IMAP access to a local Proton Mail Bridge.

Folders are opened with EXAMINE and messages fetched with ``BODY.PEEK[]``, so
read flags and locations never change. ``GuardedIMAP4`` refuses, at the wire
level, any command outside a small allowlist (no STORE, COPY, MOVE, EXPUNGE,
SELECT or APPEND can ever be sent). Passwords are never logged or put in
error messages.
"""

import imaplib
import socket
import ssl
from datetime import date
from typing import List, Optional, Protocol

from src.settings import MailboxConfig, Settings

ALLOWED_COMMANDS = {"CAPABILITY", "STARTTLS", "LOGIN", "EXAMINE", "UID", "LOGOUT", "NOOP"}
ALLOWED_UID_COMMANDS = {"SEARCH", "FETCH"}
FETCH_ITEMS = "(BODY.PEEK[])"
TIMEOUT_SECONDS = 30
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ForbiddenImapCommand(RuntimeError):
    pass


class MailError(Exception):
    """A mailbox problem in plain terms (safe to show; contains no credentials)."""


class MailConnection(Protocol):
    """The read-only operations the fetcher needs; implemented by ``ImapConnection`` and test fakes."""

    def examine(self, folder: str) -> int: ...          # returns UIDVALIDITY
    def uids_after(self, last_uid: int, since: Optional[date] = None) -> List[int]: ...
    def fetch(self, uid: int) -> bytes: ...
    def logout(self) -> None: ...


class GuardedIMAP4(imaplib.IMAP4):
    """imaplib connection that can only send allowlisted, read-only commands."""

    def _command(self, name, *args):
        if name not in ALLOWED_COMMANDS:
            raise ForbiddenImapCommand(f"IMAP command {name} is not allowed (read-only access).")
        if name == "UID" and (not args or str(args[0]).upper() not in ALLOWED_UID_COMMANDS):
            raise ForbiddenImapCommand(f"IMAP command UID {args[0] if args else ''} is not allowed (read-only access).")
        if name == "UID" and str(args[0]).upper() == "FETCH" and "PEEK" not in str(args[-1]).upper():
            raise ForbiddenImapCommand("Only BODY.PEEK fetches are allowed, so messages are never marked read.")
        return super()._command(name, *args)

    def select(self, mailbox="INBOX", readonly=False):
        if not readonly:
            raise ForbiddenImapCommand("Folders may only be opened read-only (EXAMINE).")
        return super().select(mailbox, readonly=True)


def _tls_context(settings: Settings) -> ssl.SSLContext:
    if settings.bridge_cert_path:
        context = ssl.create_default_context(cafile=settings.bridge_cert_path)
        context.check_hostname = False        # Bridge's certificate names no public host; the pinned cert is the check
        return context
    if not settings.bridge_is_loopback:
        raise MailError("Bridge is not on this computer: set BRIDGE_CERT_PATH so its certificate can be verified.")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE       # loopback only: Bridge's self-signed certificate
    return context


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapConnection:
    """A logged-in, read-only Bridge session for one mailbox."""

    def __init__(self, settings: Settings, mailbox: MailboxConfig):
        try:
            self._imap = GuardedIMAP4(settings.bridge_host, settings.bridge_port, timeout=TIMEOUT_SECONDS)
        except (OSError, socket.timeout) as exc:
            raise MailError(f"Cannot reach Proton Mail Bridge at {settings.bridge_host}:{settings.bridge_port}. "
                            "Is Bridge running?") from exc
        try:
            self._imap.starttls(ssl_context=_tls_context(settings))
        except (imaplib.IMAP4.error, ssl.SSLError, OSError) as exc:
            self._close()
            raise MailError("Could not start a secure connection to Bridge. If BRIDGE_CERT_PATH is set, "
                            "export the certificate again from Bridge.") from exc
        try:
            self._imap.login(mailbox.user, mailbox.password)
        except imaplib.IMAP4.error as exc:
            self._close()
            raise MailError(f"Bridge rejected the login for mailbox '{mailbox.label}'. Use the Bridge-generated "
                            "password (Bridge → mailbox → IMAP settings), not your Proton password.") from exc

    def _close(self) -> None:
        try:
            self._imap.shutdown()
        except OSError:
            pass

    def examine(self, folder: str) -> int:
        status, _ = self._imap.select(_quote(folder), readonly=True)
        if status != "OK":
            raise MailError(f"Folder '{folder}' was not found. Check BRIDGE_FOLDERS.")
        _, data = self._imap.response("UIDVALIDITY")
        try:
            return int(data[0])
        except (TypeError, ValueError, IndexError) as exc:
            raise MailError(f"Bridge did not report UIDVALIDITY for '{folder}'.") from exc

    def uids_after(self, last_uid: int, since: Optional[date] = None) -> List[int]:
        criteria = f"UID {int(last_uid) + 1}:*"
        if since is not None:                              # arrival date, in IMAP's English date format
            criteria += f" SINCE {since.day:02d}-{IMAP_MONTHS[since.month - 1]}-{since.year}"
        status, data = self._imap.uid("SEARCH", None, criteria)
        if status != "OK":
            raise MailError("Bridge could not list messages.")
        uids = [int(u) for u in (data[0] or b"").split()]
        return sorted(u for u in uids if u > last_uid)     # "n:*" also returns the highest UID when n is beyond it

    def fetch(self, uid: int) -> bytes:
        status, data = self._imap.uid("FETCH", str(int(uid)), FETCH_ITEMS)
        if status != "OK":
            raise MailError(f"Bridge could not read message {uid}.")
        for part in data:
            if isinstance(part, tuple) and len(part) == 2:
                return part[1]
        raise MailError(f"Bridge returned no content for message {uid}.")

    def logout(self) -> None:
        try:
            self._imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def connect(settings: Settings, mailbox: MailboxConfig) -> MailConnection:
    return ImapConnection(settings, mailbox)

