"""
CSV Upload & Bulk Prediction Service

Parses bank CSV exports into evidence envelopes and runs each eligible
transaction through the orchestrator.
Supports:
  - Swisscard: header-detected, comma separated, DD.MM.YYYY dates
  - Generic format: Date,Memo,Inflow,Outflow,Label,Catégorie
  - PostFinance format: semicolon-separated with metadata header rows

Money is parsed with Decimal into exact integer milliunits (``src.money``);
the float ``amount``/``inflow``/``outflow`` fields are display views only.
"""

import csv
import io
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from src.money import MoneyError, parse_date, to_decimal, to_milliunits, milliunits_to_units
from src.orchestrator import Orchestrator, PredictionResult
from src.transfer_detector import detect_repayment

SUPPORTED_FORMATS_HELP = (
    "Supported formats: Swisscard export (Transaction date, Description, Merchant, Card number, "
    "Currency, Amount, Debit/Credit, Status), PostFinance export, or a generic CSV with "
    "Date, Memo, Inflow, Outflow columns."
)

SWISSCARD_REQUIRED = {
    "transaction date", "description", "merchant", "card number",
    "currency", "amount", "debit/credit", "status",
}
SWISSCARD_DATE_FORMATS = ["%d.%m.%Y"]
GENERIC_DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y"]
SWISSCARD_CURRENCIES = {"CHF"}


class CSVFormatError(ValueError):
    """The upload is not a supported layout."""


@dataclass
class RowError:
    line: int
    message: str


@dataclass
class CSVTransaction:
    """Parsed row from a bank CSV export."""
    date: str
    memo: str
    inflow: Optional[float]
    outflow: Optional[float]
    label: str
    source_category: str
    amount_milliunits: Optional[int] = None
    currency: str = ""
    source_format: str = ""
    source_status: str = ""
    source_line: int = 0
    evidence: Dict[str, str] = field(default_factory=dict)

    @property
    def amount(self) -> float:
        """Positive for inflows, negative for outflows (currency units)."""
        if self.amount_milliunits is not None:
            return milliunits_to_units(self.amount_milliunits)
        if self.inflow:
            return self.inflow
        if self.outflow:
            return -self.outflow
        return 0.0


@dataclass
class ImportReport:
    source_format: str
    rows: List[CSVTransaction] = field(default_factory=list)       # eligible (Posted / valid)
    pending: List[CSVTransaction] = field(default_factory=list)    # evidence only, never written
    errors: List[RowError] = field(default_factory=list)

    @property
    def eligible_count(self) -> int:
        return len(self.rows)

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    @property
    def invalid_count(self) -> int:
        return len(self.errors)

    @property
    def detected_count(self) -> int:
        return self.eligible_count + self.pending_count + self.invalid_count

    def summary(self) -> dict:
        return {
            "source_format": self.source_format,
            "detected": self.detected_count,
            "eligible": self.eligible_count,
            "pending": self.pending_count,
            "invalid": self.invalid_count,
            "errors": [asdict(e) for e in self.errors],
            "pending_rows": [
                {"date": t.date, "memo": t.memo, "amount_milliunits": t.amount_milliunits,
                 "currency": t.currency, "source_line": t.source_line, "evidence": t.evidence}
                for t in self.pending
            ],
        }


@dataclass
class BulkPredictionRow:
    """A single row in the bulk prediction result."""
    row_index: int
    date: str
    original_memo: str
    cleaned_memo: str
    merchant_stem: str
    amount: float
    amount_milliunits: Optional[int]
    label: str
    source_category: str
    payee: Optional[str]
    category: Optional[str]
    confidence: float
    source: str
    explanation: str
    review_required: bool
    flag_ignore: bool
    payee_confidence: float = 0.0
    category_confidence: float = 0.0
    payee_source: str = "unclassified"
    category_source: str = "unclassified"
    payee_explanation: str = ""
    category_explanation: str = ""
    review_flag: bool = False
    transfer_state: str = "none"
    category_id: Optional[str] = None


def _is_postfinance(file_content: str) -> bool:
    """Detect PostFinance CSV by checking for its metadata header."""
    first_line = file_content.split("\n", 1)[0]
    return first_line.startswith("Date de d")


def _convert_date_dmy(date_str: str) -> str:
    """Convert DD.MM.YYYY to YYYY-MM-DD."""
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", date_str)
    if not match:
        return date_str
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"


def _norm_header(name: Optional[str]) -> str:
    return (name or "").replace("\ufeff", "").strip().casefold()


def _sniff_delimiter(first_line: str) -> str:
    return max([",", ";", "\t"], key=first_line.count)


def _first_nonblank_line(content: str) -> str:
    for line in content.replace("\ufeff", "").splitlines():
        if line.strip():
            return line
    return ""


