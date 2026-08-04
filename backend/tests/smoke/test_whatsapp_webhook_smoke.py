import pytest
from uuid import uuid4
import json

from backend.api.app import app
from backend.db.client import db_client
from backend.pipeline.worker import process_message
from backend.pipeline.burst_buffer import burst_buffer
from backend.core.states import ConversationState

pytestmark = pytest.mark.anyio

async def test_whatsapp_webhook_normalization_and_routing(api_client, tenant_factory, mock_redis_globally, mock_llm_globally):
    # 1. Create tenant
    tenant_info = await tenant_factory(name="WhatsApp Clinic", plan_code="enterprise")
    tenant_id = tenant_info["tenant_id"]

    # 2. Register WhatsApp Account for the tenant
    phone_id = f"wa-phone-id-{uuid4().hex[:6]}"
    phone_number = f"+1555{uuid4().hex[:7]}"
    wa_account = await db_client.create_whatsapp_account(
        tenant_id=tenant_id,
        phone_number=phone_number,
        phone_number_id=phone_id,
        display_name="Clinic WA",
        verification_status="connected",
        access_token="mock-wa-access-token"
    )
    wa_account_id = wa_account["id"]

    try:
        # 3. Post a mock inbound WhatsApp payload to webhook
        webhook_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "wa_biz_acc_123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": phone_number.replace("+", ""),
                                    "phone_number_id": phone_id
                                },
                                "contacts": [
                                    {
                                        "profile": {
                                            "name": "Wendy Webhook"
                                        },
                                        "wa_id": "15551112222"
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "15551112222",
                                        "id": f"wamid.{uuid4().hex[:16]}",
                                        "timestamp": "1777777777",
                                        "type": "text",
                                        "text": {
                                            "body": "Hello from WhatsApp webhook!"
                                        }
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }

        # Signature bypass should allow this without hub signature headers
        resp = await api_client.post(
            "/api/v1/webhook",
            json=webhook_payload
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "queued"

        # 4. Verify message got placed in the burst buffer in Redis
        # The key suffix is {tenant_id}:{customer_phone}
        active_keys = await mock_redis_globally.smembers("burst:active")
        assert len(active_keys) == 1
        key_suffix = list(active_keys)[0]
        assert str(tenant_id) in key_suffix
        assert "15551112222" in key_suffix

        # 5. Flush the burst buffer manually to push message to main queue
        combined_json = await burst_buffer.flush_burst(key_suffix)
        assert combined_json is not None

        # Verify the message is now in the main queue
        assert "message_queue" in mock_redis_globally.data
        assert len(mock_redis_globally.data["message_queue"]) == 1
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        # 6. Process message using the worker
        success = await process_message(raw_msg)
        assert success is True

        # 7. Verify conversation and customer were persisted in DB under the correct tenant
        async with db_client.acquire() as conn:
            # Check customer
            customer = await conn.fetchrow(
                "SELECT * FROM customers WHERE tenant_id = $1 AND phone_number = $2",
                tenant_id, "15551112222"
            )
            assert customer is not None
            assert customer["name"] == "Wendy Webhook"

            # Check conversation
            conv = await conn.fetchrow(
                "SELECT * FROM conversations WHERE tenant_id = $1 AND customer_id = $2",
                tenant_id, customer["id"]
            )
            assert conv is not None
            assert conv["channel_type"] == "whatsapp"
            assert conv["channel_account_id"] == wa_account_id

            # Check message
            msg = await conn.fetchrow(
                "SELECT * FROM messages WHERE tenant_id = $1 AND conversation_id = $2 AND direction = 'inbound'",
                tenant_id, conv["id"]
            )
            assert msg is not None
            assert "hello from whatsapp" in msg["message_text"].lower()

    finally:
        # Clean up whatsapp account manually
        async with db_client.acquire() as conn:
            await conn.execute("DELETE FROM whatsapp_accounts WHERE id = $1", wa_account_id)


async def test_whatsapp_webhook_mismatch_rejection(api_client):
    # Post a payload with an unknown phone_number_id
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "wa_biz_acc_123",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15559999999",
                                "phone_number_id": "unknown-phone-id"
                            },
                            "messages": [
                                {
                                    "from": "15551112222",
                                    "id": "wamid.mismatched123",
                                    "timestamp": "1777777777",
                                    "type": "text",
                                    "text": {
                                        "body": "Hello!"
                                    }
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

    resp = await api_client.post(
        "/api/v1/webhook",
        json=webhook_payload
    )
    # Unknown phone_number_id maps to no tenant, so it must return 404
    assert resp.status_code == 404
