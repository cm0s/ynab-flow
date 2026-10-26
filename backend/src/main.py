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
from src.categories import CategoryDirectory
from src import model_registry, settings
from src.splits import SplitError, SplitLine, clear_splits, set_splits, split_views
from src.receipts.matching import decide as decide_receipt, match_active_batches, match_batches, receipt_views
from src.precedence import TARGETS, alternatives as row_alternatives, apply_precedence, user_edited

validate_auth_config()

app = FastAPI(title="YNAB Flow API")
training_lock = Lock()
receipt_fetch_lock = Lock()
app.state.ai_transport = None   # tests and the local fake stack inject a fake Claude transport here

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
def ensure_models():
    """Register legacy model files, rebuild a missing active version, or train a first model."""
    from src.database import SessionLocal
    db = SessionLocal()
    try:
        plan = db.query(Plan).first()
        model_registry.ensure_active_model(db, plan.id if plan else None)
    except Exception as e:
        print(f"Model startup check skipped: {e}")
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


@app.get("/features")
def get_features(_user: str = Depends(verify_token)):
    """Which optional features (receipts, AI) are on, and why not. Never includes secret values."""
    return settings.features()


def _ai_client(db: Session, s: "settings.Settings"):
    """An AI client, or None when AI is off (no key and no injected transport)."""
    from src.ai.client import AIClient, AIDisabled
    try:
        return AIClient(db, s, app.state.ai_transport)
    except AIDisabled:
        return None


# ---------------------------------------------------------------------------
# Receipts from Proton Mail Bridge (read-only)
# ---------------------------------------------------------------------------

def _receipt_counts(db: Session) -> dict:
    from sqlalchemy import func
    from src.models import Receipt
    return {status: count for status, count in db.query(Receipt.status, func.count(Receipt.id)).group_by(Receipt.status)}


