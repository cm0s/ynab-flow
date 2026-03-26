import os
from dataclasses import asdict
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
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
from src.csv_service import CSVTransaction, parse_csv, bulk_predict
from src.write_back_service import WriteBackService, WriteTransaction
from src.transfer_detector import detect_transfer
from src.models import Plan, Account, CategoryGroup, Category, Payee, Rule, Transaction, ImportBatch, ImportRow

app = FastAPI(title="YNAB Flow API")

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
    client: YnabClient = Depends(get_ynab_client)
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
    client: YnabClient = Depends(get_ynab_client)
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
def normalize_memo(req: NormalizeRequest):
    """Normalize a raw bank memo (FR-3)."""
    result = normalize(req.memo)
    return {
        "original_memo": result.original_memo,
        "cleaned_memo": result.cleaned_memo,
        "merchant_stem": result.merchant_stem,
    }

@app.post("/classify")
def classify_transaction(req: ClassifyRequest, db: Session = Depends(get_db)):
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
def list_rules(plan_id: str, db: Session = Depends(get_db)):
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
def create_rule(req: RuleCreate, db: Session = Depends(get_db)):
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
def predict_transaction(req: PredictRequest, db: Session = Depends(get_db)):
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
    }

@app.post("/train")
def train_models(plan_id: str, db: Session = Depends(get_db)):
    """Train ML models from historical YNAB transactions (FR-6.4)."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    classifier = MLClassifier()
    stats = classifier.train(db, plan_id)
    return {"status": "success", **stats}

# ---------------------------------------------------------------------------
# Phase 5 endpoints
# ---------------------------------------------------------------------------

def _batch_rows_to_dicts(rows: list) -> list:
    """Convert ImportRow list to API response dicts."""
    return [
        {
            "id": r.id,
            "row_index": r.row_index,
            "date": r.date,
            "original_memo": r.original_memo,
            "cleaned_memo": r.cleaned_memo,
            "merchant_stem": r.merchant_stem,
            "amount": r.amount,
            "label": r.label,
            "source_category": r.source_category,
            "payee": r.payee,
            "category": r.category,
            "confidence": r.confidence,
            "source": r.source,
            "explanation": r.explanation,
            "review_required": r.review_required,
            "flag_ignore": r.flag_ignore,
            "status": r.status,
            "edited_payee": r.edited_payee,
            "edited_category": r.edited_category,
            "edited_memo": r.edited_memo,
            "flag_color": r.flag_color,
        }
        for r in sorted(rows, key=lambda r: r.row_index)
    ]


@app.post("/upload-csv")
async def upload_csv(
    plan_id: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a bank CSV and get bulk predictions, persisted as an import batch."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    content = (await file.read()).decode("utf-8-sig")
    transactions = parse_csv(content)
    results = bulk_predict(db, plan_id, transactions)

    # Abandon any existing active batch for this plan
    db.query(ImportBatch).filter(
        ImportBatch.plan_id == plan_id, ImportBatch.status == "active"
    ).update({"status": "abandoned"})

    # Create new batch and rows
    batch = ImportBatch(plan_id=plan_id, filename=file.filename)
    db.add(batch)
    db.flush()

    for r in results:
        d = asdict(r)
        auto_approved = not d["review_required"] and not d["flag_ignore"]
        row = ImportRow(
            batch_id=batch.id,
            row_index=d["row_index"],
            date=d["date"],
            original_memo=d["original_memo"],
            cleaned_memo=d["cleaned_memo"],
            merchant_stem=d["merchant_stem"],
            amount=d["amount"],
            label=d["label"],
            source_category=d["source_category"],
            payee=d["payee"],
            category=d["category"],
            confidence=d["confidence"],
            source=d["source"],
            explanation=d["explanation"],
            review_required=d["review_required"],
            flag_ignore=d["flag_ignore"],
            status="accepted" if auto_approved else "pending",
            edited_payee=d["payee"] or "",
            edited_category=d["category"] or "",
        )
        db.add(row)
    db.commit()

    rows_out = _batch_rows_to_dicts(batch.rows)
    return {"batch_id": batch.id, "count": len(rows_out), "predictions": rows_out}


class ReclassifyRequest(BaseModel):
    plan_id: str
    batch_id: Optional[str] = None

@app.post("/reclassify")
def reclassify(req: ReclassifyRequest, db: Session = Depends(get_db)):
    """Re-run classification on a persisted import batch."""
    plan = db.query(Plan).filter(Plan.id == req.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    # Find the batch to reclassify
    if req.batch_id:
        batch = db.query(ImportBatch).filter(ImportBatch.id == req.batch_id).first()
    else:
        batch = db.query(ImportBatch).filter(
            ImportBatch.plan_id == req.plan_id, ImportBatch.status == "active"
        ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="No active import batch found.")

    import_rows = sorted(batch.rows, key=lambda r: r.row_index)
    csv_txns = [
        CSVTransaction(
            date=r.date, memo=r.original_memo,
            inflow=r.amount if r.amount >= 0 else None,
            outflow=-r.amount if r.amount < 0 else None,
            label=r.label, source_category=r.source_category,
        )
        for r in import_rows
    ]
    results = bulk_predict(db, req.plan_id, csv_txns)

    # Update import rows with new predictions, preserving user overrides
    for import_row, pred in zip(import_rows, results):
        d = asdict(pred)
        user_edited_payee = import_row.edited_payee != (import_row.payee or "")
        user_edited_category = import_row.edited_category != (import_row.category or "")

        import_row.payee = d["payee"]
        import_row.category = d["category"]
        import_row.confidence = d["confidence"]
        import_row.source = d["source"]
        import_row.explanation = d["explanation"]
        import_row.review_required = d["review_required"]
        import_row.flag_ignore = d["flag_ignore"]
        import_row.cleaned_memo = d["cleaned_memo"]
        import_row.merchant_stem = d["merchant_stem"]

        # Don't overwrite user edits
        if not user_edited_payee:
            import_row.edited_payee = d["payee"] or ""
        if not user_edited_category:
            import_row.edited_category = d["category"] or ""
        # Don't change status if user manually set it to ignored
        if import_row.status != "ignored":
            auto_approved = not d["review_required"] and not d["flag_ignore"]
            if not user_edited_payee and not user_edited_category:
                import_row.status = "accepted" if auto_approved else "pending"

    db.commit()
    rows_out = _batch_rows_to_dicts(batch.rows)
    return {"batch_id": batch.id, "count": len(rows_out), "predictions": rows_out}


# ---------------------------------------------------------------------------
# Import batch management endpoints
# ---------------------------------------------------------------------------

@app.get("/import-batches/{plan_id}/active")
def get_active_batch(plan_id: str, db: Session = Depends(get_db)):
    """Return the active import batch for a plan, or 404."""
    batch = db.query(ImportBatch).filter(
        ImportBatch.plan_id == plan_id, ImportBatch.status == "active"
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="No active import batch.")
    return {
        "batch_id": batch.id,
        "filename": batch.filename,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "rows": _batch_rows_to_dicts(batch.rows),
    }


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
def update_import_rows(req: ImportRowsUpdateRequest, db: Session = Depends(get_db)):
    """Bulk-save review state changes on import rows."""
    updated = 0
    for u in req.updates:
        row = db.query(ImportRow).filter(ImportRow.id == u.id).first()
        if not row:
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


@app.post("/import-batches/{batch_id}/complete")
def complete_batch(batch_id: str, db: Session = Depends(get_db)):
    """Mark an import batch as completed."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    batch.status = "completed"
    db.commit()
    return {"status": "completed"}


