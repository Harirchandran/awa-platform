import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4, UUID
from fastapi.testclient import TestClient
import asyncpg
from fastapi import HTTPException

from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.db.client import db_client
from backend.domains.payments.service import payment_service

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000099")

async def override_get_current_user():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}

@pytest.mark.anyio
async def test_get_payment_metrics_api():
    app.dependency_overrides[get_current_user] = override_get_current_user
    client = TestClient(app)

    mock_metrics = {
        "pending": 5,
        "needs_attention": 2,
        "paid_amount": 1250.50
    }

    with patch.object(payment_service, "get_payment_metrics", new_callable=AsyncMock) as mock_get_metrics:
        mock_get_metrics.return_value = mock_metrics
        response = client.get("/api/v1/payments/metrics")

        assert response.status_code == 200
        json_resp = response.json()
        assert json_resp["success"] is True
        assert json_resp["data"] == mock_metrics
        mock_get_metrics.assert_called_once_with(TENANT_ID)

    app.dependency_overrides.pop(get_current_user, None)

@pytest.mark.anyio
async def test_create_payment_fk_violation():
    app.dependency_overrides[get_current_user] = override_get_current_user
    client = TestClient(app)

    with patch.object(payment_service, "create_payment_request", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = asyncpg.ForeignKeyViolationError("insert or update on table violates foreign key constraint")

        with patch("backend.api.routes.payments.require_module_operational_write", AsyncMock()):
            response = client.post(
                "/api/v1/payments",
                json={
                    "amount": 100.0,
                    "currency": "INR",
                    "type": "custom_payment",
                    "provider": "manual",
                    "customer_id": str(uuid4()),
                }
            )

        assert response.status_code == 400
        assert "Invalid reference" in response.json()["error"]["message"]

    app.dependency_overrides.pop(get_current_user, None)

@pytest.mark.anyio
async def test_partial_payment_sync_mapping():
    # Test action_payment_request partial_payment mapping in db_client
    class FakeTransaction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def __init__(self):
            self.executed = []
        def transaction(self):
            return FakeTransaction()
        async def fetchval(self, query, *args):
            return None
        async def fetchrow(self, query, *args):
            return {
                "id": uuid4(),
                "order_id": uuid4(),
                "status": "draft",
            }
        async def execute(self, query, *args):
            self.executed.append((query, args))
            return "UPDATE 1"

    fake_conn = FakeConnection()

    class MockContext:
        async def __aenter__(self):
            return fake_conn
        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch.object(db_client, "tenant_context", return_value=MockContext()), \
         patch.object(db_client, "get_payment_request", new_callable=AsyncMock) as mock_get_req:

        mock_get_req.return_value = {"id": uuid4(), "status": "sent"}

        await db_client.action_payment_request(
            tenant_id=TENANT_ID,
            payment_id=uuid4(),
            action="partial_payment"
        )

        args_passed = [exec_call for exec_call in fake_conn.executed if "UPDATE orders" in exec_call[0]]
        assert len(args_passed) == 1
        # The arguments are: (tenant_id, order_id, order_status)
        # So "pending" should be the third argument (index 2 in execute params)
        assert args_passed[0][1][2] == "pending"