@app.get("/receipts/status")
def receipts_status(db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Mailbox configuration, per-folder fetch state and errors in plain terms, and receipt counts."""
    from src.mail.fetch import mailbox_status
    s = settings.load_settings()
    return {"feature": settings.receipts_feature(s), "mailboxes": mailbox_status(db, s),
            "receipts": _receipt_counts(db), "running": receipt_fetch_lock.locked()}


@app.post("/receipts/fetch")
def receipts_fetch(db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Read new messages from every configured mailbox (read-only) and extract receipts."""
    from src.mail.fetch import fetch_receipts, mailbox_status
    s = settings.load_settings()
    feature = settings.receipts_feature(s)
    if not feature["enabled"]:
        raise HTTPException(status_code=409, detail=feature["reason"])
    if not receipt_fetch_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A receipt fetch is already running.")
    try:
        report = fetch_receipts(db, s, ai=_ai_client(db, s))
        matches = match_active_batches(db)
        db.commit()
    finally:
        receipt_fetch_lock.release()
    return {**report.as_dict(), "matches": matches, "mailboxes": mailbox_status(db, s),
            "receipts": _receipt_counts(db)}

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
        result = model_registry.train_candidate(db, plan_id)
        if result["version"] is None:
            raise HTTPException(status_code=422, detail="Not enough labeled history to train. Sync transactions with at least two different payees or categories.")
        return {"status": "promoted" if result["promoted"] else "not_promoted", **result}
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Training could not use the available transaction memos. Check that your synced history contains meaningful text.") from error
    finally:
        training_lock.release()

@app.get("/models")
def get_models(db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Model versions, newest first, with training counts and held-out comparisons."""
    return {"versions": model_registry.list_versions(db)}


@app.post("/models/{version_id}/activate")
def activate_model(version_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Restore a retired model version (rollback)."""
    if not training_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Model training is running. Try again when it finishes.")
    try:
        return model_registry.restore(db, version_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        training_lock.release()


# ---------------------------------------------------------------------------
# Phase 5 endpoints — import batches
# ---------------------------------------------------------------------------

REVIEW_STATUSES = ("pending", "accepted", "ignored")
PREDICTION_FIELDS = (
    "payee", "category", "confidence", "source", "explanation", "review_required", "flag_ignore",
    "cleaned_memo", "merchant_stem", "payee_confidence", "category_confidence", "payee_source",
    "category_source", "payee_explanation", "category_explanation", "review_flag", "category_id",
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
            "category_id": r.category_id,
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
            "edited_category_id": r.edited_category_id,
            "edited_memo": r.edited_memo,
            "flag_color": r.flag_color,
            "splits": split_views(r),
            "receipts": receipt_views(r),
            "ai": _ai_view(r),
            "alternatives": row_alternatives(r),
        }
        for r in sorted(rows, key=lambda r: r.row_index)
        if r.write_state not in COMPLETED
    ]


def _ai_view(r: ImportRow) -> dict:
    return {
        "status": r.ai_status, "payee": r.ai_payee, "category_id": r.ai_category_id,
        "splits": json.loads(r.ai_splits_json) if r.ai_splits_json else [],
        "explanation": r.ai_explanation, "uncertainty": r.ai_uncertainty,
        "evidence": json.loads(r.ai_evidence_json).get("items", []) if r.ai_evidence_json else [],
        "model": r.ai_model, "requested_at": r.ai_requested_at.isoformat() if r.ai_requested_at else None,
    }


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
            import_row.edited_category_id = d["category_id"]
        if import_row.status != "ignored":
            auto = not d["review_required"] and not d["flag_ignore"] and import_row.transfer_state == "none"
            if not user_edited_payee and not user_edited_category:
                import_row.status = "accepted" if auto else "pending"


def _merge_ai(db: Session, plan_id: str, rows: List[ImportRow]) -> None:
    """Re-apply evidence precedence so stored AI suggestions and fresh predictions merge the same way."""
    directory = CategoryDirectory(db, plan_id)
    for row in rows:
        if row.ai_status == "suggested" or row.alternatives_json:
            apply_precedence(row, directory)


def _directory_for_row(db: Session, row: ImportRow) -> CategoryDirectory:
    return CategoryDirectory(db, row.batch.plan_id)


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
            edited_payee=d["payee"] or "", edited_category=d["category"] or "", edited_category_id=d["category_id"],
            account_id=account.id, amount_milliunits=txn.amount_milliunits, currency=txn.currency,
            source_format=txn.source_format, source_status=txn.source_status,
            evidence_json=json.dumps(txn.evidence, ensure_ascii=False), source_record_id=alloc.record.id,
            transfer_state=d["transfer_state"],
            reconciliation_state=alloc.state, reconciliation_note=alloc.note,
        ))
    db.commit()
    db.refresh(batch)
    match_batches(db, [batch])          # link receipts fetched earlier; bank values are never touched
    db.commit()

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
    _merge_ai(db, req.plan_id, import_rows)
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
    edited_category_id: Optional[str] = None   # local category id; preferred over the name
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
        if u.edited_category_id is not None or u.edited_category is not None:
            directory = _directory_for_row(db, row)
            category_id = u.edited_category_id or directory.resolve_name(u.edited_category)
            if u.edited_category_id and u.edited_category_id not in directory:
                raise HTTPException(status_code=422, detail="Unknown category for this plan.")
            row.edited_category_id = category_id or None
            row.edited_category = directory.name(category_id) if category_id else (u.edited_category or "")
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


class SplitLineIn(BaseModel):
    amount_milliunits: int
    category_id: str
    payee: str = ""
    memo: str = ""
    origin: str = "user"

class SplitsRequest(BaseModel):
    lines: List[SplitLineIn]

