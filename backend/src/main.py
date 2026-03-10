import os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException

load_dotenv()
from sqlalchemy.orm import Session
from src.database import get_db
from src.ynab_client import YnabClient, YNABAPIError
from src.sync_service import SyncService
from src.models import Plan

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
