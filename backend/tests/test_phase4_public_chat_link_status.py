from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.awa_chat_security import hash_public_chat_token
from backend.api.dependencies import get_current_user
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000099")
async def override_user():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}
def test_tenant_link_list_marks_hash_mismatch_as_needs_rotation():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    link_id = uuid4()
    with patch("backend.api.routes.awa_chat.db_client") as mock_db:
        mock_db.list_public_chat_links = AsyncMock(return_value=[{
            "id": link_id,
            "tenant_id": TENANT_ID,
            "slug": "old-dead-link",
            "display_name": "Old Link",
            "status": "active",
            "token_hash": "not-the-current-secret-hash",
            "metadata": {"token_binding": "legacy"},
            "scan_count": 4,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "disabled_at": None,
            "last_opened_at": None,
        }])
        response = client.get("/api/v1/awa-chat/links")
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["status"] == "active"
    assert data["public_status"] == "needs_rotation"
    assert data["is_publicly_accessible"] is False
    assert "token_hash" not in data
    assert "metadata" not in data
def test_tenant_link_list_marks_current_hash_as_publicly_accessible():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    slug = "current-link"
    with patch("backend.api.routes.awa_chat.db_client") as mock_db:
        mock_db.list_public_chat_links = AsyncMock(return_value=[{
            "id": uuid4(),
            "tenant_id": TENANT_ID,
            "slug": slug,
            "display_name": "Current Link",
            "status": "active",
            "token_hash": hash_public_chat_token(slug, "link"),
            "metadata": {"token_binding": "slug"},
            "scan_count": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "disabled_at": None,
            "last_opened_at": None,
        }])
        response = client.get("/api/v1/awa-chat/links")
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["public_status"] == "active"
    assert data["is_publicly_accessible"] is True
    assert data["status_reason"] is None
def test_tenant_link_resume_rejects_hash_mismatch():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    link_id = uuid4()
    with patch("backend.api.routes.awa_chat.db_client") as mock_db:
        mock_db.list_public_chat_links = AsyncMock(return_value=[{
            "id": link_id,
            "tenant_id": TENANT_ID,
            "slug": "old-dead-link",
            "display_name": "Old Link",
            "status": "paused",
            "token_hash": "not-the-current-secret-hash",
            "metadata": {"token_binding": "legacy"},
            "scan_count": 4,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "disabled_at": None,
            "last_opened_at": None,
        }])
        mock_db.update_public_chat_link = AsyncMock()
        response = client.patch(f"/api/v1/awa-chat/links/{link_id}", json={"status": "active"})
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 409
    assert "needs a replacement" in response.json()["error"]["message"]
    mock_db.update_public_chat_link.assert_not_awaited()
def test_tenant_link_resume_allows_current_hash():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    link_id = uuid4()
    slug = "current-paused-link"
    paused_link = {
        "id": link_id,
        "tenant_id": TENANT_ID,
        "slug": slug,
        "display_name": "Current Paused Link",
        "status": "paused",
        "token_hash": hash_public_chat_token(slug, "link"),
        "metadata": {"token_binding": "slug"},
        "scan_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "disabled_at": datetime.now(timezone.utc),
        "last_opened_at": None,
    }
    active_link = {**paused_link, "status": "active", "disabled_at": None}
    with patch("backend.api.routes.awa_chat.db_client") as mock_db:
        mock_db.list_public_chat_links = AsyncMock(return_value=[paused_link])
        mock_db.update_public_chat_link = AsyncMock(return_value=active_link)
        response = client.patch(f"/api/v1/awa-chat/links/{link_id}", json={"status": "active"})
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "active"
    assert data["public_status"] == "active"
    assert data["is_publicly_accessible"] is True
    mock_db.update_public_chat_link.assert_awaited_once()
def test_public_message_reports_delayed_processing_when_queue_unavailable():
    from backend.db.client import PublicChatTurnIntakeOutcome
    client = TestClient(app)
    session_id = uuid4()
    link_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    with (
        patch("backend.api.routes.public_chat.db_client") as mock_db,
        patch("backend.api.routes.public_chat._enqueue_for_worker", new=AsyncMock(return_value="received")),
    ):
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "link_id": link_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        mock_db.create_or_get_public_chat_turn_intake = AsyncMock(return_value=PublicChatTurnIntakeOutcome(
            created=True, message_id=uuid4(), event_id=uuid4(), provider_message_id="awa:test",
            conversation_sequence_number=1, config_version=1,
            message_created_at=datetime.now(timezone.utc), processing_status="received",
            security_disposition="none", trace_id=uuid4(),
        ))
        mock_db.get_active_config_version = AsyncMock(return_value=1)
        mock_db.get_next_sequence_number = AsyncMock(return_value=1)
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": "chat_test_token_abcdefghijklmnopqrstuvwxyz",
                "message_text": "Are you open today?",
                "client_message_id": "client-delayed-1",
            },
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "received"
    assert data["processing_status"] == "delayed"
    assert data["processing_code"] == "worker_degraded"
    assert data["safe_error"]["code"] == "worker_degraded"
    assert "processing is delayed" in data["safe_error"]["safe_message"]
    assert "Message saved" in data["processing_message"]

def test_rotate_awa_chat_link():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    link_id = uuid4()
    old_slug = "old-link-slug"

    existing_link = {
        "id": link_id,
        "tenant_id": TENANT_ID,
        "slug": old_slug,
        "display_name": "AWA Chat Link",
        "status": "active",
        "token_hash": hash_public_chat_token(old_slug, "link"),
        "metadata": {"token_binding": "slug"},
        "scan_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "disabled_at": None,
        "last_opened_at": None,
    }

    rotated_link = {
        **existing_link,
        "slug": "rotated-link-slug",
        "token_hash": "new-token-hash-value",
        "updated_at": datetime.now(timezone.utc),
    }

    with patch("backend.api.routes.awa_chat.db_client") as mock_db:
        mock_db.list_public_chat_links = AsyncMock(return_value=[existing_link])
        mock_db.rotate_public_chat_link = AsyncMock(return_value=rotated_link)

        response = client.post(f"/api/v1/awa-chat/links/{link_id}/rotate")

    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["slug"] == "rotated-link-slug"
    mock_db.rotate_public_chat_link.assert_called_once()
