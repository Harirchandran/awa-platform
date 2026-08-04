import pytest
import json
from backend.billing.usage_guard import validate_plan_limits

@pytest.mark.anyio
async def test_validation_accepts_valid_standard_plans():
    # Trial-like limits
    trial_limits = {
        "monthly_messages": 300,
        "daily_messages": 50,
        "monthly_ai_tokens": 100000,
        "monthly_credits": 1000,
        "allowed_ai_modes": ["standard"],
        "allowed_modules": ["booking", "faq", "human_handoff"],
    }
    assert await validate_plan_limits(trial_limits, "trial") == trial_limits

    # Pro-like limits
    pro_limits = {
        "monthly_messages": 5000,
        "daily_messages": 750,
        "monthly_ai_tokens": 2000000,
        "monthly_credits": 10000,
        "allowed_ai_modes": ["standard", "advanced"],
        "allowed_modules": ["booking", "lead_capture", "support", "faq", "human_handoff"],
    }
    assert await validate_plan_limits(pro_limits, "pro") == pro_limits

    # Enterprise-like limits (wildcard allowed_modules)
    enterprise_limits = {
        "monthly_messages": None,
        "daily_messages": None,
        "monthly_ai_tokens": None,
        "monthly_credits": None,
        "allowed_ai_modes": ["standard", "advanced"],
        "allowed_modules": ["*"],
    }
    assert await validate_plan_limits(enterprise_limits, "enterprise") == enterprise_limits

@pytest.mark.anyio
async def test_validation_accepts_custom_plan_codes():
    # Any custom plan code is accepted and not hardcoded to starter/pro/enterprise
    custom_limits_wildcard = {
        "monthly_messages": 10000,
        "allowed_modules": ["*"],
        "allowed_ai_modes": ["standard"],
    }
    # Should accept wildcard modules for custom plans
    assert await validate_plan_limits(custom_limits_wildcard, "custom_gold") == custom_limits_wildcard

    custom_limits_normal = {
        "monthly_messages": 2000,
        "allowed_modules": ["booking", "order"],
        "allowed_ai_modes": ["standard"],
    }
    assert await validate_plan_limits(custom_limits_normal, "custom_silver") == custom_limits_normal

@pytest.mark.anyio
async def test_validation_accepts_legacy_concierge_alias_and_canonicalizes():
    limits = {
        "monthly_messages": 2000,
        "allowed_modules": ["booking", "order"],
        "allowed_ai_modes": ["standard", "concierge"],
    }
    result = await validate_plan_limits(limits, "custom_legacy")
    assert result["allowed_ai_modes"] == ["standard", "advanced"]

@pytest.mark.anyio
async def test_validation_rejects_unknown_keys():
    limits = {"monthly_messages": 100, "extra_key": "some_value"}
    with pytest.raises(ValueError, match="Unknown keys in limits_json"):
        await validate_plan_limits(limits, "pro")

@pytest.mark.anyio
async def test_validation_rejects_non_negative_checks():
    bad_limits = {"monthly_messages": -1}
    with pytest.raises(ValueError, match="must be a non-negative integer"):
        await validate_plan_limits(bad_limits, "starter")

    bad_credits = {"monthly_credits": -5.5}
    with pytest.raises(ValueError, match="must be a non-negative number"):
        await validate_plan_limits(bad_credits, "starter")

@pytest.mark.anyio
async def test_validation_rejects_invalid_types_for_numbers():
    # Reject strings where numbers are expected
    for val in ["100", "0", "unlimited"]:
        with pytest.raises(ValueError, match="must be an integer, not string or boolean"):
            await validate_plan_limits({"monthly_messages": val}, "starter")

    for val in ["10.5", "0.0"]:
        with pytest.raises(ValueError, match="must be a number, not string or boolean"):
            await validate_plan_limits({"monthly_credits": val}, "starter")

    # Reject booleans (since isinstance(True, int) is True in Python, we explicitly handle booleans)
    with pytest.raises(ValueError, match="must be an integer, not string or boolean"):
        await validate_plan_limits({"monthly_messages": True}, "starter")

    with pytest.raises(ValueError, match="must be a number, not string or boolean"):
        await validate_plan_limits({"monthly_credits": False}, "starter")

@pytest.mark.anyio
async def test_validation_rejects_invalid_list_shapes():
    # Must reject dict where list is expected
    with pytest.raises(ValueError, match="must be a list"):
        await validate_plan_limits({"allowed_ai_modes": {"standard": True}}, "pro")

    with pytest.raises(ValueError, match="must be a list"):
        await validate_plan_limits({"allowed_modules": {"booking": True}}, "pro")

    # Must reject null list if present
    with pytest.raises(ValueError, match="cannot be null"):
        await validate_plan_limits({"allowed_ai_modes": None}, "pro")

    with pytest.raises(ValueError, match="cannot be null"):
        await validate_plan_limits({"allowed_modules": None}, "pro")

@pytest.mark.anyio
async def test_validation_rejects_empty_ai_modes():
    with pytest.raises(ValueError, match="cannot be empty"):
        await validate_plan_limits({"allowed_ai_modes": []}, "starter")

@pytest.mark.anyio
async def test_validation_rejects_unknown_ai_modes():
    for mode in ["robot", ""]:
        with pytest.raises(ValueError, match="Unknown AI mode"):
            await validate_plan_limits({"allowed_ai_modes": [mode]}, "pro")

    with pytest.raises(ValueError, match="elements in 'allowed_ai_modes' must be strings"):
        await validate_plan_limits({"allowed_ai_modes": [123]}, "pro")

