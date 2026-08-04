from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.modules.payments_module import payments_module
pytestmark = pytest.mark.anyio
def _context(step_data, module_config=None):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="payments",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Cafe",
                "modules": {"payments": {"enabled": True, **(module_config or {})}},
            },
        ),
        incoming_message={"text": "confirm"},
    )
async def test_razorpay_customer_link_requires_explicit_business_opt_in(monkeypatch):
    db = SimpleNamespace(create_payment_request=AsyncMock())
    monkeypatch.setattr("backend.modules.payments_module.payment_service", db)
    context = _context({"amount": "100", "reference": "order-1"}, module_config={"provider": "razorpay"})
    result = await payments_module.execute(context)
    assert result.success is False
    assert "not enabled" in result.user_message
    db.create_payment_request.assert_not_called()
async def test_manual_payment_request_is_recorded_without_fake_url(monkeypatch):
    payment_id = uuid4()
    db = SimpleNamespace(
        create_payment_request=AsyncMock(
            return_value={
                "id": payment_id,
                "status": "draft",
                "provider": "manual",
                "payment_url": None,
            }
        )
    )
    monkeypatch.setattr("backend.modules.payments_module.payment_service", db)
    context = _context({"amount": "100", "reference": "order-1"})
    result = await payments_module.execute(context)
    assert result.success is True
    assert "manual payment request" in result.user_message
    assert result.data["payment_url"] is None
    call = db.create_payment_request.call_args.kwargs
    assert call["provider"] == "manual"
    assert call["status"] == "draft"
    assert call["metadata"]["manual_payment_required"] is True
async def test_razorpay_link_persists_provider_ids(monkeypatch):
    payment_id = uuid4()
    db = SimpleNamespace(
        create_payment_request=AsyncMock(
            return_value={
                "id": payment_id,
                "status": "draft",
                "provider": "razorpay",
                "payment_url": None,
            }
        ),
        update_payment_request_provider_details=AsyncMock(
            return_value={
                "id": payment_id,
                "status": "sent",
                "provider": "razorpay",
                "payment_url": "https://rzp.io/i/pay_123",
            }
        )
    )
    monkeypatch.setattr("backend.modules.payments_module.payment_service", db)
    monkeypatch.setattr("backend.modules.payments_module.settings.RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr("backend.modules.payments_module.settings.RAZORPAY_KEY_SECRET", "secret")
    async def fake_link(**kwargs):
        return {
            "blocked": False,
            "payment_url": "https://rzp.io/i/pay_123",
            "provider_payment_link_id": "plink_123",
            "provider_order_id": "order_123",
        }
    monkeypatch.setattr(payments_module, "_create_razorpay_payment_link", fake_link)
    context = _context(
        {"amount": "100", "reference": "order-1"},
        module_config={"use_platform_razorpay": True, "provider": "razorpay"},
    )
    result = await payments_module.execute(context)
    assert result.success is True
    assert "payment link" in result.user_message

    # Assert Phase 1 DB Reservation details
    call1 = db.create_payment_request.call_args.kwargs
    assert call1["provider"] == "razorpay"
    assert call1["status"] == "draft"
    assert call1["payment_url"] is None
    assert call1["provider_payment_link_id"] is None
    assert call1["provider_order_id"] is None

    # Assert Phase 3 DB Finalization details
    call3 = db.update_payment_request_provider_details.call_args.kwargs
    assert call3["payment_id"] == payment_id
    assert call3["status"] == "sent"
    assert call3["payment_url"] == "https://rzp.io/i/pay_123"
    assert call3["provider_payment_link_id"] == "plink_123"
    assert call3["provider_order_id"] == "order_123"
    assert call3["metadata"]["provider_scope"] == "platform_razorpay"
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import json
import hmac
import hashlib
from backend.api.webhook import razorpay_webhook
class FakeRequest:
    def __init__(self, body_bytes: bytes, headers: dict):
        self.body_bytes = body_bytes
        self.headers = headers
    async def body(self):
        return self.body_bytes
@pytest.mark.anyio
async def test_razorpay_webhook_signature_rejected(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.RAZORPAY_WEBHOOK_SECRET", "secret_key")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_WEBHOOK_SIGNATURE_BYPASS", False)
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "production")
    request = FakeRequest(
        body_bytes=b'{"event": "payment_link.paid"}',
        headers={"x-razorpay-signature": "wrong_sig"}
    )
    with pytest.raises(HTTPException) as exc_info:
        await razorpay_webhook(request)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid signature"
