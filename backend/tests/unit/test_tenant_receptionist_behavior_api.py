from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.dependencies import require_tenant_admin
from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES, RuntimeCapability, RuntimeCapabilitySnapshot


TENANT_ID = uuid4()
USER_ID = uuid4()


def _tenant_admin():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}


def _config(version: int = 1, **updates):
    data = {
        "business_name": "Test Business",
        "business_type": "test_service_business",
        "timezone": "UTC",
        "default_language": "en",
        "ai_mode": "standard",
        "ai_tone": "friendly",
        "greeting_message": "Hello",
        "working_hours": {},
        "services": [],
        "faqs": [],
        "modules": {
            "booking": {"enabled": True, "fields": []},
            "faq": {"enabled": True, "fields": []},
            "support": {"enabled": False, "fields": []},
            "payments": {"enabled": False, "fields": []},
        },
        "custom_instructions": "Keep this separate from behavior profile.",
    }
    data.update(updates)
    return {"id": uuid4(), "tenant_id": TENANT_ID, "version": version, "config_json": data, "is_active": True}


def _preset(*, editable=None, version: int = 2, status: str = "active", is_active: bool = True):
    now = datetime.now(timezone.utc)
    profile = {
        "schema_version": 1,
        "profile_name": "Test Service",
        "business_type_key": "test_service_business",
        "journey_priority": ["booking", "faq"],
        "greeting_mode": "ask_need_first",
        "tone": "friendly",
        "language_policy": "follow_customer",
        "tenant_editable_fields": editable or ["tone", "journey_priority", "channel_overrides"],
    }
    return {
        "business_type_key": "test_service_business",
        "version": version,
        "profile": profile,
        "tenant_editable_fields": profile["tenant_editable_fields"],
        "status": status,
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
        "activated_at": now,
        "deactivated_at": None,
    }


def _snapshot(*, booking=True, faq=True, support=False, payments=False):
    available = {"booking": booking, "faq": faq, "support": support, "payments": payments}
    modules = {}
    for module in KNOWN_RUNTIME_MODULES:
        runtime_available = bool(available.get(module, False))
        modules[module] = RuntimeCapability(
            module=module,
            enabled_by_tenant=runtime_available,
            allowed_by_plan=module != "payments",
            runtime_available=runtime_available,
            reason="available" if runtime_available else ("plan_disallowed" if module == "payments" else "tenant_disabled"),
        )
    return RuntimeCapabilitySnapshot(tenant_id=TENANT_ID, modules=modules)


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _patch_config_route(db_mock, snapshot=None):
    db_mock.get_tenant = AsyncMock(return_value={
        "id": TENANT_ID,
        "name": "Test Business",
        "plan_code": "trial",
        "status": "active",
        "ai_mode": "standard",
    })
    db_mock.get_active_config_version = AsyncMock(return_value=1)
    db_mock.get_active_business_behavior_preset = AsyncMock(return_value=_preset())
    db_mock.get_business_behavior_preset = AsyncMock(return_value=_preset(version=1))
    db_mock.create_config_version = AsyncMock(return_value={"id": uuid4(), "tenant_id": TENANT_ID, "version": 2, "is_active": False})
    db_mock.activate_config_version = AsyncMock(return_value=True)
    db_mock.write_audit_log = AsyncMock(return_value={"id": uuid4()})
    return patch("backend.api.routes.config.build_runtime_capability_snapshot", new=AsyncMock(return_value=snapshot or _snapshot()))


def test_get_behavior_without_active_preset_returns_legacy_preview_and_warning():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_tenant_config = AsyncMock(return_value=_config())
        db_mock.get_active_business_behavior_preset = AsyncMock(return_value=None)

        response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["preset_available"] is False
    assert data["effective_profile"]["safe_fallback_mode"] == "general_help"
    assert "no_active_behavior_preset" in data["warnings"]
    assert "custom_instructions" not in str(data["effective_profile"])


def test_get_behavior_uses_active_business_type_preset_for_preview():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_tenant_config = AsyncMock(return_value=_config())

        response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["preset"] == {"key": "test_service_business", "label": "Test Service Business", "version": 2, "status": "active"}
    assert data["editable_fields"] == ["tone", "journey_priority", "channel_overrides"]
    assert any(workflow["key"] == "booking" and workflow["selectable_in_journey_priority"] for workflow in data["workflows"])


