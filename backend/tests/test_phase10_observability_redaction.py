import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.api.routes.admin import redact_sensitive_values
from backend.core.config import settings
import logging
import time


MOCK_SUPERADMIN = {
    "user_id": uuid4(),
    "tenant_id": uuid4(),
    "role": "super_admin"
}

@pytest.fixture
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()

def test_redact_sensitive_values_handles_nested_and_lists():
    payload = {
        "ok": 123,
        "token": "secret_abc",
        "nested_dict": {
            "signature": "sig_123",
            "nested_list": [
                {"otp": "456"},
                {"normal": "val"}
            ]
        }
    }
    redacted = redact_sensitive_values(payload)
    assert redacted["ok"] == 123
    assert redacted["token"] == "[REDACTED]"
    assert redacted["nested_dict"]["signature"] == "[REDACTED]"
    assert redacted["nested_dict"]["nested_list"][0]["otp"] == "[REDACTED]"
    assert redacted["nested_dict"]["nested_list"][1]["normal"] == "val"

@pytest.mark.anyio
async def test_health_ops_no_secrets(clean_overrides):
    async def override_user():
        return MOCK_SUPERADMIN
    app.dependency_overrides[get_current_user] = override_user

    # Setup config mocks
    with patch.object(settings, "WHATSAPP_API_TOKEN", "super_secret_wa_token"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "super_secret_razorpay_secret"), \
         patch.object(settings, "CUSTOMER_OTP_WEBHOOK_SECRET", "super_secret_otp_secret"):

        client = TestClient(app)
        with patch("backend.api.health.get_redis_client") as redis_mock:
            r_client = AsyncMock()
            r_client.ping = AsyncMock(return_value=True)
            r_client.get = AsyncMock(return_value=None)
            r_client.llen = AsyncMock(return_value=0)
            redis_mock.return_value = r_client

            response = client.get("/api/v1/health/ops")
            assert response.status_code == 200
            res_json = response.json()

            # Ensure no secret values leak in text
            body_str = response.text
            assert "super_secret_wa_token" not in body_str
            assert "super_secret_razorpay_secret" not in body_str
            assert "super_secret_otp_secret" not in body_str

            # Check providers metadata: keys are true/false, not secret string
            providers = res_json["data"]["providers"]
            assert providers["whatsapp"]["checks"]["api_token_configured"] is True
            assert providers["razorpay"]["checks"]["key_secret_configured"] is True
            assert providers["otp"]["checks"]["webhook_secret_configured"] is True

@pytest.mark.anyio
async def test_admin_audit_logs_are_redacted(clean_overrides):
    async def override_user():
        return MOCK_SUPERADMIN
    app.dependency_overrides[get_current_user] = override_user

    mock_logs = {
        "items": [
            {
                "id": str(uuid4()),
                "tenant_id": str(uuid4()),
                "actor_id": str(uuid4()),
                "action": "config_activated",
                "resource_type": "config",
                "resource_id": None,
                "details": {
                    "token_val": "raw_secret_value_xyz",
                    "webhook_signature": "signature_abc",
                    "normal_config": "enabled"
                },
                "ip_address": "127.0.0.1",
                "created_at": "2026-06-15T12:00:00+00:00",
                "tenant_name": "Test Tenant",
                "actor_email": "admin@example.com"
            }
        ],
        "next_cursor": None
    }

    client = TestClient(app)
    with patch("backend.api.routes.admin.db_client") as db_mock:
        db_mock.list_platform_audit_logs = AsyncMock(return_value=mock_logs)
        response = client.get("/api/v1/admin/audit-logs")
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1

        # Details must be redacted
        details = data[0]["details"]
        assert details["token_val"] == "[REDACTED]"
        assert details["webhook_signature"] == "[REDACTED]"
        assert details["normal_config"] == "enabled"

        # Verify text response does not contain the raw values
        assert "raw_secret_value_xyz" not in response.text
        assert "signature_abc" not in response.text

@pytest.mark.anyio
async def test_webhook_invalid_signatures_dont_leak(clean_overrides):
    client = TestClient(app)

    # 1. Razorpay invalid signature
    with patch.object(settings, "RAZORPAY_WEBHOOK_SECRET", "razorpay_secret"), \
         patch.object(settings, "ENV", "production"):
        response = client.post(
            "/api/v1/razorpay",
            content="{}",
            headers={"x-razorpay-signature": "some_bad_signature_abc"}
        )
        assert response.status_code == 400
        assert "Invalid signature" in response.text
        assert "some_bad_signature_abc" not in response.text

    # 2. WhatsApp invalid signature
    with patch.object(settings, "WHATSAPP_APP_SECRET", "wa_secret"), \
         patch.object(settings, "ENABLE_WEBHOOK_SIGNATURE_BYPASS", False):
        response = client.post(
            "/api/v1/webhook",
            json={"entry": [{"time": int(time.time())}]},
            headers={"X-Hub-Signature-256": "sha256=some_bad_signature_xyz"}
        )
        assert response.status_code == 403
        assert "Invalid signature" in response.text
        assert "some_bad_signature_xyz" not in response.text
