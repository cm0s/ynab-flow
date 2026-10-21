import json
import os
from threading import Lock
from dataclasses import asdict
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

load_dotenv()
from sqlalchemy.orm import Session
from src.database import get_db
from src.ynab_client import YnabClient, YNABAPIError
from src.sync_service import SyncService
from src.normalizer import normalize
from src.rule_engine import RuleEngine
from src.orchestrator import Orchestrator
from src.ml_classifier import MLClassifier
from src.csv_service import CSVTransaction, CSVFormatError, parse_import, bulk_predict
from src.write_back_service import WriteBackService, RowOverride, recover_stale_claims
from src.transfer_detector import detect_transfer
from src.identity import allocate_occurrences, add_confirmed_occurrence, IdentityCollision, canonical_evidence
from src.reconciliation import (
    COMPLETED, candidate_transactions, refresh_history, reconcile_rows, suggest_transfer_accounts, transfer_payee_for,
)
from src.models import Plan, Account, CategoryGroup, Category, Payee, Rule, Transaction, ImportBatch, ImportRow, SourceRecord, SourceExport
from src.auth import verify_token, validate_auth_config

validate_auth_config()

app = FastAPI(title="YNAB Flow API")
training_lock = Lock()

# CORS — allow frontend origins
allowed_origins = [origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def recover_interrupted_writes():
    """A restart abandons in-flight writes: their outcome must be reconciled before any resend."""
    from src.database import SessionLocal
    db = SessionLocal()
    try:
        recover_stale_claims(db, older_than=None)
    except Exception as e:  # e.g. database not migrated yet
        print(f"Claim recovery skipped: {e}")
    finally:
        db.close()


@app.on_event("startup")
def auto_train_models():
    """Train ML models on startup if they don't exist and data is available."""
    from src.ml_classifier import PAYEE_MODEL_PATH, CATEGORY_MODEL_PATH
    if PAYEE_MODEL_PATH.exists() and CATEGORY_MODEL_PATH.exists():
        return
    from src.database import SessionLocal
    db = SessionLocal()
    try:
        plan = db.query(Plan).first()
        if plan:
            classifier = MLClassifier()
            classifier.train(db, plan.id)
    except Exception as e:
        print(f"Auto-train skipped: {e}")
    finally:
        db.close()


def get_ynab_client():
    api_key = os.environ.get("YNAB_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="YNAB_API_KEY environment variable not set.")
    return YnabClient(api_key=api_key)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/sync/budgets")
def sync_budgets(
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client),
    _user: str = Depends(verify_token),
):
    """Fetch and upsert basic budget and account structures."""
    try:
        service = SyncService(db, client)
        service.sync_budgets()
        return {"status": "success", "message": "Budgets synced successfully."}
    except YNABAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/sync/plan/{plan_id}")
def sync_plan_data(
    plan_id: str,
    full: bool = Query(False, description="Reset knowledge and re-fetch everything"),
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client),
    _user: str = Depends(verify_token),
):
    """Fetch delta updates for Categories, Payees, and Transactions for a specific plan."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found in local database.")

    if full:
        plan.last_server_knowledge = None
        plan.last_knowledge_payees = None
        plan.last_knowledge_transactions = None
        db.flush()

    try:
        service = SyncService(db, client)
        service.sync_all(plan)
        return {"status": "success", "message": f"Successfully synced data for plan: {plan.name}"}
    except YNABAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/sync/plan/{plan_id}/jobs", status_code=202)
def start_plan_sync(
    plan_id: str,
    background_tasks: BackgroundTasks,
    full: bool = False,
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client),
    _user: str = Depends(verify_token),
):
    from src.sync_jobs import start_job, run_job
    if not db.get(Plan, plan_id):
        raise HTTPException(status_code=404, detail="Plan not found in local database.")
    job, created = start_job(db, plan_id)
    if created:
        background_tasks.add_task(run_job, job.id, full, client.api_key)
    return {"id": job.id, "status": job.status, "message": job.message}


@app.get("/sync/jobs/{job_id}")
def get_sync_job(job_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    from src.models import SyncJob
    from src.sync_jobs import expire_job
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found. Please retry.")
    expire_job(db, job)
    return {"id": job.id, "status": job.status, "message": job.message}


# ---------------------------------------------------------------------------
# Pydantic schemas for Phase 3
# ---------------------------------------------------------------------------

class NormalizeRequest(BaseModel):
    memo: str

class ClassifyRequest(BaseModel):
    plan_id: str
    memo: str
    amount: float = 0.0
    account_name: str = ""
    category_name: str = ""

class RuleCreate(BaseModel):
    plan_id: str
    name: str
    priority: int = 0
    match_type: str = "contains"  # exact | contains | regex
    pattern: str
    amount_sign: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    account_filter: Optional[str] = None
    category_filter: Optional[str] = None
    assign_payee: Optional[str] = None
    assign_category: Optional[str] = None
    flag_review: bool = False
    flag_ignore: bool = False

# ---------------------------------------------------------------------------
# Phase 3 endpoints
# ---------------------------------------------------------------------------

@app.post("/normalize")
def normalize_memo(req: NormalizeRequest, _user: str = Depends(verify_token)):
    """Normalize a raw bank memo (FR-3)."""
    result = normalize(req.memo)
    return {
        "original_memo": result.original_memo,
        "cleaned_memo": result.cleaned_memo,
        "merchant_stem": result.merchant_stem,
    }

@app.post("/classify")
def classify_transaction(req: ClassifyRequest, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Normalize a memo and run it through the rule engine (FR-3 + FR-4)."""
    norm = normalize(req.memo)
    engine = RuleEngine(db, req.plan_id)
    match = engine.evaluate(
        normalized_memo=norm.cleaned_memo,
        amount=req.amount,
        account_name=req.account_name,
        category_name=req.category_name,
    )
    return {
        "original_memo": norm.original_memo,
        "cleaned_memo": norm.cleaned_memo,
        "merchant_stem": norm.merchant_stem,
        "matched_rule": match.rule_name if match else None,
        "assign_payee": match.assign_payee if match else None,
        "assign_category": match.assign_category if match else None,
        "flag_review": match.flag_review if match else False,
        "flag_ignore": match.flag_ignore if match else False,
    }

