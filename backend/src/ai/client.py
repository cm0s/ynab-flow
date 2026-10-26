"""Claude client wrapper: structured output, refusal handling, cost accounting and a monthly budget.

The ``anthropic`` SDK is imported lazily, so the app runs without it or without a
key. Tests inject a fake ``transport``; no test ever calls the network.

Budget: the month-to-date cost is read once per client, and each call reserves
an estimated cost under a lock before it starts, so concurrent requests cannot
overshoot the budget by more than one estimate. Usage rows are collected in
memory (worker threads never touch the database session) and written by
``flush_usage`` on the caller's thread.
"""

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any, Callable, List, Optional, Type

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import AIUsage
from src.settings import Settings

logger = logging.getLogger(__name__)

# USD per million tokens: input, output, cache read, cache write (5-minute TTL)
PRICES_PER_MTOK = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
UNKNOWN_MODEL_PRICE = (4.0, 20.0, 0.20, 5.00)   # conservative for budgeting
NO_SERVER_FALLBACK = {"claude-haiku-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 4000
DEFAULT_REQUEST_ESTIMATE_USD = 0.012


class AIError(Exception):
    """An AI request that produced no usable suggestion. ``status`` is stored on the row."""
    status = "error"


class AIDisabled(AIError):
    status = "error"


class BudgetExceeded(AIError):
    status = "skipped_budget"


class AIRefused(AIError):
    status = "refused"


class AIMisconfigured(AIError):
    """Authentication, permission or request errors: retrying will not help."""
    status = "error"


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class TransportResult:
    parsed: Optional[BaseModel]
    stop_reason: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    fallback_used: bool = False
    fallbacks_enabled: bool = False


Transport = Callable[[dict, Type[BaseModel]], TransportResult]


def cost_usd(model: str, usage: TokenUsage) -> float:
    price_in, price_out, price_read, price_write = PRICES_PER_MTOK.get(model, UNKNOWN_MODEL_PRICE)
    return round((usage.input_tokens * price_in + usage.output_tokens * price_out
                  + usage.cache_read_tokens * price_read + usage.cache_write_tokens * price_write) / 1_000_000, 6)


def month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    return datetime(now.year, now.month, 1)


def month_to_date_cost(db: Session, now: Optional[datetime] = None) -> float:
    total = db.query(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).filter(AIUsage.created_at >= month_start(now)).scalar()
    return float(total or 0.0)


def average_request_cost(db: Session, purpose: str = "categorize", sample: int = 50) -> float:
    """Recent average cost per request, for the estimate shown before a run."""
    costs = [c for (c,) in db.query(AIUsage.cost_usd).filter(AIUsage.purpose == purpose, AIUsage.status == "ok")
             .order_by(AIUsage.created_at.desc()).limit(sample).all()]
    return sum(costs) / len(costs) if costs else DEFAULT_REQUEST_ESTIMATE_USD


def _api_message(exc: Exception, limit: int = 300) -> str:
    """Anthropic's own explanation of a rejected request (it never contains the API key)."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return (message or type(exc).__name__)[:limit]


class AnthropicTransport:
    """Calls the Claude API through the official SDK and maps its errors to AIError types."""

    def __init__(self, api_key: str):
        import anthropic  # lazy: the app runs without the SDK
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=2)
        self._supports_fallbacks = "fallbacks" in inspect.signature(self._client.beta.messages.parse).parameters

    def __call__(self, request: dict, output_type: Type[BaseModel]) -> TransportResult:
        a = self._anthropic
        fallbacks = self._supports_fallbacks and request["model"] not in NO_SERVER_FALLBACK
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if fallbacks else {}
        try:
            response = self._client.beta.messages.parse(**request, output_format=output_type, **extra)
        except (a.AuthenticationError, a.PermissionDeniedError) as exc:
            raise AIMisconfigured("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in backend/.env.") from exc
        except (a.BadRequestError, a.NotFoundError, a.UnprocessableEntityError) as exc:
            raise AIMisconfigured(f"The AI request was rejected: {_api_message(exc)}") from exc
        except a.RateLimitError as exc:
            raise AIError("The AI service is rate limiting requests. Try again in a minute.") from exc
        except a.APIStatusError as exc:
            raise AIError(f"The AI service returned an error ({exc.status_code}). Try again later.") from exc
        except a.APIConnectionError as exc:
            raise AIError("Could not reach the AI service. Check the internet connection.") from exc
        u = response.usage
        usage = TokenUsage(
            input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
            cache_read_tokens=u.cache_read_input_tokens or 0, cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        used_fallback = any(getattr(it, "type", "") == "fallback_message" for it in (u.iterations or []))
        # Check the refusal before reading content
        parsed = None if response.stop_reason == "refusal" else response.parsed_output
        return TransportResult(parsed=parsed, stop_reason=response.stop_reason or "", usage=usage,
                               fallback_used=used_fallback, fallbacks_enabled=fallbacks)


class AIClient:
    def __init__(self, db: Session, settings: Settings, transport: Optional[Transport] = None):
        self.settings = settings
        if transport is None:
            if not settings.anthropic_api_key:
                raise AIDisabled("AI is off: ANTHROPIC_API_KEY is not set in backend/.env.")
            try:
                transport = AnthropicTransport(settings.anthropic_api_key)
            except ImportError as exc:
                raise AIDisabled("The anthropic package is not installed.") from exc
        self.transport = transport
        self._lock = Lock()
        self._spent = month_to_date_cost(db)
        self._estimate = average_request_cost(db)
        self._pending: List[AIUsage] = []

    @property
    def budget(self) -> float:
        return self.settings.ai_monthly_budget_usd

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self.budget - self._spent)

    def call(
        self,
        purpose: str,
        model: str,
        system: List[dict],
        content: List[dict],
        output_type: Type[BaseModel],
        effort: str = "medium",
    ) -> BaseModel:
        """One structured request. Raises an AIError subclass when nothing usable came back."""
        with self._lock:
            if self._spent + self._estimate > self.budget:
                raise BudgetExceeded(
                    f"Monthly AI budget reached: ${self._spent:.2f} used of ${self.budget:.2f}. "
                    "Raise AI_MONTHLY_BUDGET_USD or wait until next month.")
            self._spent += self._estimate          # reservation, replaced by the actual cost below
        request = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        try:
            result = self.transport(request, output_type)
        except AIError as exc:
            self._record(purpose, model, TokenUsage(), "error", str(exc))
            raise
        status = "refused" if result.stop_reason == "refusal" else "ok" if result.parsed is not None else "error"
        detail = "fallback used" if result.fallback_used else ("fallbacks on" if result.fallbacks_enabled else "fallbacks off")
        self._record(purpose, model, result.usage, status, detail)
        if result.stop_reason == "refusal":
            raise AIRefused("The AI declined this request; nothing was changed.")
        if result.parsed is None:
            reason = "it ran out of space" if result.stop_reason == "max_tokens" else "it was incomplete"
            raise AIError(f"The AI response could not be used ({reason}).")
        return result.parsed

    def _record(self, purpose: str, model: str, usage: TokenUsage, status: str, detail: str) -> None:
        cost = cost_usd(model, usage)
        with self._lock:
            self._spent += cost - self._estimate
            self._pending.append(AIUsage(
                created_at=datetime.utcnow(), purpose=purpose, model=model,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens,
                cost_usd=cost, status=status, detail=detail[:200],
            ))

    def flush_usage(self, db: Session) -> None:
        """Write collected usage rows (call from the request's own thread); the caller commits."""
        with self._lock:
            pending, self._pending = self._pending, []
        db.add_all(pending)


def usage_summary(db: Session, settings: Settings) -> dict:
    start = month_start()
    rows = db.query(AIUsage.purpose, func.count(AIUsage.id), func.coalesce(func.sum(AIUsage.cost_usd), 0.0)) \
        .filter(AIUsage.created_at >= start).group_by(AIUsage.purpose).all()
    spent = sum(float(c) for _, _, c in rows)
    return {
        "month_start": start.date().isoformat(),
        "spent_usd": round(spent, 4),
        "budget_usd": settings.ai_monthly_budget_usd,
        "remaining_usd": round(max(0.0, settings.ai_monthly_budget_usd - spent), 4),
        "requests": {purpose: count for purpose, count, _ in rows},
        "estimate_per_request_usd": round(average_request_cost(db), 4),
        "model": settings.ai_model,
    }


def text_block(text: str, cache: bool = False) -> dict:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block
