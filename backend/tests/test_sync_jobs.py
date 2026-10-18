from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import sync_jobs
from src.auth import verify_token
from src.database import get_db
from src.main import app, get_ynab_client
from src.models import Base, Plan, SyncJob


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(sync_jobs, 'SessionLocal', sessions)
    with sessions() as db:
        plan = Plan(name='Budget', ynab_plan_id='budget')
        db.add(plan)
        db.commit()
        yield db, plan.id
    engine.dispose()


def test_duplicate_start_reuses_active_job_and_retry_gets_new_id(setup):
    db, plan_id = setup
    job, created = sync_jobs.start_job(db, plan_id)
    first_id = job.id
    assert created
    second, created = sync_jobs.start_job(db, plan_id)
    assert not created
    assert second.id == first_id
    job.status = 'success'
    db.commit()
    retry, created = sync_jobs.start_job(db, plan_id)
    assert created
    assert retry.id != first_id
    assert db.query(SyncJob).count() == 1


@pytest.mark.parametrize('fail', [False, True])
def test_worker_records_stages_and_terminal_status(setup, monkeypatch, fail):
    db, plan_id = setup
    job, _ = sync_jobs.start_job(db, plan_id)
    job_id = job.id
    observed = []

    def stage(plan):
        with sync_jobs.SessionLocal() as observer:
            observed.append(observer.get(SyncJob, job_id).message)
        if fail:
            raise RuntimeError('private database connection details')

    service = Mock()
    service.sync_categories.side_effect = stage
    service.sync_payees.side_effect = stage
    service.sync_transactions.side_effect = stage
    monkeypatch.setattr(sync_jobs, 'SyncService', Mock(return_value=service))
    ynab = Mock()
    monkeypatch.setattr(sync_jobs, 'YnabClient', Mock(return_value=ynab))
    sync_jobs.run_job(job_id, True, 'test-key')
    db.expire_all()
    job = db.get(SyncJob, job_id)
    assert job.status == ('error' if fail else 'success')
    assert 'private' not in job.message
    assert observed[0] == 'Syncing categories…'
    if not fail:
        assert observed == ['Syncing categories…', 'Syncing payees…', 'Syncing transactions…']
    ynab.client.close.assert_called_once()


def test_stale_job_reports_interruption(setup):
    db, plan_id = setup
    job, _ = sync_jobs.start_job(db, plan_id)
    job.updated_at = datetime.utcnow() - timedelta(hours=2)
    db.commit()
    assert sync_jobs.expire_job(db, job).status == 'error'


def test_api_responds_with_job_before_sync_runs(setup, monkeypatch):
    db, plan_id = setup
    queued = []
    from fastapi import BackgroundTasks
    monkeypatch.setattr(BackgroundTasks, 'add_task', lambda self, fn, *args: queued.append((fn, args)))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_token] = lambda: 'admin'
    app.dependency_overrides[get_ynab_client] = lambda: Mock(api_key='test-key')
    try:
        client = TestClient(app)
        response = client.post(f'/sync/plan/{plan_id}/jobs?full=true')
        assert response.status_code == 202
        job = response.json()
        assert job['status'] == 'running'
        assert len(queued) == 1
        assert queued[0][1] == (job['id'], True, 'test-key')
        assert client.get(f"/sync/jobs/{job['id']}").json() == job
        assert client.post(f'/sync/plan/{plan_id}/jobs').json()['id'] == job['id']
        assert len(queued) == 1
        assert client.get('/sync/jobs/missing').status_code == 404
        assert client.post('/sync/plan/missing/jobs').status_code == 404
    finally:
        app.dependency_overrides.clear()
