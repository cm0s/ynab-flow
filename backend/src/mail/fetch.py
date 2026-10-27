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
from src.ai.extract import extract_receipt
from src.mail.extract import ParsedMessage, analyze, content_hash, excerpt, parse_message
from src.mail.imap_source import MailConnection, MailError, connect as imap_connect
from src.models import MailboxCursor, Receipt, ReceiptSource
from src.receipts.review import apply_extraction, classify
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
            apply_extraction(receipt, {"status": "not_receipt"})
            return receipt
        read_locally(receipt, parsed, local)
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
        apply_ai_result(receipt, result)

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


def read_locally(receipt: Receipt, parsed: ParsedMessage, local) -> None:
    """Locally read fields of a receipt candidate (no AI); fields the user corrected are kept."""
    receipt.local_total_milliunits, receipt.card_hint = local.total_milliunits, local.card_hint
    receipt.text_excerpt = excerpt(parsed)
    receipt.extraction_method = "pdf_text" if parsed.pdf_text else "text"
    apply_extraction(receipt, {"kind": local.kind, "merchant": local.merchant, "reference": local.reference,
                               "total_milliunits": local.total_milliunits, "currency": local.currency,
                               "purchase_date": local.purchase_date, "status": "candidate"})


def apply_ai_result(receipt: Receipt, result) -> None:
    """Store an AI extraction; fields and the classification the user corrected are kept."""
    if not result.is_receipt:
        apply_extraction(receipt, {"status": "not_receipt"})
        return
    receipt.extraction_method = result.method
    apply_extraction(receipt, {
        "merchant": result.merchant, "reference": result.reference, "kind": result.kind,
        "purchase_date": result.purchase_date, "total_milliunits": result.total_milliunits,
        "currency": result.currency, "items": list(result.items),
        "status": "needs_check" if result.needs_check else "extracted",
    })


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


def fetch_message(settings: Settings, receipt: Receipt, connect: Connector = imap_connect) -> bytes:
    """Read one stored message again, read-only: by UID, or by Message-ID after a UIDVALIDITY change.

    Tries the receipt's sightings newest first; raises MailError in plain terms when none can be read.
    """
    mailboxes = {m.label: m for m in settings.mailboxes}
    reason = "This message's mailbox is no longer configured."
    for source in sorted(receipt.sources, key=lambda s: (s.created_at or datetime.min, s.uid), reverse=True):
        mailbox = mailboxes.get(source.mailbox)
        if mailbox is None:
            continue
        try:
            conn = connect(settings, mailbox)
        except MailError as exc:
            reason = str(exc)
            continue
        try:
            uidvalidity = conn.examine(source.folder)
            uid, raw = (source.uid if uidvalidity == source.uidvalidity else None), None
            if uid is not None:
                try:
                    raw = conn.fetch(uid)
                except MailError:
                    raw = None
            if raw is None:
                uid = conn.find_message_id(source.message_id)
                if uid is None:
                    reason = "The message is no longer in the mailbox."
                    continue
                raw = conn.fetch(uid)
            source.uidvalidity, source.uid = uidvalidity, uid
            return raw
        except MailError as exc:
            reason = str(exc)
        except (imaplib.IMAP4.error, OSError) as exc:
            logger.warning("Bridge connection lost: %s", type(exc).__name__)
            reason = "The connection to Bridge was interrupted. Try again."
        finally:
            conn.logout()
    raise MailError(reason)


def rescue(db: Session, settings: Settings, receipt: Receipt, ai: Optional[AIClient] = None,
           connect: Connector = imap_connect) -> str:
    """The user says a skipped message is a receipt: read it again (read-only) and extract it.

    The local receipt check is skipped. Returns a note for the user ("" when all went well); when Bridge
    cannot be reached the receipt keeps the user's classification and waits, with the reason stored.
    """
    classify(receipt, "candidate")
    receipt.error = ""
    try:
        parsed = parse_message(fetch_message(settings, receipt, connect))
    except MailError as exc:
        receipt.error = str(exc)[:300]
        return str(exc)
    local = analyze(parsed)
    read_locally(receipt, parsed, local)
    fetcher = _Fetcher(db, ai)
    fetcher._ai_extract(receipt, parsed, local)
    if ai is not None:
        ai.flush_usage(db)
    return receipt.error or (fetcher.ai_note if fetcher.ai is None else "")


def reread(db: Session, settings: Settings, receipt: Receipt, ai: AIClient, connect: Connector = imap_connect) -> None:
    """Read one receipt again with AI. Raises AIError (budget, refusal, invalid) and leaves the receipt unchanged.

    The full message is read again from Bridge when possible (PDFs included); otherwise the stored excerpt is used.
    """
    try:
        parsed = parse_message(fetch_message(settings, receipt, connect))
    except MailError:
        if not receipt.text_excerpt:
            raise
        parsed = ParsedMessage(subject=receipt.subject, sender_name="", sender_address=receipt.sender,
                               message_id="", message_date=receipt.message_date, text=receipt.text_excerpt)
    try:
        result = extract_receipt(ai, parsed, analyze(parsed))
    finally:
        ai.flush_usage(db)
    receipt.error = ""
    apply_ai_result(receipt, result)


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
