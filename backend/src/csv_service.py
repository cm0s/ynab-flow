"""
CSV Upload & Bulk Prediction Service

Parses bank CSV exports and runs each transaction through the orchestrator.
"""

import csv
import io
from dataclasses import dataclass, asdict
from typing import List, Optional
from sqlalchemy.orm import Session

from src.orchestrator import Orchestrator, PredictionResult


@dataclass
class CSVTransaction:
    """Parsed row from a bank CSV export."""
    date: str
    memo: str
    inflow: Optional[float]
    outflow: Optional[float]
    label: str
    source_category: str

    @property
    def amount(self) -> float:
        """Positive for inflows, negative for outflows."""
        if self.inflow:
            return self.inflow
        if self.outflow:
            return -self.outflow
        return 0.0


@dataclass
class BulkPredictionRow:
    """A single row in the bulk prediction result."""
    row_index: int
    date: str
    original_memo: str
    cleaned_memo: str
    merchant_stem: str
    amount: float
    label: str
    source_category: str
    payee: Optional[str]
    category: Optional[str]
    confidence: float
    source: str
    explanation: str
    review_required: bool
    flag_ignore: bool


def parse_csv(file_content: str) -> List[CSVTransaction]:
    """
    Parse a bank CSV with columns: Date, Memo, Inflow, Outflow, Label, Catégorie.
    Skips empty rows and rows with invalid dates.
    """
    reader = csv.DictReader(io.StringIO(file_content))
    transactions = []

    for row in reader:
        date = (row.get("Date") or "").strip()
        memo = (row.get("Memo") or "").strip()

        # Skip empty rows and invalid dates
        if not date or not memo or date.lower() == "invalid date":
            continue

        # Parse amounts
        inflow_str = (row.get("Inflow") or "").strip()
        outflow_str = (row.get("Outflow") or "").strip()
        inflow = float(inflow_str) if inflow_str else None
        outflow = float(outflow_str) if outflow_str else None

        label = (row.get("Label") or "").strip()
        # Handle the accented header "Catégorie"
        source_category = (
            row.get("Catégorie") or row.get("Categorie") or row.get("Category") or ""
        ).strip()

        transactions.append(CSVTransaction(
            date=date,
            memo=memo,
            inflow=inflow,
            outflow=outflow,
            label=label,
            source_category=source_category,
        ))

    return transactions


def bulk_predict(
    db: Session,
    plan_id: str,
    transactions: List[CSVTransaction],
) -> List[BulkPredictionRow]:
    """Run the orchestrator on every parsed CSV transaction."""
    orch = Orchestrator(db, plan_id)
    results = []

    for i, txn in enumerate(transactions):
        pred = orch.predict(
            memo=txn.memo,
            amount=txn.amount,
            category_name=txn.source_category,
        )
        results.append(BulkPredictionRow(
            row_index=i,
            date=txn.date,
            original_memo=pred.original_memo,
            cleaned_memo=pred.cleaned_memo,
            merchant_stem=pred.merchant_stem,
            amount=txn.amount,
            label=txn.label,
            source_category=txn.source_category,
            payee=pred.payee,
            category=pred.category,
            confidence=pred.confidence,
            source=pred.source,
            explanation=pred.explanation,
            review_required=pred.review_required,
            flag_ignore=pred.flag_ignore,
        ))

    return results
