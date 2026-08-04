from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import asyncpg
import pytest
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.db.client import DatabaseClient
MOCK_TENANT_ID = uuid4()
MOCK_USER_ID = uuid4()
async def override_get_current_user():
    return {
        "user_id": MOCK_USER_ID,
        "tenant_id": MOCK_TENANT_ID,
        "role": "tenant_admin",
    }
@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
def test_cost_summary_passes_date_objects_to_db(client):
    captured = {}
    async def fake_get_cost_daily(tenant_id, date_from, date_to):
        captured["tenant_id"] = tenant_id
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return [{
            "tenant_id": tenant_id,
            "cost_date": date_to,
            "ai_token_cost": 1.25,
            "compute_cost": 0.75,
            "message_count": 3,
            "ai_tokens_used": 100,
        }]
    with patch("backend.api.routes.costs.db_client") as mock_db:
        mock_db.get_cost_daily = AsyncMock(side_effect=fake_get_cost_daily)
        response = client.get("/api/v1/costs?days=7")
    assert response.status_code == 200
    assert captured["tenant_id"] == MOCK_TENANT_ID
    assert isinstance(captured["date_from"], date)
    assert isinstance(captured["date_to"], date)
    assert not isinstance(captured["date_from"], str)
    assert response.json()["data"]["total_spend"] == 2.0
    assert len(response.json()["data"]["items"]) == 7
def test_cost_summary_zero_fills_missing_days(client):
    async def fake_get_cost_daily(tenant_id, date_from, date_to):
        return [{
            "tenant_id": tenant_id,
            "cost_date": date_from,
            "ai_token_cost": 0.5,
            "compute_cost": 0.0,
            "message_count": 1,
            "ai_tokens_used": 10,
        }]
    with patch("backend.api.routes.costs.db_client") as mock_db:
        mock_db.get_cost_daily = AsyncMock(side_effect=fake_get_cost_daily)
        response = client.get("/api/v1/costs?days=3")
    data = response.json()["data"]
    assert response.status_code == 200
    assert len(data["items"]) == 3
    assert data["items"][0]["cost_usd"] == 0.5
    assert data["items"][1]["cost_usd"] == 0.0
    assert data["items"][2]["message_count"] == 0
    assert data["total_messages"] == 1
def test_db_client_coerces_iso_date_strings_for_cost_queries():
    client = DatabaseClient("postgresql://unused")
    assert client._coerce_date("2026-04-23") == date(2026, 4, 23)
    assert client._coerce_date(datetime(2026, 4, 23, 10, 30)) == date(2026, 4, 23)
def test_reminder_schema_errors_return_service_unavailable(client):
    with patch("backend.api.routes.reminders.db_client") as mock_db:
        mock_db.list_reminder_rules = AsyncMock(
            side_effect=asyncpg.UndefinedTableError("relation reminder_rules does not exist")
        )
        response = client.get("/api/v1/reminder-rules")
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "HTTP_503"
    assert "Reminder storage is unavailable" in body["error"]["message"]