@app.put("/import-rows/{row_id}/splits")
def put_splits(row_id: str, req: SplitsRequest, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Replace a row's split lines; they must sum exactly to the bank amount."""
    row = _owned_row(db, row_id)
    if row.write_state in COMPLETED:
        raise HTTPException(status_code=409, detail="This row is already in YNAB.")
    try:
        set_splits(db, row, [SplitLine(**line.model_dump()) for line in req.lines], _directory_for_row(db, row))
    except SplitError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    return _row_view(row)


@app.delete("/import-rows/{row_id}/splits")
def delete_splits(row_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Clear the split so the row returns to a single category."""
    row = _owned_row(db, row_id)
    if row.write_state in COMPLETED:
        raise HTTPException(status_code=409, detail="This row is already in YNAB.")
    clear_splits(row)
    db.commit()
    return _row_view(row)


def _ai_locked(row: ImportRow, target: str) -> bool:
    """Targets the AI may not replace: user edits, rules and confident history."""
    source = getattr(row, f"{target}_source")
    if user_edited(row, target) or source == "rule":
        return True
    return source in ("exact_history", "fuzzy_history") and (getattr(row, f"{target}_confidence") or 0) >= 0.95


def _ai_eligible(row: ImportRow) -> bool:
    return (row.write_state not in COMPLETED and row.status != "ignored" and not row.flag_ignore
            and row.transfer_state == "none" and row.amount_milliunits is not None
            and not all(_ai_locked(row, t) for t in TARGETS))


def _run_ai(db: Session, plan_id: str, rows: List[ImportRow]) -> dict:
    """Ask the AI about each row (bounded concurrency); results are stored and merged on this thread."""
    from concurrent.futures import ThreadPoolExecutor
    from src.ai.categorize import CategorizationContext, prepare, request_suggestion, store_suggestion
    from src.ai.client import AIError
    s = settings.load_settings()
    ai = _ai_client(db, s)
    if ai is None:
        raise HTTPException(status_code=409, detail=settings.ai_feature(s)["reason"])
    ctx = CategorizationContext(db, plan_id)
    prepared = [(row, prepare(ctx, row)) for row in rows]

    def work(request):
        try:
            return request_suggestion(ai, ctx, request), None
        except AIError as error:
            return None, error

    with ThreadPoolExecutor(max_workers=max(1, s.ai_max_concurrency)) as pool:
        outcomes = list(pool.map(work, [p for _, p in prepared]))
    counts = {"requested": len(rows), "suggested": 0, "invalid": 0, "refused": 0, "error": 0, "skipped_budget": 0}
    for (row, _), (suggestion, error) in zip(prepared, outcomes):
        store_suggestion(row, suggestion, error, s.ai_model)
        counts[row.ai_status] = counts.get(row.ai_status, 0) + 1
    _merge_ai(db, plan_id, [row for row, _ in prepared])
    ai.flush_usage(db)
    db.commit()
    return counts


class AISuggestRequest(BaseModel):
    row_ids: Optional[List[str]] = None   # default: every eligible row of the batch


@app.post("/import-batches/{batch_id}/ai-suggest")
def ai_suggest_batch(batch_id: str, req: Optional[AISuggestRequest] = None, db: Session = Depends(get_db),
                     _user: str = Depends(verify_token)):
    """AI suggestions for rows that rules, user edits and confident history do not settle. Rows stay pending."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    wanted = set(req.row_ids) if req and req.row_ids else None
    rows = [r for r in sorted(batch.rows, key=lambda r: r.row_index)
            if _ai_eligible(r) and (wanted is None or r.id in wanted)]
    counts = _run_ai(db, batch.plan_id, rows) if rows else {"requested": 0}
    from src.ai.client import usage_summary
    return {**counts, "usage": usage_summary(db, settings.load_settings()), **_batch_payload(db, batch)}


@app.post("/import-rows/{row_id}/ai-suggest")
def ai_suggest_row(row_id: str, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """AI suggestion for one row."""
    row = _owned_row(db, row_id)
    if not _ai_eligible(row):
        raise HTTPException(status_code=409, detail="Rules, your edits or confident history already settle this row "
                                                    "(or it is a transfer, ignored or already in YNAB).")
    _run_ai(db, row.batch.plan_id, [row])
    db.refresh(row)
    return _row_view(row)


@app.get("/ai/usage")
def ai_usage(db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Month-to-date AI cost against the budget, and the average cost per request for estimates."""
    from src.ai.client import usage_summary
    s = settings.load_settings()
    return {**usage_summary(db, s), "feature": settings.ai_feature(s)}


class ReceiptDecision(BaseModel):
    receipt_id: str
    action: str  # link | unlink | reject

@app.post("/import-rows/{row_id}/receipts")
def decide_row_receipt(row_id: str, req: ReceiptDecision, db: Session = Depends(get_db), _user: str = Depends(verify_token)):
    """Link, unlink or reject a receipt for a row. Receipts add evidence only; bank values never change."""
    from src.models import Receipt
    row = _owned_row(db, row_id)
    if row.write_state in COMPLETED:
        raise HTTPException(status_code=409, detail="This row is already in YNAB.")
    receipt = db.get(Receipt, req.receipt_id)
    if receipt is None or receipt.status == "not_receipt":
        raise HTTPException(status_code=404, detail="Receipt not found.")
    try:
        decide_receipt(db, row, receipt, req.action)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    db.refresh(row)
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
    _merge_ai(db, batch.plan_id, movable)
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
    category_id: Optional[str] = None   # local category id; preferred over category_name
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
            payee_name=item.payee_name, category_name=item.category_name, category_id=item.category_id, memo=item.memo,
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


