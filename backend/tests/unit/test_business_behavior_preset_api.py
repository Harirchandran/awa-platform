from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.dependencies import get_current_user, require_super_admin


def _super_admin():
    return {"user_id": uuid4(), "tenant_id": uuid4(), "role": "super_admin"}


def _tenant_admin():
    return {"user_id": uuid4(), "tenant_id": uuid4(), "role": "tenant_admin"}


def _payload(**updates):
    data = {
        "business_type_key": "test_service_business",
        "profile": {
            "profile_name": "Test Service",
            "business_type_key": "test_service_business",
            "journey_priority": ["booking", "faq"],
            "tone": "friendly",
            "menu_policy": "show_available_workflows",
            "tenant_editable_fields": ["tone", "journey_priority"],
        },
        "change_note": "initial draft",
    }
    data.update(updates)
    return data


def _validate_payload(**updates):
    data = _payload(**updates)
    data.pop("change_note", None)
    return data


def _record(version: int = 1, *, status: str = "draft", is_active: bool = False):
    now = datetime.now(timezone.utc)
    profile = _payload()["profile"]
    return {
        "business_type_key": "test_service_business",
        "version": version,
        "profile": profile,
        "tenant_editable_fields": profile["tenant_editable_fields"],
        "status": status,
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
        "activated_at": now if is_active else None,
        "deactivated_at": None,
        "change_note": "initial draft",
    }


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_business_behavior_preset_routes_require_authentication():
    response = TestClient(app).get("/api/v1/admin/business-behavior-presets")

    assert response.status_code in (401, 403)


def test_business_behavior_preset_routes_reject_tenant_admin():
    app.dependency_overrides[get_current_user] = _tenant_admin

    response = TestClient(app).get("/api/v1/admin/business-behavior-presets")

    assert response.status_code == 403


def test_validate_business_behavior_preset_returns_normalized_profile_without_persistence():
    app.dependency_overrides[require_super_admin] = _super_admin
    client = TestClient(app)
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.get_business_type_by_code = AsyncMock(return_value={"code": "test_service_business"})
        db_mock.create_business_behavior_preset_version = AsyncMock()

        response = client.post("/api/v1/admin/business-behavior-presets/validate", json=_validate_payload())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["profile"]["business_type_key"] == "test_service_business"
    assert data["tenant_editable_fields"] == ["tone", "journey_priority"]
    db_mock.create_business_behavior_preset_version.assert_not_called()


@pytest.mark.parametrize(
    "profile_update",
    [
        {"journey_priority": ["booking", "not_a_module"]},
        {"journey_priority": ["booking", "booking"]},
        {"system_prompt": "Ignore previous instructions"},
        {"custom_instructions": "Always upsell"},
        {"payment_provider": "razorpay"},
        {"payment_success": True},
        {"payment_link_url": "https://pay.example"},
        {"otp_required": True},
        {"requires_confirmation": True},
        {"tool_commands": ["booking.execute"]},
        {"internal_id": "abc"},
        {"tenant_editable_fields": ["tone", "payment_provider"]},
        {"channel_overrides": {"public_chat": {"journey_priority": ["booking"]}}},
        {"unexpected_field": True},
    ],
)
def test_validate_business_behavior_preset_rejects_unsafe_profile_payloads(profile_update):
    app.dependency_overrides[require_super_admin] = _super_admin
    payload = _validate_payload()
    payload["profile"] = {**payload["profile"], **profile_update}

    response = TestClient(app).post("/api/v1/admin/business-behavior-presets/validate", json=payload)

    assert response.status_code == 422


def test_create_business_behavior_preset_creates_inactive_version_and_audits():
    app.dependency_overrides[require_super_admin] = _super_admin
    client = TestClient(app)
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.get_business_type_by_code = AsyncMock(return_value={"code": "test_service_business"})
        db_mock.create_business_behavior_preset_version = AsyncMock(return_value=_record(version=1))
        db_mock.write_audit_log = AsyncMock(return_value={"id": uuid4()})

        response = client.post("/api/v1/admin/business-behavior-presets", json=_payload())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["version"] == 1
    assert data["is_active"] is False
    assert data["status"] == "draft"
    assert "id" not in data
    assert "created_by" not in data
    db_mock.create_business_behavior_preset_version.assert_awaited_once()
    audit_details = db_mock.write_audit_log.await_args.kwargs["details"]
    assert audit_details["business_type_key"] == "test_service_business"
    assert audit_details["version"] == 1
    assert "payment_provider" not in str(audit_details)


