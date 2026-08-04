from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.bookings.repository import BookingRepository
from backend.domains.bookings.service import BookingService
from backend.domains.orders.repository import OrderRepository
from backend.domains.orders.service import OrderService
from backend.modules.booking_module import booking_module
from backend.modules.order_module import order_module

pytestmark = pytest.mark.anyio


class FakeEvents:
    def __init__(self):
        self.emitted = []

    async def emit(self, event):
        self.emitted.append(event)
        return {"id": uuid4(), "event_type": event.event_type}


class FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    async def execute(self, *args, **kwargs):
        pass
    def transaction(self):
        return FakeTransaction()
    async def fetchrow(self, *args, **kwargs):
        return None


class FakeConnectionContext:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False


class TenantScopedBookingDb:
    def __init__(self, tenant_id, other_tenant_id):
        self.tenant_id = tenant_id
        self.other_tenant_id = other_tenant_id
        self.booking_id = uuid4()
        self.create_booking = AsyncMock(side_effect=self._create_booking)
        self.list_bookings = AsyncMock(side_effect=self._list_bookings)
        self.get_booking = AsyncMock(side_effect=self._get_booking)
        self.update_booking = AsyncMock(side_effect=self._update_booking)

    def acquire(self):
        return FakeConnectionContext(FakeConnection())

    async def _create_booking(self, **kwargs):
        assert kwargs["tenant_id"] == self.tenant_id
        return {"id": self.booking_id, "tenant_id": self.tenant_id, "status": "confirmed"}

    async def _list_bookings(self, tenant_id, **kwargs):
        items = [{"id": self.booking_id, "tenant_id": self.tenant_id}] if tenant_id == self.tenant_id else []
        return {"items": items, "next_cursor": None}

    async def _get_booking(self, tenant_id, booking_id):
        if tenant_id == self.tenant_id and booking_id == self.booking_id:
            return {"id": booking_id, "tenant_id": tenant_id}
        return None

    async def _update_booking(self, tenant_id, booking_id, **kwargs):
        return tenant_id == self.tenant_id and booking_id == self.booking_id


class TenantScopedOrderDb:
    def __init__(self, tenant_id, other_tenant_id):
        self.tenant_id = tenant_id
        self.other_tenant_id = other_tenant_id
        self.order_id = uuid4()
        self.item_id = uuid4()
        self.create_order = AsyncMock(side_effect=self._create_order)
        self.list_orders = AsyncMock(side_effect=self._list_orders)
        self.get_order = AsyncMock(side_effect=self._get_order)
        self.update_order = AsyncMock(side_effect=self._update_order)
        self.resolve_catalog_item = AsyncMock(return_value=None)

    async def _create_order(self, **kwargs):
        assert kwargs["tenant_id"] == self.tenant_id
        for item in kwargs.get("items") or []:
            assert item.get("tenant_id") in {None, self.tenant_id}
        return {"id": self.order_id, "tenant_id": self.tenant_id, "status": "new"}

    async def _list_orders(self, tenant_id, **kwargs):
        items = [{"id": self.order_id, "tenant_id": self.tenant_id}] if tenant_id == self.tenant_id else []
        return {"items": items, "next_cursor": None}

    async def _get_order(self, tenant_id, order_id):
        if tenant_id == self.tenant_id and order_id == self.order_id:
            return {
                "id": order_id,
                "tenant_id": tenant_id,
                "items": [{"id": self.item_id, "tenant_id": tenant_id, "order_id": order_id}],
            }
        return None

    async def _update_order(self, tenant_id, order_id, **kwargs):
        return tenant_id == self.tenant_id and order_id == self.order_id


def _future_monday() -> date:
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def _booking_context(step_data):
    tenant_id = uuid4()
    config_data = {
        "timezone": "Asia/Kolkata",
        "working_hours": {"monday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"}},
        "services": [{"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45}],
        "modules": {"booking": {"enabled": True, "fields": []}},
    }
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Clinic", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="booking",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"},
    )


def _order_context(step_data):
    tenant_id = uuid4()
    config_data = {
        "business_name": "Cafe",
        "modules": {
            "order": {
                "enabled": True,
                "currency": "INR",
                "fulfillment_types": ["pickup", "delivery"],
                "categories": [],
            }
        },
    }
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="order",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"},
    )


