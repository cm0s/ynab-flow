"""Feature switches for receipts and AI: on/off reasons, and no secrets in responses."""
import json

from src.settings import features, load_settings

MAILBOX_ENV = {
    "BRIDGE_MAILBOXES": "me, partner",
    "BRIDGE_ME_USER": "me@proton.me", "BRIDGE_ME_PASSWORD": "bridge-secret-1",
    "BRIDGE_PARTNER_USER": "partner@proton.me", "BRIDGE_PARTNER_PASSWORD": "bridge-secret-2",
}


def test_defaults_are_off_with_reasons():
    s = load_settings({})
    assert s.ai_model == "claude-sonnet-5-5" and s.ai_extraction_model == "claude-sonnet-5-5"
    assert s.ai_monthly_budget_usd == 10.0 and s.ai_max_concurrency == 4 and s.folders == ["INBOX"]
    f = features(s)
    assert not f["receipts"]["enabled"] and "BRIDGE_MAILBOXES" in f["receipts"]["reason"]
    assert not f["ai"]["enabled"] and "ANTHROPIC_API_KEY" in f["ai"]["reason"]


def test_configured_features_are_on_and_hide_secrets():
    env = {**MAILBOX_ENV, "ANTHROPIC_API_KEY": "sk-ant-secret", "AI_EXTRACTION_MODEL": "claude-haiku-5-5"}
    s = load_settings(env)
    f = features(s)
    assert f["receipts"]["enabled"] and f["receipts"]["mailboxes"] == ["me", "partner"]
    assert "not verified" in f["receipts"]["reason"]
    assert f["ai"]["enabled"] and f["ai"]["extraction_model"] == "claude-haiku-5-5"
    dumped = json.dumps(f) + repr(s)
    for secret in ("bridge-secret-1", "bridge-secret-2", "sk-ant-secret"):
        assert secret not in dumped


def test_incomplete_mailbox_names_the_missing_variables():
    f = features(load_settings({"BRIDGE_MAILBOXES": "me", "BRIDGE_ME_USER": "me@proton.me"}))
    assert not f["receipts"]["enabled"] and "BRIDGE_ME_USER/PASSWORD" in f["receipts"]["reason"]


def test_remote_bridge_requires_certificate(tmp_path):
    remote = {**MAILBOX_ENV, "BRIDGE_IMAP_HOST": "192.168.1.20"}
    f = features(load_settings(remote))
    assert not f["receipts"]["enabled"] and "BRIDGE_CERT_PATH" in f["receipts"]["reason"]
    cert = tmp_path / "bridge.pem"
    cert.write_text("cert")
    f = features(load_settings({**remote, "BRIDGE_CERT_PATH": str(cert)}))
    assert f["receipts"]["enabled"] and "verified" in f["receipts"]["reason"]


def test_invalid_numbers_are_reported():
    f = features(load_settings({"ANTHROPIC_API_KEY": "k", "AI_MONTHLY_BUDGET_USD": "lots"}))
    assert not f["ai"]["enabled"] and "AI_MONTHLY_BUDGET_USD" in f["ai"]["reason"]
    f = features(load_settings({"BRIDGE_MAILBOXES": "me", "BRIDGE_ME_USER": "u", "BRIDGE_ME_PASSWORD": "p",
                                "BRIDGE_FETCH_SINCE_DAYS": "-1"}))
    assert not f["receipts"]["enabled"] and "BRIDGE_FETCH_SINCE_DAYS" in f["receipts"]["reason"]


def test_features_endpoint_never_returns_credentials(env, monkeypatch):
    for key, value in {**MAILBOX_ENV, "ANTHROPIC_API_KEY": "sk-ant-secret"}.items():
        monkeypatch.setenv(key, value)
    response = env.client.get("/features")
    assert response.status_code == 200
    body = response.text
    assert response.json()["ai"]["enabled"] and response.json()["receipts"]["enabled"]
    for secret in ("bridge-secret-1", "bridge-secret-2", "sk-ant-secret", "me@proton.me"):
        assert secret not in body
