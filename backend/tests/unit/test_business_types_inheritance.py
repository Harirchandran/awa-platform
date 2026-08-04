import pytest
import json
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.db.client import db_client
from backend.schemas.config import build_default_tenant_config
pytestmark = pytest.mark.anyio
@pytest.mark.asyncio
async def test_get_tenant_config_preserves_explicit_module_overrides():
    # Business type presets seed or intentionally apply modules, but active
    # config reads must not silently reapply presets over saved choices.
    tenant_id = uuid4()
    mock_config_json = {
        "business_name": "Test Salon",
        "business_type": "salon",
        "modules": {
            "booking": {"enabled": False, "fields": []},
            "order": {"enabled": True, "fields": []},
            "faq": {"enabled": False, "fields": []},
        }
    }
    mock_config_row = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "version": 1,
        "config_json": json.dumps(mock_config_json),
        "is_active": True
    }
    # Mock connection and fetchrow
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_config_row)
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    with patch.object(db_client, "acquire", return_value=mock_acquire):
        res = await db_client.get_tenant_config(tenant_id)
        assert res is not None
        config_json = res["config_json"]
        assert isinstance(config_json, dict)
        # Explicit saved flags should win even when business_type has a preset.
        assert config_json["modules"]["booking"]["enabled"] is False
        assert config_json["modules"]["faq"]["enabled"] is False
        assert config_json["modules"]["order"]["enabled"] is True
        assert "human_handoff" not in config_json["modules"]
        # No business type lookup query should be run during config reads.
        assert mock_conn.fetchrow.call_count == 1
@pytest.mark.asyncio
async def test_get_tenant_config_bypasses_other_general():
    tenant_id = uuid4()
    mock_config_json = {
        "business_name": "Generic Business",
        "business_type": "general",
        "modules": {
            "booking": {"enabled": True, "fields": []},
            "order": {"enabled": True, "fields": []},
        }
    }
    mock_config_row = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "version": 1,
        "config_json": json.dumps(mock_config_json),
        "is_active": True
    }
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_config_row)
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    with patch.object(db_client, "acquire", return_value=mock_acquire):
        res = await db_client.get_tenant_config(tenant_id)
        assert res is not None
        config_json = res["config_json"]
        assert isinstance(config_json, dict)
        # Modules should NOT be mutated because "general" is bypassed
        assert config_json["modules"]["booking"]["enabled"] is True
        assert config_json["modules"]["order"]["enabled"] is True
        # No business type lookup query should be run (only 1 call to fetchrow for the config itself)
        assert mock_conn.fetchrow.call_count == 1
@pytest.mark.asyncio
async def test_register_flow_fetches_business_type_modules():
    client = TestClient(app)
    # Mock business type query during registration
    mock_business_type_row = {
        "enabled_modules": json.dumps(["booking", "faq"])
    }
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=None)  # No existing user, no existing slug
    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)
    def fetchrow_side_effect(query, *args):
        if "business_types" in query:
            return mock_business_type_row
        return None
    mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    # Override settings to use mock auth so we don't hit Supabase
    with patch("backend.api.routes.auth.settings") as settings_mock, \
         patch("backend.api.routes.auth.db_client.acquire", return_value=mock_acquire), \
         patch("backend.api.routes.auth.build_default_tenant_config", wraps=build_default_tenant_config) as build_config_spy:
        settings_mock.effective_auth_mode = "mock"
        payload = {
            "business_name": "Test Salon Registration",
            "email": "test-reg@example.com",
            "password": "Password123!",
            "business_type": "salon"
        }
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Verify that build_default_tenant_config was called with modules list fetched from DB
        build_config_spy.assert_called_once()
        kwargs = build_config_spy.call_args[1]
        assert kwargs["business_name"] == "Test Salon Registration"
        assert kwargs["business_type"] == "salon"
        assert kwargs["enabled_modules"] == ["booking", "faq"]


@pytest.mark.asyncio
async def test_register_flow_removes_plan_disallowed_business_type_defaults():
    client = TestClient(app)
    mock_business_type_row = {
        "enabled_modules": json.dumps(["booking", "faq", "human_handoff", "reminders"])
    }
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    def fetchrow_side_effect(query, *args):
        if "business_types" in query:
            return mock_business_type_row
        return None

    mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("backend.api.routes.auth.settings") as settings_mock,
        patch("backend.api.routes.auth.db_client.acquire", return_value=mock_acquire),
        patch("backend.api.routes.auth.build_default_tenant_config", wraps=build_default_tenant_config) as build_config_spy,
    ):
        settings_mock.effective_auth_mode = "mock"
        settings_mock.allow_mock_auth = True
        response = client.post("/api/v1/auth/register", json={
            "business_name": "Trial Clinic Registration",
            "email": "trial-clinic@example.com",
            "password": "Password123!",
            "business_type": "clinic",
        })

    assert response.status_code == 200
    kwargs = build_config_spy.call_args.kwargs
    assert kwargs["enabled_modules"] == ["booking", "faq", "human_handoff"]
    config = build_default_tenant_config(**kwargs)
    assert config["modules"]["reminders"]["enabled"] is False