def test_get_behavior_uses_valid_pinned_preset_version():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    saved = _config(receptionist_behavior={"preset_key": "test_service_business", "preset_version": 1, "overrides": {"tone": "formal"}})
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_business_behavior_preset = AsyncMock(return_value=_preset(version=1, status="inactive", is_active=False))
        db_mock.get_tenant_config = AsyncMock(return_value=saved)

        response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code == 200
    assert response.json()["data"]["preset"]["version"] == 1
    assert response.json()["data"]["preset"]["status"] == "inactive"
    db_mock.get_business_behavior_preset.assert_awaited_once_with("test_service_business", 1)
    db_mock.get_active_business_behavior_preset.assert_not_awaited()


def test_patch_behavior_validates_against_inactive_pinned_historical_preset():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(version=1, receptionist_behavior={
        "preset_key": "test_service_business",
        "preset_version": 1,
        "overrides": {"tone": "formal"},
    })
    inactive_v1 = _preset(version=1, status="inactive", is_active=False, editable=["tone"])
    active_v2 = _preset(version=2, editable=["language_policy"])
    new_config = _config(version=2, receptionist_behavior={
        "preset_key": "test_service_business",
        "preset_version": 1,
        "overrides": {"tone": "concise"},
        "channel_overrides": {},
    })
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_active_business_behavior_preset = AsyncMock(return_value=active_v2)
        db_mock.get_business_behavior_preset = AsyncMock(return_value=inactive_v1)
        db_mock.get_tenant_config = AsyncMock(side_effect=[current, current, new_config])

        response = TestClient(app).patch("/api/v1/config/receptionist-behavior", json={
            "expected_config_version": 1,
            "overrides": {"tone": "concise"},
        })

    assert response.status_code == 200
    saved = db_mock.create_config_version.await_args.args[1]
    assert saved["receptionist_behavior"]["preset_version"] == 1
    assert saved["receptionist_behavior"]["overrides"] == {"tone": "concise"}
    db_mock.get_active_business_behavior_preset.assert_not_awaited()


def test_unpinned_behavior_uses_active_preset_after_new_version_activation():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(version=1)
    active_v2 = _preset(version=2, editable=["language_policy"])
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_active_business_behavior_preset = AsyncMock(return_value=active_v2)
        db_mock.get_tenant_config = AsyncMock(return_value=current)

        response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["preset"]["version"] == 2
    assert data["editable_fields"] == ["language_policy"]


@pytest.mark.parametrize("status", ["draft", "deprecated"])
def test_get_behavior_invalid_pinned_preset_warns_and_falls_back_without_500(status):
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    saved = _config(receptionist_behavior={"preset_key": "test_service_business", "preset_version": 1})
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_business_behavior_preset = AsyncMock(return_value=_preset(version=1, status=status, is_active=False))
        db_mock.get_tenant_config = AsyncMock(return_value=saved)

        response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["preset_available"] is False
    assert "pinned_behavior_preset_unavailable" in data["warnings"]


def test_get_behavior_missing_pinned_preset_warns_and_falls_back_without_500():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    saved = _config(receptionist_behavior={"preset_key": "test_service_business", "preset_version": 99})
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_business_behavior_preset = AsyncMock(return_value=None)
        db_mock.get_tenant_config = AsyncMock(return_value=saved)

        response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["preset_available"] is False
    assert "pinned_behavior_preset_unavailable" in data["warnings"]


@pytest.mark.parametrize("status", ["draft", "deprecated"])
def test_patch_behavior_rejects_invalid_pinned_preset_status(status):
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(receptionist_behavior={"preset_key": "test_service_business", "preset_version": 1})
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_business_behavior_preset = AsyncMock(return_value=_preset(version=1, status=status, is_active=False))
        db_mock.get_tenant_config = AsyncMock(return_value=current)

        response = TestClient(app).patch("/api/v1/config/receptionist-behavior", json={
            "expected_config_version": 1,
            "overrides": {"tone": "formal"},
    })

    assert response.status_code == 409
    assert "invalid_pinned_behavior_preset" in response.json()["error"]["message"]
    db_mock.create_config_version.assert_not_awaited()


