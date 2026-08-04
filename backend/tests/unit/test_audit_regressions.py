from uuid import UUID, uuid4
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.api.webhook import _extract_webhook_timestamp
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000099")
async def override_tenant_admin():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}
def test_order_payment_status_matches_database_contract():
    app.dependency_overrides[get_current_user] = override_tenant_admin
    client = TestClient(app)
    order_id = uuid4()
    try:
        with (
            patch("backend.api.routes.orders.order_service") as service,
            patch("backend.api.routes.orders.require_module_operational_write", new=AsyncMock()),
        ):
            service.update_order = AsyncMock(return_value=True)
            ok = client.patch(
                f"/api/v1/orders/{order_id}",
                json={"payment_status": "not_collected"},
            )
            bad = client.patch(
                f"/api/v1/orders/{order_id}",
                json={"payment_status": "cancelled"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert ok.status_code == 200
    assert bad.status_code == 422
def test_booking_invalid_status_rejected_before_db_call():
    app.dependency_overrides[get_current_user] = override_tenant_admin
    client = TestClient(app)
    try:
        with patch("backend.api.routes.bookings.booking_service") as service:
            service.list_bookings = AsyncMock(return_value={"items": [], "next_cursor": None})
            response = client.get("/api/v1/bookings?status=pending")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 422
    service.list_bookings.assert_not_called()
def test_webhook_replay_timestamp_falls_back_to_message_timestamp():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{"timestamp": "1779240000"}]
                }
            }]
        }]
    }
    assert _extract_webhook_timestamp(payload) == 1779240000
