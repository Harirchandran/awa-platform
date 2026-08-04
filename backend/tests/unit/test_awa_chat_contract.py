import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.awa_chat_security import hash_public_chat_token
from backend.api.dependencies import get_current_user
from backend.schemas.canonical_message import (
    CanonicalMessage,
    ChannelType,
    MessageDirection,
    MessageType,
    SourceType,
)
from backend.api.routes import public_chat
from backend.core.redis_client import claim_otp_challenge, restore_otp_challenge_after_enqueue_failure
from backend.db.client import PublicChatTurnIntakeOutcome
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000099")


def _intake_outcome(*, created=True):
    return PublicChatTurnIntakeOutcome(
        created=created, message_id=uuid4(), event_id=uuid4(), provider_message_id="awa:test",
        conversation_sequence_number=1, config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received" if created else "ignored",
        security_disposition="none", trace_id=uuid4(),
    )


def _pending_booking_state(*, args_hash="a" * 24):
    return {
        "config_version": 1,
        "state": {
            "workflow_state": "COLLECTING_DATA",
            "action_ledger": {
                "current_task": "booking",
                "task_status": "awaiting_confirmation",
                "pending_action": {
                    "tool_key": "booking.execute",
                    "args": {"service_name": "Dental Cleaning"},
                    "args_hash": args_hash,
                    "action_nonce": "c" * 32,
                    "requires_confirmation": True,
                    "requires_verified_customer": True,
                    "verification_action": "booking_commitment",
                },
            },
        },
    }
async def override_user():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}
class FailingRedis:
    async def incr(self, key):
        raise RuntimeError("redis down")
    async def aclose(self):
        return None


class AtomicOtpRedis:
    """Contention-capable fake implementing the two production Lua contracts."""
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.lock = asyncio.Lock()

    async def eval(self, script, key_count, *parts):
        keys = parts[:key_count]
        args = parts[key_count:]
        async with self.lock:
            if "local challenge" in script:
                challenge_key, claim_key = keys
                expected, claim_value, claim_ttl_ms = args
                challenge = self.values.get(challenge_key)
                if challenge is None or claim_key in self.values:
                    return [0, 0]
                if challenge != expected:
                    return [-1, 0]
                remaining = self.ttls.get(challenge_key, 60_000)
                self.values[claim_key] = claim_value
                self.ttls[claim_key] = int(claim_ttl_ms)
                self.values.pop(challenge_key, None)
                self.ttls.pop(challenge_key, None)
                return [1, remaining]
            challenge_key, claim_key = keys
            expected, claim_value, remaining_ttl_ms = args
            if self.values.get(claim_key) != claim_value or challenge_key in self.values:
                return 0
            self.values.pop(claim_key, None)
            self.ttls.pop(claim_key, None)
            self.values[challenge_key] = expected
            self.ttls[challenge_key] = int(remaining_ttl_ms)
            return 1
def test_canonical_message_supports_awa_chat_without_whatsapp_account():
    message = CanonicalMessage(
        event_id=uuid4(),
        source_type=SourceType.AWA_CHAT,
        channel_type=ChannelType.AWA_CHAT,
        channel_account_id=uuid4(),
        channel_session_id="browser-session",
        tenant_id=TENANT_ID,
        whatsapp_account_id=None,
        conversation_id=uuid4(),
        conversation_sequence_number=1,
        customer_id=uuid4(),
        config_version=1,
        worker_metadata={"worker_version": "test"},
        message={
            "message_id": uuid4(),
            "provider_message_id": "awa:test:1",
            "direction": MessageDirection.INBOUND,
            "timestamp": datetime.now(timezone.utc),
            "message_type": MessageType.TEXT,
            "text": "hello",
        },
        processing={"first_received_at": datetime.now(timezone.utc)},
        trace={"trace_id": uuid4()},
    )
    assert message.source_type == SourceType.AWA_CHAT
    assert message.channel_type == ChannelType.AWA_CHAT
    assert message.whatsapp_account_id is None
