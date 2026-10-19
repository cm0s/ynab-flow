"""
FR-9: YNAB Write-Back Service

Pushes persisted import rows to YNAB. Bank values (date, amount, account,
identity) are read from the persisted source record, never from the caller;
only payee, category, memo and flag color are editable. Every created
transaction is sent with ``approved=false`` so YNAB approval stays a separate
audit step.

Outcomes are correlated by import ID, never by response position. A source
record is claimed (short committed state) before the network call, so no
database transaction is held open during the request and simultaneous
pushes cannot send the same source twice. Timeouts and 5xx responses leave
the record ``reconcile_required``; the next push checks YNAB history before
resending the same import ID.
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models import Account, Category, CategoryGroup, ImportRow, Payee, Plan, SourceRecord
from src.money import milliunits_to_units
from src.reconciliation import (
    COMPLETED, block_reason, reconcile_rows, refresh_history, transfer_payee_for,
)
from src.ynab_client import YnabClient, YNABAPIError

logger = logging.getLogger(__name__)

CLAIM_TTL = timedelta(minutes=5)
YNAB_MEMO_LIMIT = 200


@dataclass
class RowOverride:
    """The only values a caller may supply for a persisted row."""
    payee_name: str = ""
    category_name: str = ""
    memo: str = ""
    flag_color: Optional[str] = None
    cleared: str = "uncleared"


@dataclass
class WriteResult:
    """Result for one submitted row."""
    index: int
    row_id: str
    source_id: Optional[str]
    import_id: Optional[str]
    date: str
    payee_name: str
    amount: float                      # currency units, display only
    amount_milliunits: Optional[int]
    status: str                        # created | existing | error | unresolved | dry_run_ok | dry_run_error
    ynab_transaction_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class WriteBackResult:
    mode: str
    total: int = 0
    created: int = 0
    existing: int = 0
    errors: int = 0
    unresolved: int = 0
    results: List[WriteResult] = field(default_factory=list)


def recover_stale_claims(db: Session, older_than: Optional[timedelta] = CLAIM_TTL) -> int:
    """Turn abandoned claims into reconcile_required (``older_than=None``: all, e.g. at startup)."""
    query = db.query(SourceRecord).filter(SourceRecord.write_state == "claimed")
    if older_than is not None:
        query = query.filter(SourceRecord.claimed_at < datetime.utcnow() - older_than)
    count = 0
    for rec in query.all():
        rec.write_state = "reconcile_required"
        rec.claim_token = None
        count += 1
    db.commit()
    return count


class WriteBackService:
    def __init__(self, db: Session, ynab_client: YnabClient):
        self.db = db
        self.ynab = ynab_client

    # ------------------------------------------------------------------

    def execute(
        self,
        plan_id: str,
        rows: List[ImportRow],
        overrides: Dict[str, RowOverride],
        mode: str = "dry_run",   # "dry_run" | "create" | "create_or_skip"
    ) -> WriteBackResult:
        plan = self.db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")
        dry = mode == "dry_run"
        result = WriteBackResult(mode=mode, total=len(rows))

        recover_stale_claims(self.db)
        fresh, fresh_msg = refresh_history(self.db, plan, self.ynab)
        prepared: List[tuple] = []          # (WriteResult, row, override)
        seen_sources = set()
        for i, row in enumerate(rows):
            ov = overrides.get(row.id) or RowOverride()
            rec = row.source_record
            wr = WriteResult(
                index=i, row_id=row.id, source_id=rec.id if rec else None,
                import_id=rec.import_id if rec else None, date=rec.date if rec else row.date,
                payee_name=ov.payee_name, amount=milliunits_to_units(rec.amount_milliunits) if rec else row.amount,
                amount_milliunits=rec.amount_milliunits if rec else None, status="pending",
            )
            result.results.append(wr)
            if rec is None:
                self._fail(wr, "Row has no persisted source record; re-import it to obtain one.", dry)
            elif rec.id in seen_sources:
                self._fail(wr, "Duplicate submission of the same source row.", dry)
            else:
                seen_sources.add(rec.id)
                prepared.append((wr, row, ov))

        # Reconcile with refreshed history; a dry run must not persist links or completion
        reconcile_rows(self.db, plan, [r for _, r, _ in prepared], fresh)
        for wr, row, ov in prepared:
            self._precheck(plan, wr, row, ov, dry, fresh_msg)
            rec = row.source_record
            if wr.status == "pending" and rec.write_state == "reconcile_required" and fresh and not dry:
                rec.write_state = "unsent"     # confirmed absent from YNAB: same import ID may be resent
        if dry:
            for wr, row, _ in prepared:
                if wr.status == "pending":
                    wr.status = "dry_run_ok"
            self._tally(result)
            self.db.rollback()
            return result
        self._persist_blocked(prepared)
        self.db.commit()

        sendable = [(wr, row, ov) for wr, row, ov in prepared if wr.status == "pending"]
        payloads = []
        token = uuid.uuid4().hex
        for wr, row, ov in sendable:
            if not self._claim(row.source_record, token):
                wr.status = "unresolved"
                wr.error = "A write for this source row is already in progress or needs reconciliation."
                continue
            payloads.append((wr, row, self._build_payload(plan, row, ov)))
        self._persist_blocked(sendable)
        self.db.commit()

        if payloads:
            self._send(plan, payloads)
        self._tally(result)
        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _persist_blocked(items: List[tuple]) -> None:
        """Keep local failures visible on the row (separately from review status) across reloads."""
        for wr, row, _ in items:
            if wr.status in ("error", "unresolved") and row.write_state not in COMPLETED:
                row.write_state, row.write_error = wr.status, wr.error or ""

    @staticmethod
    def _fail(wr: WriteResult, message: str, dry: bool) -> None:
        wr.status = "dry_run_error" if dry else "error"
        wr.error = message

    def _precheck(self, plan: Plan, wr: WriteResult, row: ImportRow, ov: RowOverride, dry: bool, fresh_msg: str) -> None:
        rec = row.source_record
        if row.write_state in COMPLETED:
            wr.status, wr.ynab_transaction_id = "existing", row.ynab_transaction_id
            wr.error = row.reconciliation_note or "Already in YNAB."
            return
        account = self.db.query(Account).filter(Account.id == rec.account_id).first()
        if account is None or account.plan_id != plan.id:
            return self._fail(wr, f"Account not found in this plan: {rec.account_id}", dry)
        if row.batch is not None and row.batch.plan_id != plan.id:
            return self._fail(wr, "Row belongs to a different plan.", dry)
        try:
            datetime.strptime(rec.date, "%Y-%m-%d")
        except ValueError:
            return self._fail(wr, f"Invalid date format: {rec.date}", dry)
        if row.transfer_state != "resolved" and not (ov.payee_name or "").strip():
            return self._fail(wr, "Payee name is required", dry)
        reason = block_reason(row)
        if reason:
            wr.status = "dry_run_error" if dry else "unresolved"
            wr.error = reason if not fresh_msg or row.reconciliation_state != "unverified" else fresh_msg
            return
        if row.transfer_state == "resolved":
            source = self.db.get(Account, row.transfer_account_id) if row.transfer_account_id else None
            if source is None or transfer_payee_for(self.db, plan.id, source) is None:
                return self._fail(wr, "Repayment has no valid transfer account.", dry)

    # ------------------------------------------------------------------
    # Claims and payloads
    # ------------------------------------------------------------------

    def _claim(self, rec: SourceRecord, token: str) -> bool:
        stmt = (
            update(SourceRecord)
            .where(SourceRecord.id == rec.id, SourceRecord.write_state.in_(("unsent", "error")))
            .values(write_state="claimed", claim_token=token, claimed_at=datetime.utcnow(), last_error="")
        )
        claimed = self.db.execute(stmt).rowcount == 1
        if claimed:
            self.db.refresh(rec)
        return claimed

    def _build_payload(self, plan: Plan, row: ImportRow, ov: RowOverride) -> dict:
        rec = row.source_record
        account = self.db.query(Account).filter(Account.id == rec.account_id).first()
        payload = {
            "account_id": account.ynab_account_id,
            "date": rec.date,
            "amount": int(rec.amount_milliunits),
            "memo": (ov.memo or row.edited_memo or row.original_memo or "")[:YNAB_MEMO_LIMIT],
            "cleared": ov.cleared or "uncleared",
            "approved": False,
            "import_id": rec.import_id,
        }
        if ov.flag_color:
            payload["flag_color"] = ov.flag_color
        if row.transfer_state == "resolved":
            payee = transfer_payee_for(self.db, plan.id, self.db.get(Account, row.transfer_account_id))
            payload["payee_id"] = payee.ynab_payee_id
            payload["category_id"] = None           # transfers carry no ordinary category
            return payload
        payee_id = self._resolve_payee_id(plan.id, ov.payee_name)
        if payee_id:
            payload["payee_id"] = payee_id
        else:
            payload["payee_name"] = ov.payee_name
        # Explicit null stops YNAB auto-assigning a category from payee history
        payload["category_id"] = self._resolve_category_id(plan.id, ov.category_name)
        return payload

    # ------------------------------------------------------------------
    # Sending and outcome correlation
    # ------------------------------------------------------------------

    def _send(self, plan: Plan, payloads: List[tuple]) -> None:
        body = [p for _, _, p in payloads]
        try:
            logger.info("Sending %d transactions to YNAB", len(body))
            response = self.ynab.create_transactions(plan.ynab_plan_id, body)
        except YNABAPIError as exc:
            uncertain = exc.status_code is None or exc.status_code >= 500
            for wr, row, _ in payloads:
                self._finish(wr, row, "unresolved" if uncertain else "error", error=str(exc))
            return
        except httpx.HTTPError as exc:       # timeouts and transport failures prove nothing
            for wr, row, _ in payloads:
                self._finish(wr, row, "unresolved", error=f"No confirmed response from YNAB ({type(exc).__name__}).")
            return

        outcomes = self._correlate(plan, response, [(wr.import_id) for wr, _, _ in payloads])
        for wr, row, _ in payloads:
            status, ynab_id, error = outcomes[wr.import_id]
            self._finish(wr, row, status, ynab_id=ynab_id, error=error)

    def _correlate(self, plan: Plan, response: dict, import_ids: List[str]) -> Dict[str, tuple]:
        txns = list(response.get("transactions") or [])
        if not txns and response.get("transaction"):
            txns = [response["transaction"]]
        by_import: Dict[str, str] = {}
        repeated = set()
        for t in txns:
            iid = t.get("import_id")
            if not iid:
                continue
            if iid in by_import:
                repeated.add(iid)
            by_import[iid] = t.get("id")
        # ID-only responses: read each transaction to learn its import_id (no positional zip)
        if not txns and response.get("transaction_ids"):
            for tid in response["transaction_ids"]:
                try:
                    t = self.ynab.get_transaction(plan.ynab_plan_id, tid).get("transaction", {})
                except Exception as exc:       # leave unmapped -> unresolved
                    logger.warning("Could not read created transaction %s: %s", tid, exc)
                    continue
                iid = t.get("import_id")
                if iid:
                    if iid in by_import:
                        repeated.add(iid)
                    by_import[iid] = t.get("id") or tid
        dups = Counter(response.get("duplicate_import_ids") or [])
        wanted = Counter(import_ids)
        out: Dict[str, tuple] = {}
        for iid in import_ids:
            if wanted[iid] > 1 or iid in repeated or (iid in by_import and dups[iid]):
                out[iid] = ("unresolved", None, "Contradictory or repeated YNAB response for this import ID.")
            elif iid in by_import:
                out[iid] = ("created", by_import[iid], None)
            elif dups[iid] == 1:
                out[iid] = ("existing", None, "Duplicate: already exists in YNAB")
            else:
                out[iid] = ("unresolved", None, "YNAB did not report an outcome for this import ID.")
        return out

    def _finish(self, wr: WriteResult, row: ImportRow, status: str, ynab_id: Optional[str] = None, error: Optional[str] = None) -> None:
        rec = row.source_record
        wr.status, wr.error = status, error
        wr.ynab_transaction_id = ynab_id
        rec.claim_token = None
        if status == "created":
            rec.write_state, rec.ynab_transaction_id, rec.reconciliation_state = "created", ynab_id, "linked"
            row.write_state, row.ynab_transaction_id, row.write_error = "created", ynab_id, ""
        elif status == "existing":
            rec.write_state, rec.reconciliation_state = "existing", "linked"
            row.write_state, row.write_error = "existing", ""
        elif status == "unresolved":
            rec.write_state, rec.last_error = "reconcile_required", error or ""
            row.write_state, row.write_error = "unresolved", error or ""
        else:
            rec.write_state, rec.last_error = "error", error or ""
            row.write_state, row.write_error = "error", error or ""
        self.db.commit()

    @staticmethod
    def _tally(result: WriteBackResult) -> None:
        counts = Counter(r.status for r in result.results)
        result.created = counts["created"]
        result.existing = counts["existing"]
        result.errors = counts["error"] + counts["dry_run_error"]
        result.unresolved = counts["unresolved"]

    # ------------------------------------------------------------------

    def _resolve_payee_id(self, plan_id: str, payee_name: str) -> Optional[str]:
        if not payee_name:
            return None
        payee = (
            self.db.query(Payee)
            .filter(Payee.plan_id == plan_id, Payee.name == payee_name, Payee.deleted == False)  # noqa: E712
            .first()
        )
        return payee.ynab_payee_id if payee else None

    def _resolve_category_id(self, plan_id: str, category_name: str) -> Optional[str]:
        if not category_name:
            return None
        cat = (
            self.db.query(Category)
            .join(Category.category_group)
            .filter(CategoryGroup.plan_id == plan_id, Category.name == category_name, Category.deleted == False)  # noqa: E712
            .first()
        )
        return cat.ynab_category_id if cat else None
