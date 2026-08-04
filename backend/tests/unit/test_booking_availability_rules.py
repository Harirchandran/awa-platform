from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.modules.booking_module import booking_module
pytestmark = pytest.mark.anyio


class FixedDateTime(datetime):
    fixed_now_utc = datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        current = cls.fixed_now_utc
        if tz is None:
            return current.replace(tzinfo=None)
        return current.astimezone(tz)


def _future_monday() -> date:
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)
def _context(step_data, booking_settings=None):
    tenant_id = uuid4()
    config_data = {
        "business_name": "Smile Clinic",
        "timezone": "Asia/Kolkata",
        "working_hours": {
            "monday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
            "tuesday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
            "wednesday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
            "thursday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
            "friday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
            "saturday": {"is_open": False, "open_time": "09:00", "close_time": "17:00"},
            "sunday": {"is_open": False, "open_time": "09:00", "close_time": "17:00"},
        },
        "services": [
            {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45, "price": 800},
        ],
        "modules": {"booking": {"enabled": True, "fields": []}},
        "booking_settings": booking_settings or {},
    }
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Smile Clinic", plan_code="enterprise", status="active"),
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
        incoming_message={"text": "confirm"},
    )
async def test_booking_rejects_slot_outside_working_hours(monkeypatch):
    service = SimpleNamespace(create_booking=AsyncMock())
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        {
            "service_id": "cleaning",
            "preferred_date": _future_monday().isoformat(),
            "preferred_time": "16:30",
            "customer_name": "Asha",
        }
    )
    result = await booking_module.execute(context)
    assert result.success is False
    assert "outside business hours" in result.user_message
    service.create_booking.assert_not_called()
async def test_booking_fails_safely_when_approval_is_required(monkeypatch):
    service = SimpleNamespace(create_booking=AsyncMock())
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        {
            "service_id": "cleaning",
            "preferred_date": _future_monday().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        },
        booking_settings={"confirmation_mode": "approval_required"},
    )
    result = await booking_module.execute(context)
    assert result.success is False
    assert "staff approval" in result.user_message
    service.create_booking.assert_not_called()
async def test_booking_passes_duration_buffer_and_day_limit_to_db(monkeypatch):
    booking_id = uuid4()
    service = SimpleNamespace(
        create_booking=AsyncMock(
            return_value={
                "id": booking_id,
                "status": "confirmed",
            }
        )
    )
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        {
            "service_id": "cleaning",
            "preferred_date": _future_monday().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        },
        booking_settings={
            "buffer_before_mins": 10,
            "buffer_after_mins": 15,
            "max_bookings_per_day": 3,
        },
    )
    result = await booking_module.execute(context)
    assert result.success is True
    assert result.created_resource_type == "booking"
    call = service.create_booking.call_args.kwargs
    assert call["service_name"] == "Dental Cleaning"
    assert call["duration_mins"] == 45
    assert call["buffer_before_mins"] == 10
    assert call["buffer_after_mins"] == 15
    assert call["max_bookings_per_day"] == 3
    assert call["booking_time"] == time(10, 0)
    assert call["booking_data"]["service_duration_mins"] == 45
async def test_booking_returns_customer_safe_collision_message(monkeypatch):
    async def unavailable(**kwargs):
        raise ValueError("That time is already booked. Please choose another available slot.")
    service = SimpleNamespace(create_booking=AsyncMock(side_effect=unavailable))
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        {
            "service_id": "cleaning",
            "preferred_date": _future_monday().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        }
    )
    result = await booking_module.execute(context)
    assert result.success is False
    assert result.user_message == "That time is already booked. Please choose another available slot."


async def test_relative_booking_dates_use_tenant_timezone(monkeypatch):
    monkeypatch.setattr("backend.modules.booking_module.datetime", FixedDateTime)
    FixedDateTime.fixed_now_utc = datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc)

    assert booking_module._coerce_booking_date("today", {"timezone": "Asia/Kolkata"}) == date(2026, 1, 2)
    assert booking_module._coerce_booking_date("tomorrow", {"timezone": "Asia/Kolkata"}) == date(2026, 1, 3)
    assert booking_module._coerce_booking_date("today", {"timezone": "America/Los_Angeles"}) == date(2026, 1, 1)
    assert booking_module._coerce_booking_date("tomorrow", {"timezone": "America/Los_Angeles"}) == date(2026, 1, 2)
    assert booking_module._coerce_booking_date("today", {"timezone": "Not/AZone"}) == date(2026, 1, 1)


async def test_ambiguous_same_day_past_time_asks_for_am_pm(monkeypatch):
    monkeypatch.setattr("backend.modules.booking_module.datetime", FixedDateTime)
    FixedDateTime.fixed_now_utc = datetime(2026, 1, 2, 4, 30, tzinfo=timezone.utc)  # 10:00 Asia/Kolkata
    service = SimpleNamespace(create_booking=AsyncMock())
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        {
            "service_id": "cleaning",
            "preferred_date": "today",
            "preferred_time": "8",
            "customer_name": "Asha",
        }
    )

    result = await booking_module.execute(context)

    assert result.success is False
    assert "AM or PM" in result.user_message
    service.create_booking.assert_not_called()


async def test_explicit_am_pm_and_future_ambiguous_time_remain_valid(monkeypatch):
    monkeypatch.setattr("backend.modules.booking_module.datetime", FixedDateTime)
    FixedDateTime.fixed_now_utc = datetime(2026, 1, 2, 4, 30, tzinfo=timezone.utc)  # 10:00 Asia/Kolkata

    assert booking_module._coerce_booking_time("8 PM", {"timezone": "Asia/Kolkata"}) == time(20, 0)
    assert booking_module._coerce_booking_time("8 AM", {"timezone": "Asia/Kolkata"}) == time(8, 0)

    service = SimpleNamespace(create_booking=AsyncMock(return_value={"id": uuid4(), "status": "confirmed"}))
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        {
            "service_id": "cleaning",
            "preferred_date": "tomorrow",
            "preferred_time": "8",
            "customer_name": "Asha",
        }
    )
    context.config.config_data["working_hours"]["saturday"] = {
        "is_open": True,
        "open_time": "07:00",
        "close_time": "17:00",
    }

    result = await booking_module.execute(context)

    assert result.success is True
    assert service.create_booking.await_args.kwargs["booking_time"] == time(8, 0)