@pytest.mark.anyio
async def test_public_chat_rate_limit_fails_closed_in_production(monkeypatch):
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.5"))
    monkeypatch.setattr(public_chat.settings, "ENV", "production")
    monkeypatch.setattr("backend.core.redis_client.redis.from_url", lambda *args, **kwargs: FailingRedis())
    with pytest.raises(HTTPException) as exc:
        await public_chat._check_public_rate_limit(request, "session:test")
    assert exc.value.status_code == 503
    assert "temporarily unavailable" in exc.value.detail
@pytest.mark.anyio
async def test_public_chat_rate_limit_uses_memory_fallback_outside_production(monkeypatch):
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.5"))
    called = {}
    def fake_memory_fallback(req, key):
        called["host"] = req.client.host
        called["key"] = key
    monkeypatch.setattr(public_chat.settings, "ENV", "local")
    monkeypatch.setattr("backend.core.redis_client.redis.from_url", lambda *args, **kwargs: FailingRedis())
    monkeypatch.setattr(public_chat, "_check_memory_rate_limit", fake_memory_fallback)
    await public_chat._check_public_rate_limit(request, "session:test")
    assert called == {"host": "203.0.113.5", "key": "session:test"}
def test_create_awa_chat_link_uses_authenticated_tenant():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    link_id = uuid4()
    with patch("backend.api.routes.awa_chat.db_client") as mock_db:
        mock_db.get_tenant = AsyncMock(return_value={
            "id": TENANT_ID,
            "name": "A Salon",
            "status": "active",
        })
        mock_db.create_public_chat_link = AsyncMock(return_value={
            "id": link_id,
            "tenant_id": TENANT_ID,
            "slug": "a-salon-abc123",
            "display_name": "A Salon",
            "status": "active",
            "scan_count": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "disabled_at": None,
            "last_opened_at": None,
        })
        response = client.post("/api/v1/awa-chat/links", json={"display_name": "A Salon"})
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["tenant_id"] == str(TENANT_ID)
    assert payload["data"]["public_url"].endswith("/chat/a-salon-abc123")
    mock_db.create_public_chat_link.assert_awaited_once()
    assert mock_db.create_public_chat_link.await_args.kwargs["tenant_id"] == TENANT_ID
    created_kwargs = mock_db.create_public_chat_link.await_args.kwargs
    assert len(created_kwargs["slug"].rsplit("-", 1)[-1]) >= 20
    assert created_kwargs["token_hash"] == hash_public_chat_token(created_kwargs["slug"], "link")
def test_public_message_creates_awa_chat_canonical_not_whatsapp():
    client = TestClient(app)
    session_id = uuid4()
    link_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._enqueue_for_worker", new=AsyncMock(return_value="queued")) as enqueue:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "link_id": link_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        mock_db.create_or_get_public_chat_turn_intake = AsyncMock(return_value=_intake_outcome())
        mock_db.mark_public_chat_turn_queued = AsyncMock(return_value=True)
        mock_db.get_active_config_version = AsyncMock(return_value=1)
        mock_db.get_next_sequence_number = AsyncMock(return_value=1)
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "Need haircut appointment",
                "client_message_id": "client-1",
                "tenant_id": str(uuid4()),
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    mock_db.create_or_get_public_chat_turn_intake.assert_awaited_once()
    assert mock_db.create_or_get_public_chat_turn_intake.await_args.kwargs["tenant_id"] == TENANT_ID
    canonical = enqueue.await_args.args[0]
    assert canonical.channel_type == ChannelType.AWA_CHAT
    assert canonical.source_type == SourceType.AWA_CHAT
    assert canonical.whatsapp_account_id is None
def test_duplicate_public_message_does_not_enqueue_or_allocate_sequence():
    client = TestClient(app)
    session_id = uuid4()
    link_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._enqueue_for_worker", new=AsyncMock(return_value="queued")) as enqueue:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "link_id": link_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        mock_db.create_or_get_public_chat_turn_intake = AsyncMock(return_value=_intake_outcome(created=False))
        mock_db.get_active_config_version = AsyncMock()
        mock_db.get_next_sequence_number = AsyncMock()
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "Need haircut appointment",
                "client_message_id": "client-dup",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "duplicate"
    assert payload["data"]["processing_status"] == "ignored"
    assert payload["data"]["duplicate"] is True
    mock_db.get_active_config_version.assert_not_awaited()
    mock_db.get_next_sequence_number.assert_not_awaited()
    enqueue.assert_not_awaited()