async def test_booking_repository_create_list_get_and_patch_are_tenant_scoped():
    tenant_a = uuid4()
    tenant_b = uuid4()
    db = TenantScopedBookingDb(tenant_a, tenant_b)
    repo = BookingRepository(db)

    created = await repo.create_booking(
        tenant_id=tenant_a,
        customer_id=uuid4(),
        conversation_id=uuid4(),
        service_name="Dental Cleaning",
        booking_date=date.today(),
        booking_time=time(10, 0),
    )
    assert created["tenant_id"] == tenant_a
    assert (await repo.list_bookings(tenant_a))["items"]
    assert (await repo.list_bookings(tenant_b))["items"] == []
    assert await repo.get_booking(tenant_a, db.booking_id)
    assert await repo.get_booking(tenant_b, db.booking_id) is None
    assert await repo.update_booking(tenant_a, db.booking_id, status="cancelled") is True
    assert await repo.update_booking(tenant_b, db.booking_id, status="cancelled") is False


async def test_booking_service_emits_created_event_only_after_new_create():
    tenant_id = uuid4()
    booking_id = uuid4()
    events = FakeEvents()
    repo = SimpleNamespace(
        create_booking=AsyncMock(return_value={"id": booking_id, "status": "confirmed", "_created": True})
    )
    service = BookingService(repository=repo, events=events)

    booking = await service.create_booking(
        tenant_id=tenant_id,
        customer_id=uuid4(),
        conversation_id=uuid4(),
        service_name="Dental Cleaning",
        booking_date=date.today(),
        booking_time=time(10, 0),
        booking_data={"service_id": "cleaning"},
        source_channel="awa_chat",
    )

    assert booking["id"] == booking_id
    assert len(events.emitted) == 1
    event = events.emitted[0]
    assert event.event_type == "booking.created"
    assert event.tenant_id == tenant_id
    assert event.payload["booking_id"] == str(booking_id)
    assert event.payload["source_channel"] == "awa_chat"

    repo.create_booking.return_value = {"id": booking_id, "status": "confirmed", "_created": False}
    await service.create_booking(
        tenant_id=tenant_id, customer_id=uuid4(), conversation_id=uuid4(),
        service_name="Dental Cleaning", booking_date=date.today(), booking_time=time(10, 0),
        idempotency_key="booking-same",
    )
    assert len(events.emitted) == 1


async def test_booking_completion_transition_emits_once_and_creation_does_not():
    tenant_id, booking_id, customer_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    record = {
        "id": booking_id, "tenant_id": tenant_id, "customer_id": customer_id,
        "conversation_id": conversation_id, "status": "confirmed",
        "booking_data": {"source_channel": "awa_chat"},
    }
    events = FakeEvents()
    repo = SimpleNamespace(
        get_booking=AsyncMock(side_effect=lambda *_: dict(record)),
        update_booking=AsyncMock(return_value=True),
    )
    service = BookingService(repository=repo, events=events)

    assert await service.update_booking(tenant_id, booking_id, status="completed") is True
    assert [event.event_type for event in events.emitted] == ["booking.completed"]
    event = events.emitted[0]
    assert event.aggregate_id == booking_id
    assert event.customer_id == customer_id
    assert event.conversation_id == conversation_id
    assert event.payload["source_channel"] == "awa_chat"

    record["status"] = "completed"
    await service.update_booking(tenant_id, booking_id, status="completed")
    await service.update_booking(tenant_id, booking_id, status="cancelled")
    assert len(events.emitted) == 1


async def test_booking_module_creates_booking_through_service(monkeypatch):
    booking_id = uuid4()
    service = SimpleNamespace(create_booking=AsyncMock(return_value={"id": booking_id, "status": "confirmed"}))
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _booking_context(
        {
            "service_id": "cleaning",
            "preferred_date": _future_monday().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        }
    )

    result = await booking_module.execute(context)

    assert result.success is True
    assert result.created_resource_type == "booking"
    service.create_booking.assert_awaited_once()
    assert service.create_booking.await_args.kwargs["tenant_id"] == context.tenant.id


