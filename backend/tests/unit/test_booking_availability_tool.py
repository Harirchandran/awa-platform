from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.billing.usage_guard import UsageGuard
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.domains.bookings.repository import BookingRepository
from backend.domains.bookings.service import BookingService
from backend.modules.booking_availability_module import booking_availability_module
from backend.modules.booking_module import booking_module
from backend.modules.human_handoff_module import human_handoff_module
from backend.tools.registry import ToolRegistry


pytestmark = pytest.mark.anyio


class AllowBookingUsageDb:
    async def get_tenant_usage_summary(self, tenant_id):
        return {
            "today_message_count": 0,
            "month_message_count": 0,
            "month_ai_tokens_used": 0,
            "month_credits_used": 0,
            "month_tool_action_count": 0,
        }

    async def get_subscription_plan_by_code(self, plan_code):
        return {
            "plan_code": plan_code,
            "limits_json": {
                "allowed_modules": ["booking", "human_handoff"],
                "allowed_ai_modes": ["standard", "concierge"],
            },
        }

    async def upsert_cost_daily(self, **kwargs):
        return True


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FixedDateTime(datetime):
    fixed_now_utc = datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.fixed_now_utc.replace(tzinfo=None)
        return cls.fixed_now_utc.astimezone(tz)


def _working_hours(open_time="09:00", close_time="18:00"):
    return {
        day: {"is_open": True, "open_time": open_time, "close_time": close_time}
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    }


def make_context(
    *,
    preferred_date=None,
    preferred_time="17:15",
    earliest_start_time=None,
    timezone_name="Asia/Kolkata",
    booking_settings=None,
    incoming_text="Is that slot available?",
    extra_step_data=None,
):
    tenant_id = uuid4()
    target_date = preferred_date or (date.today() + timedelta(days=7)).isoformat()
    step_data = {
        "service_name": "Dental Cleaning",
        "preferred_date": target_date,
    }
    if preferred_time is not None:
        step_data["preferred_time"] = preferred_time
    if earliest_start_time is not None:
        step_data["earliest_start_time"] = earliest_start_time
    step_data.update(extra_step_data or {})
    state = SessionState()
    state.update_task_step_data("booking", step_data)
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Smile Clinic", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="booking",
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Smile Clinic",
                "timezone": timezone_name,
                "working_hours": _working_hours(),
                "services": [
                    {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45, "price": 800},
                ],
                "modules": {
                    "booking": {"enabled": True, "fields": []},
                    "human_handoff": {"enabled": True, "fields": []},
                },
                "booking_settings": booking_settings or {},
            },
        ),
        incoming_message={"text": incoming_text, "channel_type": "awa_chat"},
    )


def make_registry(*, include_booking_write=False, include_handoff=False):
    registry = ToolRegistry(guard=UsageGuard(AllowBookingUsageDb()))
    registry._write_audit = AsyncMock()
    registry.register_module(booking_availability_module)
    if include_booking_write:
        registry.register_module(booking_module)
    if include_handoff:
        registry.register_module(human_handoff_module)
    return registry


async def test_45_minute_service_can_start_at_1715_before_1800_close(monkeypatch):
    service = SimpleNamespace(list_busy_intervals=AsyncMock(return_value=[]))
    monkeypatch.setattr("backend.modules.booking_availability_module.booking_service", service)
    context = make_context(preferred_time="17:15")

    result = await make_registry().execute_tool("booking.availability", context)

    assert result.success is True
    assert result.data["service_duration_minutes"] == 45
    assert result.data["requested_slot"] == {
        "start_time": "17:15",
        "end_time": "18:00",
        "available": True,
        "reason": "available",
    }
    assert result.data["available_start_windows"] == [
        {"first_start_time": "09:00", "last_start_time": "17:15"}
    ]
    assert "would end at 18:00" in result.user_message
    assert "rechecked" in result.user_message
    service.list_busy_intervals.assert_awaited_once_with(
        context.tenant.id,
        date.fromisoformat(context.session_data.state.step_data["preferred_date"]),
    )


