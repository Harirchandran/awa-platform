from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.dependencies import get_current_user


TENANT_A = uuid4()
TENANT_B = uuid4()
USER_A = uuid4()


def _client_for(role="tenant_admin"):
    async def user():
        return {"user_id": USER_A, "tenant_id": TENANT_A, "role": role}

    app.dependency_overrides[get_current_user] = user
    return TestClient(app)


def _tenant_arg(mock):
    if mock.await_args.args:
        return mock.await_args.args[0]
    return mock.await_args.kwargs.get("tenant_id")


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_tenant_lists_are_scoped_to_authenticated_tenant():
    client = _client_for("member")
    with (
        patch("backend.api.routes.conversations.db_client.list_conversations", new=AsyncMock(return_value={"items": [], "next_cursor": None})) as conversations,
        patch("backend.api.routes.customers.customer_service.list_customers", new=AsyncMock(return_value={"items": [], "next_cursor": None})) as customers,
        patch("backend.api.routes.leads.lead_service.list_leads", new=AsyncMock(return_value={"items": [], "next_cursor": None})) as leads,
        patch("backend.api.routes.bookings.booking_service.list_bookings", new=AsyncMock(return_value={"items": [], "next_cursor": None})) as bookings,
        patch("backend.api.routes.orders.order_service.list_orders", new=AsyncMock(return_value={"items": [], "next_cursor": None})) as orders,
        patch("backend.api.routes.payments.payment_service.list_payment_requests", new=AsyncMock(return_value={"items": [], "next_cursor": None})) as payments,
    ):
        for path in ("/api/v1/conversations", "/api/v1/customers", "/api/v1/leads", "/api/v1/bookings", "/api/v1/orders", "/api/v1/payments"):
            response = client.get(path)
            assert response.status_code == 200

    conversations.assert_awaited_once()
    customers.assert_awaited_once()
    leads.assert_awaited_once()
    bookings.assert_awaited_once()
    orders.assert_awaited_once()
    payments.assert_awaited_once()
    for mock in (conversations, customers, leads, bookings, orders, payments):
        assert _tenant_arg(mock) == TENANT_A


def test_tenant_a_cannot_patch_tenant_b_resources_when_db_scopes_by_tenant():
    client = _client_for("tenant_admin")
    other_id = uuid4()
    with (
        patch("backend.api.routes.leads.lead_service.update_lead_status", new=AsyncMock(return_value=False)) as lead_update,
        patch("backend.api.routes.leads.require_module_operational_write", new=AsyncMock()),
        patch("backend.api.routes.bookings.booking_service.update_booking", new=AsyncMock(return_value=False)) as booking_update,
        patch("backend.api.routes.bookings.require_module_operational_write", new=AsyncMock()),
        patch("backend.api.routes.orders.order_service.update_order", new=AsyncMock(return_value=False)) as order_update,
        patch("backend.api.routes.orders.require_module_operational_write", new=AsyncMock()),
        patch("backend.api.routes.payments.payment_service.update_payment_request", new=AsyncMock(return_value=None)) as payment_update,
        patch("backend.api.routes.payments.require_module_operational_write", new=AsyncMock()),
    ):
        assert client.patch(f"/api/v1/leads/{other_id}", json={"status": "contacted"}).status_code == 404
        assert client.patch(f"/api/v1/bookings/{other_id}", json={"status": "cancelled"}).status_code == 400
        assert client.patch(f"/api/v1/orders/{other_id}", json={"status": "cancelled"}).status_code == 400
        assert client.patch(f"/api/v1/payments/{other_id}", json={"amount": 123}).status_code == 404

    assert lead_update.await_args.args[:2] == (TENANT_A, other_id)
    assert booking_update.await_args.args[:2] == (TENANT_A, other_id)
    assert order_update.await_args.args[:2] == (TENANT_A, other_id)
    assert payment_update.await_args.args[:2] == (TENANT_A, other_id)


def test_tenant_a_public_chat_token_cannot_open_tenant_b_session():
    client = TestClient(app)
    session_id = uuid4()
    with patch("backend.api.routes.public_chat.hash_public_chat_token", return_value="tenant-a-token-hash"), patch(
        "backend.api.routes.public_chat.db_client.get_public_chat_session",
        new=AsyncMock(return_value=None),
    ) as get_session:
        response = client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages/list",
            json={"session_token": "tenant-a-token-with-valid-length"},
        )
    assert response.status_code in {401, 403, 404, 422}
    get_session.assert_awaited_once_with(session_id, "tenant-a-token-hash")
