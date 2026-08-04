import pytest
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import require_super_admin
from backend.schemas.business_type import (
    BusinessTypeCreateRequest,
    BusinessTypeUpdateRequest,
    BusinessTypeResponse,
)
# Mock super admin dependency override
async def override_super_admin():
    return {
        "user_id": uuid4(),
        "tenant_id": uuid4(),
        "role": "super_admin",
    }
def test_schema_create_validation_success():
    # Valid model validation
    req = BusinessTypeCreateRequest(
        code="custom-type_1",
        label="Custom Service Type",
        enabled_modules=["booking", "faq"],
        note="Valid note"
    )
    assert req.code == "custom-type_1"
    assert req.label == "Custom Service Type"
    assert "booking" in req.enabled_modules
def test_schema_create_validation_invalid_code():
    # Invalid code pattern (e.g. capitals or special symbols not allowed)
    with pytest.raises(ValueError, match="code must be lowercase alphanumeric"):
        BusinessTypeCreateRequest(
            code="InvalidCode!",
            label="Service",
            enabled_modules=["faq"],
        )
def test_schema_create_validation_allows_dynamic_module_keys():
    req = BusinessTypeCreateRequest(
        code="validcode",
        label="Service",
        enabled_modules=["booking", "custom_tool"],
    )
    assert "custom_tool" in req.enabled_modules
def test_schema_create_validation_invalid_module_format():
    with pytest.raises(ValueError, match="Invalid module key format"):
        BusinessTypeCreateRequest(
            code="validcode",
            label="Service",
            enabled_modules=["booking", "bad-tool"],
        )
def test_schema_update_validation_invalid_modules():
    with pytest.raises(ValueError, match="Invalid module key format"):
        BusinessTypeUpdateRequest(
            enabled_modules=["bad-tool"]
        )
def test_non_super_admin_blocked():
    client = TestClient(app)
    # We do NOT override require_super_admin dependency, meaning it will use real auth logic
    # which will block the unauthenticated request or return 403/401.
    response = client.get("/api/v1/admin/business-types")
    assert response.status_code in (401, 403)
def test_list_business_types_api():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    mock_types = [
        {
            "id": uuid4(),
            "code": "salon",
            "label": "Salon / Beauty",
            "enabled_modules": ["booking", "faq"],
            "note": "Initial salon seed",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    ]
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_all_business_types = AsyncMock(return_value=mock_types)
        response = client.get("/api/v1/admin/business-types")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "salon"
        assert data["data"][0]["label"] == "Salon / Beauty"
    app.dependency_overrides.pop(require_super_admin, None)
def test_get_business_type_by_code_success():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    mock_type = {
        "id": uuid4(),
        "code": "salon",
        "label": "Salon / Beauty",
        "enabled_modules": ["booking", "faq"],
        "note": "Initial salon seed",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_business_type_by_code = AsyncMock(return_value=mock_type)
        response = client.get("/api/v1/admin/business-types/salon")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["code"] == "salon"
    app.dependency_overrides.pop(require_super_admin, None)
def test_get_business_type_by_code_not_found():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_business_type_by_code = AsyncMock(return_value=None)
        response = client.get("/api/v1/admin/business-types/missing")
        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"].lower()
    app.dependency_overrides.pop(require_super_admin, None)
def test_create_business_type_success():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    payload = {
        "code": "new-type",
        "label": "New Type label",
        "enabled_modules": ["booking"],
        "note": "Some note"
    }
    mock_response = {
        "id": uuid4(),
        "code": "new-type",
        "label": "New Type label",
        "enabled_modules": ["booking"],
        "note": "Some note",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_business_type_by_code = AsyncMock(return_value=None)
        admin_db_mock.create_business_type = AsyncMock(return_value=mock_response)
        response = client.post("/api/v1/admin/business-types", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["code"] == "new-type"
        assert data["data"]["label"] == "New Type label"
    app.dependency_overrides.pop(require_super_admin, None)
def test_create_business_type_already_exists():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    payload = {
        "code": "salon",
        "label": "Salon / Beauty",
        "enabled_modules": ["booking"],
    }
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_business_type_by_code = AsyncMock(return_value={"id": uuid4()})
        response = client.post("/api/v1/admin/business-types", json=payload)
        assert response.status_code == 400
        assert "already exists" in response.json()["error"]["message"].lower()
    app.dependency_overrides.pop(require_super_admin, None)
def test_update_business_type_success():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    mock_response = {
        "id": uuid4(),
        "code": "salon",
        "label": "Updated Salon Label",
        "enabled_modules": ["booking", "reminders"],
        "note": "Updated note",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_business_type_by_code = AsyncMock(return_value={"id": uuid4()})
        admin_db_mock.update_business_type = AsyncMock(return_value=mock_response)
        response = client.patch("/api/v1/admin/business-types/salon", json={"label": "Updated Salon Label"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["label"] == "Updated Salon Label"
    app.dependency_overrides.pop(require_super_admin, None)
def test_delete_business_type_success():
    app.dependency_overrides[require_super_admin] = override_super_admin
    client = TestClient(app)
    with patch("backend.api.routes.admin.db_client") as admin_db_mock:
        admin_db_mock.get_business_type_by_code = AsyncMock(return_value={"id": uuid4()})
        admin_db_mock.delete_business_type = AsyncMock(return_value=True)
        response = client.delete("/api/v1/admin/business-types/salon")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "deleted successfully" in data["data"]["message"]
    app.dependency_overrides.pop(require_super_admin, None)