@pytest.mark.anyio
async def test_razorpay_webhook_unknown_payment_link(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_WEBHOOK_SIGNATURE_BYPASS", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = None
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    payload = {
        "event": "payment_link.paid",
        "id": "evt_test_unknown",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_unknown",
                    "status": "paid"
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    data = json.loads(response.body.decode("utf-8"))
    assert data["status"] == "ignored"
    assert data["reason"] == "unknown_payment_link_id"
@pytest.mark.anyio
async def test_razorpay_webhook_paid_event_success(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    tenant_id = uuid4()
    payment_req_id = uuid4()
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = {
        "id": payment_req_id,
        "tenant_id": tenant_id,
        "status": "sent"
    }
    mock_service.action_payment_request.return_value = {"id": payment_req_id, "status": "paid"}
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    payload = {
        "event": "payment_link.paid",
        "id": "evt_test_paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "status": "paid",
                    "amount": 1000,
                    "currency": "INR"
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_999"
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    data = json.loads(response.body.decode("utf-8"))
    assert data["status"] == "ok"
    mock_service.action_payment_request.assert_called_once()
    kwargs = mock_service.action_payment_request.call_args.kwargs
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["payment_id"] == payment_req_id
    assert kwargs["action"] == "mark_paid"
    assert kwargs["provider_payment_id"] == "pay_999"
    assert kwargs["provider_event_id"] == "evt_test_paid"
    assert "amount" in kwargs["provider_payload"]
    assert "currency" in kwargs["provider_payload"]
    assert "payment_link_id" in kwargs["provider_payload"]
    assert "notes" not in kwargs["provider_payload"]
@pytest.mark.anyio
async def test_razorpay_webhook_cancelled_event_success(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    tenant_id = uuid4()
    payment_req_id = uuid4()
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = {
        "id": payment_req_id,
        "tenant_id": tenant_id,
        "status": "sent"
    }
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    payload = {
        "event": "payment_link.cancelled",
        "id": "evt_test_cancel",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "status": "cancelled",
                    "amount": 1000,
                    "currency": "INR"
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    mock_service.action_payment_request.assert_called_once()
    kwargs = mock_service.action_payment_request.call_args.kwargs
    assert kwargs["action"] == "cancel"
@pytest.mark.anyio
async def test_razorpay_webhook_expired_event_success(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    tenant_id = uuid4()
    payment_req_id = uuid4()
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = {
        "id": payment_req_id,
        "tenant_id": tenant_id,
        "status": "sent"
    }
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    payload = {
        "event": "payment_link.expired",
        "id": "evt_test_expire",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "status": "expired",
                    "amount": 1000,
                    "currency": "INR"
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    mock_service.action_payment_request.assert_called_once()
    kwargs = mock_service.action_payment_request.call_args.kwargs
    assert kwargs["action"] == "expire"
@pytest.mark.anyio
async def test_razorpay_webhook_partially_paid_event(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    tenant_id = uuid4()
    payment_req_id = uuid4()
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = {
        "id": payment_req_id,
        "tenant_id": tenant_id,
        "status": "sent"
    }
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    payload = {
        "event": "payment_link.partially_paid",
        "id": "evt_test_partial",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "status": "partially_paid",
                    "amount": 500,
                    "currency": "INR"
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_partial_123"
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    mock_service.action_payment_request.assert_called_once()
    kwargs = mock_service.action_payment_request.call_args.kwargs
    assert kwargs["action"] == "partial_payment"
    assert kwargs["provider_payment_id"] == "pay_partial_123"
@pytest.mark.anyio
async def test_razorpay_webhook_unhandled_payment_link_event(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    tenant_id = uuid4()
    payment_req_id = uuid4()
    mock_db = AsyncMock()
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = {
        "id": payment_req_id,
        "tenant_id": tenant_id,
        "status": "sent"
    }
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    monkeypatch.setattr("backend.api.webhook.db_client", mock_db)
    payload = {
        "event": "payment_link.updated",
        "id": "evt_test_unhandled",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "status": "issued"
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    data = json.loads(response.body.decode("utf-8"))
    assert data["status"] == "ignored"
    assert data["reason"] == "unhandled_event_type"
    mock_db.write_audit_log.assert_called_once()
@pytest.mark.anyio
async def test_razorpay_webhook_idempotency_duplicate(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = False
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    mock_db = AsyncMock()
    monkeypatch.setattr("backend.api.webhook.db_client", mock_db)
    payload = {
        "event": "payment_link.paid",
        "id": "evt_duplicate",
        "payload": {}
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    data = json.loads(response.body.decode("utf-8"))
    assert data["status"] == "ok"
    assert data["duplicate"] is True
    mock_db.get_payment_request_by_provider_link_id.assert_not_called()
@pytest.mark.anyio
async def test_razorpay_webhook_tenant_isolation(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    db_tenant_id = uuid4()
    payload_tenant_id = uuid4()
    payment_req_id = uuid4()
    mock_service = AsyncMock()
    mock_service.get_payment_request_by_provider_link_id.return_value = {
        "id": payment_req_id,
        "tenant_id": db_tenant_id,
        "status": "sent"
    }
    monkeypatch.setattr("backend.api.webhook.payment_service", mock_service)
    payload = {
        "event": "payment_link.paid",
        "id": "evt_isolation_test",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "status": "paid",
                    "notes": {
                        "tenant_id": str(payload_tenant_id)
                    }
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    await razorpay_webhook(request)
    mock_service.action_payment_request.assert_called_once()
    kwargs = mock_service.action_payment_request.call_args.kwargs
    assert kwargs["tenant_id"] == db_tenant_id
    assert kwargs["tenant_id"] != payload_tenant_id
class FakeAcquireConn:
    def __init__(self, row):
        self.row = row
        self.fetchrow_calls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.row
@pytest.mark.anyio
async def test_razorpay_webhook_saas_subscription_success(monkeypatch):
    monkeypatch.setattr("backend.api.webhook.settings.ENV", "test")
    monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "mock")
    monkeypatch.setattr("backend.api.webhook.settings.ENABLE_MOCK_BILLING", True)
    mock_redis = AsyncMock()
    mock_redis.setnx.return_value = True
    monkeypatch.setattr("backend.api.webhook.redis_client", mock_redis)
    tenant_id = uuid4()
    tenant_row = {
        "id": tenant_id,
        "plan_code": "enterprise",
        "status": "active",
        "razorpay_customer_id": "cust_123",
        "current_period_end": None
    }
    fake_conn = FakeAcquireConn(tenant_row)
    monkeypatch.setattr("backend.api.webhook.db_client.acquire", lambda: fake_conn)
    mock_write_audit_log = AsyncMock()
    monkeypatch.setattr("backend.api.webhook.db_client.write_audit_log", mock_write_audit_log)
    payload = {
        "event": "subscription.charged",
        "id": "evt_test_sub_charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_123",
                    "customer_id": "cust_123",
                    "current_end": 1234567
                }
            }
        }
    }
    request = FakeRequest(
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"x-razorpay-signature": "bypass"}
    )
    response = await razorpay_webhook(request)
    assert response.status_code == 200
    assert len(fake_conn.fetchrow_calls) == 1
    assert "UPDATE tenants" in fake_conn.fetchrow_calls[0][0]
    mock_write_audit_log.assert_called_once()
    kwargs = mock_write_audit_log.call_args.kwargs
    assert kwargs["action"] == "razorpay_subscription_activated"