@pytest.mark.anyio
async def test_validation_rejects_unknown_modules():
    for mod in ["invalid_module", "loyalty_extra", ""]:
        with pytest.raises(ValueError, match="Unknown module"):
            await validate_plan_limits({"allowed_modules": [mod]}, "pro")

    with pytest.raises(ValueError, match="elements in 'allowed_modules' must be strings"):
        await validate_plan_limits({"allowed_modules": [True]}, "pro")

@pytest.mark.anyio
async def test_validation_rejects_mixed_wildcard_lists():
    # Mixed wildcards must be rejected on any plan
    for plan in ["trial", "starter", "pro", "enterprise", "custom_plan"]:
        with pytest.raises(ValueError, match="Wildcard '\\*' must be the sole entry"):
            await validate_plan_limits({"allowed_modules": ["*", "booking"]}, plan)

        with pytest.raises(ValueError, match="Wildcard '\\*' must be the sole entry"):
            await validate_plan_limits({"allowed_modules": ["booking", "*"]}, plan)

@pytest.mark.anyio
async def test_validation_accepts_valid_json_string():
    limits_str = '{"monthly_messages": 500, "allowed_ai_modes": ["standard"]}'
    res = await validate_plan_limits(limits_str, "trial")
    assert res["monthly_messages"] == 500
    assert res["allowed_ai_modes"] == ["standard"]

@pytest.mark.anyio
async def test_validation_rejects_malformed_json_string():
    with pytest.raises(ValueError, match="Invalid JSON format"):
        await validate_plan_limits("{malformed_json}", "trial")

from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import require_super_admin
from unittest.mock import patch, AsyncMock

async def override_require_super_admin():
    return {"user_id": "00000000-0000-0000-0000-000000000000", "tenant_id": "00000000-0000-0000-0000-000000000000"}

def test_admin_plans_post_endpoint_validation():
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    try:
        # Invalid limits (negative messages)
        response = TestClient(app).post(
            "/api/v1/admin/plans",
            json={
                "name": "Invalid Limits Plan",
                "plan_code": "invalid_limits",
                "monthly_price_cents": 0,
                "currency": "INR",
                "razorpay_plan_id": "awa_invalid_limits",
                "is_active": True,
                "features_json": [],
                "limits_json": {"monthly_messages": -10},
            }
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "must be a non-negative integer" in payload["error"]["message"]

        # Valid limits - mock DB client fetchrow and audit log
        mock_row = {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Valid Plan",
            "plan_code": "valid_limits",
            "monthly_price_cents": 0,
            "currency": "INR",
            "razorpay_plan_id": "awa_valid_limits",
            "is_active": True,
            "features_json": "[]",
            "limits_json": "{}",
        }
        with (
            patch("backend.api.routes.admin.db_client.acquire") as mock_acquire,
            patch("backend.api.routes.admin.db_client.write_audit_log", AsyncMock())
        ):
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = mock_row
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            response = TestClient(app).post(
                "/api/v1/admin/plans",
                json={
                    "name": "Valid Plan",
                    "plan_code": "valid_limits",
                    "monthly_price_cents": 0,
                    "currency": "INR",
                    "razorpay_plan_id": "awa_valid_limits",
                    "is_active": True,
                    "features_json": [],
                    "limits_json": {"monthly_messages": 100},
                }
            )
            assert response.status_code == 200
            assert response.json()["success"] is True
    finally:
        app.dependency_overrides.pop(require_super_admin, None)

def test_admin_plans_patch_endpoint_validation():
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    plan_id = "11111111-1111-1111-1111-111111111111"
    try:
        # Invalid limits (mixed wildcard)
        response = TestClient(app).patch(
            f"/api/v1/admin/plans/{plan_id}",
            json={
                "name": "Invalid Limits Plan",
                "plan_code": "invalid_limits",
                "monthly_price_cents": 0,
                "currency": "INR",
                "razorpay_plan_id": "awa_invalid_limits",
                "is_active": True,
                "features_json": [],
                "limits_json": {"allowed_modules": ["*", "booking"]},
            }
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "must be the sole entry" in payload["error"]["message"]

        # Valid limits - mock DB client fetchrow and audit log
        mock_row = {
            "id": plan_id,
            "name": "Valid Plan",
            "plan_code": "valid_limits",
            "monthly_price_cents": 0,
            "currency": "INR",
            "razorpay_plan_id": "awa_valid_limits",
            "is_active": True,
            "features_json": "[]",
            "limits_json": "{}",
        }
        with (
            patch("backend.api.routes.admin.db_client.acquire") as mock_acquire,
            patch("backend.api.routes.admin.db_client.write_audit_log", AsyncMock())
        ):
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = mock_row
            mock_acquire.return_value.__aenter__.return_value = mock_conn

            response = TestClient(app).patch(
                f"/api/v1/admin/plans/{plan_id}",
                json={
                    "name": "Valid Plan",
                    "plan_code": "valid_limits",
                    "monthly_price_cents": 0,
                    "currency": "INR",
                    "razorpay_plan_id": "awa_valid_limits",
                    "is_active": True,
                    "features_json": [],
                    "limits_json": {"allowed_modules": ["*"]},
                }
            )
            assert response.status_code == 200
            assert response.json()["success"] is True
    finally:
        app.dependency_overrides.pop(require_super_admin, None)
