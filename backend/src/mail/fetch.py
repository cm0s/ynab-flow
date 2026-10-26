"""Incremental, read-only receipt fetching from the configured Bridge mailboxes.

Each mailbox folder keeps a cursor (UIDVALIDITY + last UID); only newer UIDs
are fetched, and only messages that arrived within ``fetch_since_days``, so the
first fetch of a large mailbox does not read years of mail. A receipt is identified by its content hash, so the same receipt
arriving in both household mailboxes is stored once with two sources, and a
UIDVALIDITY change rescans the folder without duplicating receipts. The
mailbox a receipt came from says nothing about who it was for.
"""

import imaplib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from src.ai.client import AIClient, AIError, BudgetExceeded
from src.ai.extract import extract_receipt, items_json
from src.mail.extract import ParsedMessage, analyze, content_hash, excerpt, parse_message
from src.mail.imap_source import MailConnection, MailError, connect as imap_connect
from src.models import MailboxCursor, Receipt, ReceiptSource
from src.settings import MailboxConfig, Settings

logger = logging.getLogger(__name__)

Connector = Callable[[Settings, MailboxConfig], MailConnection]


@dataclass
class FolderReport:
    mailbox: str
    folder: str
    messages: int = 0
    new_receipts: int = 0
    duplicates: int = 0
    not_receipts: int = 0
    error: str = ""


@dataclass
class FetchReport:
    folders: List[FolderReport] = field(default_factory=list)
    ai_note: str = ""

    def as_dict(self) -> dict:
        totals: Dict[str, int] = {k: sum(getattr(f, k) for f in self.folders)
                                  for k in ("messages", "new_receipts", "duplicates", "not_receipts")}
        return {**totals, "ai_note": self.ai_note, "folders": [f.__dict__ for f in self.folders]}


class _Fetcher:
    def __init__(self, db: Session, ai: Optional[AIClient]):
        self.db, self.ai = db, ai
        self.ai_note = "" if ai else "AI is off: receipts were read with local rules only."

    def ingest(self, raw: bytes, mailbox: str, folder: str, uidvalidity: int, uid: int, report: FolderReport) -> None:
        parsed = parse_message(raw)
        digest = content_hash(parsed)
        receipt = self.db.query(Receipt).filter(Receipt.content_hash == digest).first()
        if receipt is None:
            receipt = self._new_receipt(parsed, digest)
            if receipt.status == "not_receipt":
                report.not_receipts += 1
            else:
                report.new_receipts += 1
        else:
            report.duplicates += 1
            if receipt.status == "candidate":
                self._ai_extract(receipt, parsed)        # e.g. the budget was reached on an earlier fetch
        self._add_source(receipt, mailbox, folder, uidvalidity, uid, parsed.message_id)

    def _new_receipt(self, parsed: ParsedMessage, digest: str) -> Receipt:
        local = analyze(parsed)
        receipt = Receipt(content_hash=digest, subject=parsed.subject[:300], sender=parsed.sender_address[:200],
                          message_date=parsed.message_date, items_json="[]", text_excerpt="")
        self.db.add(receipt)
        if not local.is_candidate:
            receipt.status = "not_receipt"
            return receipt
        receipt.kind, receipt.merchant, receipt.reference = local.kind, local.merchant, local.reference
        receipt.total_milliunits = receipt.local_total_milliunits = local.total_milliunits
        receipt.currency, receipt.purchase_date, receipt.card_hint = local.currency, local.purchase_date, local.card_hint
        receipt.text_excerpt = excerpt(parsed)
        receipt.extraction_method = "pdf_text" if parsed.pdf_text else "text"
        receipt.status = "candidate"
        self._ai_extract(receipt, parsed, local)
        return receipt

    def _ai_extract(self, receipt: Receipt, parsed: ParsedMessage, local=None) -> None:
        if self.ai is None:
            return
        try:
            result = extract_receipt(self.ai, parsed, local or analyze(parsed))
        except BudgetExceeded as exc:
            self.ai, self.ai_note = None, str(exc)
            return
        except AIError as exc:
            receipt.error = str(exc)[:300]
            return
        receipt.error = ""
        if not result.is_receipt:
            receipt.status = "not_receipt"
            return
        receipt.merchant, receipt.reference, receipt.kind = result.merchant, result.reference, result.kind
        receipt.purchase_date, receipt.total_milliunits = result.purchase_date, result.total_milliunits
        receipt.currency, receipt.items_json = result.currency, items_json(result.items)
        receipt.extraction_method = result.method
        receipt.status = "needs_check" if result.needs_check else "extracted"

    def _add_source(self, receipt: Receipt, mailbox: str, folder: str, uidvalidity: int, uid: int,
                    message_id: str) -> None:
        self.db.flush()
        same_uid = self.db.query(ReceiptSource).filter_by(mailbox=mailbox, folder=folder, uidvalidity=uidvalidity,
                                                          uid=uid).first()
        if same_uid:
            return
        if message_id:
            seen = self.db.query(ReceiptSource).filter_by(receipt_id=receipt.id, mailbox=mailbox, folder=folder,
                                                          message_id=message_id).first()
            if seen:          # rescanned after a UIDVALIDITY change: same message, new UID
                seen.uidvalidity, seen.uid = uidvalidity, uid
                return
        self.db.add(ReceiptSource(receipt_id=receipt.id, mailbox=mailbox, folder=folder, uidvalidity=uidvalidity,
                                  uid=uid, message_id=message_id[:300]))


