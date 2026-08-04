from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import get_current_user
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000099")
async def override_user():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}
def test_list_escalation_knowledge_entries():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    entry_id = uuid4()
    with patch("backend.api.routes.knowledge.knowledge_service") as mock_db:
        mock_db.list_entries = AsyncMock(return_value=[{
            "id": entry_id,
            "tenant_id": TENANT_ID,
            "document_title": "Escalation answer - Asha",
            "chunk_text": "Escalation reason/question: Hours?\nTenant answer: We close at 8 PM.",
            "metadata": {"source": "tenant_escalation_reply", "enabled": True},
            "created_at": datetime.now(timezone.utc),
        }])
        response = client.get("/api/v1/knowledge-base?source=tenant_escalation_reply")
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"][0]["id"] == str(entry_id)
    mock_db.list_entries.assert_awaited_once()
    assert mock_db.list_entries.await_args.kwargs["tenant_id"] == TENANT_ID
    assert mock_db.list_entries.await_args.kwargs["source"] == "tenant_escalation_reply"
def test_update_knowledge_entry_reembeds_answer_and_preserves_tenant_scope():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    entry_id = uuid4()
    with patch("backend.api.routes.knowledge.knowledge_service") as mock_db, \
         patch("backend.api.routes.knowledge.rag_service") as mock_rag:
        mock_rag.embed_query = AsyncMock(return_value=[0.0] * 1536)
        mock_db.update_entry = AsyncMock(return_value={
            "id": entry_id,
            "tenant_id": TENANT_ID,
            "document_title": "Updated answer",
            "chunk_text": "Escalation reason/question: Hours?\nTenant answer: Updated answer text.",
            "metadata": {"source": "tenant_escalation_reply", "enabled": False},
            "created_at": datetime.now(timezone.utc),
        })
        response = client.patch(
            f"/api/v1/knowledge-base/{entry_id}",
            json={
                "document_title": "Updated answer",
                "chunk_text": "Escalation reason/question: Hours?\nTenant answer: Updated answer text.",
                "enabled": False,
            },
        )
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["metadata"]["enabled"] is False
    mock_rag.embed_query.assert_awaited_once()
    kwargs = mock_db.update_entry.await_args.kwargs
    assert kwargs["tenant_id"] == TENANT_ID
    assert kwargs["entry_id"] == entry_id
    assert kwargs["metadata_patch"] == {"enabled": False}
def test_create_manual_knowledge_entry_uses_ingestion_service():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    entry_id = uuid4()
    with patch("backend.api.routes.knowledge.knowledge_ingestion_service") as mock_ingestion:
        mock_ingestion.create_manual_entry = AsyncMock(return_value=[{
            "id": entry_id,
            "tenant_id": TENANT_ID,
            "document_title": "Parking policy",
            "chunk_text": "Parking is available behind the clinic.",
            "metadata": {"source": "manual", "enabled": True},
            "created_at": datetime.now(timezone.utc),
        }])
        response = client.post(
            "/api/v1/knowledge-base",
            json={
                "document_title": "Parking policy",
                "chunk_text": "Parking is available behind the clinic.",
                "source": "manual",
            },
        )
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == str(entry_id)
    mock_ingestion.create_manual_entry.assert_awaited_once_with(
        TENANT_ID,
        "Parking policy",
        "Parking is available behind the clinic.",
    )
def test_import_website_rejects_localhost_url():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    response = client.post(
        "/api/v1/knowledge-base/import-website",
        json={"url": "http://localhost:8000/private"},
    )
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 422
def test_upload_document_knowledge_uses_document_ingestion():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    entry_id = uuid4()
    with patch("backend.api.routes.knowledge.knowledge_ingestion_service") as mock_ingestion:
        mock_ingestion.create_document_entry = AsyncMock(return_value=[{
            "id": entry_id,
            "tenant_id": TENANT_ID,
            "document_title": "Clinic policy",
            "chunk_text": "Customers should arrive ten minutes early.",
            "metadata": {"source": "document", "enabled": True},
            "created_at": datetime.now(timezone.utc),
        }])
        response = client.post(
            "/api/v1/knowledge-base/upload-document",
            data={"title": "Clinic policy"},
            files={"file": ("policy.txt", b"Customers should arrive ten minutes early.", "text/plain")},
        )
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == str(entry_id)
    mock_ingestion.create_document_entry.assert_awaited_once_with(
        TENANT_ID,
        "Clinic policy",
        "Customers should arrive ten minutes early.",
        "policy.txt",
    )
def test_source_summary_requires_tenant_scope():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    with patch("backend.api.routes.knowledge.knowledge_service") as mock_db:
        mock_db.summarize_sources = AsyncMock(return_value=[{
            "source": "manual",
            "total_count": 2,
            "enabled_count": 2,
            "disabled_count": 0,
            "last_created_at": datetime.now(timezone.utc),
        }])
        response = client.get("/api/v1/knowledge-base/sources")
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    assert response.json()["data"][0]["source"] == "manual"
    mock_db.summarize_sources.assert_awaited_once_with(TENANT_ID)
def test_delete_knowledge_entry_requires_tenant_match():
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    entry_id = uuid4()
    with patch("backend.api.routes.knowledge.knowledge_service") as mock_db:
        mock_db.delete_entry = AsyncMock(return_value=True)
        response = client.delete(f"/api/v1/knowledge-base/{entry_id}")
    app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    assert response.json()["data"]["deleted"] is True
    mock_db.delete_entry.assert_awaited_once_with(TENANT_ID, entry_id)