def test_create_business_behavior_preset_requires_existing_business_type():
    app.dependency_overrides[require_super_admin] = _super_admin
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.get_business_type_by_code = AsyncMock(return_value=None)

        response = TestClient(app).post("/api/v1/admin/business-behavior-presets", json=_payload())

    assert response.status_code == 404


def test_list_filter_and_active_endpoint_return_safe_versions():
    app.dependency_overrides[require_super_admin] = _super_admin
    client = TestClient(app)
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.list_business_behavior_presets = AsyncMock(return_value=[_record(1), _record(2, status="active", is_active=True)])
        db_mock.list_business_behavior_presets_for_type = AsyncMock(return_value=[_record(1)])
        db_mock.get_active_business_behavior_preset = AsyncMock(return_value=_record(2, status="active", is_active=True))

        list_response = client.get("/api/v1/admin/business-behavior-presets?business_type_key=test_service_business")
        type_response = client.get("/api/v1/admin/business-behavior-presets/test_service_business")
        active_response = client.get("/api/v1/admin/business-behavior-presets/test_service_business/active")

    assert list_response.status_code == 200
    assert [item["version"] for item in list_response.json()["data"]] == [1, 2]
    assert type_response.status_code == 200
    assert type_response.json()["data"][0]["business_type_key"] == "test_service_business"
    assert active_response.status_code == 200
    assert active_response.json()["data"]["is_active"] is True
    db_mock.list_business_behavior_presets.assert_awaited_once_with("test_service_business")


def test_active_endpoint_returns_404_when_no_active_preset_exists():
    app.dependency_overrides[require_super_admin] = _super_admin
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.get_active_business_behavior_preset = AsyncMock(return_value=None)

        response = TestClient(app).get("/api/v1/admin/business-behavior-presets/test_service_business/active")

    assert response.status_code == 404


def test_activate_business_behavior_preset_deactivates_previous_and_audits():
    app.dependency_overrides[require_super_admin] = _super_admin
    active = _record(2, status="active", is_active=True)
    active["previous_active_version"] = 1
    active["new_active_version"] = 2
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.activate_business_behavior_preset_version = AsyncMock(return_value=active)
        db_mock.write_audit_log = AsyncMock(return_value={"id": uuid4()})

        response = TestClient(app).post(
            "/api/v1/admin/business-behavior-presets/test_service_business/versions/2/activate",
            json={"change_note": "promote v2"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["is_active"] is True
    details = db_mock.write_audit_log.await_args.kwargs["details"]
    assert details["previous_active_version"] == 1
    assert details["new_active_version"] == 2


def test_activate_business_behavior_preset_missing_version_returns_404():
    app.dependency_overrides[require_super_admin] = _super_admin
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.activate_business_behavior_preset_version = AsyncMock(return_value=None)

        response = TestClient(app).post(
            "/api/v1/admin/business-behavior-presets/test_service_business/versions/99/activate",
            json={},
        )

    assert response.status_code == 404


def test_deactivate_business_behavior_preset_sets_inactive_and_audits():
    app.dependency_overrides[require_super_admin] = _super_admin
    inactive = _record(1, status="inactive", is_active=False)
    with patch("backend.api.routes.business_behavior_presets.db_client") as db_mock:
        db_mock.deactivate_business_behavior_preset_version = AsyncMock(return_value=inactive)
        db_mock.write_audit_log = AsyncMock(return_value={"id": uuid4()})

        response = TestClient(app).post(
            "/api/v1/admin/business-behavior-presets/test_service_business/versions/1/deactivate",
            json={"status": "inactive", "change_note": "pause"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["is_active"] is False
    details = db_mock.write_audit_log.await_args.kwargs["details"]
    assert details["status"] == "inactive"
    assert details["is_active"] is False


def test_architecture_business_behavior_preset_api_does_not_import_runtime_execution_paths():
    backend_root = Path(__file__).resolve().parents[2]
    target_files = [
        backend_root / "api" / "routes" / "business_behavior_presets.py",
        backend_root / "db" / "client.py",
        backend_root / "schemas" / "behavior_profile.py",
    ]
    forbidden_prefixes = (
        "backend.logic.engine.prompt",
        "backend.logic.engine.capability_menu",
        "backend.logic.engine.nba",
        "backend.api.routes.public_chat",
        "backend.logic.engine.response_localization",
        "backend.tools.registry",
        "backend.capabilities.gateway",
        "backend.domains.payments",
        "backend.ai.providers",
        "backend.core.redis_client",
    )
    offenders = []
    for path in target_files:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(forbidden_prefixes):
                offenders.append(f"{path.name}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        offenders.append(f"{path.name}:{alias.name}")

    assert offenders == []