def detect_format(content: str) -> str:
    """Return ``blank``, ``postfinance``, ``swisscard`` or ``generic``; else raise."""
    content = content.replace("\ufeff", "", 1)
    if not content.strip():
        return "blank"
    if _is_postfinance(content):
        return "postfinance"
    line = _first_nonblank_line(content)
    headers = {_norm_header(h) for h in next(csv.reader([line], delimiter=_sniff_delimiter(line)))}
    if SWISSCARD_REQUIRED <= headers:
        return "swisscard"
    if {"date", "memo"} <= headers and ({"inflow", "outflow"} & headers):
        return "generic"
    raise CSVFormatError(f"Unrecognized CSV layout (columns: {', '.join(sorted(h for h in headers if h))}). {SUPPORTED_FORMATS_HELP}")


def _signed_units(milliunits: int) -> tuple:
    """(inflow, outflow) float display views for a signed milliunit amount."""
    units = milliunits_to_units(abs(milliunits))
    return (units, None) if milliunits > 0 else (None, units) if milliunits < 0 else (None, None)


def _make_txn(*, date, memo, milliunits, label, source_category, currency, fmt, status, line, evidence):
    inflow, outflow = _signed_units(milliunits)
    return CSVTransaction(
        date=date, memo=memo, inflow=inflow, outflow=outflow, label=label,
        source_category=source_category, amount_milliunits=milliunits, currency=currency,
        source_format=fmt, source_status=status, source_line=line, evidence=evidence,
    )


def _normalized_row(row: dict) -> Dict[str, str]:
    """Row keyed by normalized header; values kept exactly as read."""
    return {_norm_header(k): (v if v is not None else "") for k, v in row.items() if k is not None}


# ---------------------------------------------------------------------------
# Swisscard
# ---------------------------------------------------------------------------

def _parse_swisscard(content: str, report: ImportReport) -> None:
    line = _first_nonblank_line(content)
    delim = _sniff_delimiter(line)
    reader = csv.DictReader(io.StringIO(content.replace("\ufeff", "", 1)), delimiter=delim)
    reader.fieldnames = [(_n or "").replace("\ufeff", "").strip() for _n in reader.fieldnames or []]
    for raw in reader:
        row = _normalized_row(raw)
        if not any((v or "").strip() for v in row.values()):
            continue
        lineno = reader.line_num
        evidence = {
            (k or "").replace("\ufeff", "").strip(): (v if v is not None else "")
            for k, v in raw.items() if k is not None
        }
        try:
            date = parse_date(row["transaction date"], SWISSCARD_DATE_FORMATS)
            currency = row["currency"].strip().upper()
            if currency not in SWISSCARD_CURRENCIES:
                raise MoneyError(f"Unsupported currency {row['currency'].strip()!r}; only CHF is supported")
            direction = row["debit/credit"].strip().casefold()
            amount = to_decimal(row["amount"])
            if direction == "debit":
                if amount < 0:
                    raise MoneyError("Debit row has a negative amount (sign inconsistent with Debit)")
            elif direction == "credit":
                if amount > 0:
                    raise MoneyError("Credit row has a positive amount (sign inconsistent with Credit)")
            else:
                raise MoneyError(f"Unsupported Debit/Credit value {row['debit/credit'].strip()!r}")
            milliunits = -to_milliunits(amount)  # YNAB money is the negation of the export convention
            status = row["status"].strip()
            if status.casefold() not in ("posted", "pending"):
                raise MoneyError(f"Unsupported status {status!r}")
            description = row["description"].strip()
            merchant = row["merchant"].strip()
            memo = description or merchant
            if not memo:
                raise MoneyError("Row has neither Description nor Merchant")
        except (MoneyError, KeyError) as exc:
            report.errors.append(RowError(lineno, str(exc)))
            continue
        txn = _make_txn(
            date=date, memo=memo, milliunits=milliunits, label="",
            source_category=(row.get("registered category") or row.get("merchant category") or "").strip(),
            currency=currency, fmt="swisscard", status=status.capitalize(), line=lineno, evidence=evidence,
        )
        (report.rows if txn.source_status == "Posted" else report.pending).append(txn)


# ---------------------------------------------------------------------------
# PostFinance
# ---------------------------------------------------------------------------

def _parse_postfinance(file_content: str, report: ImportReport) -> None:
    """
    Parse a PostFinance CSV export.

    Format: 4 metadata lines, blank line, header line, blank line, then data.
    Columns: Date;Type de transaction;Texte de notification;Crédit en CHF;Débit en CHF;Label;Catégorie
    """
    lines = file_content.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.startswith("Date;")), None)
    if header_idx is None:
        raise CSVFormatError(f"PostFinance export has no 'Date;' header row. {SUPPORTED_FORMATS_HELP}")

    csv_block = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_block), delimiter=";")
    for row in reader:
        date_raw = (row.get("Date") or "").strip()
        memo = (row.get("Texte de notification") or "").strip()
        if not date_raw or not memo:
            continue
        lineno = header_idx + reader.line_num
        evidence = {k: (v or "") for k, v in row.items() if k is not None}
        try:
            date = parse_date(date_raw, ["%d.%m.%Y"])
            credit_str = (row.get("Crédit en CHF") or "").strip()
            debit_str = (row.get("Débit en CHF") or "").strip()
            milliunits = 0
            if credit_str:
                credit = to_milliunits(credit_str)
                if credit < 0:
                    raise MoneyError("Credit column holds a negative amount")
                milliunits += credit
            if debit_str:
                milliunits -= abs(to_milliunits(debit_str))  # debits are negative in the file
        except MoneyError as exc:
            report.errors.append(RowError(lineno, str(exc)))
            continue
        label = (row.get("Label") or "").strip()
        source_category = (row.get("Catégorie") or row.get("Categorie") or "").strip()
        report.rows.append(_make_txn(
            date=date, memo=memo, milliunits=milliunits, label=label, source_category=source_category,
            currency="CHF", fmt="postfinance", status="", line=lineno, evidence=evidence,
        ))


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------