@app.put("/rules/{rule_id}")
def update_rule(rule_id: str, req: RuleCreate, db: Session = Depends(get_db)):
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
def delete_rule(rule_id: str, db: Session = Depends(get_db)):
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    db.delete(rule)
    db.commit()
    return {"status": "success"}


@app.get("/plans")
def list_plans(db: Session = Depends(get_db)):
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
def list_accounts(plan_id: str, db: Session = Depends(get_db)):
    """List accounts for a plan."""
    accounts = db.query(Account).filter(Account.plan_id == plan_id).all()
    return [
        {"id": a.id, "name": a.name, "type": a.type, "closed": a.closed}
        for a in accounts
    ]


@app.get("/plans/{plan_id}/categories")
def list_categories(plan_id: str, db: Session = Depends(get_db)):
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
def list_payees(plan_id: str, db: Session = Depends(get_db)):
    """List payees for a plan."""
    payees = db.query(Payee).filter(
        Payee.plan_id == plan_id, Payee.deleted == False
    ).all()
    return [{"id": p.id, "name": p.name} for p in payees]


# ---------------------------------------------------------------------------
# Phase 8 endpoints — YNAB Write-Back
# ---------------------------------------------------------------------------

class WriteBackTransactionItem(BaseModel):
    date: str
    amount: float
    payee_name: str
    category_name: str = ""
    memo: str = ""
    account_id: str
    cleared: str = "uncleared"
    flag_color: Optional[str] = None

class WriteBackRequest(BaseModel):
    plan_id: str
    mode: str = "dry_run"  # "dry_run" | "create" | "create_or_skip"
    transactions: List[WriteBackTransactionItem]
    batch_id: Optional[str] = None

@app.post("/write-back")
def write_back(req: WriteBackRequest, db: Session = Depends(get_db)):
    """Push approved transactions to YNAB (FR-9)."""
    plan = db.query(Plan).filter(Plan.id == req.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if req.mode not in ("dry_run", "create", "create_or_skip"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use: dry_run, create, create_or_skip.")

    client = get_ynab_client()
    service = WriteBackService(db, client)

    txns = [
        WriteTransaction(
            date=t.date,
            amount=t.amount,
            payee_name=t.payee_name,
            category_name=t.category_name,
            memo=t.memo,
            account_id=t.account_id,
            cleared=t.cleared,
            flag_color=t.flag_color,
        )
        for t in req.transactions
    ]

    result = service.execute(req.plan_id, txns, mode=req.mode, batch_id=req.batch_id)

    return {
        "mode": result.mode,
        "total": result.total,
        "created": result.created,
        "skipped": result.skipped,
        "errors": result.errors,
        "results": [
            {
                "index": r.index,
                "date": r.date,
                "payee_name": r.payee_name,
                "amount": r.amount,
                "status": r.status,
                "ynab_transaction_id": r.ynab_transaction_id,
                "error": r.error,
                "import_id": r.import_id,
            }
            for r in result.results
        ],
    }


# ---------------------------------------------------------------------------
# Phase 10 endpoints — Transfer Detection, Metrics, Export
# ---------------------------------------------------------------------------

@app.post("/detect-transfer")
def detect_transfer_endpoint(memo: str, source_category: str = "", payee: str = "", amount: float = 0.0):
    """Detect if a transaction is likely a transfer (FR-11)."""
    result = detect_transfer(memo, source_category, payee, amount)
    return {
        "is_transfer": result.is_transfer,
        "confidence": result.confidence,
        "reason": result.reason,
    }


@app.get("/metrics")
def get_metrics(plan_id: str, db: Session = Depends(get_db)):
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
def export_csv(req: ExportRequest):
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


