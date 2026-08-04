import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from fastapi import HTTPException
from backend.api.webhook import receive_webhook

pytestmark = pytest.mark.anyio

class MockRedis:
    def __init__(self):
        self.store = {}
    async def exists(self, key):
        return key in self.store
    async def setnx(self, key, val):
        if key in self.store:
            return False
        self.store[key] = val
        return True
    async def delete(self, key):
        self.store.pop(key, None)
        return 1
    async def expire(self, key, seconds):
        return True
    async def aclose(self):
        pass

async def test_webhook_idempotency_claim_only_on_success(monkeypatch):
    mock_redis = MockRedis()
    import backend.api.webhook as webhook
    monkeypatch.setattr(webhook, "redis_client", mock_redis)

    # Mock dependencies inside webhook to avoid complex setup
    mock_db = AsyncMock()
    mock_db.get_tenant_by_wa_phone_number_id = AsyncMock(return_value={
        "tenant_id": uuid4(),
        "tenant_slug": "test-tenant",
        "whatsapp_account_id": uuid4(),
        "tenant_status": "active",
    })
    mock_db.get_active_config_version = AsyncMock(return_value=1)
    mock_db.get_tenant_config = AsyncMock(return_value={"ignored_numbers": []})
    mock_db.get_tenant = AsyncMock(return_value={"status": "active"})
    monkeypatch.setattr(webhook, "db_client", mock_db)

    mock_cust_service = AsyncMock()
    mock_cust_service.get_or_create_customer = AsyncMock(return_value={"id": uuid4(), "consent_status": "granted"})
    monkeypatch.setattr(webhook, "customer_service", mock_cust_service)

    mock_db.get_or_create_conversation = AsyncMock(return_value={"id": uuid4()})
    mock_db.get_next_sequence_number = AsyncMock(return_value=1)

    mock_burst_buffer = AsyncMock()
    monkeypatch.setattr(webhook, "burst_buffer", mock_burst_buffer)

    # 1. Simulate failure in burst_buffer.add_message
    mock_burst_buffer.add_message = AsyncMock(side_effect=RuntimeError("redis buffer down"))

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "wa_acc_id",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "16505551234", "phone_number_id": "123456789"},
                    "contacts": [{"profile": {"name": "Test User"}, "wa_id": "919876543210"}],
                    "messages": [{
                        "from": "919876543210",
                        "id": "ABGGFlPnTYa0Ago-YxXg1Z4VTHB2",
                        "timestamp": "1657536000",
                        "text": {"body": "hello test"},
                        "type": "text"
                    }]
                },
                "field": "messages"
            }]
        }]
    }

    class FakeRequest:
        async def json(self):
            return payload

    # Call receive_webhook and expect failure
    with pytest.raises(RuntimeError, match="redis buffer down"):
        await receive_webhook(
            request=FakeRequest(),
            valid=True
        )

    # Assert that the event key was NOT claimed/set in Redis
    assert not await mock_redis.exists("whatsapp:webhook:ABGGFlPnTYa0Ago-YxXg1Z4VTHB2")

    # 2. Simulate success in burst_buffer.add_message
    mock_burst_buffer.add_message = AsyncMock(return_value=True)

    await receive_webhook(
        request=FakeRequest(),
        valid=True
    )

    # Assert that the event key WAS claimed/set in Redis
    assert await mock_redis.exists("whatsapp:webhook:ABGGFlPnTYa0Ago-YxXg1Z4VTHB2")

async def test_empty_webhook_arrays_are_ignored_successfully():
    class FakeRequest:
        async def json(self):
            return {"object": "whatsapp_business_account", "entry": []}

    response = await receive_webhook(request=FakeRequest(), valid=True)
    assert response["success"] is True
    assert response["data"]["status"] == "ignored_no_messages_or_statuses"


def _whatsapp_payload(sender_phone: str, message_id: str = "wamid-test"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "wa_acc_id",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "16505551234", "phone_number_id": "123456789"},
                    "contacts": [{"profile": {"name": "Test User"}, "wa_id": sender_phone}],
                    "messages": [{
                        "from": sender_phone,
                        "id": message_id,
                        "timestamp": "1657536000",
                        "text": {"body": "hello test"},
                        "type": "text",
                    }],
                },
                "field": "messages",
            }]
        }]
    }


async def _install_webhook_doubles(monkeypatch, ignored_numbers):
    import backend.api.webhook as webhook

    mock_redis = MockRedis()
    monkeypatch.setattr(webhook, "redis_client", mock_redis)

    tenant_id = uuid4()
    mock_db = AsyncMock()
    mock_db.get_tenant_by_wa_phone_number_id = AsyncMock(return_value={
        "tenant_id": tenant_id,
        "tenant_slug": "test-tenant",
        "whatsapp_account_id": uuid4(),
        "tenant_status": "active",
    })
    mock_db.get_active_config_version = AsyncMock(return_value=1)
    mock_db.get_tenant_config = AsyncMock(return_value={"config_json": {"ignored_numbers": ignored_numbers}})
    mock_db.get_or_create_conversation = AsyncMock(return_value={"id": uuid4()})
    mock_db.get_next_sequence_number = AsyncMock(return_value=1)
    monkeypatch.setattr(webhook, "db_client", mock_db)

    mock_customer_service = AsyncMock()
    mock_customer_service.get_or_create_customer = AsyncMock(return_value={"id": uuid4(), "consent_status": "granted"})
    monkeypatch.setattr(webhook, "customer_service", mock_customer_service)

    mock_burst_buffer = AsyncMock()
    mock_burst_buffer.add_message = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook, "burst_buffer", mock_burst_buffer)
    return mock_customer_service, mock_burst_buffer


@pytest.mark.parametrize(
    ("ignored_number", "incoming_number"),
    [
        ("+91 98765 43210", "919876543210"),
        ("9876543210", "9876543210"),
        ("98765-43210", "9876543210"),
    ],
)
async def test_whatsapp_ignored_numbers_are_normalized(monkeypatch, ignored_number, incoming_number):
    customer_service, burst_buffer = await _install_webhook_doubles(monkeypatch, [ignored_number])

    class FakeRequest:
        async def json(self):
            return _whatsapp_payload(incoming_number, message_id=f"wamid-{incoming_number}-{ignored_number}")

    response = await receive_webhook(request=FakeRequest(), valid=True)

    assert response["success"] is True
    assert response["data"]["count"] == 0
    customer_service.get_or_create_customer.assert_not_awaited()
    burst_buffer.add_message.assert_not_awaited()


async def test_whatsapp_non_ignored_number_still_processes(monkeypatch):
    customer_service, burst_buffer = await _install_webhook_doubles(monkeypatch, ["11111-22222"])

    class FakeRequest:
        async def json(self):
            return _whatsapp_payload("9876543210", message_id="wamid-non-ignored")

    response = await receive_webhook(request=FakeRequest(), valid=True)

    assert response["success"] is True
    assert response["data"]["count"] == 1
    customer_service.get_or_create_customer.assert_awaited_once()
    burst_buffer.add_message.assert_awaited_once()