def _parse_generic(file_content: str, report: ImportReport) -> None:
    """Parse a generic bank CSV with columns: Date, Memo, Inflow, Outflow, Label, Catégorie."""
    reader = csv.DictReader(io.StringIO(file_content.replace("\ufeff", "", 1)))
    for raw in reader:
        row = _normalized_row(raw)
        date_raw = row.get("date", "").strip()
        memo = row.get("memo", "").strip()
        # Skip empty rows and the literal "Invalid date" placeholders
        if not date_raw or not memo or date_raw.lower() == "invalid date":
            continue
        lineno = reader.line_num
        evidence = {(k or "").strip(): (v if v is not None else "") for k, v in raw.items() if k is not None}
        try:
            date = parse_date(date_raw, GENERIC_DATE_FORMATS)
            inflow_str = row.get("inflow", "").strip()
            outflow_str = row.get("outflow", "").strip()
            milliunits = 0
            if inflow_str:
                inflow = to_milliunits(inflow_str)
                if inflow < 0:
                    raise MoneyError("Inflow holds a negative amount")
                milliunits += inflow
            if outflow_str:
                outflow = to_milliunits(outflow_str)
                if outflow < 0:
                    raise MoneyError("Outflow holds a negative amount")
                milliunits -= outflow
        except MoneyError as exc:
            report.errors.append(RowError(lineno, str(exc)))
            continue
        label = row.get("label", "").strip()
        source_category = (row.get("catégorie") or row.get("categorie") or row.get("category") or "").strip()
        report.rows.append(_make_txn(
            date=date, memo=memo, milliunits=milliunits, label=label, source_category=source_category,
            currency="", fmt="generic", status="", line=lineno, evidence=evidence,
        ))


def parse_import(file_content: str) -> ImportReport:
    """Auto-detect the layout and parse into an ``ImportReport``."""
    fmt = detect_format(file_content)
    report = ImportReport(source_format=fmt)
    if fmt == "swisscard":
        _parse_swisscard(file_content, report)
    elif fmt == "postfinance":
        _parse_postfinance(file_content, report)
    elif fmt == "generic":
        _parse_generic(file_content, report)
    return report


def parse_csv(file_content: str) -> List[CSVTransaction]:
    """Eligible transactions of an import (compatibility wrapper)."""
    return parse_import(file_content).rows


def bulk_predict(
    db: Session,
    plan_id: str,
    transactions: List[CSVTransaction],
    account_id: str = "",
    account_name: str = "",
) -> List[BulkPredictionRow]:
    """Run the orchestrator on every parsed CSV transaction in the target account's context."""
    orch = Orchestrator(db, plan_id)
    results = []

    for i, txn in enumerate(transactions):
        repayment = detect_repayment(txn.memo, txn.amount, (txn.evidence or {}).get("Merchant", ""))
        pred = orch.predict(
            memo=txn.memo,
            amount=txn.amount,
            account_name=account_name,
            account_id=account_id,
            category_name=txn.source_category,
            require_category=not repayment.is_transfer,
        )
        transfer_state = "none"
        if repayment.is_transfer and not pred.flag_ignore:
            # Repayment candidate: no ordinary payee/category until routing is confirmed
            transfer_state = "candidate"
            pred.payee = pred.category = pred.category_id = None
            pred.payee_confidence = pred.category_confidence = 0.0
            pred.payee_source = pred.category_source = "unclassified"
            pred.confidence = 0.0
            pred.review_required = True
            pred.review_flag = True
            pred.explanation = f"Possible card repayment (transfer): {repayment.reason}"
        results.append(BulkPredictionRow(
            row_index=i,
            date=txn.date,
            original_memo=pred.original_memo,
            cleaned_memo=pred.cleaned_memo,
            merchant_stem=pred.merchant_stem,
            amount=txn.amount,
            amount_milliunits=txn.amount_milliunits,
            label=txn.label,
            source_category=txn.source_category,
            payee=pred.payee,
            category=pred.category,
            category_id=pred.category_id,
            confidence=pred.confidence,
            source=pred.source,
            explanation=pred.explanation,
            review_required=pred.review_required,
            flag_ignore=pred.flag_ignore,
            payee_confidence=pred.payee_confidence,
            category_confidence=pred.category_confidence,
            payee_source=pred.payee_source,
            category_source=pred.category_source,
            payee_explanation=pred.payee_explanation,
            category_explanation=pred.category_explanation,
            review_flag=pred.review_flag,
            transfer_state=transfer_state,
        ))

    return results