def _cursor(db: Session, mailbox: str, folder: str) -> MailboxCursor:
    cursor = db.query(MailboxCursor).filter_by(mailbox=mailbox, folder=folder).first()
    if cursor is None:
        cursor = MailboxCursor(mailbox=mailbox, folder=folder, last_uid=0, last_error="")
        db.add(cursor)
        db.flush()
    return cursor


def fetch_receipts(db: Session, settings: Settings, ai: Optional[AIClient] = None,
                   connect: Connector = imap_connect) -> FetchReport:
    """Fetch new messages from every configured mailbox folder, read-only; returns per-folder counts."""
    fetcher = _Fetcher(db, ai)
    report = FetchReport()
    for mailbox in settings.mailboxes:
        try:
            conn = connect(settings, mailbox)
        except MailError as exc:
            for folder in settings.folders:
                cursor = _cursor(db, mailbox.label, folder)
                cursor.last_error, cursor.last_fetch_at = str(exc), datetime.utcnow()
                report.folders.append(FolderReport(mailbox.label, folder, error=str(exc)))
            db.commit()
            continue
        since = date.today() - timedelta(days=settings.fetch_since_days) if settings.fetch_since_days else None
        try:
            for folder in settings.folders:
                report.folders.append(_fetch_folder(db, fetcher, conn, mailbox.label, folder, since))
        finally:
            conn.logout()
    if ai is not None:
        ai.flush_usage(db)
    db.commit()
    report.ai_note = fetcher.ai_note
    return report


def _fetch_folder(db: Session, fetcher: _Fetcher, conn: MailConnection, mailbox: str, folder: str,
                  since: Optional[date] = None) -> FolderReport:
    folder_report = FolderReport(mailbox, folder)
    cursor = _cursor(db, mailbox, folder)
    try:
        uidvalidity = conn.examine(folder)
        if cursor.uidvalidity != uidvalidity:
            cursor.uidvalidity, cursor.last_uid = uidvalidity, 0
        for uid in conn.uids_after(cursor.last_uid, since):
            fetcher.ingest(conn.fetch(uid), mailbox, folder, uidvalidity, uid, folder_report)
            folder_report.messages += 1
            cursor.last_uid = uid
            if fetcher.ai is not None:
                fetcher.ai.flush_usage(db)
            db.commit()                                   # progress survives an interrupted fetch
        cursor.last_error = ""
    except MailError as exc:
        cursor.last_error = folder_report.error = str(exc)
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.warning("Bridge connection lost: %s", type(exc).__name__)
        cursor.last_error = folder_report.error = "The connection to Bridge was interrupted. Try again."
    cursor.last_fetch_at = datetime.utcnow()
    db.commit()
    return folder_report


def mailbox_status(db: Session, settings: Settings) -> List[dict]:
    """Per configured mailbox and folder: last fetch, progress and any error, in plain terms."""
    out = []
    for mailbox in settings.mailboxes:
        folders = []
        for folder in settings.folders:
            cursor = db.query(MailboxCursor).filter_by(mailbox=mailbox.label, folder=folder).first()
            folders.append({
                "folder": folder,
                "last_fetch_at": cursor.last_fetch_at.isoformat() if cursor and cursor.last_fetch_at else None,
                "last_uid": cursor.last_uid if cursor else 0,
                "error": cursor.last_error if cursor else "",
            })
        received = db.query(ReceiptSource).filter_by(mailbox=mailbox.label).count()
        out.append({"mailbox": mailbox.label, "complete": mailbox.complete, "folders": folders, "messages_seen": received})
    return out