def test_public_message_accepts_800_characters():
    client = TestClient(app)
    session_id = uuid4()
    link_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._enqueue_for_worker", new=AsyncMock(return_value="queued")):
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "link_id": link_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        mock_db.create_or_get_public_chat_turn_intake = AsyncMock(return_value=_intake_outcome())
        mock_db.mark_public_chat_turn_queued = AsyncMock(return_value=True)
        mock_db.get_active_config_version = AsyncMock(return_value=1)
        mock_db.get_next_sequence_number = AsyncMock(return_value=1)
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "x" * 800,
                "client_message_id": "client-800",
            },
        )
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_db.create_or_get_public_chat_turn_intake.assert_awaited_once()
def test_public_message_rejects_801_characters():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock()
        mock_db.save_message = AsyncMock()
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "x" * 801,
                "client_message_id": "client-801",
            },
        )
    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "800 characters" in payload["error"]["message"]
    mock_db.get_public_chat_session.assert_not_awaited()
    mock_db.save_message.assert_not_awaited()
def test_public_chat_contact_capture_updates_customer():
    client = TestClient(app)
    session_id = uuid4()
    customer_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat.customer_service") as mock_customer_service:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": customer_id,
            "conversation_id": uuid4(),
            "tenant_status": "active",
        })
        mock_customer_service.update_customer = AsyncMock(return_value=True)
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/contact",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "email": "customer@example.com",
                "phone": "+1 555 0100",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["email_saved"] is True
    mock_customer_service.update_customer.assert_awaited_once()
    kwargs = mock_customer_service.update_customer.await_args.kwargs
    assert kwargs["customer_id"] == customer_id
    assert kwargs["email"] == "customer@example.com"
    assert "phone_number" not in kwargs
    assert "consent_status" not in kwargs
    assert kwargs["metadata"]["public_chat_contact"]["phone"] == "+15550100"
    assert kwargs["metadata"]["public_chat_contact"]["phone_verified"] is False
    assert kwargs["metadata"]["public_chat_contact"]["whatsapp_opted_in"] is False


def test_public_chat_contact_capture_phone_only_stores_unverified_metadata():
    client = TestClient(app)
    session_id = uuid4()
    customer_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat.customer_service") as mock_customer_service:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": customer_id,
            "conversation_id": uuid4(),
            "tenant_status": "active",
        })
        mock_customer_service.update_customer = AsyncMock(return_value=True)
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/contact",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "phone": "+1 555 0100",
                "whatsapp_opted_in": True,
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["phone_saved"] is True
    kwargs = mock_customer_service.update_customer.await_args.kwargs
    assert kwargs["customer_id"] == customer_id
    assert "phone_number" not in kwargs
    assert "consent_status" not in kwargs
    assert kwargs["metadata"]["public_chat_contact"] == {
        "phone": "+15550100",
        "email": None,
        "phone_verified": False,
        "email_verified": False,
        "whatsapp_opted_in": True,
        "source": "public_chat_contact_form",
    }
def test_public_chat_contact_capture_rejects_invalid_email():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat.customer_service") as mock_customer_service:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": uuid4(),
            "tenant_status": "active",
        })
        mock_customer_service.update_customer = AsyncMock()
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/contact",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "email": "not-an-email",
            },
        )
    assert response.status_code == 400
    assert "valid email" in response.json()["error"]["message"]
    mock_customer_service.update_customer.assert_not_awaited()
def test_public_chat_contact_capture_rejects_short_phone():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat.customer_service") as mock_customer_service:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": uuid4(),
            "tenant_status": "active",
        })
        mock_customer_service.update_customer = AsyncMock()
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/contact",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "phone": "123",
            },
        )
    assert response.status_code == 400
    assert "valid phone" in response.json()["error"]["message"]
    mock_customer_service.update_customer.assert_not_awaited()
