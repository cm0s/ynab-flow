"""Receipt (Proton Bridge) and AI settings, read from the environment (``backend/.env``).

Secrets (mailbox passwords, the Anthropic key) are kept out of ``repr`` and are
never returned by the API: ``features()`` reports only on/off and a reason.
"""

import ipaddress
import os
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_AI_MODEL = "claude-sonnet-5-5"


@dataclass
class MailboxConfig:
    label: str
    user: str = field(default="", repr=False)
    password: str = field(default="", repr=False)

    @property
    def complete(self) -> bool:
        return bool(self.user and self.password)


@dataclass
class Settings:
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 1143
    bridge_cert_path: str = ""
    mailboxes: List[MailboxConfig] = field(default_factory=list)
    folders: List[str] = field(default_factory=lambda: ["INBOX"])
    fetch_since_days: int = 60                       # only messages that arrived this recently; 0 = no limit
    anthropic_api_key: str = field(default="", repr=False)
    ai_model: str = DEFAULT_AI_MODEL
    ai_extraction_model: str = DEFAULT_AI_MODEL
    ai_monthly_budget_usd: float = 10.0
    ai_max_concurrency: int = 4
    errors: List[str] = field(default_factory=list)  # invalid values, reported by features()

    @property
    def bridge_is_loopback(self) -> bool:
        host = self.bridge_host.strip().lower()
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False


def _split(value: str) -> List[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _number(env, name: str, default, cast, errors: List[str], minimum=None):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        errors.append(f"{name} must be a number.")
        return default
    if minimum is not None and value < minimum:
        errors.append(f"{name} must be at least {minimum}.")
        return default
    return value


def load_settings(env: Optional[dict] = None) -> Settings:
    """Read settings at call time so a changed ``.env`` (or a test) takes effect without import tricks."""
    env = os.environ if env is None else env
    errors: List[str] = []
    mailboxes = []
    for label in _split(env.get("BRIDGE_MAILBOXES", "")):
        key = env_key(label)
        mailboxes.append(MailboxConfig(
            label=label,
            user=env.get(f"BRIDGE_{key}_USER", "").strip(),
            password=env.get(f"BRIDGE_{key}_PASSWORD", ""),
        ))
    ai_model = env.get("AI_MODEL", "").strip() or DEFAULT_AI_MODEL
    return Settings(
        bridge_host=env.get("BRIDGE_IMAP_HOST", "").strip() or "127.0.0.1",
        bridge_port=_number(env, "BRIDGE_IMAP_PORT", 1143, int, errors, minimum=1),
        bridge_cert_path=env.get("BRIDGE_CERT_PATH", "").strip(),
        mailboxes=mailboxes,
        folders=_split(env.get("BRIDGE_FOLDERS", "")) or ["INBOX"],
        fetch_since_days=_number(env, "BRIDGE_FETCH_SINCE_DAYS", 60, int, errors, minimum=0),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
        ai_model=ai_model,
        ai_extraction_model=env.get("AI_EXTRACTION_MODEL", "").strip() or ai_model,
        ai_monthly_budget_usd=_number(env, "AI_MONTHLY_BUDGET_USD", 10.0, float, errors, minimum=0),
        ai_max_concurrency=_number(env, "AI_MAX_CONCURRENCY", 4, int, errors, minimum=1),
        errors=errors,
    )


def env_key(label: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in label).upper()


def receipts_feature(s: Settings) -> dict:
    if not s.mailboxes:
        return {"enabled": False, "reason": "No mailbox configured. Set BRIDGE_MAILBOXES and the per-mailbox "
                                            "BRIDGE_<LABEL>_USER / BRIDGE_<LABEL>_PASSWORD in backend/.env."}
    incomplete = [m.label for m in s.mailboxes if not m.complete]
    if incomplete:
        names = ", ".join(f"BRIDGE_{env_key(label)}_USER/PASSWORD" for label in incomplete)
        return {"enabled": False, "reason": f"Missing credentials for mailbox {', '.join(incomplete)} ({names})."}
    bridge_errors = [e for e in s.errors if e.startswith("BRIDGE_")]
    if bridge_errors:
        return {"enabled": False, "reason": " ".join(bridge_errors)}
    if s.bridge_cert_path:
        if not os.path.isfile(s.bridge_cert_path):
            return {"enabled": False, "reason": "BRIDGE_CERT_PATH does not point to a readable certificate file."}
        tls = "Bridge certificate verified against BRIDGE_CERT_PATH."
    elif s.bridge_is_loopback:
        tls = "Bridge certificate not verified (allowed only because Bridge runs on this computer)."
    else:
        return {"enabled": False, "reason": "Bridge is not on this computer: set BRIDGE_CERT_PATH so its "
                                            "certificate can be verified."}
    window = (f"Only messages from the last {s.fetch_since_days} days are read." if s.fetch_since_days
              else "All messages are read (BRIDGE_FETCH_SINCE_DAYS=0).")
    return {"enabled": True, "reason": f"{len(s.mailboxes)} mailbox(es) configured. {window} {tls}",
            "mailboxes": [m.label for m in s.mailboxes], "folders": s.folders,
            "fetch_since_days": s.fetch_since_days}


def ai_feature(s: Settings) -> dict:
    if not s.anthropic_api_key:
        return {"enabled": False, "reason": "AI is off: ANTHROPIC_API_KEY is not set in backend/.env."}
    ai_errors = [e for e in s.errors if e.startswith("AI_")]
    if ai_errors:
        return {"enabled": False, "reason": " ".join(ai_errors)}
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {"enabled": False, "reason": "The anthropic package is not installed (pip install -r requirements.txt)."}
    return {"enabled": True, "reason": "AI suggestions are available on request.", "model": s.ai_model,
            "extraction_model": s.ai_extraction_model, "monthly_budget_usd": s.ai_monthly_budget_usd}


def features(s: Optional[Settings] = None) -> dict:
    s = s or load_settings()
    return {"receipts": receipts_feature(s), "ai": ai_feature(s)}
