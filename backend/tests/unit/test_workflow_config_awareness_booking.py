from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, PendingAction, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.core import _set_runtime_available_modules, _unavailable_active_workflow_response
from backend.modules.booking_module import booking_module

pytestmark = pytest.mark.anyio


def _future_day(days: int = 2) -> date:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date()


def _booking_config(**settings):
    return {
        "timezone": "UTC",
        "working_hours": {
            day: {"is_open": True, "open_time": "09:00", "close_time": "17:00"}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        },
        "services": [{"id": "consult", "name": "Consultation", "duration_mins": 30, "price": 100}],
        "booking_settings": settings,
        "modules": {
            "booking": {
                "enabled": True,
                "fields": [
                    {"key": "service_name", "label": "Service", "type": "string", "required": True},
                    {"key": "preferred_date", "label": "Date", "type": "date", "required": True},
                    {"key": "preferred_time", "label": "Time", "type": "time", "required": True},
                    {"key": "customer_name", "label": "Name", "type": "string", "required": True},
                ],
            }
        },
    }


def _context(step_data=None, config=None):
    tenant_id = uuid4()
    state = SessionState(
        workflow_state=ConversationState.EXECUTING,
        step_data=dict(step_data or {}),
        scoped_step_data={"booking": dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = "booking"
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Booking Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="booking",
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config or _booking_config()),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"},
    )


async def test_invalid_service_rejected_before_persistence():
    service = SimpleNamespace(create_booking=AsyncMock())
    ctx = _context(
        {
            "service_name": "Unconfigured Service",
            "preferred_date": _future_day().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        }
    )

    result = await booking_module.execute(ctx)

    assert result.success is False
    assert "service" in result.user_message.lower()
    service.create_booking.assert_not_called()


async def test_working_hours_min_notice_and_advance_window_are_config_authority():
    outside_hours = _context(
        {
            "service_name": "Consultation",
            "preferred_date": _future_day().isoformat(),
            "preferred_time": "18:00",
            "customer_name": "Asha",
        }
    )
    assert "outside business hours" in (await booking_module.execute(outside_hours)).user_message

    too_soon = datetime.now(timezone.utc) + timedelta(minutes=10)
    min_notice = _context(
        {
            "service_name": "Consultation",
            "preferred_date": too_soon.date().isoformat(),
            "preferred_time": too_soon.time().replace(microsecond=0).isoformat(timespec="minutes"),
            "customer_name": "Asha",
        },
        config=_booking_config(minimum_notice_minutes=120),
    )
    assert "at least 120 minutes" in (await booking_module.execute(min_notice)).user_message

    too_far = _context(
        {
            "service_name": "Consultation",
            "preferred_date": _future_day(10).isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        },
        config=_booking_config(advance_booking_days=3),
    )
    assert "within the next 3 days" in (await booking_module.execute(too_far)).user_message


async def test_approval_required_does_not_fake_confirmed_booking():
    ctx = _context(
        {
            "service_name": "Consultation",
            "preferred_date": _future_day().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        },
        config=_booking_config(confirmation_mode="approval_required"),
    )

    result = await booking_module.execute(ctx)

    assert result.success is False
    assert "needs staff approval" in result.user_message
    assert result.created_resource_type is None


def test_stale_pending_booking_after_module_disabled_hides_confirm_and_clears_state():
    ctx = _context({"service_name": "Consultation"})
    ctx.session_data.state.action_ledger.pending_action = PendingAction(
        tool_key="booking.execute",
        args={"service_name": "Consultation"},
        summary="Please review this booking.",
        requires_confirmation=True,
    )
    _set_runtime_available_modules(ctx, {"support"})

    result = _unavailable_active_workflow_response(ctx, {"support"})

    assert result is not None
    assert "currently unavailable" in result.response_text
    assert "Confirm" not in result.quick_replies
    assert ctx.session_data.state.action_ledger.pending_action is None