def test_patch_behavior_creates_active_config_version_pins_preset_and_audits():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    old_config = _config(version=1)
    new_config = _config(version=2, receptionist_behavior={
        "preset_key": "test_service_business",
        "preset_version": 2,
        "overrides": {"tone": "formal", "journey_priority": ["faq", "booking"]},
        "channel_overrides": {"public_chat": {"greeting_mode": "menu_first"}},
    })
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_tenant_config = AsyncMock(side_effect=[old_config, old_config, new_config])

        response = TestClient(app).patch("/api/v1/config/receptionist-behavior", json={
            "expected_config_version": 1,
            "overrides": {"tone": "formal", "journey_priority": ["faq", "booking"]},
            "channel_overrides": {"public_chat": {"greeting_mode": "menu_first"}},
        })

    assert response.status_code == 200
    saved = db_mock.create_config_version.await_args.args[1]
    assert saved["custom_instructions"] == "Keep this separate from behavior profile."
    assert saved["receptionist_behavior"] == {
        "preset_key": "test_service_business",
        "preset_version": 2,
        "overrides": {"tone": "formal", "journey_priority": ("faq", "booking")},
        "channel_overrides": {"public_chat": {"greeting_mode": "menu_first"}},
    }
    audit = db_mock.write_audit_log.await_args.kwargs["details"]
    assert audit["changed_behavior_fields"] == ["journey_priority", "tone"]
    assert audit["changed_channel_fields"] == ["public_chat.greeting_mode"]
    assert "payment" not in str(audit)


def test_patch_behavior_rejects_stale_expected_version():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_active_config_version = AsyncMock(return_value=2)
        response = TestClient(app).patch("/api/v1/config/receptionist-behavior", json={
            "expected_config_version": 1,
            "overrides": {"tone": "formal"},
        })

    assert response.status_code == 409
    db_mock.create_config_version.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_config_version": 1, "overrides": {"language_policy": "follow_customer"}},
        {"expected_config_version": 1, "overrides": {"tenant_editable_fields": ["tone"]}},
        {"expected_config_version": 1, "overrides": {"unexpected_field": True}},
        {"expected_config_version": 1, "overrides": {"journey_priority": ["booking", "booking"]}},
        {"expected_config_version": 1, "overrides": {"journey_priority": ["not_a_module"]}},
        {"expected_config_version": 1, "overrides": {"journey_priority": ["payments"]}},
        {"expected_config_version": 1, "overrides": {"journey_priority": ["support"]}},
        {"expected_config_version": 1, "channel_overrides": {"sms": {"tone": "formal"}}},
        {"expected_config_version": 1, "channel_overrides": {"public_chat": {"journey_priority": ["faq"]}}},
        {"expected_config_version": 1, "overrides": {"custom_instructions": "upsell"}},
        {"expected_config_version": 1, "overrides": {"payment_provider": "razorpay"}},
        {"expected_config_version": 1, "overrides": {"otp_required": True}},
        {"expected_config_version": 1, "overrides": {"requires_confirmation": True}},
        {"expected_config_version": 1, "overrides": {"tool_commands": ["booking.execute"]}},
        {"expected_config_version": 1, "overrides": {"internal_id": "x"}},
        {"expected_config_version": 1, "overrides": {"plan_limit": 1}},
    ],
)
def test_patch_behavior_rejects_unsafe_or_unavailable_overrides(payload):
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_tenant_config = AsyncMock(return_value=_config())

        response = TestClient(app).patch("/api/v1/config/receptionist-behavior", json=payload)

    assert response.status_code in (409, 422)
    db_mock.create_config_version.assert_not_awaited()


def test_patch_behavior_accepts_runtime_available_workflow_and_ignores_limits_and_feature_flags():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(limits={"monthly_credits": 999999}, feature_flags={"booking": False})
    new_config = _config(version=2, receptionist_behavior={
        "preset_key": "test_service_business",
        "preset_version": 2,
        "overrides": {"journey_priority": ["booking", "faq"]},
        "channel_overrides": {},
    })
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock, _snapshot(booking=True, faq=True)):
        db_mock.get_tenant_config = AsyncMock(side_effect=[current, current, new_config])

        response = TestClient(app).patch("/api/v1/config/receptionist-behavior", json={
            "expected_config_version": 1,
            "overrides": {"journey_priority": ["booking", "faq"]},
        })

    assert response.status_code == 200