def test_public_chat_end_marks_session_closed():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": uuid4(),
            "tenant_status": "active",
        })
        mock_db.update_public_chat_session_status = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "status": "closed",
        })
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/end",
            json={"session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "closed"
    mock_db.update_public_chat_session_status.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        session_id=session_id,
        status="closed",
    )
def test_public_chat_messages_get_rejects_query_token():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock()
        response = client.get(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            params={"session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz"},
        )
    assert response.status_code == 405
    mock_db.get_public_chat_session.assert_not_awaited()
def test_public_chat_messages_get_rejects_header_token():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock()
        response = client.get(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            headers={"X-AWA-Chat-Session": "chat_test_token_abcdefghijklmnopqrstuvwxyz"},
        )
    assert response.status_code == 405
    mock_db.get_public_chat_session.assert_not_awaited()
def test_public_chat_message_list_exposes_latest_quick_replies_and_marks_stale():
    client = TestClient(app)
    session_id = uuid4()
    conversation_id = uuid4()
    old_message_id = uuid4()
    new_message_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        mock_db.get_conversation = AsyncMock(return_value={
            "status": "active",
        })
        mock_db.list_messages = AsyncMock(return_value={
            "items": [
                {
                    "id": new_message_id,
                    "direction": "outbound",
                    "message_type": "interactive",
                    "message_text": "Confirm details",
                    "raw_payload": {
                        "type": "button",
                        "action": {
                            "buttons": [
                                {"type": "reply", "reply": {"id": "confirm", "title": "Confirm"}},
                                {"type": "reply", "reply": {"id": "change", "title": "Change"}},
                            ],
                        },
                    },
                    "processing_status": None,
                    "delivery_status": None,
                    "created_at": datetime.now(timezone.utc),
                    "channel_type": "awa_chat",
                },
                {
                    "id": old_message_id,
                    "direction": "outbound",
                    "message_type": "interactive",
                    "message_text": "Old choices",
                    "raw_payload": {
                        "type": "button",
                        "action": {
                            "buttons": [
                                {"type": "reply", "reply": {"id": "old", "title": "Old"}},
                            ],
                        },
                    },
                    "processing_status": None,
                    "delivery_status": None,
                    "created_at": datetime.now(timezone.utc),
                    "channel_type": "awa_chat",
                },
            ],
        })
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages/list",
            json={"session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    old_message, new_message = payload["data"]
    assert old_message["id"] == str(old_message_id)
    assert old_message["quick_replies"] == ["Old"]
    assert old_message["quick_replies_stale"] is True
    assert old_message["quick_replies_state"] == "expired"
    assert new_message["id"] == str(new_message_id)
    assert new_message["quick_replies"] == ["Confirm", "Change"]
    assert new_message["quick_replies_stale"] is False
    assert new_message["quick_replies_state"] == "active"
def test_public_chat_security_config_flags():
    config = {
        "security": {
            "public_chat_captcha_required": True,
            "customer_otp_required_for_commitments": True,
        }
    }
    assert public_chat._requires_public_chat_captcha(config) is True
    assert public_chat._requires_customer_otp(config, "booking") is True
    assert public_chat._requires_customer_otp(config, "order") is True
    assert public_chat._requires_customer_otp(config, "payments") is True
    assert public_chat._requires_customer_otp(config, "faq") is False
def test_public_chat_confirmation_detection_is_strict():
    assert public_chat._is_commitment_confirmation("Confirm") is True
    assert public_chat._is_commitment_confirmation("yes") is True
    assert public_chat._is_commitment_confirmation("I have a question before confirming") is False


def test_conversation_session_config_version_accepts_matching_authorities():
    state = _pending_booking_state()
    assert public_chat._conversation_session_config_version({"state_json": state}) == 1
    assert public_chat._conversation_session_config_version({"config_version": 1, "state_json": state}) == 1


@pytest.mark.parametrize("value", [None, 0, -1, True, False, "zero", "1.0", "-1", "0"])
def test_conversation_session_config_version_rejects_missing_or_invalid(value):
    state_json = {} if value is None else {"config_version": value}
    conversation_session = {"state_json": state_json}
    assert public_chat._conversation_session_config_version(conversation_session) is None
    assert public_chat._pending_action_binding(
        _pending_booking_state()["state"]["action_ledger"]["pending_action"],
        session={"id": uuid4(), "tenant_id": TENANT_ID, "conversation_id": uuid4()},
        conversation_session=conversation_session,
    ) is None


@pytest.mark.anyio
async def test_conflicting_conversation_session_versions_fail_before_otp_claim(monkeypatch):
    session_id = uuid4()
    conversation_id = uuid4()
    session = {
        "id": session_id,
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
        "customer_id": uuid4(),
    }
    claim = AsyncMock()
    mark = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(public_chat, "_check_public_rate_limit", AsyncMock())
    monkeypatch.setattr(public_chat, "_get_active_session", AsyncMock(return_value=session))
    monkeypatch.setattr(public_chat, "claim_otp_challenge", claim)
    monkeypatch.setattr(public_chat, "_mark_session_verified_action", mark)
    monkeypatch.setattr(public_chat, "_enqueue_verified_pending_action_continuation", enqueue)
    mock_db = AsyncMock()
    mock_db.get_conversation_session = AsyncMock(return_value={
        "id": uuid4(),
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
        "config_version": 2,
        "state_json": _pending_booking_state(),
        "current_module": "booking",
    })
    monkeypatch.setattr(public_chat, "db_client", mock_db)

    with pytest.raises(HTTPException) as exc:
        await public_chat.verify_public_chat_otp(
            session_id,
            public_chat.PublicChatOtpVerifyRequest(
                session_token="chat_test_token_abcdefghijklmnopqrstuvwxyz",
                phone="+15550100",
                code="123456",
            ),
            SimpleNamespace(client=None),
        )

    assert exc.value.status_code == 409
    claim.assert_not_awaited()
    mark.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.anyio
async def test_public_chat_otp_gate_uses_pending_action_ledger():
    session_id = uuid4()
    conversation_id = uuid4()
    session = {
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
    }
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._get_public_config", new=AsyncMock(return_value={
             "security": {"customer_otp_required_for_commitments": True},
         })), \
         patch("backend.api.routes.public_chat._is_session_verified_for_action", new=AsyncMock(return_value=False)):
        mock_db.get_conversation_session = AsyncMock(return_value={
            "tenant_id": TENANT_ID,
            "conversation_id": conversation_id,
            "config_version": 1,
            "current_module": "booking",
            "current_intent": "book",
            "state_json": _pending_booking_state(),
        })
        mock_db.update_conversation_session = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await public_chat._require_otp_before_commitment(session_id, session, "Confirm")
    assert exc.value.status_code == 403
    assert "Verify your phone number" in exc.value.detail
    mock_db.update_conversation_session.assert_not_awaited()
@pytest.mark.anyio
async def test_public_chat_verified_commit_waits_for_server_continuation():
    session_id = uuid4()
    conversation_id = uuid4()
    session = {
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
    }
    mock_sm = AsyncMock()
    mock_sm.invalidate_cache = AsyncMock()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._get_public_config", new=AsyncMock(return_value={
             "security": {"customer_otp_required_for_commitments": True},
         })), \
         patch("backend.api.routes.public_chat._is_session_verified_for_action", new=AsyncMock(return_value=True)), \
         patch("backend.api.routes.public_chat._redis_get", new=AsyncMock(return_value="+15550100")), \
         patch("backend.pipeline.session_manager.session_manager", mock_sm):
        mock_db.get_conversation_session = AsyncMock(return_value={
            "tenant_id": TENANT_ID,
            "conversation_id": conversation_id,
            "config_version": 1,
            "current_module": "booking",
            "current_intent": "book",
            "state_json": _pending_booking_state(),
        })
        mock_db.update_conversation_session = AsyncMock(return_value=True)
        with pytest.raises(HTTPException) as exc:
            await public_chat._require_otp_before_commitment(session_id, session, "Confirm")
    assert exc.value.status_code == 403
    assert "Please wait" in exc.value.detail
    mock_db.update_conversation_session.assert_not_awaited()


@pytest.mark.anyio
async def test_public_chat_otp_gate_derives_missing_legacy_verification_action():
    session_id = uuid4()
    conversation_id = uuid4()
    session = {
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
    }
    state_json = _pending_booking_state()
    state_json["state"]["action_ledger"]["pending_action"].pop(
        "verification_action"
    )

    with patch("backend.api.routes.public_chat.db_client") as mock_db, patch(
        "backend.api.routes.public_chat._is_session_verified_for_action",
        new=AsyncMock(return_value=False),
    ):
        mock_db.get_conversation_session = AsyncMock(return_value={
            "tenant_id": TENANT_ID,
            "conversation_id": conversation_id,
            "config_version": 1,
            "current_module": "booking",
            "current_intent": "book",
            "state_json": state_json,
        })

        with pytest.raises(HTTPException) as exc:
            await public_chat._require_otp_before_commitment(
                session_id,
                session,
                "Confirm",
            )

    assert exc.value.status_code == 403
    assert "Verify your phone number" in exc.value.detail

@pytest.mark.anyio
async def test_public_chat_otp_verify_deletes_redis_keys(monkeypatch):
    client = TestClient(app)
    session_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()

    mock_db = AsyncMock()
    mock_db.get_public_chat_session = AsyncMock(return_value={
        "id": session_id,
        "tenant_id": TENANT_ID,
        "link_id": uuid4(),
        "customer_id": customer_id,
        "conversation_id": conversation_id,
        "tenant_status": "active",
        "visitor_name": "Asha",
    })
    mock_db.get_customer_by_phone = AsyncMock(return_value=None)
    state_record = {
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
        "config_version": 1,
        "current_module": "booking",
        "current_intent": "book",
        "state_json": _pending_booking_state(),
    }
    mock_db.get_conversation_session = AsyncMock(return_value=state_record)
    mock_db.update_conversation_session = AsyncMock()
    mock_db.get_next_sequence_number = AsyncMock(return_value=9)

    mock_customer_service = AsyncMock()
    mock_customer_service.get_customer_by_phone = AsyncMock(return_value=None)
    mock_customer_service.update_customer = AsyncMock(return_value=True)

    monkeypatch.setattr("backend.api.routes.public_chat.db_client", mock_db)
    monkeypatch.setattr("backend.api.routes.public_chat.customer_service", mock_customer_service)

    mock_get = AsyncMock()
    mock_set = AsyncMock()
    mock_delete = AsyncMock()

    monkeypatch.setattr(public_chat, "_redis_get", mock_get)
    monkeypatch.setattr(public_chat, "_redis_set", mock_set)
    monkeypatch.setattr(public_chat, "_redis_delete", mock_delete)
    claim = AsyncMock(side_effect=[("claimed", 60_000), ("consumed", 0)])
    monkeypatch.setattr(public_chat, "claim_otp_challenge", claim)

    enqueue = AsyncMock(return_value="queued")
    monkeypatch.setattr(public_chat, "_enqueue_for_worker", enqueue)

    with patch("backend.pipeline.session_manager.session_manager.invalidate_cache", new=AsyncMock()):
        sent = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/send",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "phone": "+1 555 0100",
                "action": "payments_commitment",
            },
        )
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/verify",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "phone": "+1 555 0100",
                "code": "123456",
                "action": "payments_commitment",
            },
        )

    assert sent.status_code == 200
    assert sent.json()["data"]["action"] == "booking_commitment"
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["verified"] is True
    assert response.json()["data"]["action"] == "booking_commitment"
    assert response.json()["data"]["processing_status"] == "verified"
    # OTP only establishes phone possession.  It must not enqueue an internal
    # continuation that would execute the pending action without an explicit
    # customer confirmation.
    enqueue.assert_not_awaited()
    challenge_key = mock_set.await_args_list[0].args[0]
    assert claim.await_args_list[0].kwargs["challenge_key"] == challenge_key
    assert public_chat._pending_action_binding(
        state_record["state_json"]["state"]["action_ledger"]["pending_action"],
        session={**mock_db.get_public_chat_session.return_value, "id": session_id},
        conversation_session=state_record,
    ) in challenge_key
    mock_db.get_active_config_version.assert_not_awaited()

    assert mock_delete.call_count == 1
    deleted_keys = [call.args[0] for call in mock_delete.call_args_list]
    assert any("attempts" in key for key in deleted_keys)

    replay = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/otp/verify",
        json={
            "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
            "phone": "+1 555 0100",
            "code": "123456",
            "action": "booking_commitment",
        },
    )
    assert replay.status_code == 409
    enqueue.assert_not_awaited()


def test_public_payload_cannot_declare_internal_authority_metadata():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._enqueue_for_worker", new=AsyncMock(return_value="queued")) as enqueue:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "link_id": uuid4(),
            "customer_id": uuid4(),
            "conversation_id": uuid4(),
            "tenant_status": "active",
        })
        mock_db.create_or_get_public_chat_turn_intake = AsyncMock(return_value=_intake_outcome())
        mock_db.mark_public_chat_turn_queued = AsyncMock(return_value=True)
        mock_db.get_active_config_version = AsyncMock(return_value=1)
        mock_db.get_next_sequence_number = AsyncMock(return_value=1)
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "ordinary customer text",
                "source_type": "internal_event",
                "event_type": "otp_verified_pending_action",
                "is_internal": True,
            },
        )
    assert response.status_code == 200
    canonical = enqueue.await_args.args[0]
    assert canonical.source_type == SourceType.AWA_CHAT
    assert canonical.event_type == "incoming_message"
    assert canonical.message.text == "ordinary customer text"


def test_reserved_auto_confirm_literal_is_rejected_without_enqueue_or_history_write():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db, \
         patch("backend.api.routes.public_chat._enqueue_for_worker", new=AsyncMock()) as enqueue:
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "__SYSTEM_AUTO_CONFIRM__",
            },
        )
    assert response.status_code == 400
    mock_db.save_message.assert_not_called()
    enqueue.assert_not_awaited()


def test_public_transcript_hides_internal_event_by_provider_metadata_not_text():
    client = TestClient(app)
    session_id = uuid4()
    conversation_id = uuid4()
    normal_id = uuid4()
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        mock_db.get_conversation = AsyncMock(return_value={"status": "active"})
        base = {
            "direction": "inbound",
            "message_type": "text",
            "raw_payload": {},
            "processing_status": None,
            "delivery_status": None,
            "created_at": datetime.now(timezone.utc),
            "channel_type": "awa_chat",
        }
        mock_db.list_messages = AsyncMock(return_value={"items": [
            {
                **base,
                "id": uuid4(),
                "provider_message_id": "awa-internal:otp_verified_pending_action:" + "a" * 24 + ":" + "b" * 32,
                "message_text": None,
            },
            {
                **base,
                "id": normal_id,
                "provider_message_id": "awa:customer:marker-like",
                "message_text": "I saw __SYSTEM_AUTO_CONFIRM__ in an old note",
            },
        ]})
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages/list",
            json={"session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz"},
        )
    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["id"] for item in items] == [str(normal_id)]
    assert "__SYSTEM_AUTO_CONFIRM__" in items[0]["message_text"]


@pytest.mark.anyio
async def test_stale_action_binding_cannot_mark_replacement_pending_action(monkeypatch):
    session_id = uuid4()
    session = {"id": session_id, "tenant_id": TENANT_ID, "conversation_id": uuid4()}
    original = _pending_booking_state(args_hash="a" * 24)["state"]["action_ledger"]["pending_action"]
    replacement_state = _pending_booking_state(args_hash="c" * 24)
    stale_binding = public_chat._pending_action_binding(
        original,
        session=session,
        conversation_session={"config_version": 1},
    )
    mock_db = AsyncMock()
    mock_db.get_conversation_session = AsyncMock(return_value={
        "config_version": 1,
        "current_module": "booking",
        "current_intent": "book",
        "state_json": replacement_state,
    })
    monkeypatch.setattr(public_chat, "db_client", mock_db)

    marked = await public_chat._mark_session_verified_action(
        session,
        "booking_commitment",
        "+15550100",
        action_binding=stale_binding,
        verification_ref="b" * 32,
        session_id=session_id,
        config_version=1,
    )

    assert marked is False
    mock_db.update_conversation_session.assert_not_awaited()

