import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.models import Plan

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_model_instantiation():
    plan = Plan(name="Test Plan", ynab_plan_id="123")
    assert plan.name == "Test Plan"