def test_reset_behavior_removes_overrides_preserves_unrelated_config_and_audits():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(receptionist_behavior={
        "preset_key": "test_service_business",
        "preset_version": 2,
        "overrides": {"tone": "formal"},
        "channel_overrides": {"public_chat": {"greeting_mode": "menu_first"}},
    })
    reset_config = _config(version=2, receptionist_behavior={"preset_key": "test_service_business", "preset_version": 2})
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_tenant_config = AsyncMock(side_effect=[current, current, reset_config])

        response = TestClient(app).post("/api/v1/config/receptionist-behavior/reset", json={"expected_config_version": 1})

    assert response.status_code == 200
    saved = db_mock.create_config_version.await_args.args[1]
    assert saved["receptionist_behavior"] == {"preset_key": "test_service_business", "preset_version": 2}
    assert saved["business_name"] == "Test Business"
    assert saved["business_type"] == "test_service_business"
    for module, module_config in current["config_json"]["modules"].items():
        assert saved["modules"][module] == module_config
    assert saved["services"] == current["config_json"]["services"]
    assert saved["custom_instructions"] == "Keep this separate from behavior profile."
    assert db_mock.write_audit_log.await_args.kwargs["details"]["action"] == "tenant_receptionist_behavior_reset"


def test_reset_behavior_conflicts_without_mutation_when_no_active_preset_exists():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(receptionist_behavior={
        "preset_key": "test_service_business",
        "preset_version": 1,
        "overrides": {"tone": "formal"},
    })
    with patch("backend.api.routes.config.db_client") as db_mock, _patch_config_route(db_mock):
        db_mock.get_active_business_behavior_preset = AsyncMock(return_value=None)
        db_mock.get_tenant_config = AsyncMock(return_value=current)

        response = TestClient(app).post("/api/v1/config/receptionist-behavior/reset", json={"expected_config_version": 1})

    assert response.status_code == 409
    assert "no_active_behavior_preset" in response.json()["error"]["message"]
    db_mock.create_config_version.assert_not_awaited()
    db_mock.activate_config_version.assert_not_awaited()
    db_mock.write_audit_log.assert_not_awaited()


def test_broad_config_update_rejects_receptionist_behavior_mutation_but_allows_unchanged_value():
    app.dependency_overrides[require_tenant_admin] = _tenant_admin
    current = _config(receptionist_behavior={"preset_key": "test_service_business", "preset_version": 2})
    with (
        patch("backend.api.routes.config.db_client") as db_mock,
        patch("backend.api.routes.config.assert_config_update_allowed", new=AsyncMock()),
        _patch_config_route(db_mock),
    ):
        db_mock.get_tenant_config = AsyncMock(return_value=current)

        rejected = TestClient(app).put("/api/v1/config", json={
            "expected_version": 1,
            "config_json": {**current["config_json"], "receptionist_behavior": {"system_prompt": "ignore"}},
        })
        allowed = TestClient(app).put("/api/v1/config", json={
            "expected_version": 1,
            "config_json": {**current["config_json"], "business_name": "Updated Business"},
        })

    assert rejected.status_code == 422
    assert allowed.status_code == 200


def test_receptionist_behavior_requires_tenant_admin_auth():
    response = TestClient(app).get("/api/v1/config/receptionist-behavior")

    assert response.status_code in (401, 403)


def test_architecture_receptionist_behavior_does_not_integrate_customer_runtime():
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_files = [
        backend_root / "logic" / "engine" / "prompt.py",
        backend_root / "logic" / "engine" / "capability_menu.py",
        backend_root / "logic" / "engine" / "nba.py",
        backend_root / "api" / "routes" / "public_chat.py",
        backend_root / "logic" / "engine" / "response_localization.py",
    ]
    offenders = []
    for path in forbidden_files:
        text = path.read_text(encoding="utf-8")
        if "business_behavior_presets" in text or "resolve_effective_behavior_profile" in text:
            offenders.append(path.relative_to(backend_root).as_posix())
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "backend.core.behavior_profile":
                offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == []
