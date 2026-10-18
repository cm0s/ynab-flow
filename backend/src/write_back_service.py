"""
FR-9: YNAB Write-Back Service

Validates and pushes approved transactions to YNAB via API.
Supports dry-run, create-only, and create-or-skip-if-duplicate modes.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from src.ynab_client import YnabClient, YNABAPIError
from src.models import Plan, Account, Category, CategoryGroup, Payee

logger = logging.getLogger(__name__)


@dataclass
class WriteTransaction:
    """A single transaction ready for write-back."""
    date: str              # YYYY-MM-DD
    amount: float          # Positive = inflow, negative = outflow (user units)
    payee_name: str
    category_name: str
    memo: str
    account_id: str        # Local account ID
    cleared: str = "uncleared"
    flag_color: Optional[str] = None  # "red"|"orange"|"yellow"|"green"|"blue"|"purple" or None


@dataclass
class WriteResult:
    """Result of a single write operation."""
    index: int
    date: str
    payee_name: str
    amount: float
    status: str              # "created" | "skipped" | "error" | "dry_run_ok" | "dry_run_error"
    ynab_transaction_id: Optional[str] = None
    error: Optional[str] = None
    import_id: Optional[str] = None


@dataclass
class WriteBackResult:
    """Aggregate result of the entire write-back operation."""
    mode: str
    total: int = 0
    created: int = 0
    skipped: int = 0
    errors: int = 0
    results: List[WriteResult] = field(default_factory=list)


class WriteBackService:
    """
    Orchestrates writing approved transactions to YNAB (FR-9).
    """

    def __init__(self, db: Session, ynab_client: YnabClient):
        self.db = db
        self.ynab = ynab_client

    def execute(
        self,
        plan_id: str,
        transactions: List[WriteTransaction],
        mode: str = "dry_run",  # "dry_run" | "create" | "create_or_skip"
        batch_id: Optional[str] = None,
    ) -> WriteBackResult:
        """
        Validate and optionally write transactions to YNAB.
        """
        plan = self.db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")

        result = WriteBackResult(mode=mode, total=len(transactions))

        # Build YNAB payloads with validation
        # Track occurrence counts so identical transactions get unique import_ids
        occurrence_counts: dict[str, int] = {}
        payloads = []
        for i, txn in enumerate(transactions):
            wr = self._validate_and_build(plan, txn, i)
            # Assign import_id with occurrence counter to avoid collisions
            # Include batch_id so re-imports after deletion get fresh IDs
            base_id = self._generate_import_id(txn, occurrence=1, batch_id=batch_id)
            base_key = base_id.rsplit(":", 1)[0]  # strip the ":1" suffix
            occurrence_counts[base_key] = occurrence_counts.get(base_key, 0) + 1
            wr.import_id = f"{base_key}:{occurrence_counts[base_key]}"
            result.results.append(wr)

            if wr.status.startswith("dry_run_error") or wr.status == "error":
                result.errors += 1
            elif mode == "dry_run":
                wr.status = "dry_run_ok"
            else:
                payloads.append((i, wr))

        if mode == "dry_run" or not payloads:
            return result

        # Build the YNAB transaction list
        ynab_txns = []
        payload_indices = []
        for idx, wr in payloads:
            txn = transactions[idx]
            account = self.db.query(Account).filter(Account.id == txn.account_id).first()
            ynab_account_id = account.ynab_account_id if account else ""

            # YNAB amount is in milliunits (multiply by 1000)
            millis = int(txn.amount * 1000)

            ynab_txn = {
                "account_id": ynab_account_id,
                "date": txn.date,
                "amount": millis,
                "memo": txn.memo[:200] if txn.memo else "",
                "cleared": txn.cleared,
                "import_id": wr.import_id,
            }

            if txn.flag_color:
                ynab_txn["flag_color"] = txn.flag_color

            # Resolve payee to YNAB ID; fall back to payee_name for new payees
            payee_id = self._resolve_payee_id(plan.id, txn.payee_name)
            if payee_id:
                ynab_txn["payee_id"] = payee_id
            else:
                ynab_txn["payee_name"] = txn.payee_name

            # Resolve category to YNAB ID; explicitly set null to prevent
            # YNAB from auto-assigning based on payee history
            ynab_txn["category_id"] = self._resolve_category_id(plan.id, txn.category_name)

            ynab_txns.append(ynab_txn)
            payload_indices.append(idx)

        # Send to YNAB
        try:
            import_ids = [t["import_id"] for t in ynab_txns]
            unique_import_ids = set(import_ids)
            logger.info(
                "Sending %d transactions to YNAB (%d unique import_ids)",
                len(ynab_txns), len(unique_import_ids),
            )
            if len(unique_import_ids) < len(import_ids):
                from collections import Counter
                dupes = {k: v for k, v in Counter(import_ids).items() if v > 1}
                logger.warning("Duplicate import_ids within batch: %s", dupes)

            t0 = time.monotonic()
            response = self.ynab.create_transactions(plan.ynab_plan_id, ynab_txns)
            elapsed = time.monotonic() - t0
            created_ids = response.get("transaction_ids", [])
            duplicate_ids = response.get("duplicate_import_ids", [])
            logger.info(
                "YNAB responded in %.1fs: %d created_ids, %d duplicate_import_ids, keys=%s",
                elapsed, len(created_ids), len(duplicate_ids), list(response.keys()),
            )

            for j, idx in enumerate(payload_indices):
                wr = result.results[idx]
                if j < len(created_ids):
                    wr.ynab_transaction_id = created_ids[j]
                    wr.status = "created"
                    result.created += 1
                elif wr.import_id in duplicate_ids:
                    wr.status = "skipped"
                    wr.error = "Duplicate: already exists in YNAB"
                    result.skipped += 1
                else:
                    wr.status = "created"
                    result.created += 1

        except YNABAPIError as e:
            for idx in payload_indices:
                wr = result.results[idx]
                wr.status = "error"
                wr.error = str(e)
                result.errors += 1

        return result

    def _validate_and_build(self, plan: Plan, txn: WriteTransaction, index: int) -> WriteResult:
        """Validate a single transaction and build its WriteResult (FR-9.3)."""
        wr = WriteResult(
            index=index,
            date=txn.date,
            payee_name=txn.payee_name,
            amount=txn.amount,
            status="pending",
        )

        # Validate account exists
        account = self.db.query(Account).filter(Account.id == txn.account_id).first()
        if not account:
            wr.status = "dry_run_error"
            wr.error = f"Account not found: {txn.account_id}"
            return wr

        # Validate date format
        try:
            datetime.strptime(txn.date, "%Y-%m-%d")
        except ValueError:
            wr.status = "dry_run_error"
            wr.error = f"Invalid date format: {txn.date}"
            return wr

        # Validate payee name
        if not txn.payee_name.strip():
            wr.status = "dry_run_error"
            wr.error = "Payee name is required"
            return wr

        return wr

    @staticmethod
    def _generate_import_id(
        txn: WriteTransaction, occurrence: int = 1, batch_id: Optional[str] = None,
    ) -> str:
        """
        Generate a deterministic import_id for deduplication (FR-9.3).
        YNAB uses import_id to prevent duplicates on repeated imports.
        Includes batch_id so that re-importing a CSV (new batch) after
        deleting transactions in YNAB produces fresh import_ids.
        The occurrence counter distinguishes transactions that share the
        same date/amount/payee/memo (e.g. two identical purchases on one day).
        """
        raw = f"{txn.date}:{txn.amount:.2f}:{txn.payee_name}:{txn.memo[:50]}"
        if batch_id:
            raw += f":{batch_id}"
        digest = hashlib.md5(raw.encode()).hexdigest()[:8]
        return f"YF:{digest}:{occurrence}"

    def _resolve_payee_id(self, plan_id: str, payee_name: str) -> Optional[str]:
        """Look up the YNAB payee ID from the local database."""
        if not payee_name:
            return None
        payee = (
            self.db.query(Payee)
            .filter(Payee.plan_id == plan_id, Payee.name == payee_name, Payee.deleted == False)
            .first()
        )
        return payee.ynab_payee_id if payee else None

    def _resolve_category_id(self, plan_id: str, category_name: str) -> Optional[str]:
        """Look up the YNAB category ID from the local database."""
        if not category_name:
            return None
        cat = (
            self.db.query(Category)
            .join(Category.category_group)
            .filter(
                CategoryGroup.plan_id == plan_id,
                Category.name == category_name,
                Category.deleted == False,
            )
            .first()
        )
        return cat.ynab_category_id if cat else None
