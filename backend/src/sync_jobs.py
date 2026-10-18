"""Run historical sync outside the request and persist its observable status."""
import logging
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from src.database import SessionLocal
from src.models import Plan, SyncJob
from src.sync_service import SyncService
from src.ynab_client import YnabClient, YNABAPIError

logger = logging.getLogger(__name__)


def expire_job(db, job):
    if job.status == 'running' and job.updated_at < datetime.utcnow() - timedelta(hours=1):
        job.status = 'error'
        job.message = 'Sync stopped reporting progress. The server may have restarted. Please retry.'
        db.commit()
    return job


def start_job(db, plan_id):
    """Claim one job per plan; concurrent callers share the active job."""
    job = db.query(SyncJob).filter_by(plan_id=plan_id).first()
    if job:
        expire_job(db, job)
        if job.status == 'running':
            return job, False
        job_id = str(uuid4())
        claimed = db.query(SyncJob).filter(
            SyncJob.plan_id == plan_id, SyncJob.status != 'running',
        ).update(dict(id=job_id, status='running', message='Starting sync…', updated_at=datetime.utcnow()), synchronize_session=False)
        db.commit()
        db.expire_all()
        return db.query(SyncJob).filter_by(plan_id=plan_id).one(), bool(claimed)
    job = SyncJob(id=str(uuid4()), plan_id=plan_id, status='running', message='Starting sync…')
    db.add(job)
    try:
        db.commit()
        return job, True
    except IntegrityError:
        db.rollback()
        return db.query(SyncJob).filter_by(plan_id=plan_id).one(), False


def run_job(job_id, full, api_key):
    # The request's session/client must not outlive its response.
    with SessionLocal() as db:
        job = db.get(SyncJob, job_id)
        if not job or job.status != 'running':
            return
        client = YnabClient(api_key)
        try:
            plan = db.get(Plan, job.plan_id)
            if full:
                plan.last_server_knowledge = None
                plan.last_knowledge_payees = None
                plan.last_knowledge_transactions = None
                db.commit()
            service = SyncService(db, client)
            for message, sync in [('Syncing categories…', service.sync_categories),
                                  ('Syncing payees…', service.sync_payees),
                                  ('Syncing transactions…', service.sync_transactions)]:
                job.message = message
                job.updated_at = datetime.utcnow()
                db.commit()
                sync(plan)
            job.status = 'success'
            job.message = 'Historical sync completed.'
            job.updated_at = datetime.utcnow()
            db.commit()
        except Exception as error:
            db.rollback()
            logger.exception('Historical sync failed: %s', job_id)
            job = db.get(SyncJob, job_id)
            if job:
                job.status = 'error'
                job.message = str(error) if isinstance(error, YNABAPIError) else 'Historical sync failed on the server. Please retry or check the backend logs.'
                job.updated_at = datetime.utcnow()
                db.commit()
        finally:
            client.client.close()