async def test_45_minute_service_cannot_start_at_1716_before_1800_close(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.booking_availability_module.booking_service",
        SimpleNamespace(list_busy_intervals=AsyncMock(return_value=[])),
    )
    context = make_context(preferred_time="17:16")

    result = await make_registry().execute_tool("booking.availability", context)

    assert result.success is True
    assert result.data["requested_slot"]["available"] is False
    assert result.data["requested_slot"]["reason"] == "ends_after_closing_time"
    assert result.data["requested_slot"]["end_time"] == "18:01"
    assert "latest currently valid start is 17:15" in result.user_message


async def test_after_five_is_an_earliest_constraint_not_an_exact_slot(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.booking_availability_module.booking_service",
        SimpleNamespace(list_busy_intervals=AsyncMock(return_value=[])),
    )
    context = make_context(
        preferred_time=None,
        earliest_start_time="17:00",
        incoming_text="What is available after five?",
    )

    result = await make_registry().execute_tool("booking.availability", context)

    assert result.success is True
    assert "requested_slot" not in result.data
    assert result.data["query_constraint"] == {
        "type": "earliest_start_time",
        "inclusive": True,
        "start_time": "17:00",
    }
    assert result.data["available_start_windows"] == [
        {"first_start_time": "17:00", "last_start_time": "17:15"}
    ]
    assert "at or after 17:00" in result.user_message
    assert "earliest_start_time" not in context.session_data.state.step_data
    assert "earliest_start_time" not in context.session_data.state.scoped_step_data["booking"]


async def test_range_language_recovers_extracted_time_without_false_unavailability(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.booking_availability_module.booking_service",
        SimpleNamespace(list_busy_intervals=AsyncMock(return_value=[])),
    )
    context = make_context(
        preferred_time="17:00",
        incoming_text="I need a dental cleaning next Tuesday after five; my name is Arjun.",
        extra_step_data={
            "customer_name": "Arjun",
            "special_requests": "Has insurance question",
        },
    )

    result = await make_registry().execute_tool("booking.availability", context)

    assert result.success is True
    assert "requested_slot" not in result.data
    assert result.data["available_start_windows"] == [
        {"first_start_time": "17:00", "last_start_time": "17:15"}
    ]
    assert "not available" not in result.user_message.lower()
    assert context.session_data.state.step_data["customer_name"] == "Arjun"
    assert context.session_data.state.step_data["special_requests"] == "Has insurance question"
    assert "preferred_time" not in context.session_data.state.step_data


async def test_missing_date_fails_closed_and_cleans_search_constraint(monkeypatch):
    service = SimpleNamespace(list_busy_intervals=AsyncMock(return_value=[]))
    monkeypatch.setattr("backend.modules.booking_availability_module.booking_service", service)
    context = make_context(preferred_time=None, earliest_start_time="17:00")
    context.session_data.state.step_data.pop("preferred_date")
    context.session_data.state.scoped_step_data["booking"].pop("preferred_date")

    result = await make_registry().execute_tool("booking.availability", context)

    assert result.success is False
    assert result.denied_reason == "argument_validation_failed:booking"
    assert result.validation_errors["preferred_date"] == "This required field is missing."
    assert "earliest_start_time" not in context.session_data.state.step_data
    assert "earliest_start_time" not in context.session_data.state.scoped_step_data["booking"]
    service.list_busy_intervals.assert_not_awaited()


async def test_existing_booking_blocks_overlap_without_exposing_booking_identity(monkeypatch):
    target_date = date.today() + timedelta(days=7)
    rows = [{"booking_date": target_date, "booking_time": time(17, 0), "duration_mins": 30}]
    monkeypatch.setattr(
        "backend.modules.booking_availability_module.booking_service",
        SimpleNamespace(list_busy_intervals=AsyncMock(return_value=rows)),
    )
    context = make_context(preferred_date=target_date.isoformat(), preferred_time="17:15")

    result = await make_registry().execute_tool("booking.availability", context)

    assert result.success is True
    assert result.data["requested_slot"]["available"] is False
    assert result.data["requested_slot"]["reason"] == "overlaps_existing_booking"
    assert result.data["available_start_windows"] == [
        {"first_start_time": "09:00", "last_start_time": "16:15"}
    ]
    serialized = result.to_tool_message()
    assert "booking_id" not in serialized
    assert "customer" not in serialized.lower()


