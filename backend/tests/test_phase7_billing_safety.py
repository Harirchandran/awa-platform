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
class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False
class FakeBillingConnection:
    def __init__(self, tenant):
        self.tenant = tenant
        self.update_calls: list[tuple[str, tuple[object, ...]]] = []
    async def fetchrow(self, query, *args):
        if "SELECT id, name, slug" in query:
            return self.tenant
        if "UPDATE tenants" in query:
            self.update_calls.append((query, args))
            assignments = query.split("SET", 1)[1].split("updated_at", 1)[0]
            changed_fields = [
                part.strip().split(" ", 1)[0]
                for part in assignments.split(",")
                if part.strip()
            ]
            updated = dict(self.tenant)
            for field_name, value in zip(changed_fields, args[:-1]):
                updated[field_name] = value
            updated["updated_at"] = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
            return updated
        return None
def make_tenant(tenant_id):
    return {
        "id": tenant_id,
        "name": "Phase Seven Clinic",
        "slug": "phase_seven_clinic",
        "status": "active",
        "plan_code": "trial",
        "trial_ends_at": datetime(2026, 5, 30, tzinfo=timezone.utc),
        "current_period_end": None,
        "razorpay_customer_id": "cust_existing",
        "razorpay_subscription_id": None,
        "updated_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
    }
def test_tenant_billing_noop_patch_does_not_write_or_audit():
    tenant_id = uuid4()
    conn = FakeBillingConnection(make_tenant(tenant_id))
    audit = AsyncMock()
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    try:
        with (
            patch("backend.api.routes.admin.db_client.acquire", return_value=FakeAcquire(conn)),
            patch("backend.api.routes.admin.db_client.write_audit_log", audit),
        ):
            response = TestClient(app).patch(
                f"/api/v1/admin/billing/tenants/{tenant_id}",
                json={
                    "plan_code": "trial",
                    "status": "active",
                    "razorpay_customer_id": "cust_existing",
                },
            )
    finally:
        app.dependency_overrides.pop(require_super_admin, None)
    assert response.status_code == 200
    assert response.json()["data"]["noop"] is True
    assert response.json()["data"]["changed_fields"] == []
    assert conn.update_calls == []
    audit.assert_not_awaited()
def test_admin_plan_rejects_negative_monthly_price_before_db_write():
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    try:
        with patch("backend.api.routes.admin.db_client.acquire") as acquire:
            response = TestClient(app).post(
                "/api/v1/admin/plans",
                json={
                    "name": "Negative Price Test",
                    "plan_code": "negative_price_test",
                    "monthly_price_cents": -100,
                    "currency": "INR",
                    "razorpay_plan_id": "plan_negative_price_test",
                    "is_active": False,
                    "features_json": [],
                    "limits_json": {},
                },
            )
    finally:
        app.dependency_overrides.pop(require_super_admin, None)
    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "monthly_price_cents" in str(payload["error"].get("details", {}))
    acquire.assert_not_called()
def test_tenant_billing_patch_writes_only_changed_fields_and_audits():
    tenant_id = uuid4()
    conn = FakeBillingConnection(make_tenant(tenant_id))
    audit = AsyncMock()
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    try:
        with (
            patch("backend.api.routes.admin.db_client.acquire", return_value=FakeAcquire(conn)),
            patch("backend.api.routes.admin.db_client.write_audit_log", audit),
        ):
            response = TestClient(app).patch(
                f"/api/v1/admin/billing/tenants/{tenant_id}",
                json={
                    "status": "suspended",
                    "razorpay_customer_id": "",
                    "reason": "phase_7_regression",
                },
            )
    finally:
        app.dependency_overrides.pop(require_super_admin, None)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["noop"] is False
    assert data["changed_fields"] == ["razorpay_customer_id", "status"]
    assert len(conn.update_calls) == 1
    query, args = conn.update_calls[0]
    assert "status = $1::tenant_status" in query
    assert "razorpay_customer_id = $2" in query
    assert "plan_code =" not in query
    assert args == ("suspended", None, tenant_id)
    audit.assert_awaited_once()
    audit_details = audit.await_args.kwargs["details"]
    assert audit_details["reason"] == "phase_7_regression"
    assert audit_details["changed_fields"] == ["razorpay_customer_id", "status"]

from backend.api.dependencies import require_tenant_admin

def test_create_subscription_does_not_update_plan_code():
    tenant_id = uuid4()
    plan_id = "plan_enterprise"
    plan_code = "enterprise"

    async def override_require_tenant_admin():
        return {
            "user_id": uuid4(),
            "tenant_id": tenant_id,
            "role": "tenant_admin",
        }

    mock_subscription = {"id": "sub_test123", "customer_id": "cust_test123"}

    class FakeSubscribeConnection:
        def __init__(self):
            self.executed_queries = []
        async def fetchrow(self, query, *args):
            if "SELECT plan_code" in query:
                return {"plan_code": plan_code, "razorpay_plan_id": plan_id}
            if "SELECT t.id, t.name" in query:
                return {
                    "id": tenant_id,
                    "name": "Test Tenant",
                    "razorpay_customer_id": None,
                    "email": "admin@example.com",
                    "phone_number": "123456789"
                }
            return None
        async def execute(self, query, *args):
            self.executed_queries.append((query, args))
            return "UPDATE 1"

    conn = FakeSubscribeConnection()
    app.dependency_overrides[require_tenant_admin] = override_require_tenant_admin

    import sys
    from unittest.mock import MagicMock
    mock_razorpay = MagicMock()
    # Save the original module if it exists
    original_razorpay = sys.modules.get("razorpay")
    sys.modules["razorpay"] = mock_razorpay
    mock_client = mock_razorpay.Client.return_value
    mock_client.subscription.create.return_value = mock_subscription

    try:
        with (
            patch("backend.api.routes.subscriptions.db_client.acquire", return_value=FakeAcquire(conn)),
            patch("backend.api.routes.subscriptions.db_client.write_audit_log") as mock_audit,
            patch("backend.api.routes.subscriptions.settings") as mock_settings
        ):
            mock_settings.allow_mock_billing = False
            mock_settings.RAZORPAY_KEY_ID = "real_key"
            mock_settings.RAZORPAY_KEY_SECRET = "real_secret"

            response = TestClient(app).post(
                "/api/v1/billing/subscribe",
                json={"plan_id": plan_id}
            )
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
        if original_razorpay is not None:
            sys.modules["razorpay"] = original_razorpay
        else:
            sys.modules.pop("razorpay", None)

    assert response.status_code == 200
    assert response.json()["success"] is True

    update_calls = [q for q in conn.executed_queries if "UPDATE tenants" in q[0]]
    assert len(update_calls) == 1
    query_str, args = update_calls[0]
    assert "plan_code" not in query_str
    assert "razorpay_subscription_id" in query_str
    assert args == ("sub_test123", "cust_test123", tenant_id)
