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
from src.csv_service import parse_csv, bulk_predict
from src.write_back_service import WriteBackService, WriteTransaction
from src.models import Plan, Account, CategoryGroup, Category, Payee, Rule

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
    db: Session = Depends(get_db),
    client: YnabClient = Depends(get_ynab_client)
):
    """Fetch delta updates for Categories, Payees, and Transactions for a specific plan."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found in local database.")
        
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

@app.post("/upload-csv")
async def upload_csv(
    plan_id: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a bank CSV and get bulk predictions."""
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    content = (await file.read()).decode("utf-8-sig")
    transactions = parse_csv(content)
    results = bulk_predict(db, plan_id, transactions)
    return {"count": len(results), "predictions": [asdict(r) for r in results]}


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
    """List all synced plans."""
    plans = db.query(Plan).all()
    return [
        {"id": p.id, "name": p.name, "ynab_plan_id": p.ynab_plan_id}
        for p in plans
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

class WriteBackRequest(BaseModel):
    plan_id: str
    mode: str = "dry_run"  # "dry_run" | "create" | "create_or_skip"
    transactions: List[WriteBackTransactionItem]

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
        )
        for t in req.transactions
    ]

    result = service.execute(req.plan_id, txns, mode=req.mode)

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

