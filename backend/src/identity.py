"""Persistent, occurrence-aware source identities (ledger) for bank rows.

A fingerprint hashes only immutable source evidence. Identical rows in one
export get separate occurrence ordinals; the ``source_records`` unique
constraint (plan, account, fingerprint, occurrence) makes allocation safe
under concurrency. Predictions, edits, upload batches and Pending/Posted
state never influence identity.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.csv_service import CSVTransaction
from src.models import SourceRecord, SourceExport

FINGERPRINT_VERSION = 1
IMPORT_ID_PREFIX = "YF1"          # YNAB import_id is limited to 36 characters
IMPORT_ID_HASH_LEN = 24
COMPLETED_WRITE_STATES = ("created", "existing")


class IdentityCollision(Exception):
    """Two different pieces of evidence produced the same fingerprint."""


def _transport(text: Optional[str]) -> str:
    """Declared transport normalization only: NFC and surrounding whitespace."""
    return unicodedata.normalize("NFC", (text or "").strip())


def canonical_evidence(txn: CSVTransaction) -> Dict[str, object]:
    """The exact inputs of the fingerprint (kept alongside the hash)."""
    ev = txn.evidence or {}
    return {
        "v": FINGERPRINT_VERSION,
        "format": txn.source_format,
        "date": txn.date,
        "amount_milliunits": txn.amount_milliunits,
        "currency": txn.currency,
        "card": _transport(ev.get("Card number")),
        "description": _transport(ev.get("Description") if "Description" in ev else txn.memo),
        "merchant": _transport(ev.get("Merchant")),
    }


def fingerprint_of(canonical: Dict[str, object]) -> str:
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def make_import_id(plan_id: str, account_id: str, fingerprint: str, occurrence: int) -> str:
    scope = hashlib.sha256(f"{plan_id}|{account_id}|{fingerprint}".encode()).hexdigest()
    return f"{IMPORT_ID_PREFIX}:{scope[:IMPORT_ID_HASH_LEN]}:{occurrence}"


def export_signature(counts: Dict[str, int]) -> str:
    """Order-independent signature of an export's fingerprint multiset."""
    blob = json.dumps(sorted(counts.items()), separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Allocation:
    """Result of mapping one eligible CSV row onto the ledger."""
    txn: CSVTransaction
    record: SourceRecord
    state: str = "pending"   # pending | ambiguous
    note: str = ""


@dataclass
class AllocationResult:
    allocations: List[Allocation] = field(default_factory=list)
    signature: str = ""
    repeated_export: bool = False


def _insert_record(db: Session, plan_id, account_id, fp, occurrence, canonical, txn) -> SourceRecord:
    """Insert a ledger entry; on a concurrent duplicate, reuse the winner."""
    record = SourceRecord(
        plan_id=plan_id, account_id=account_id, fingerprint_version=FINGERPRINT_VERSION,
        fingerprint=fp, occurrence=occurrence, source_format=txn.source_format,
        date=txn.date, amount_milliunits=txn.amount_milliunits, currency=txn.currency,
        evidence_json=json.dumps({"canonical": canonical, "raw": txn.evidence}, sort_keys=True, ensure_ascii=False),
        import_id=make_import_id(plan_id, account_id, fp, occurrence),
    )
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
        return record
    except IntegrityError:
        existing = (
            db.query(SourceRecord)
            .filter_by(plan_id=plan_id, account_id=account_id, fingerprint=fp, occurrence=occurrence)
            .one_or_none()
        )
        if existing is None:
            raise
        return existing


def allocate_occurrences(
    db: Session, plan_id: str, account_id: str, rows: List[CSVTransaction],
) -> AllocationResult:
    """Map eligible rows to ledger occurrences (creating only demonstrated extras)."""
    canon = [canonical_evidence(r) for r in rows]
    fps = [fingerprint_of(c) for c in canon]
    counts: Dict[str, int] = {}
    for fp in fps:
        counts[fp] = counts.get(fp, 0) + 1
    signature = export_signature(counts)
    repeated = (
        db.query(SourceExport)
        .filter_by(plan_id=plan_id, account_id=account_id, signature=signature)
        .first()
    ) is not None

    ledger: Dict[str, List[SourceRecord]] = {}
    if counts:
        for rec in (
            db.query(SourceRecord)
            .filter(SourceRecord.plan_id == plan_id, SourceRecord.account_id == account_id,
                    SourceRecord.fingerprint.in_(list(counts)))
            .order_by(SourceRecord.occurrence)
            .all()
        ):
            ledger.setdefault(rec.fingerprint, []).append(rec)

    # Raw-evidence comparison guards against hash collisions
    for fp, c in zip(fps, canon):
        for rec in ledger.get(fp, [])[:1]:
            stored = json.loads(rec.evidence_json).get("canonical")
            if stored != c:
                raise IdentityCollision(f"Fingerprint {fp[:12]} matches different stored evidence")

    prior = {fp: len(recs) for fp, recs in ledger.items()}
    seen: Dict[str, int] = {}
    result = AllocationResult(signature=signature, repeated_export=repeated)
    for txn, fp, c in zip(rows, fps, canon):
        seen[fp] = seen.get(fp, 0) + 1
        ordinal = seen[fp]
        existing = ledger.setdefault(fp, [])
        by_occ = {r.occurrence: r for r in existing}
        record = by_occ.get(ordinal)
        state, note = "pending", ""
        if record is None:
            record = _insert_record(db, plan_id, account_id, fp, ordinal, c, txn)
            existing.append(record)
        elif counts[fp] < prior.get(fp, 0) and not repeated:
            # Fewer copies than previously observed in a different export: which
            # occurrence this is cannot be known unless their write states agree.
            if len({r.write_state in COMPLETED_WRITE_STATES for r in existing[:prior[fp]]}) > 1:
                state = "ambiguous"
                note = (f"This row was previously seen {prior[fp]} times but appears {counts[fp]} "
                        f"time(s) here; confirm whether it is the same purchase or a new one.")
        result.allocations.append(Allocation(txn=txn, record=record, state=state, note=note))

    if not repeated and counts:
        db.add(SourceExport(plan_id=plan_id, account_id=account_id, signature=signature,
                            counts_json=json.dumps(counts, sort_keys=True)))
        db.flush()
    return result


def add_confirmed_occurrence(db: Session, record: SourceRecord) -> SourceRecord:
    """User-confirmed distinct purchase: allocate the next occurrence for this fingerprint."""
    highest = (
        db.query(SourceRecord.occurrence)
        .filter_by(plan_id=record.plan_id, account_id=record.account_id, fingerprint=record.fingerprint)
        .order_by(SourceRecord.occurrence.desc())
        .first()
    )[0]
    clone = SourceRecord(
        plan_id=record.plan_id, account_id=record.account_id, fingerprint_version=record.fingerprint_version,
        fingerprint=record.fingerprint, occurrence=highest + 1, source_format=record.source_format,
        date=record.date, amount_milliunits=record.amount_milliunits, currency=record.currency,
        evidence_json=record.evidence_json,
        import_id=make_import_id(record.plan_id, record.account_id, record.fingerprint, highest + 1),
    )
    db.add(clone)
    db.flush()
    return clone