@pytest.mark.anyio
async def test_public_chat_rate_limit_uses_x_forwarded_for(monkeypatch):
    from starlette.datastructures import Headers

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    mock_redis.aclose = AsyncMock()

    monkeypatch.setattr(public_chat.redis, "from_url", lambda *args, **kwargs: mock_redis)

    req = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers=Headers({"x-forwarded-for": "185.120.3.4, 10.0.0.1"}),
    )

    await public_chat._check_public_rate_limit(req, "test_key")

    mock_redis.incr.assert_called_once_with("public-chat:185.120.3.4:test_key")


@pytest.mark.anyio
async def test_atomic_otp_challenge_allows_one_concurrent_continuation():
    fake = AtomicOtpRedis()
    challenge_key = "otp:challenge"
    claim_key = "otp:continuation"
    fake.values[challenge_key] = "123456"
    fake.ttls[challenge_key] = 45_000
    start = asyncio.Event()
    enqueued = []

    async def verify(ref):
        await start.wait()
        status, _ = await claim_otp_challenge(
            fake,
            challenge_key=challenge_key,
            expected_code="123456",
            claim_key=claim_key,
            claim_value=ref,
            claim_ttl_seconds=300,
        )
        if status == "claimed":
            enqueued.append(ref)
        return status

    tasks = [asyncio.create_task(verify("a" * 32)), asyncio.create_task(verify("b" * 32))]
    start.set()
    statuses = await asyncio.gather(*tasks)

    assert sorted(statuses) == ["claimed", "consumed"]
    assert len(enqueued) == 1
    assert fake.values[claim_key] == enqueued[0]
    assert challenge_key not in fake.values


@pytest.mark.anyio
async def test_enqueue_failure_restores_exact_otp_claim_for_one_safe_retry():
    fake = AtomicOtpRedis()
    fake.values["challenge"] = "123456"
    fake.ttls["challenge"] = 30_000
    status, remaining = await claim_otp_challenge(
        fake,
        challenge_key="challenge",
        expected_code="123456",
        claim_key="continuation",
        claim_value="a" * 32,
        claim_ttl_seconds=300,
    )
    assert status == "claimed"

    async def failed_enqueue():
        raise RuntimeError("queue unavailable")

    with pytest.raises(RuntimeError):
        try:
            await failed_enqueue()
        except RuntimeError:
            restored = await restore_otp_challenge_after_enqueue_failure(
                fake,
                challenge_key="challenge",
                expected_code="123456",
                claim_key="continuation",
                claim_value="a" * 32,
                remaining_ttl_ms=remaining,
            )
            raise
    retry, _ = await claim_otp_challenge(
        fake,
        challenge_key="challenge",
        expected_code="123456",
        claim_key="continuation",
        claim_value="b" * 32,
        claim_ttl_seconds=300,
    )
    duplicate, _ = await claim_otp_challenge(
        fake,
        challenge_key="challenge",
        expected_code="123456",
        claim_key="continuation",
        claim_value="c" * 32,
        claim_ttl_seconds=300,
    )

    assert restored is True
    assert retry == "claimed"
    assert duplicate == "consumed"
    assert fake.values["continuation"] == "b" * 32

def test_privacy():
    import inspect
    from backend.pipeline import worker
    from backend.realtime.public_chat_events import publish_public_chat_terminal_failure
    source = inspect.getsource(worker.handle_retry) + inspect.getsource(publish_public_chat_terminal_failure)
    for sentinel in ("PRIVATE_CUSTOMER_MESSAGE", "person@example.com", "+15550001111", "OTP-123456", "RAW_PROVIDER_FAILURE"):
        assert sentinel not in source