@app.get("/rules")
def list_rules(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """List all rules for a plan, ordered by priority."""
    rules = (
        db.query(Rule)
        .filter(Rule.plan_id == plan_id)
        .order_by(Rule.priority.desc())
        .all()
    )
    return [
        {
            "id": r.id, "name": r.name, "priority": r.priority,
            "match_type": r.match_type, "pattern": r.pattern,
            "assign_payee": r.assign_payee, "assign_category": r.assign_category,
            "flag_review": r.flag_review, "flag_ignore": r.flag_ignore,
        }
        for r in rules
    ]

@app.post("/rules")
def create_rule(req: RuleCreate, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Create a new deterministic rule."""
    rule = Rule(
        plan_id=req.plan_id,
        name=req.name,
        priority=req.priority,
        match_type=req.match_type,
        pattern=req.pattern,
        amount_sign=req.amount_sign,
        amount_min=req.amount_min,
        amount_max=req.amount_max,
        account_filter=req.account_filter,
        category_filter=req.category_filter,
        assign_payee=req.assign_payee,
        assign_category=req.assign_category,
        flag_review=req.flag_review,
        flag_ignore=req.flag_ignore,
    )
    db.add(rule)
    db.commit()
    return {"status": "success", "rule_id": rule.id, "name": rule.name}

# ---------------------------------------------------------------------------
# Phase 4 endpoints
# ---------------------------------------------------------------------------

class PredictRequest(BaseModel):
    plan_id: str
    memo: str
    amount: float = 0.0
    account_name: str = ""
    account_id: str = ""
    category_name: str = ""

@app.post("/predict")
def predict_transaction(req: PredictRequest, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Full prediction orchestration (FR-7)."""
    plan = db.query(Plan).filter(Plan.id == req.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    orch = Orchestrator(db, req.plan_id)
    result = orch.predict(
        memo=req.memo,
        amount=req.amount,
        account_name=req.account_name,
        account_id=req.account_id,
        category_name=req.category_name,
    )
    return {
        "original_memo": result.original_memo,
        "cleaned_memo": result.cleaned_memo,
        "merchant_stem": result.merchant_stem,
        "payee": result.payee,
        "category": result.category,
        "confidence": result.confidence,
        "source": result.source,
        "explanation": result.explanation,
        "review_required": result.review_required,
        "flag_ignore": result.flag_ignore,
        "payee_confidence": result.payee_confidence,
        "category_confidence": result.category_confidence,
        "payee_source": result.payee_source,
        "category_source": result.category_source,
        "payee_explanation": result.payee_explanation,
        "category_explanation": result.category_explanation,
        "review_flag": result.review_flag,
    }

@app.post("/train")
def train_models(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Train ML models from historical YNAB transactions (FR-6.4)."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if not training_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Model training is already running. Please wait before retrying.")
    try:
        classifier = MLClassifier()
        stats = classifier.train(db, plan_id)
        if not stats["payee_trained"] and not stats["category_trained"]:
            raise HTTPException(status_code=422, detail="Not enough labeled history to train. Sync transactions with at least two different payees or categories.")
        return {"status": "success", **stats}
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Training could not use the available transaction memos. Check that your synced history contains meaningful text.") from error
    finally:
        training_lock.release()

# ---------------------------------------------------------------------------
# Phase 5 endpoints — import batches
# ---------------------------------------------------------------------------

REVIEW_STATUSES = ("pending", "accepted", "ignored")
PREDICTION_FIELDS = (
    "payee", "category", "confidence", "source", "explanation", "review_required", "flag_ignore",
    "cleaned_memo", "merchant_stem", "payee_confidence", "category_confidence", "payee_source",
    "category_source", "payee_explanation", "category_explanation", "review_flag",
)


def _batch_rows_to_dicts(rows: list) -> list:
    """Convert ImportRow list to API response dicts (rows already in YNAB are excluded)."""
    return [
        {
            "id": r.id,
            "row_index": r.row_index,
            "date": r.date,
            "original_memo": r.original_memo,
            "cleaned_memo": r.cleaned_memo,
            "merchant_stem": r.merchant_stem,
            "amount": r.amount,
            "amount_milliunits": r.amount_milliunits,
            "currency": r.currency,
            "label": r.label,
            "source_category": r.source_category,
            "source_format": r.source_format,
            "source_status": r.source_status,
            "evidence": json.loads(r.evidence_json or "{}"),
            "account_id": r.account_id,
            "source_id": r.source_record_id,
            "import_id": r.source_record.import_id if r.source_record else None,
            "payee": r.payee,
            "category": r.category,
            "confidence": r.confidence,
            "payee_confidence": r.payee_confidence,
            "category_confidence": r.category_confidence,
            "payee_source": r.payee_source,
            "category_source": r.category_source,
            "payee_explanation": r.payee_explanation,
            "category_explanation": r.category_explanation,
            "source": r.source,
            "explanation": r.explanation,
            "review_required": r.review_required,
            "review_flag": r.review_flag,
            "flag_ignore": r.flag_ignore,
            "transfer_state": r.transfer_state,
            "transfer_account_id": r.transfer_account_id,
            "reconciliation_state": r.reconciliation_state,
            "reconciliation_note": r.reconciliation_note,
            "write_state": r.write_state,
            "write_error": r.write_error,
            "ynab_transaction_id": r.ynab_transaction_id,
            "status": r.status,
            "edited_payee": r.edited_payee,
            "edited_category": r.edited_category,
            "edited_memo": r.edited_memo,
            "flag_color": r.flag_color,
        }
        for r in sorted(rows, key=lambda r: r.row_index)
        if r.write_state not in COMPLETED
    ]


def _batch_payload(db: Session, batch: ImportBatch) -> dict:
    account = db.get(Account, batch.account_id) if batch.account_id else None
    return {
        "batch_id": batch.id,
        "filename": batch.filename,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "account_id": batch.account_id,
        "account_name": account.name if account else None,
        "account_confirmed": bool(batch.account_confirmed),
        "source_format": batch.source_format,
        "report": json.loads(batch.report_json or "{}"),
        "rows": _batch_rows_to_dicts(batch.rows),
    }


def _row_to_txn(r: ImportRow) -> CSVTransaction:
    """Rebuild the parsed form of a persisted row (immutable evidence + edited-free memo)."""
    return CSVTransaction(
        date=r.date, memo=r.original_memo,
        inflow=r.amount if r.amount >= 0 else None,
        outflow=-r.amount if r.amount < 0 else None,
        label=r.label, source_category=r.source_category,
        amount_milliunits=r.amount_milliunits, currency=r.currency,
        source_format=r.source_format or "legacy", source_status=r.source_status,
        evidence=json.loads(r.evidence_json or "{}"),
    )


def _apply_predictions(rows: List[ImportRow], results, keep_transfer: bool = True) -> None:
    """Write fresh predictions onto rows while preserving user edits and review decisions."""
    from dataclasses import asdict
    for import_row, pred in zip(rows, results):
        d = asdict(pred)
        user_edited_payee = import_row.edited_payee != (import_row.payee or "")
        user_edited_category = import_row.edited_category != (import_row.category or "")
        for name in PREDICTION_FIELDS:
            setattr(import_row, name, d[name])
        if not (keep_transfer and import_row.transfer_state in ("resolved", "linked", "ambiguous")):
            import_row.transfer_state = d["transfer_state"]
        if not user_edited_payee:
            import_row.edited_payee = d["payee"] or ""
        if not user_edited_category:
            import_row.edited_category = d["category"] or ""
        if import_row.status != "ignored":
            auto = not d["review_required"] and not d["flag_ignore"] and import_row.transfer_state == "none"
            if not user_edited_payee and not user_edited_category:
                import_row.status = "accepted" if auto else "pending"


def _account_for_batch(db: Session, batch: ImportBatch):
    return db.get(Account, batch.account_id) if batch.account_id else None


@app.post("/upload-csv")
async def upload_csv(
    plan_id: str = Query(...),
    account_id: str = Query(..., description="Target account (local id) the file belongs to"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: str = Depends(verify_token),
):
    """Upload a bank CSV for a target account; predictions and identities are persisted as an import batch."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    account = db.query(Account).filter(Account.id == account_id, Account.plan_id == plan_id).first()
    if not account or account.closed:
        raise HTTPException(status_code=422, detail="Select an open target account that belongs to this budget before importing.")

    try:
        content = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="The file is not valid UTF-8 text. Export it again as CSV (UTF-8).")
    try:
        report = parse_import(content)
    except CSVFormatError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        results = bulk_predict(db, plan_id, report.rows, account.id, account.name)
        allocation = allocate_occurrences(db, plan_id, account.id, report.rows)
    except IdentityCollision as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))

    summary = report.summary()
    # Ambiguous rows stay visible even when their reused occurrence is done: the user must resolve them
    kept = [(r, a) for r, a in zip(results, allocation.allocations)
            if a.state == "ambiguous" or a.record.write_state not in COMPLETED]
    summary["already_imported"] = len(results) - len(kept)
    summary["repeated_export"] = allocation.repeated_export

    # Only now replace the active batch: a failed upload never abandons it
    db.query(ImportBatch).filter(
        ImportBatch.plan_id == plan_id, ImportBatch.status == "active"
    ).update({"status": "abandoned"})
    batch = ImportBatch(
        plan_id=plan_id, filename=file.filename, account_id=account.id, account_confirmed=True,
        source_format=report.source_format, report_json=json.dumps(summary),
        export_signature=allocation.signature, status="active" if kept else "completed",
    )
    db.add(batch)
    db.flush()

    from dataclasses import asdict
    for index, (r, alloc) in enumerate(kept):
        d = asdict(r)
        txn = alloc.txn
        auto_approved = not d["review_required"] and not d["flag_ignore"] and d["transfer_state"] == "none" and alloc.state == "pending"
        db.add(ImportRow(
            batch_id=batch.id, row_index=index, date=d["date"],
            original_memo=d["original_memo"], amount=d["amount"], label=d["label"],
            source_category=d["source_category"],
            **{name: d[name] for name in PREDICTION_FIELDS if name not in ("cleaned_memo", "merchant_stem")},
            cleaned_memo=d["cleaned_memo"], merchant_stem=d["merchant_stem"],
            status="accepted" if auto_approved else "pending",
            edited_payee=d["payee"] or "", edited_category=d["category"] or "",
            account_id=account.id, amount_milliunits=txn.amount_milliunits, currency=txn.currency,
            source_format=txn.source_format, source_status=txn.source_status,
            evidence_json=json.dumps(txn.evidence, ensure_ascii=False), source_record_id=alloc.record.id,
            transfer_state=d["transfer_state"],
            reconciliation_state=alloc.state, reconciliation_note=alloc.note,
        ))
    db.commit()
    db.refresh(batch)

    payload = _batch_payload(db, batch)
    return {**payload, "count": len(payload["rows"]), "predictions": payload["rows"]}


class ReclassifyRequest(BaseModel):
    plan_id: str
    batch_id: Optional[str] = None

@app.post("/reclassify")
def reclassify(req: ReclassifyRequest, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Re-run classification on a persisted import batch using its persisted account."""
    plan = db.query(Plan).filter(Plan.id == req.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if req.batch_id:
        batch = db.query(ImportBatch).filter(ImportBatch.id == req.batch_id, ImportBatch.plan_id == req.plan_id).first()
    else:
        batch = db.query(ImportBatch).filter(
            ImportBatch.plan_id == req.plan_id, ImportBatch.status == "active"
        ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="No active import batch found.")

    account = _account_for_batch(db, batch)
    import_rows = [r for r in sorted(batch.rows, key=lambda r: r.row_index) if r.write_state not in COMPLETED]
    results = bulk_predict(
        db, req.plan_id, [_row_to_txn(r) for r in import_rows],
        account.id if account else "", account.name if account else "",
    )
    _apply_predictions(import_rows, results)
    db.commit()
    payload = _batch_payload(db, batch)
    return {**payload, "count": len(payload["rows"]), "predictions": payload["rows"]}


@app.get("/import-batches/{plan_id}/active")
def get_active_batch(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Return the active import batch for a plan, or 404."""
    batch = db.query(ImportBatch).filter(
        ImportBatch.plan_id == plan_id, ImportBatch.status == "active"
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="No active import batch.")
    return _batch_payload(db, batch)


class ImportRowUpdate(BaseModel):
    id: str
    status: Optional[str] = None
    edited_payee: Optional[str] = None
    edited_category: Optional[str] = None
    edited_memo: Optional[str] = None
    flag_color: Optional[str] = None

class ImportRowsUpdateRequest(BaseModel):
    updates: List[ImportRowUpdate]

@app.patch("/import-rows")
def update_import_rows(req: ImportRowsUpdateRequest, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Bulk-save review state changes on import rows. Bank evidence is not editable."""
    updated = 0
    for u in req.updates:
        if u.status is not None and u.status not in REVIEW_STATUSES:
            raise HTTPException(status_code=422, detail=f"Invalid status '{u.status}'. Use: {', '.join(REVIEW_STATUSES)}.")
        row = db.query(ImportRow).filter(ImportRow.id == u.id).first()
        if not row or row.write_state in COMPLETED:
            continue
        if u.status is not None:
            row.status = u.status
        if u.edited_payee is not None:
            row.edited_payee = u.edited_payee
        if u.edited_category is not None:
            row.edited_category = u.edited_category
        if u.edited_memo is not None:
            row.edited_memo = u.edited_memo
        if u.flag_color is not None:
            row.flag_color = u.flag_color
        updated += 1
    db.commit()
    return {"updated": updated}


def _retryable(rows: List[ImportRow]) -> List[ImportRow]:
    """Rows that still need a decision or a (re)try: not in YNAB and not deliberately ignored."""
    return [r for r in rows if r.write_state not in COMPLETED and r.status != "ignored"]


@app.post("/import-batches/{batch_id}/complete")
def complete_batch(batch_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Mark an import batch as completed once every row is handled or ignored."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    remaining = _retryable(batch.rows)
    if remaining:
        raise HTTPException(
            status_code=409,
            detail=f"{len(remaining)} row(s) are not yet in YNAB or ignored. Retry or ignore them before completing the batch.",
        )
    batch.status = "completed"
    db.commit()
    return {"status": "completed"}


@app.post("/import-batches/{batch_id}/discard")
def discard_batch(batch_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Remove an active import that has not been pushed.

    Rows already in YNAB (and any record that may have reached YNAB) keep their
    identity so a re-upload cannot duplicate them; everything else is forgotten.
    """
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if batch.status != "active":
        raise HTTPException(status_code=409, detail="Only the active import can be removed.")
    unsure = [r for r in batch.rows if r.source_record is not None
              and r.source_record.write_state in ("claimed", "reconcile_required")]
    if unsure:
        raise HTTPException(
            status_code=409,
            detail=f"{len(unsure)} row(s) may already be in YNAB. Check them against YNAB before removing this import.",
        )
    removable = [r for r in batch.rows if r.write_state not in COMPLETED]
    keep_rows = [r for r in batch.rows if r.write_state in COMPLETED]
    record_ids = {r.source_record_id for r in removable if r.source_record_id}
    removed = len(removable)
    for r in removable:
        db.delete(r)
    db.flush()
    # Forget identities only this import created and nothing else references
    in_use = {x[0] for x in db.query(ImportRow.source_record_id).filter(ImportRow.source_record_id.in_(record_ids)).all()} \
        if record_ids else set()
    for rec in db.query(SourceRecord).filter(SourceRecord.id.in_(record_ids)).all() if record_ids else []:
        if rec.id not in in_use and rec.write_state in ("unsent", "error") and not rec.ynab_transaction_id:
            db.delete(rec)
    report = json.loads(batch.report_json or "{}")
    if batch.export_signature and not report.get("repeated_export") and batch.account_id:
        db.query(SourceExport).filter_by(
            plan_id=batch.plan_id, account_id=batch.account_id, signature=batch.export_signature).delete()
    if keep_rows:
        batch.status = "abandoned"
    else:
        db.delete(batch)
    db.commit()
    return {"removed_rows": removed, "kept_rows": len(keep_rows)}


# ---------------------------------------------------------------------------
# Reconciliation, repayment routing and account reassignment
# ---------------------------------------------------------------------------

def _row_view(row: ImportRow) -> dict:
    """API view of one row (rows already in YNAB only report their write state)."""
    if row.write_state in COMPLETED:
        return {"id": row.id, "write_state": row.write_state, "ynab_transaction_id": row.ynab_transaction_id}
    return _batch_rows_to_dicts([row])[0]


def _owned_row(db: Session, row_id: str) -> ImportRow:
    row = db.query(ImportRow).filter(ImportRow.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Import row not found.")
    return row


def _reconcile_batch_rows(db: Session, batch: ImportBatch, rows: List[ImportRow], client: YnabClient) -> Optional[str]:
    plan = db.get(Plan, batch.plan_id)
    fresh, message = refresh_history(db, plan, client)
    reconcile_rows(db, plan, rows, fresh)
    db.commit()
    return None if fresh else message


@app.post("/import-batches/{batch_id}/reconcile")
def reconcile_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client),
    _user: str = Depends(verify_token),
):
    """Refresh YNAB history and update reconciliation / repayment state on pending rows."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    warning = _reconcile_batch_rows(db, batch, [r for r in batch.rows if r.write_state not in COMPLETED], client)
    suggestions = suggest_transfer_accounts(db, batch.plan_id, batch.account_id) if batch.account_id else []
    linked = sum(1 for r in batch.rows if r.reconciliation_state == "linked" and r.write_state in COMPLETED)
    return {**_batch_payload(db, batch), "warning": warning, "transfer_account_suggestions": suggestions,
            "linked_count": linked}


@app.get("/import-rows/{row_id}/candidates")
def row_candidates(row_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """YNAB transactions this blocked row could be linked to."""
    row = _owned_row(db, row_id)
    return {"candidates": candidate_transactions(db, row)}


class RowResolution(BaseModel):
    action: str  # link_existing | confirm_new | confirm_same
    ynab_transaction_id: Optional[str] = None

@app.post("/import-rows/{row_id}/resolve")
def resolve_row(row_id: str, req: RowResolution, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Resolve a reconciliation block without touching immutable bank values."""
    row = _owned_row(db, row_id)
    rec = row.source_record
    if rec is None or row.write_state in COMPLETED:
        raise HTTPException(status_code=409, detail="This row has nothing to resolve.")
    if req.action == "link_existing":
        txn = db.query(Transaction).filter(
            Transaction.ynab_transaction_id == (req.ynab_transaction_id or ""),
            Transaction.plan_id == rec.plan_id, Transaction.account_id == rec.account_id,
            Transaction.deleted == False,  # noqa: E712
        ).first()
        if txn is None:
            raise HTTPException(status_code=422, detail="No live YNAB transaction with that id exists on the target account.")
        taken = db.query(SourceRecord).filter(
            SourceRecord.ynab_transaction_id == txn.ynab_transaction_id, SourceRecord.id != rec.id).first()
        if taken:
            raise HTTPException(status_code=409, detail="That YNAB transaction is already linked to another source row.")
        rec.ynab_transaction_id = txn.ynab_transaction_id
        rec.write_state = "existing"
        rec.reconciliation_state = "linked"
        row.reconciliation_state, row.write_state = "linked", "existing"
        row.ynab_transaction_id = txn.ynab_transaction_id
        row.reconciliation_note = "Linked by user."
    elif req.action == "confirm_new":
        if row.reconciliation_state in ("unverified", "quarantined", "legacy_unresolved"):
            raise HTTPException(status_code=409, detail="Refresh YNAB history and confirm the account before marking this row as new.")
        if row.reconciliation_state in ("ambiguous", "deleted_link"):
            # A new purchase / recreation needs its own occurrence and import ID
            new_rec = add_confirmed_occurrence(db, rec)
            row.source_record_id = new_rec.id
            rec = new_rec
        rec.reconciliation_state = row.reconciliation_state = "confirmed_new"
        row.reconciliation_note = "Confirmed by user as a distinct transaction."
    elif req.action == "confirm_same":
        if row.reconciliation_state != "ambiguous":
            raise HTTPException(status_code=409, detail="Only ambiguous rows can be confirmed as a repeat.")
        row.reconciliation_state, row.reconciliation_note = "pending", "Confirmed by user as the previously seen purchase."
        if rec.write_state in COMPLETED:
            row.write_state, row.ynab_transaction_id = rec.write_state, rec.ynab_transaction_id
    else:
        raise HTTPException(status_code=422, detail="Invalid action. Use: link_existing, confirm_new, confirm_same.")
    db.commit()
    return _row_view(row)


class TransferRouting(BaseModel):
    account_id: Optional[str] = None  # local id of the paying account; null keeps the repayment unresolved

@app.post("/import-rows/{row_id}/transfer")
def route_transfer(
    row_id: str, req: TransferRouting,
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client),
    _user: str = Depends(verify_token),
):
    """Choose (or clear) the account a repayment was paid from."""
    row = _owned_row(db, row_id)
    rec = row.source_record
    if rec is None or row.transfer_state == "none" or row.write_state in COMPLETED:
        raise HTTPException(status_code=409, detail="This row is not an unresolved repayment.")
    if req.account_id:
        source = db.query(Account).filter(
            Account.id == req.account_id, Account.plan_id == rec.plan_id).first()
        if not source or source.closed or source.id == rec.account_id:
            raise HTTPException(status_code=422, detail="Choose another open account in this budget.")
        if transfer_payee_for(db, rec.plan_id, source) is None:
            raise HTTPException(status_code=422, detail=f"YNAB has no transfer payee for {source.name}; sync and retry.")
    row.transfer_account_id = req.account_id
    if row.transfer_state == "resolved":
        row.transfer_state = "candidate"
    batch = row.batch
    warning = _reconcile_batch_rows(db, batch, [row], client)
    return {**_row_view(row), "warning": warning}


class ReassignRequest(BaseModel):
    account_id: str

@app.post("/import-batches/{batch_id}/reassign-account")
def reassign_account(batch_id: str, req: ReassignRequest, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Explicitly (re)assign the batch's target account; identities and predictions are recomputed."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    account = db.query(Account).filter(Account.id == req.account_id, Account.plan_id == batch.plan_id).first()
    if not account or account.closed:
        raise HTTPException(status_code=422, detail="Choose an open account that belongs to this budget.")
    rows = [r for r in sorted(batch.rows, key=lambda r: r.row_index) if r.write_state not in COMPLETED]
    if any(r.source_record and r.source_record.write_state in ("claimed", "reconcile_required") for r in rows):
        raise HTTPException(status_code=409, detail="A write is in progress or unresolved; reconcile it before changing the account.")
    movable = [r for r in rows if r.amount_milliunits is not None]
    txns = [_row_to_txn(r) for r in movable]
    try:
        allocation = allocate_occurrences(db, batch.plan_id, account.id, txns)
    except IdentityCollision as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    results = bulk_predict(db, batch.plan_id, txns, account.id, account.name)
    for row, alloc in zip(movable, allocation.allocations):
        row.account_id = account.id
        row.source_record_id = alloc.record.id
        row.reconciliation_state = alloc.state
        row.reconciliation_note = alloc.note
        row.transfer_account_id = None if row.transfer_account_id == account.id else row.transfer_account_id
        if row.transfer_state in ("resolved", "linked", "ambiguous"):
            row.transfer_state = "candidate"
        if alloc.record.write_state in COMPLETED and alloc.state != "ambiguous":
            row.write_state, row.ynab_transaction_id = alloc.record.write_state, alloc.record.ynab_transaction_id
    _apply_predictions(movable, results, keep_transfer=False)
    batch.account_id, batch.account_confirmed = account.id, True
    db.commit()
    return _batch_payload(db, batch)


@app.put("/rules/{rule_id}")
def update_rule(rule_id: str, req: RuleCreate, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Update an existing rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    for field in [
        "name", "priority", "match_type", "pattern", "amount_sign",
        "amount_min", "amount_max", "account_filter", "category_filter",
        "assign_payee", "assign_category", "flag_review", "flag_ignore",
    ]:
        setattr(rule, field, getattr(req, field))
    db.commit()
    return {"status": "success", "rule_id": rule.id}


@app.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    db.delete(rule)
    db.commit()
    return {"status": "success"}


@app.get("/plans")
def list_plans(db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """List all synced plans, ordered by transaction count (most data first)."""
    from sqlalchemy import func
    plans = (
        db.query(Plan, func.count(Transaction.id).label("txn_count"))
        .outerjoin(Transaction, (Transaction.plan_id == Plan.id) & (Transaction.deleted == False))
        .group_by(Plan.id)
        .order_by(func.count(Transaction.id).desc())
        .all()
    )
    return [
        {"id": p.id, "name": p.name, "ynab_plan_id": p.ynab_plan_id}
        for p, _ in plans
    ]


@app.get("/plans/{plan_id}/accounts")
def list_accounts(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """List accounts for a plan."""
    accounts = db.query(Account).filter(Account.plan_id == plan_id).all()
    return [
        {"id": a.id, "name": a.name, "type": a.type, "closed": a.closed}
        for a in accounts
    ]


@app.get("/plans/{plan_id}/categories")
def list_categories(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """List category groups and categories for a plan."""
    groups = db.query(CategoryGroup).filter(CategoryGroup.plan_id == plan_id).all()
    return [
        {
            "id": g.id, "name": g.name,
            "categories": [
                {"id": c.id, "name": c.name}
                for c in g.categories if not c.deleted
            ],
        }
        for g in groups if not g.deleted
    ]


@app.get("/plans/{plan_id}/payees")
def list_payees(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """List payees for a plan."""
    payees = db.query(Payee).filter(
        Payee.plan_id == plan_id, Payee.deleted == False
    ).all()
    return [{"id": p.id, "name": p.name} for p in payees]


# ---------------------------------------------------------------------------
# Phase 8 endpoints — YNAB Write-Back
# ---------------------------------------------------------------------------

class WriteBackRowItem(BaseModel):
    """Reference to a persisted import row plus the only values a caller may edit."""
    row_id: str
    payee_name: str = ""
    category_name: str = ""
    memo: str = ""
    flag_color: Optional[str] = None
    cleared: str = "uncleared"

class WriteBackRequest(BaseModel):
    plan_id: str
    mode: str = "dry_run"  # "dry_run" | "create" | "create_or_skip"
    rows: List[WriteBackRowItem] = []
    batch_id: Optional[str] = None
    transactions: Optional[List[dict]] = None  # legacy standalone payloads: rejected with guidance

@app.post("/write-back")
def write_back(
    req: WriteBackRequest,
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client),
    _user: str = Depends(verify_token),
):
    """Push persisted import rows to YNAB (FR-9). Amount, date, account and identity come from the server."""
    plan = db.query(Plan).filter(Plan.id == req.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if req.mode not in ("dry_run", "create", "create_or_skip"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use: dry_run, create, create_or_skip.")

    if req.transactions:
        raise HTTPException(
            status_code=422,
            detail="Standalone transaction payloads are no longer accepted. Import the CSV first, then send "
                   "{\"rows\": [{\"row_id\": ...}]} referencing the persisted import rows.",
        )

    rows, overrides = [], {}
    for item in req.rows:
        row = db.query(ImportRow).filter(ImportRow.id == item.row_id).first()
        if row is None or row.batch is None or row.batch.plan_id != req.plan_id:
            raise HTTPException(status_code=403, detail=f"Row {item.row_id} does not belong to this budget.")
        if req.batch_id and row.batch_id != req.batch_id:
            raise HTTPException(status_code=403, detail=f"Row {item.row_id} is not part of batch {req.batch_id}.")
        if not row.batch.account_confirmed or row.account_id is None:
            raise HTTPException(
                status_code=409,
                detail="This batch predates account selection. Confirm its target account (reassign) before writing.",
            )
        rows.append(row)
        overrides[row.id] = RowOverride(
            payee_name=item.payee_name, category_name=item.category_name, memo=item.memo,
            flag_color=item.flag_color, cleared=item.cleared,
        )

    service = WriteBackService(db, client)
    result = service.execute(req.plan_id, rows, overrides, mode=req.mode)

    return {
        "mode": result.mode,
        "total": result.total,
        "created": result.created,
        "existing": result.existing,
        "errors": result.errors,
        "unresolved": result.unresolved,
        "results": [
            {
                "index": r.index,
                "row_id": r.row_id,
                "source_id": r.source_id,
                "import_id": r.import_id,
                "date": r.date,
                "payee_name": r.payee_name,
                "amount": r.amount,
                "amount_milliunits": r.amount_milliunits,
                "status": r.status,
                "ynab_transaction_id": r.ynab_transaction_id,
                "error": r.error,
            }
            for r in result.results
        ],
    }


# ---------------------------------------------------------------------------
# Phase 10 endpoints — Transfer Detection, Metrics, Export
# ---------------------------------------------------------------------------

@app.post("/detect-transfer")
def detect_transfer_endpoint(memo: str, source_category: str = "", payee: str = "", amount: float = 0.0, _user: str = Depends(verify_token)):
    """Detect if a transaction is likely a transfer (FR-11)."""
    result = detect_transfer(memo, source_category, payee, amount)
    return {
        "is_transfer": result.is_transfer,
        "confidence": result.confidence,
        "reason": result.reason,
    }


@app.get("/metrics")
def get_metrics(plan_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """FR-14: User-facing operational metrics."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    total_transactions = db.query(Transaction).filter(Transaction.plan_id == plan_id, Transaction.deleted == False).count()
    total_rules = db.query(Rule).filter(Rule.plan_id == plan_id).count()
    total_accounts = db.query(Account).filter(Account.plan_id == plan_id, Account.closed == False).count()
    total_categories = db.query(Category).filter(Category.deleted == False).count()
    total_payees = db.query(Payee).filter(Payee.plan_id == plan_id, Payee.deleted == False).count()

    # Per-category breakdown
    from sqlalchemy import func
    category_counts = (
        db.query(Category.name, func.count(Transaction.id))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(Transaction.plan_id == plan_id, Transaction.deleted == False)
        .group_by(Category.name)
        .order_by(func.count(Transaction.id).desc())
        .limit(15)
        .all()
    )

    return {
        "plan_name": plan.name,
        "total_transactions": total_transactions,
        "total_rules": total_rules,
        "total_accounts": total_accounts,
        "total_categories": total_categories,
        "total_payees": total_payees,
        "training_set_size": total_transactions,
        "top_categories": [{"name": name, "count": count} for name, count in category_counts],
    }


class ExportRequest(BaseModel):
    predictions: List[dict]
    export_type: str = "ynab"  # "ynab" | "review" | "unresolved" | "audit"

@app.post("/export")
def export_csv(req: ExportRequest, _user: str = Depends(verify_token)):
    """FR-10.1: Generate various export artifacts."""
    import json

    if req.export_type == "ynab":
        lines = ["Date,Payee,Category,Memo,Inflow,Outflow"]
        for p in req.predictions:
            if p.get("status") != "accepted":
                continue
            memo = p.get("original_memo", "").replace('"', '""')
            amt = p.get("amount", 0)
            inflow = f'{amt:.2f}' if amt >= 0 else ""
            outflow = f'{abs(amt):.2f}' if amt < 0 else ""
            lines.append(f'{p.get("date", "")},"{p.get("payee", "")}","{p.get("category", "")}","{memo}",{inflow},{outflow}')
        return {"format": "csv", "filename": "ynab-import.csv", "content": "\n".join(lines)}

    elif req.export_type == "review":
        lines = ["Date,Memo,Amount,Payee,Category,Confidence,Source,Status"]
        for p in req.predictions:
            memo = p.get("original_memo", "").replace('"', '""')
            lines.append(
                f'{p.get("date", "")},"{memo}",{p.get("amount", 0)},'
                f'"{p.get("payee", "")}","{p.get("category", "")}",'
                f'{p.get("confidence", 0)},{p.get("source", "")},{p.get("status", "")}'
            )
        return {"format": "csv", "filename": "review-summary.csv", "content": "\n".join(lines)}

    elif req.export_type == "unresolved":
        lines = ["Date,Memo,Amount,Source Category,Confidence,Explanation"]
        for p in req.predictions:
            if p.get("status") == "accepted":
                continue
            memo = p.get("original_memo", "").replace('"', '""')
            lines.append(
                f'{p.get("date", "")},"{memo}",{p.get("amount", 0)},'
                f'"{p.get("source_category", "")}",{p.get("confidence", 0)},'
                f'"{p.get("explanation", "")}"'
            )
        return {"format": "csv", "filename": "unresolved.csv", "content": "\n".join(lines)}

    elif req.export_type == "audit":
        return {"format": "json", "filename": "audit.json", "content": json.dumps(req.predictions, indent=2)}

    raise HTTPException(status_code=400, detail="Invalid export_type")


