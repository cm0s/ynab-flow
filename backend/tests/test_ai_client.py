"""Claude wrapper: budget, no key, refusal, authentication errors, cost accounting, SDK request shape."""
from datetime import datetime

import anthropic
import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.ai.client import (
    AIClient, AIDisabled, AIMisconfigured, AIRefused, AnthropicTransport, BudgetExceeded, TokenUsage,
    TransportResult, cost_usd, month_to_date_cost, text_block, usage_summary,
)
from src.models import AIUsage, Base
from src.settings import load_settings
from tests.ai_fakes import FakeTransport, refusal


class Answer(BaseModel):
    value: str


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


SETTINGS = load_settings({"ANTHROPIC_API_KEY": "sk-test", "AI_MONTHLY_BUDGET_USD": "1"})


def call(client, model="claude-sonnet-5-5"):
    return client.call("categorize", model, [text_block("system", cache=True)], [text_block("row")], Answer)


def test_no_key_means_ai_is_off(db):
    with pytest.raises(AIDisabled, match="ANTHROPIC_API_KEY"):
        AIClient(db, load_settings({}))


def test_cost_accounting_and_usage_rows(db):
    usage = TokenUsage(input_tokens=1000, output_tokens=500, cache_read_tokens=2000, cache_write_tokens=400)
    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx((2000 + 5000 + 400 + 1000) / 1e6)
    transport = FakeTransport(TransportResult(parsed=Answer(value="ok"), stop_reason="end_turn", usage=usage))
    client = AIClient(db, SETTINGS, transport)
    assert call(client).value == "ok"
    client.flush_usage(db)
    db.commit()
    row = db.query(AIUsage).one()
    assert (row.purpose, row.model, row.status, row.input_tokens, row.cache_read_tokens) == \
        ("categorize", "claude-sonnet-5-5", "ok", 1000, 2000)
    assert row.cost_usd == pytest.approx(0.0084)
    assert month_to_date_cost(db) == pytest.approx(0.0084)
    request, output_type = transport.requests[0]
    assert request["thinking"] == {"type": "adaptive"} and request["output_config"] == {"effort": "medium"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"} and output_type is Answer


def test_budget_reached_makes_no_call(db):
    db.add(AIUsage(created_at=datetime.utcnow(), purpose="categorize", model="claude-sonnet-5-5", cost_usd=0.995,
                   status="ok"))
    db.commit()
    transport = FakeTransport(Answer(value="never"))
    client = AIClient(db, SETTINGS, transport)
    with pytest.raises(BudgetExceeded, match=r"\$0\.99 used of \$1\.00"):
        call(client)
    assert transport.requests == []
    assert usage_summary(db, SETTINGS)["remaining_usd"] == pytest.approx(0.005)


def test_last_month_usage_does_not_count(db):
    db.add(AIUsage(created_at=datetime(2020, 1, 5), purpose="extract", model="m", cost_usd=50, status="ok"))
    db.commit()
    assert call(AIClient(db, SETTINGS, FakeTransport(Answer(value="ok")))).value == "ok"


def test_refusal_is_recorded_and_raised(db):
    client = AIClient(db, SETTINGS, FakeTransport(refusal()))
    with pytest.raises(AIRefused):
        call(client)
    client.flush_usage(db)
    assert db.query(AIUsage).one().status == "refused"


def test_authentication_error_is_misconfiguration(db):
    client = AIClient(db, SETTINGS, FakeTransport(AIMisconfigured("Anthropic rejected the API key.")))
    with pytest.raises(AIMisconfigured):
        call(client)
    client.flush_usage(db)
    row = db.query(AIUsage).one()
    assert row.status == "error" and row.cost_usd == 0


def _sdk_error(cls, status, body=None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=request), body=body)


def test_sdk_transport_request_shape_and_error_mapping(monkeypatch):
    transport = AnthropicTransport("sk-test")
    seen = {}

    class Usage:
        input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10, 5, 0, 0
        iterations = [type("It", (), {"type": "fallback_message"})()]

    class Response:
        stop_reason, usage, parsed_output = "end_turn", Usage(), Answer(value="ok")

    def fake_parse(**kwargs):
        seen.update(kwargs)
        return Response()
    monkeypatch.setattr(transport._client.beta.messages, "parse", fake_parse)
    result = transport({"model": "claude-sonnet-5-5", "max_tokens": 10, "messages": []}, Answer)
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]
    assert seen["output_format"] is Answer and result.fallback_used and result.parsed.value == "ok"

    seen.clear()
    transport({"model": "claude-haiku-5-5", "max_tokens": 10, "messages": []}, Answer)
    assert "fallbacks" not in seen                     # no server-side fallback for this model

    def failing(**kwargs):
        raise _sdk_error(anthropic.AuthenticationError, 401)
    monkeypatch.setattr(transport._client.beta.messages, "parse", failing)
    with pytest.raises(AIMisconfigured, match="ANTHROPIC_API_KEY"):
        transport({"model": "claude-sonnet-5-5", "max_tokens": 10, "messages": []}, Answer)

    def bad_request(**kwargs):                         # Anthropic's own reason is kept, not just the class name
        raise _sdk_error(anthropic.BadRequestError, 400, {"type": "error", "error": {
            "type": "invalid_request_error", "message": "Schema is too complex for compilation."}})
    monkeypatch.setattr(transport._client.beta.messages, "parse", bad_request)
    with pytest.raises(AIMisconfigured, match="Schema is too complex"):
        transport({"model": "claude-sonnet-5-5", "max_tokens": 10, "messages": []}, Answer)
