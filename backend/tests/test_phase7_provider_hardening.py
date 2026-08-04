import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.health import _provider_health_snapshot
from backend.api.routes import public_chat
from backend.core.config import Settings
from backend.core.context import PendingAction, SessionState
from backend.domains.payments.tool_handlers import execute_payments_tool


@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch):
    for key in list(os.environ.keys()):
        if key not in {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}:
            monkeypatch.delenv(key, raising=False)


def safe_production_settings(**overrides):
    base = {
        "ENV": "production",
        "AWA_INTEGRATION_MODE": "live",
        "AUTH_MODE": "postgres",
        "WHATSAPP_MODE": "live",
        "BILLING_MODE": "live",
        "LOG_LEVEL": "INFO",
        "DATABASE_URL": "postgresql://postgres:" + "postgres@localhost:5432/postgres",
        "REDIS_URL": "redis://localhost:6379/0",
        "JWT_SECRET": "a" * 32,
        "WHATSAPP_TOKEN_ENCRYPTION_KEY": "a" * 32,
        "WHATSAPP_API_TOKEN": "wa_api_token",
        "WHATSAPP_APP_SECRET": "wa_app_secret",
        "WHATSAPP_VERIFY_TOKEN": "wa_verify",
        "WHATSAPP_PHONE_NUMBER_ID": "wa_phone_id",
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
        "TURNSTILE_SECRET_KEY": "turnstile_secret",
        "CUSTOMER_OTP_MODE": "live",
        "CUSTOMER_OTP_WEBHOOK_URL": "https://example.com/otp",
        "CUSTOMER_OTP_WEBHOOK_SECRET": "otp_secret",
        "CORS_ORIGINS": "https://example.com",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_production_requires_whatsapp_send_credentials():
    with pytest.raises(ValidationError, match="WHATSAPP_API_TOKEN is required"):
        safe_production_settings(WHATSAPP_API_TOKEN=None)
    with pytest.raises(ValidationError, match="WHATSAPP_PHONE_NUMBER_ID is required"):
        safe_production_settings(WHATSAPP_PHONE_NUMBER_ID=None)


def test_production_requires_signed_live_otp_webhook():
    with pytest.raises(ValidationError, match="CUSTOMER_OTP_WEBHOOK_SECRET is required"):
        safe_production_settings(CUSTOMER_OTP_WEBHOOK_SECRET=None)
    with pytest.raises(ValidationError, match="CUSTOMER_OTP_WEBHOOK_URL is unsafe"):
        safe_production_settings(CUSTOMER_OTP_WEBHOOK_URL="http://localhost:8080/otp")


def test_provider_health_snapshot_redacts_secret_values(monkeypatch):
    settings = safe_production_settings()
    monkeypatch.setattr("backend.api.health.settings", settings)

    snapshot = _provider_health_snapshot()

    assert snapshot["whatsapp"]["status"] == "ready"
    assert snapshot["razorpay"]["status"] == "ready"
    assert snapshot["turnstile"]["status"] == "ready"
    assert snapshot["otp"]["status"] == "ready"
    assert snapshot["smtp_email"]["status"] == "blocked"
    rendered = str(snapshot)
    assert "wa_api_token" not in rendered
    assert "wa_app_secret" not in rendered
    assert "rzp_secret" not in rendered
    assert "otp_secret" not in rendered
    assert "turnstile_secret" not in rendered


@pytest.mark.anyio
async def test_public_chat_otp_invalid_attempts_are_limited(monkeypatch):
    session_id = uuid4()
    session = {
        "id": session_id,
        "tenant_id": uuid4(),
        "customer_id": uuid4(),
        "conversation_id": uuid4(),
        "tenant_status": "active",
    }
    attempts = {"count": 0}
    pending = PendingAction(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
    )
    state = SessionState()
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "awaiting_confirmation"
    state.action_ledger.pending_action = pending
    conversation_session = {
        "id": uuid4(),
        "tenant_id": session["tenant_id"],
        "conversation_id": session["conversation_id"],
        "current_module": "booking",
        "state_json": {"config_version": 7, "state": state.model_dump(mode="json")},
    }

    class InvalidAtomicChallenge:
        def __init__(self):
            self.values = {}
            self.eval_count = 0

        async def eval(self, script, key_count, *parts):
            self.eval_count += 1
            challenge_key = parts[0]
            expected_code = parts[key_count]
            assert self.values[challenge_key] != expected_code
            return [-1, 0]

    async def noop_rate_limit(*args, **kwargs):
        return None

    async def fake_session(*args, **kwargs):
        return session

    async def fake_incr_attempts(key, ex):
        attempts["count"] += 1
        return attempts["count"]

    monkeypatch.setattr(public_chat, "_check_public_rate_limit", noop_rate_limit)
    monkeypatch.setattr(public_chat, "_get_active_session", fake_session)
    monkeypatch.setattr(public_chat, "_redis_incr_with_ttl", fake_incr_attempts)
    monkeypatch.setattr(public_chat.settings, "CUSTOMER_OTP_MAX_ATTEMPTS", 2)
    mock_db = AsyncMock()
    mock_db.get_conversation_session = AsyncMock(return_value=conversation_session)
    monkeypatch.setattr(public_chat, "db_client", mock_db)
    atomic = InvalidAtomicChallenge()
    binding = public_chat._pending_action_binding(
        pending.model_dump(mode="json"),
        session=session,
        conversation_session=conversation_session,
    )
    challenge_key = public_chat._otp_challenge_key(session_id, binding, "+15550100")
    atomic.values[challenge_key] = "123456"
    monkeypatch.setattr("backend.core.redis_client.redis_client", atomic)
    mark = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(public_chat, "_mark_session_verified_action", mark)
    monkeypatch.setattr(public_chat, "_enqueue_verified_pending_action_continuation", enqueue)

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.5"))
    body = public_chat.PublicChatOtpVerifyRequest(
        session_token="chat_test_token_abcdefghijklmnopqrstuvwxyz",
        phone="+1 555 0100",
        action="booking_commitment",
        code="000000",
    )

    with pytest.raises(HTTPException) as first:
        await public_chat.verify_public_chat_otp(session_id, body, request)
    assert first.value.status_code == 400

    with pytest.raises(HTTPException) as second:
        await public_chat.verify_public_chat_otp(session_id, body, request)
    assert second.value.status_code == 429
    assert attempts["count"] == 2
    assert atomic.eval_count == 2
    assert atomic.values[challenge_key] == "123456"
    mark.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.anyio
async def test_payments_tool_creates_request_but_cannot_mark_paid():
    calls = {}

    class PaymentService:
        async def create_payment_request(self, **kwargs):
            calls.update(kwargs)
            return {
                "id": uuid4(),
                "status": kwargs["status"],
                "provider": kwargs["provider"],
                "payment_url": kwargs.get("payment_url"),
            }

    context = SimpleNamespace(
        tenant=SimpleNamespace(id=uuid4()),
        user=SimpleNamespace(id=uuid4()),
        session_data=SimpleNamespace(
            conversation_id=uuid4(),
            state=SimpleNamespace(step_data={"amount": "100", "reference": "order-1"}),
        ),
        config=SimpleNamespace(config_data={"modules": {"payments": {"enabled": True, "provider": "manual"}}}),
    )

    result = await execute_payments_tool(
        context=context,
        payment_service=PaymentService(),
        create_razorpay_payment_link=None,
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
    )

    assert result.success is True
    assert calls["status"] == "draft"
    assert "action" not in calls
    assert "provider_payment_id" not in calls
