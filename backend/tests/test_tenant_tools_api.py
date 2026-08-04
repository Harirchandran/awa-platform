from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import get_current_user
async def override_current_user():
    return {
        "user_id": uuid4(),
        "tenant_id": override_current_user.tenant_id,
        "role": "tenant_admin",
    }
def test_tenant_tools_endpoint_uses_db_tool_catalog():
    tenant_id = uuid4()
    override_current_user.tenant_id = tenant_id
    tenant = {
        "id": tenant_id,
        "name": "Rewards Shop",
        "slug": "rewards_shop",
        "plan_code": "enterprise",
        "status": "active",
        "created_at": datetime(2026, 5, 28, tzinfo=timezone.utc),
    }
    config = {
        "version": 4,
        "config_json": {
            "business_name": "Rewards Shop",
            "business_type": "retail",
            "modules": {"loyalty": {"enabled": True, "fields": []}},
        },
    }
    tool_catalog = [{
        "key": "loyalty",
        "label": "Loyalty",
        "category": "Growth",
        "description": "Track rewards.",
        "icon_key": "star",
        "route_path": "/dashboard/loyalty",
        "actions": [],
        "sort_order": 10,
    }]
    mock_db = AsyncMock()
    mock_db.get_tenant.return_value = tenant
    mock_db.get_tenant_config.return_value = config
    mock_db.get_tool_catalog.return_value = tool_catalog
    mock_db.get_subscription_plan_by_code.return_value = None
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with (
            patch("backend.api.routes.config.db_client", mock_db),
            patch("backend.api.entitlements.db_client", mock_db),
            patch("backend.db.client.db_client", mock_db),
            patch(
                "backend.api.routes.config._get_tenant_tool_policy",
                AsyncMock(return_value=(
                    {
                        "plan_code": "enterprise",
                        "allowed_ai_modes": ["standard", "concierge"],
                        "allowed_modules": ["loyalty"],
                    },
                    ["loyalty"],
                )),
            ),
        ):
            response = TestClient(app).get("/api/v1/tools")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active_modules"] == ["loyalty"]
    assert data["tools"][0]["key"] == "loyalty"
    assert data["tools"][0]["route_path"] == "/dashboard/loyalty"