async def test_order_repository_create_list_get_items_and_patch_are_tenant_scoped():
    tenant_a = uuid4()
    tenant_b = uuid4()
    db = TenantScopedOrderDb(tenant_a, tenant_b)
    repo = OrderRepository(db)

    created = await repo.create_order(
        tenant_id=tenant_a,
        customer_id=uuid4(),
        conversation_id=uuid4(),
        channel_type="awa_chat",
        status="new",
        currency="INR",
        subtotal_amount=20,
        total_amount=20,
        fulfillment_type="pickup",
        payment_method="cash",
        payment_status="not_collected",
        items=[{"tenant_id": tenant_a, "item_id": "samosa", "quantity": 1}],
    )
    assert created["tenant_id"] == tenant_a
    assert (await repo.list_orders(tenant_a))["items"]
    assert (await repo.list_orders(tenant_b))["items"] == []
    assert (await repo.get_order(tenant_a, db.order_id))["items"][0]["tenant_id"] == tenant_a
    assert await repo.get_order(tenant_b, db.order_id) is None
    assert await repo.update_order(tenant_a, db.order_id, status="cancelled") is True
    assert await repo.update_order(tenant_b, db.order_id, status="cancelled") is False


async def test_order_service_emits_created_event_only_after_new_create():
    tenant_id = uuid4()
    order_id = uuid4()
    events = FakeEvents()
    repo = SimpleNamespace(
        create_order=AsyncMock(return_value={"id": order_id, "status": "new", "_created": True})
    )
    service = OrderService(repository=repo, events=events)

    order = await service.create_order(
        tenant_id=tenant_id,
        customer_id=uuid4(),
        conversation_id=uuid4(),
        channel_type="awa_chat",
        status="new",
        currency="INR",
        subtotal_amount=20,
        total_amount=20,
        fulfillment_type="pickup",
        payment_method="cash",
        payment_status="not_collected",
        items=[{"item_id": "samosa"}],
    )

    assert order["id"] == order_id
    assert len(events.emitted) == 1
    event = events.emitted[0]
    assert event.event_type == "order.created"
    assert event.tenant_id == tenant_id
    assert event.payload["order_id"] == str(order_id)
    assert event.payload["item_count"] == 1

    repo.create_order.return_value = {"id": order_id, "status": "new", "_created": False}
    await service.create_order(
        tenant_id=tenant_id,
        customer_id=uuid4(),
        conversation_id=uuid4(),
        channel_type="awa_chat",
        status="new",
        currency="INR",
        subtotal_amount=20,
        total_amount=20,
        fulfillment_type="pickup",
        payment_method="cash",
        payment_status="not_collected",
        idempotency_key="order-same",
    )
    assert len(events.emitted) == 1


async def test_order_module_creates_order_through_service_and_invalid_item_has_no_event(monkeypatch):
    order_id = uuid4()
    catalog_item_id = uuid4()
    service = SimpleNamespace(
        resolve_catalog_item=AsyncMock(return_value={
            "id": catalog_item_id,
            "name": "Samosa",
            "price": 20,
            "currency": "INR",
            "source": "catalog_db",
        }),
        create_order=AsyncMock(return_value={"id": order_id, "payment_status": "not_collected"}),
    )
    monkeypatch.setattr("backend.modules.order_module.order_service", service)
    monkeypatch.setattr("backend.modules.order_module.catalog_service", service)
    context = _order_context(
        {
            "item_id": "samosa",
            "quantity": 2,
            "fulfillment_type": "pickup",
            "customer_name": "Asha",
            "phone": "+919999999999",
        }
    )

    result = await order_module.execute(context)

    assert result.success is True
    assert result.created_resource_type == "order"
    service.create_order.assert_awaited_once()
    assert service.create_order.await_args.kwargs["tenant_id"] == context.tenant.id

    service.resolve_catalog_item.return_value = None
    service.create_order.reset_mock()
    missing_context = _order_context(
        {
            "item_id": "missing",
            "quantity": 1,
            "fulfillment_type": "pickup",
            "customer_name": "Asha",
            "phone": "+919999999999",
        }
    )
    missing = await order_module.execute(missing_context)
    assert missing.success is False
    service.create_order.assert_not_called()
