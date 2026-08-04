from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import require_super_admin
async def override_require_super_admin():
    return {
        "user_id": uuid4(),
        "tenant_id": uuid4(),
        "role": "super_admin",
    }
def test_tool_control_update_response_matches_explicit_super_admin_modules():
    tenant_id = uuid4()
    config_id = uuid4()
    requested_modules = ["booking", "order", "payments", "faq", "human_handoff"]
    captured_config = {}
    tenant = {
        "id": tenant_id,
        "name": "Clinic With Commerce",
        "slug": "clinic_with_commerce",
        "plan_code": "ENTERPRISE",
        "status": "active",
        "created_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
    }
    current_config = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "version": 1,
        "is_active": True,
        "config_json": {
            "business_name": "Clinic With Commerce",
            "business_type": "clinic",
            "modules": {
                "booking": {"enabled": True, "fields": []},
                "order": {"enabled": False, "fields": []},
                "payments": {"enabled": False, "fields": []},
                "faq": {"enabled": True, "fields": []},
                "human_handoff": {"enabled": True, "fields": []},
            },
        },
    }
    async def create_config_version(_tenant_id, config_json):
        captured_config["config_json"] = config_json
        return {"id": config_id, "version": 2}
    mock_db = AsyncMock()
    mock_db.get_tenant.return_value = tenant
    mock_db.get_tenant_config.return_value = current_config
    mock_db.create_config_version.side_effect = create_config_version
    mock_db.activate_config_version.return_value = True
    mock_db.write_audit_log.return_value = None
    mock_db.get_all_business_types.return_value = [{
        "code": "clinic",
        "label": "Clinic / Healthcare",
        "enabled_modules": ["booking", "reminders", "reviews", "support", "faq", "human_handoff"],
        "note": "Clinic preset excludes commerce by default.",
    }]
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    try:
        with (
            patch("backend.api.routes.admin.db_client", mock_db),
            patch(
                "backend.api.routes.admin._get_tenant_tool_policy",
                AsyncMock(return_value=(
                    {
                        "plan_code": "ENTERPRISE",
                        "allowed_ai_modes": ["standard", "concierge"],
                        "allowed_modules": ["*"],
                    },
                    ["*"],
                )),
            ),
        ):
            response = TestClient(app).put(
                f"/api/v1/admin/tenants/{tenant_id}/tool-controls",
                json={
                    "enabled_modules": requested_modules,
                    "business_type": "clinic",
                    "reason": "phase_3_regression",
                },
            )
    finally:
        app.dependency_overrides.pop(require_super_admin, None)
    assert response.status_code == 200
    data = response.json()["data"]
    assert sorted(data["active_modules"]) == sorted(requested_modules)
    assert all(captured_config["config_json"]["modules"][key]["enabled"] is True for key in requested_modules)
    assert captured_config["config_json"]["modules"]["order"]["enabled"] is True
    assert captured_config["config_json"]["modules"]["payments"]["enabled"] is True