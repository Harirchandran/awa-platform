import os
import pytest
import hmac
import hashlib
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from backend.core.config import Settings
from backend.api.webhook import _claim_whatsapp_event, _claim_razorpay_event, _extract_webhook_timestamp, validate_signature, _razorpay_event_id
from backend.logic.engine.llm import get_llm
from backend.db.client import db_client
from backend.api.health import _event_visibility_record

# Fake Redis for in-memory idempotency testing
class FakeRedis:
    def __init__(self):
        self.keys = {}

    async def setnx(self, key, value):
        if key in self.keys:
            return 0
        self.keys[key] = value
        return 1

    async def expire(self, key, ttl):
        pass

    async def delete(self, key):
        self.keys.pop(key, None)

    async def aclose(self):
        pass

# Autouse fixture to clear environment variables and prevent test pollution from host env
@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch):
    for key in list(os.environ.keys()):
        if key not in {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}:
            monkeypatch.delenv(key, raising=False)

# Base safe production settings helper
def get_safe_production_settings(**overrides):
    base = {
        "ENV": "production",
        "AWA_INTEGRATION_MODE": "live",
        "AUTH_MODE": "postgres",
        "WHATSAPP_MODE": "live",
        "BILLING_MODE": "live",
        "LOG_LEVEL": "INFO",
        "DATABASE_URL": "postgresql://postgres:" + "postgres@localhost:5432/postgres",
        "REDIS_URL": "redis://localhost:6379/0",
        "JWT_SECRET": "a" * 32, # Strong JWT secret
        "WHATSAPP_TOKEN_ENCRYPTION_KEY": "a" * 32,
        "WHATSAPP_API_TOKEN": "whatsapp_api_token",
        "WHATSAPP_APP_SECRET": "whatsapp_secret",
        "WHATSAPP_VERIFY_TOKEN": "whatsapp_verify",
        "WHATSAPP_PHONE_NUMBER_ID": "wa_phone_number_id",
        "RAZORPAY_KEY_ID": "rzp_key",
        "RAZORPAY_KEY_SECRET": "rzp_secret",
        "RAZORPAY_WEBHOOK_SECRET": "rzp_webhook_secret",
        "ROUTING_LLM_PROVIDER": "groq",
        "AGENT_LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "groq_key",
        "AWA_PUBLIC_CHAT_BASE_URL": "https://example.com/chat",
        "PUBLIC_CHAT_LINK_SECRET": "a" * 32,
        "PUBLIC_CHAT_SESSION_SECRET": "a" * 32,
        "PUBLIC_CHAT_CAPTCHA_MODE": "turnstile",
        "TURNSTILE_SECRET_KEY": "turnstile_key",
        "CUSTOMER_OTP_MODE": "live",
        "CUSTOMER_OTP_WEBHOOK_URL": "https://example.com/otp",
        "CUSTOMER_OTP_WEBHOOK_SECRET": "otp_webhook_secret",
        "CORS_ORIGINS": "https://example.com",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)

def test_production_accepts_safe_settings():
    settings = get_safe_production_settings()
    assert settings.ENV == "production"

def test_production_rejects_debug_logging():
    with pytest.raises(ValidationError, match="LOG_LEVEL must not be DEBUG in production"):
        get_safe_production_settings(LOG_LEVEL="DEBUG")

def test_production_rejects_localhost_public_url():
    with pytest.raises(ValidationError, match="AWA_PUBLIC_CHAT_BASE_URL must not be localhost in production"):
        get_safe_production_settings(AWA_PUBLIC_CHAT_BASE_URL="http://localhost:5173")

def test_production_rejects_disabled_captcha():
    with pytest.raises(ValidationError, match="PUBLIC_CHAT_CAPTCHA_MODE must be turnstile in production"):
        get_safe_production_settings(PUBLIC_CHAT_CAPTCHA_MODE="disabled")

def test_production_rejects_mock_captcha():
    with pytest.raises(ValidationError, match="PUBLIC_CHAT_CAPTCHA_MODE must be turnstile in production"):
        get_safe_production_settings(PUBLIC_CHAT_CAPTCHA_MODE="mock")

def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError, match=r"CORS_ORIGINS must not contain '\*'"):
        get_safe_production_settings(CORS_ORIGINS="https://example.com,*")

def test_production_rejects_http_cors():
    with pytest.raises(ValidationError, match="CORS_ORIGINS must use HTTPS in production"):
        get_safe_production_settings(CORS_ORIGINS="http://example.com")

def test_production_rejects_weak_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET must be configured with a strong secret"):
        get_safe_production_settings(JWT_SECRET="short_secret")
    with pytest.raises(ValidationError, match="JWT_SECRET must be configured with a strong secret"):
        get_safe_production_settings(JWT_SECRET="dummy_secret_containing_dummy_here")

def test_production_rejects_mock_auth():
    with pytest.raises(ValidationError, match="ENABLE_MOCK_AUTH must be false"):
        get_safe_production_settings(ENABLE_MOCK_AUTH=True)

def test_production_rejects_signature_bypass():
    with pytest.raises(ValidationError, match="ENABLE_WEBHOOK_SIGNATURE_BYPASS must be false"):
        get_safe_production_settings(ENABLE_WEBHOOK_SIGNATURE_BYPASS=True)

def test_production_rejects_deterministic_fake_llm():
    with pytest.raises(ValidationError, match="ROUTING_LLM_PROVIDER must be one of"):
        get_safe_production_settings(ROUTING_LLM_PROVIDER="deterministic_fake")

@pytest.mark.anyio
async def test_get_llm_blocks_deterministic_fake_in_live(monkeypatch):
    settings = get_safe_production_settings(ENV="local", AGENT_LLM_PROVIDER="deterministic_fake")
    monkeypatch.setattr("backend.logic.engine.llm.settings", settings)
    with pytest.raises(RuntimeError, match="Deterministic fake tool-calling LLM is not allowed in live integration mode"):
        get_llm()

class MockRequest:
    def __init__(self, headers, body_bytes):
        self.headers = headers
        self.body_bytes = body_bytes

    async def body(self):
        return self.body_bytes

@pytest.mark.anyio
async def test_whatsapp_webhook_signature_validation(monkeypatch):
    # Setup settings without bypass
    settings = get_safe_production_settings()
    monkeypatch.setattr("backend.api.webhook.settings", settings)

    # Missing signature
    req = MockRequest({}, b"{}")
    with pytest.raises(HTTPException) as exc:
        await validate_signature(req)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing signature"

    # Invalid signature
    body_invalid = ('{"entry": [{"time": ' + str(int(time.time())) + "}]}").encode("utf-8")
    req = MockRequest({"X-Hub-Signature-256": "sha256=invalid"}, body_invalid)
    with pytest.raises(HTTPException) as exc:
        await validate_signature(req)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Invalid signature"

    # Valid signature but stale timestamp
    body = b'{"entry": [{"time": 1000000000}]}'
    sig = "sha256=" + hmac.new(b"whatsapp_secret", body, hashlib.sha256).hexdigest()
    req = MockRequest({"X-Hub-Signature-256": sig}, body)
    with pytest.raises(HTTPException) as exc:
        await validate_signature(req)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Stale webhook payload"

@pytest.mark.anyio
async def test_whatsapp_webhook_idempotency_redis(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr("backend.api.webhook.redis_client", fake_redis)

    msg_id = f"wa_msg_{uuid4()}"

    # First claim should succeed
    claimed1 = await _claim_whatsapp_event(msg_id)
    assert claimed1 is True

    # Second claim should fail (duplicate)
    claimed2 = await _claim_whatsapp_event(msg_id)
    assert claimed2 is False

@pytest.mark.anyio
async def test_razorpay_webhook_idempotency_redis(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr("backend.api.webhook.redis_client", fake_redis)

    event_id = f"rzp_evt_{uuid4()}"
    payload = {"id": event_id, "event": "payment_link.paid"}

    claimed1, eid1 = await _claim_razorpay_event(payload)
    assert claimed1 is True
    assert eid1 == event_id

    claimed2, eid2 = await _claim_razorpay_event(payload)
    assert claimed2 is False
    assert eid2 == event_id

def test_telemetry_health_record_safety():
    # Verify no raw payload or secret fields in the record mapping
    evt = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "event_type": "payment.requested",
        "source_domain": "payments",
        "aggregate_type": "payment_request",
        "aggregate_id": uuid4(),
        "status": "pending",
        "attempts": 1,
        "max_attempts": 3,
        "last_error": "Failed connection",
        "payload": {"secret": "secretkey", "amount": 100},
        "metadata": {"user_ip": "1.2.3.4"}
    }

    record = _event_visibility_record(evt)
    # Verify only mapped fields exist, no payload or metadata keys
    assert "payload" not in record
    assert "metadata" not in record
    assert record["event_type"] == "payment.requested"
    assert record["reason_code"] == "Failed connection"