async def test_date_search_uses_tenant_timezone_for_relative_today(monkeypatch):
    monkeypatch.setattr("backend.modules.booking_availability_module.datetime", FixedDateTime)
    monkeypatch.setattr("backend.modules.booking_module.datetime", FixedDateTime)
    service = SimpleNamespace(list_busy_intervals=AsyncMock(return_value=[]))
    monkeypatch.setattr("backend.modules.booking_availability_module.booking_service", service)

    kolkata = make_context(preferred_date="today", preferred_time=None, timezone_name="Asia/Kolkata")
    await make_registry().execute_tool("booking.availability", kolkata)
    assert service.list_busy_intervals.await_args.args[1] == date(2026, 1, 2)

    los_angeles = make_context(preferred_date="today", preferred_time=None, timezone_name="America/Los_Angeles")
    await make_registry().execute_tool("booking.availability", los_angeles)
    assert service.list_busy_intervals.await_args.args[1] == date(2026, 1, 1)


async def test_read_only_availability_is_exposed_only_in_booking_scope():
    registry = make_registry(include_booking_write=True, include_handoff=True)
    context = make_context()

    booking_names = [
        tool["function"]["name"]
        for tool in await registry.get_llm_tools_for_context(
            context,
            intent="booking",
            route_type="workflow",
            current_task="booking",
        )
    ]
    assert "booking_check_availability" in booking_names
    assert "booking_execute" in booking_names

    faq_names = [
        tool["function"]["name"]
        for tool in await registry.get_llm_tools_for_context(
            context,
            intent="faq",
            route_type="direct_answer",
        )
    ]
    assert "booking_check_availability" not in faq_names
    assert faq_names == ["human_handoff_execute"]

    metadata = booking_availability_module.get_tool_metadata(context.config.config_data)
    assert metadata.risk_level == "read"
    assert metadata.requires_confirmation is False
    assert metadata.requires_verified_customer is False


def test_availability_schema_exposes_no_internal_or_customer_identifiers():
    schema = booking_availability_module.get_tool_schema({})

    assert set(schema["properties"]) == {
        "service_name",
        "preferred_date",
        "preferred_time",
        "earliest_start_time",
    }
    assert schema["required"] == ["service_name", "preferred_date"]
    serialized = str(schema).lower()
    for blocked in ("service_id", "booking_id", "customer_id", "uuid", "phone", "email"):
        assert blocked not in serialized


async def test_repository_busy_interval_read_is_tenant_scoped_and_minimal():
    tenant_id = uuid4()
    target_date = date(2026, 8, 3)
    connection = AsyncMock()
    connection.fetch = AsyncMock(
        return_value=[
            {"booking_date": target_date, "booking_time": time(10, 0), "duration_mins": 45}
        ]
    )
    repository = BookingRepository(SimpleNamespace(acquire=lambda: FakeAcquire(connection)))

    rows = await repository.list_busy_intervals(tenant_id, target_date)

    assert rows == [{"booking_date": target_date, "booking_time": time(10, 0), "duration_mins": 45}]
    query, query_tenant_id, query_date = connection.fetch.await_args.args
    normalized_query = " ".join(query.lower().split())
    assert query_tenant_id == tenant_id
    assert query_date == target_date
    assert "where tenant_id = $1" in normalized_query
    assert "status != 'cancelled'" in normalized_query
    assert "customer" not in normalized_query
    assert "conversation" not in normalized_query
    assert "select id" not in normalized_query


async def test_booking_service_delegates_busy_interval_read():
    tenant_id = uuid4()
    target_date = date(2026, 8, 3)
    expected = [{"booking_date": target_date, "booking_time": time(10, 0), "duration_mins": 45}]
    repository = SimpleNamespace(list_busy_intervals=AsyncMock(return_value=expected))
    service = BookingService(repository=repository)

    assert await service.list_busy_intervals(tenant_id, target_date) == expected
    repository.list_busy_intervals.assert_awaited_once_with(tenant_id, target_date)
