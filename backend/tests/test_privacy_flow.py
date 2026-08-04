
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from backend.api.webhook import receive_webhook
@pytest.mark.anyio
async def test_webhook_consent_flow():
    """
    Test webhook logic:
    1. New customer (pending consent) -> Should log/info but return queued status
    2. Withdrawn customer -> Should reject processing
    """
    # Mock Request object
    mock_request = MagicMock()
    # request.json() is awaited, so json should return an awaitable
    mock_request.json.return_value = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919876543210",
                        "id": "wamid.123",
                        "type": "text",
                        "text": {"body": "Hello"},
                        "timestamp": "1234567890"
                    }],
                    "metadata": {"phone_number_id": "12345", "display_phone_number": "12345"},
                    "contacts": [{"profile": {"name": "Test User"}}]
                }
            }]
        }]
    }
    # To be awaited, json() call must return an awaitable. AsyncMock creates awaitable automatically.
    # MagicMock doesn't.
    # So replace json with AsyncMock
    mock_request.json = AsyncMock(return_value=mock_request.json.return_value)
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.client.port = 12345
    tenant_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    whatsapp_account_id = uuid4()
    pending_message_id = f"wamid.{uuid4()}"
    withdrawn_message_id = f"wamid.{uuid4()}"
    mock_request.json = AsyncMock(return_value={
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919876543210",
                        "id": pending_message_id,
                        "type": "text",
                        "text": {"body": "Hello"},
                        "timestamp": "1234567890"
                    }],
                    "metadata": {"phone_number_id": "12345", "display_phone_number": "12345"},
                    "contacts": [{"profile": {"name": "Test User"}}]
                }
            }]
        }]
    })
    # Mock DB Client
    with patch("backend.api.webhook.db_client") as mock_db, \
         patch("backend.api.webhook.customer_service") as mock_customer_service:
        # Scenario 1: Pending Consent (New User)
        mock_db.get_tenant_by_wa_phone_number_id = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "tenant_slug": "test-tenant",
            "tenant_status": "trial",
            "whatsapp_account_id": whatsapp_account_id,
        })
        mock_db.get_active_config_version = AsyncMock(return_value=1)
        mock_db.get_tenant_config = AsyncMock(return_value={"config_json": {}})
        # get_or_create_customer returns pending status
        mock_customer_service.get_or_create_customer = AsyncMock(return_value={
            "id": customer_id, "consent_status": "pending"
        })
        mock_db.get_or_create_conversation = AsyncMock(return_value={
            "id": conversation_id
        })
        mock_db.get_whatsapp_account_id = AsyncMock(return_value=whatsapp_account_id)
        mock_db.get_next_sequence_number = AsyncMock(return_value=1)
        with patch("backend.api.webhook.burst_buffer") as mock_burst:
            mock_burst.add_message = AsyncMock()
            # Execute webhook
            response = await receive_webhook(mock_request, valid=True)
            assert response["success"] is True
            assert response["data"]["status"] == "queued"
            assert response["data"]["count"] == 1
            # Message is buffered despite pending consent because only withdrawn
            # consent is explicitly blocked by the current webhook flow.
            mock_burst.add_message.assert_called_once()
    # Scenario 2: Withdrawn Consent
    mock_request.json = AsyncMock(return_value={
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919876543210",
                        "id": withdrawn_message_id,
                        "type": "text",
                        "text": {"body": "Hello"},
                        "timestamp": "1234567890"
                    }],
                    "metadata": {"phone_number_id": "12345", "display_phone_number": "12345"},
                    "contacts": [{"profile": {"name": "Test User"}}]
                }
            }]
        }]
    })
    with patch("backend.api.webhook.db_client") as mock_db2, \
         patch("backend.api.webhook.customer_service") as mock_customer_service2:
        mock_db2.get_tenant_by_wa_phone_number_id = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "tenant_slug": "test-tenant",
            "tenant_status": "active",
            "whatsapp_account_id": whatsapp_account_id,
        })
        mock_db2.get_active_config_version = AsyncMock(return_value=1)
        mock_db2.get_tenant_config = AsyncMock(return_value={"config_json": {}})
        # get_or_create_customer returns withdrawn status
        mock_customer_service2.get_or_create_customer = AsyncMock(return_value={
            "id": customer_id, "consent_status": "withdrawn"
        })
        with patch("backend.api.webhook.burst_buffer") as mock_burst2:
            mock_burst2.add_message = AsyncMock()
            response = await receive_webhook(mock_request, valid=True)
            assert response["success"] is True
            assert response["data"]["status"] == "queued"
            assert response["data"]["count"] == 0
            # Withdrawn consent blocks buffering entirely.
            mock_burst2.add_message.assert_not_called()
@pytest.mark.anyio
async def test_webhook_rejects_missing_whatsapp_account_mapping():
    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value={
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919876543210",
                        "id": "wamid.unknown-account",
                        "type": "text",
                        "text": {"body": "Hello"},
                        "timestamp": "1234567890"
                    }],
                    "metadata": {"phone_number_id": "unknown-phone-id", "display_phone_number": "12345"},
                    "contacts": [{"profile": {"name": "Test User"}}]
                }
            }]
        }]
    })
    tenant_id = uuid4()
    with patch("backend.api.webhook.db_client") as mock_db, \
         patch("backend.api.webhook.customer_service") as mock_customer_service:
        mock_db.get_tenant_by_wa_phone_number_id = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "tenant_slug": "test-tenant",
            "tenant_status": "active",
            "whatsapp_account_id": None,
        })
        mock_db.get_whatsapp_account_id = AsyncMock(return_value=None)
        mock_customer_service.get_or_create_customer = AsyncMock()
        mock_db.get_or_create_conversation = AsyncMock()
        with patch("backend.api.webhook.burst_buffer") as mock_burst:
            mock_burst.add_message = AsyncMock()
            with pytest.raises(HTTPException) as exc_info:
                await receive_webhook(mock_request, valid=True)
            assert exc_info.value.status_code == 404
            assert "no account mapping" in exc_info.value.detail
            mock_customer_service.get_or_create_customer.assert_not_called()
            mock_db.get_or_create_conversation.assert_not_called()
            mock_burst.add_message.assert_not_called()
