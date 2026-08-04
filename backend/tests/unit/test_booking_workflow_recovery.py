from __future__ import annotations

import json
import re
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.config import settings
from backend.core.states import ConversationState
from backend.logic.engine.core import agentic_engine
from backend.logic.engine.field_extraction import (
    BookingFieldExtraction,
    BOOKING_FIELD_ALIASES,
    _gateway_semantic_input,
)
from backend.logic.engine.core import _booking_field_key_for_update, _booking_missing_required_fields
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.modules.booking_module import booking_module
from backend.tools.registry import ToolExecutionResult

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)

UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def test_booking_patient_name_alias_is_canonicalized_and_written_back():
    fields = [
        {"key": "service_name", "required": True},
        {"key": "patient_name", "required": True},
        {"key": "preferred_date", "required": True},
        {"key": "preferred_time", "required": True},
    ]
    context = _context(
        config_data=_booking_config(fields=fields),
        step_data={"service_name": "Consultation"},
        message_text="My name is Alex",
    )
    semantic_input = _gateway_semantic_input(context, workflow="booking", module_name="booking")
    schema_names = [item["field_name"] for item in semantic_input["configured_field_schema"]]

    assert BOOKING_FIELD_ALIASES["patient_name"] == "customer_name"
    assert schema_names == ["service_name", "customer_name", "booking_date", "booking_time"]
    assert "customer_name" in semantic_input["missing_field_names"]
    assert _booking_field_key_for_update("customer_name", {item["key"]: item for item in fields}, {}) == "patient_name"


def test_patient_name_satisfies_configured_requirement_without_duplicate_canonical_state():
    fields = [
        {"key": "patient_name", "required": True},
        {"key": "preferred_date", "required": True},
    ]
    context = _context(
        config_data=_booking_config(fields=fields),
        step_data={"patient_name": "Alex"},
        message_text="Alex, July 20 at 10 AM",
    )
    tool_def = SimpleNamespace(module=booking_module)
    missing = _booking_missing_required_fields(tool_def, context)
    semantic_input = _gateway_semantic_input(context, workflow="booking", module_name="booking")

    assert "patient_name" not in missing
    assert missing == ["service_name", "preferred_date"]
    assert semantic_input["collected_field_names"].count("customer_name") == 1


def _future_weekday() -> date:
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def _booking_config(*, enabled=True, services=None, fields=None):
    booking_fields = fields or [
        {"key": "service_name", "label": "Service", "type": "string", "required": True},
        {"key": "preferred_date", "label": "Preferred Date", "type": "date", "required": True},
        {"key": "preferred_time", "label": "Preferred Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "string", "required": False},
        {"key": "email", "label": "Email", "type": "email", "required": False},
        {"key": "notes", "label": "Notes", "type": "string", "required": False},
        {"key": "special_requests", "label": "Special Requests", "type": "string", "required": False},
    ]
    return {
        "timezone": "UTC",
        "working_hours": {
            day: {"is_open": True, "open_time": "09:00", "close_time": "17:00"}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        },
        "services": services or [
            {"id": "svc-public-a", "name": "Consultation", "duration_mins": 30, "price": 100},
            {"id": "svc-public-b", "name": "Follow Up", "duration_mins": 30, "price": 80},
        ],
        "modules": {"booking": {"enabled": enabled, "fields": booking_fields}},
        "booking_settings": {},
    }


def _context(
    *,
    step_data=None,
    config_data=None,
    enabled=True,
    current_module="booking",
    current_task="booking",
    message_text="black-box booking reply",
):
    tenant_id = uuid4()
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=dict(step_data or {}),
        scoped_step_data={"booking": dict(step_data or {})} if step_data else {},
    )
    if current_task:
        state.action_ledger.current_task = current_task
        state.action_ledger.task_status = "collecting"
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="Test User"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=current_module,
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data=config_data or _booking_config(enabled=enabled),
        ),
        incoming_message={"text": message_text, "channel_type": "awa_chat"},
    )


def _booking_route(context):
    from backend.pipeline.message_router import message_router

    return message_router.route_message(
        context.incoming_message.get("text", ""),
        context.config.config_data,
        current_task=context.session_data.state.action_ledger.current_task,
        current_module=context.session_data.current_module,
    )


def _semantic_booking_extraction(**kwargs):
    defaults = {
        "detected_language": "black-box",
        "workflow": "booking",
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": {},
        "field_confidence": {},
        "clarification_needed": False,
    }
    defaults.update(kwargs)
    return BookingFieldExtraction(**defaults)


async def _install_booking_extraction_stub(monkeypatch, extraction=None, exc: Exception | None = None):
    async def fake_extract(ctx):
        if exc:
            raise exc
        return extraction

    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", fake_extract)


def _service_handoff_fields():
    return [
        {"key": "guest_display_name", "label": "Guest", "type": "string", "required": True},
        {"key": "visit_date", "label": "Visit date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Visit time", "type": "time", "required": True},
    ]


async def test_service_handoff_persists_only_authoritative_service_before_custom_focus():
    context = _context(
        config_data=_booking_config(
            fields=_service_handoff_fields(),
            services=[{"id": "retest", "name": "Retest Consultation", "duration_mins": 30, "price": 0}],
        ),
        message_text="Retest Consultation",
    )

    result = await agentic_engine._handle_booking_service_selection(context)

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking") == {
        "service_id": "retest", "service_name": "Retest Consultation",
    }
    assert result.prompt_focus_fields == ["guest_display_name"]


@pytest.mark.parametrize(
    ("services", "message_text"),
    [
        ([{"id": "retest", "name": "Retest Consultation", "duration_mins": 30, "price": 0}], "Unknown service"),
        ([
            {"id": "first", "name": "Consultation", "duration_mins": 30, "price": 0},
            {"id": "second", "name": "Consultation", "duration_mins": 30, "price": 0},
        ], "Consultation"),
        ([{"id": "disabled", "name": "Retest Consultation", "duration_mins": 30, "price": 0, "enabled": False}], "Retest Consultation"),
    ],
)
async def test_unresolved_invalid_ambiguous_or_disabled_service_leaves_custom_state_unchanged(services, message_text):
    context = _context(
        config_data=_booking_config(fields=_service_handoff_fields(), services=services),
        message_text=message_text,
    )

    result = await agentic_engine._handle_booking_service_selection(context)

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking") == {}
    assert result.prompt_focus_fields == ["service_name"]


async def test_cancel_preempts_unresolved_service_selection():
    context = _context(
        config_data=_booking_config(fields=_service_handoff_fields()),
        message_text="cancel",
    )

    result = await agentic_engine._handle_booking_service_selection(context)

    assert result is not None
    assert result.next_state == ConversationState.CANCELLED
    assert context.session_data.state.get_task_step_data("booking") == {}


@pytest.mark.parametrize(
    "routing_decision",
    [
        SimpleNamespace(should_clear_active_task=True, is_tangent=False, intent="order", route_type="switch"),
        SimpleNamespace(should_clear_active_task=False, is_tangent=True, intent="faq", route_type="direct_answer"),
    ],
)
async def test_switch_or_tangent_preempts_unresolved_service_collection(routing_decision):
    context = _context(
        config_data=_booking_config(fields=_service_handoff_fields()),
        message_text="Retest Consultation",
    )

    result = await agentic_engine._handle_booking_service_selection(context, routing_decision)

    assert result is None
    assert context.session_data.state.get_task_step_data("booking") == {}


async def test_active_booking_semantic_stub_applies_service_date_time_and_customer_details(monkeypatch):
    context = _context(message_text="black-box mixed-language booking details")
    day = _future_weekday().isoformat()
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={
                "service_name": "Consultation",
                "booking_date": day,
                "booking_time": "10:00",
                "customer_name": "Asha",
                "phone": "+10000000000",
                "email": "asha@example.com",
                "notes": "Needs accessible entry",
                "special_requests": "Quiet room",
            },
            field_confidence={
                "service_name": 0.95,
                "booking_date": 0.94,
                "booking_time": 0.93,
                "customer_name": 0.92,
                "phone": 0.9,
                "email": 0.9,
                "notes": 0.9,
                "special_requests": 0.9,
            },
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.action_ledger.pending_action is not None
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]
    step_data = context.session_data.state.get_task_step_data("booking")
    assert step_data["service_name"] == "Consultation"
    assert step_data["preferred_date"] == day
    assert step_data["preferred_time"] == "10:00"
    assert step_data["customer_name"] == "Asha"
    assert step_data["phone"] == "+10000000000"
    assert step_data["email"] == "asha@example.com"
    assert step_data["notes"] == "Needs accessible entry"
    assert step_data["special_requests"] == "Quiet room"


async def test_booking_alias_updates_merge_atomically_without_erasing_existing_fields(monkeypatch):
    context = _context(
        step_data={"service_name": "Consultation", "preferred_date": _future_weekday().isoformat()},
    )
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"preferred_time": "16:00", "customer_name": "Arjun", "phone": "+919876543210"},
            field_confidence={"preferred_time": 0.99, "customer_name": 0.99, "phone": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))
    state = context.session_data.state.get_task_step_data("booking")

    assert result is not None
    assert context.session_data.state.action_ledger.pending_action is not None
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]
    assert state == {
        "service_name": "Consultation",
        "preferred_date": context.session_data.state.step_data["preferred_date"],
        "preferred_time": "16:00",
        "customer_name": "Arjun",
        "phone": "+919876543210",
    }


async def test_invalid_empty_booking_update_does_not_erase_valid_state(monkeypatch):
    existing = {"service_name": "Consultation", "preferred_date": _future_weekday().isoformat()}
    context = _context(step_data=existing)
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"service_name": "", "booking_time": "16:00"},
            field_confidence={"service_name": 0.99, "booking_time": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking") == existing


@pytest.mark.parametrize("service_name", ["Order Support Consultation", "Payment Support Review"])
async def test_configured_booking_field_keyword_collision_is_owned_before_switch(monkeypatch, service_name):
    config = _booking_config(services=[{"id": "svc-collision", "name": service_name, "duration_mins": 30, "price": 100}])
    config["modules"].update({"order": {"enabled": True}, "payments": {"enabled": True}, "support": {"enabled": True}})
    context = _context(config_data=config, message_text=service_name)
    route = _booking_route(context)
    assert route.should_clear_active_task is True
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"service_name": service_name},
            field_confidence={"service_name": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, route)

    assert result is None
    assert context.session_data.state.get_task_step_data("booking")["service_name"] == service_name


async def test_non_english_booking_input_uses_semantic_stub_not_word_lists(monkeypatch):
    context = _context(message_text="black-box non-English booking details")
    day = _future_weekday().isoformat()
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"service_name": "Consultation", "booking_date": day, "booking_time": "11:00"},
            field_confidence={"service_name": 0.95, "booking_date": 0.95, "booking_time": 0.95},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is None
    assert context.session_data.state.get_task_step_data("booking")["preferred_time"] == "11:00"


async def test_typo_partial_booking_input_preserves_state_and_does_not_reset(monkeypatch):
    context = _context(step_data={"service_name": "Consultation"}, message_text="black-box typo fragment")
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(confidence=0.2, field_updates={"booking_time": "10:00"}),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"
    assert context.session_data.state.get_task_step_data("booking")["service_name"] == "Consultation"


async def test_low_confidence_booking_extraction_preserves_state_and_clarifies(monkeypatch):
    context = _context(step_data={"service_name": "Consultation"})
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(confidence=0.1, field_updates={"booking_date": _future_weekday().isoformat()}),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking") == {"service_name": "Consultation"}


async def test_provider_failure_booking_extraction_preserves_state_and_clarifies(monkeypatch):
    context = _context(step_data={"service_name": "Consultation"})
    await _install_booking_extraction_stub(monkeypatch, exc=ValueError("invalid semantic json"))

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking") == {"service_name": "Consultation"}


async def test_extracted_date_time_pass_deterministic_booking_validation(monkeypatch):
    context = _context()
    day = _future_weekday().isoformat()
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_date": day, "booking_time": "12:00"},
            field_confidence={"booking_date": 0.95, "booking_time": 0.95},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is None
    assert booking_module.validate_payload(context) is None


async def test_past_booking_date_is_rejected_by_backend_validation(monkeypatch):
    context = _context(step_data={"service_name": "Consultation"})
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_date": "2000-01-01"},
            field_confidence={"booking_date": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert "date" in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("booking") == {"service_name": "Consultation"}


async def test_outside_hours_time_is_rejected_by_booking_validation(monkeypatch):
    service = SimpleNamespace(create_booking=AsyncMock())
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    context = _context(
        step_data={
            "service_name": "Consultation",
            "preferred_date": _future_weekday().isoformat(),
            "preferred_time": "18:00",
            "customer_name": "Asha",
        }
    )

    result = await booking_module.execute(context)

    assert result.success is False
    assert "outside business hours" in result.user_message
    service.create_booking.assert_not_called()


async def test_extracted_service_not_found_is_rejected_by_backend_resolver(monkeypatch):
    context = _context(
        step_data={
            "service_name": "Unavailable Service",
            "preferred_date": _future_weekday().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        }
    )

    result = await booking_module.execute(context)

    assert result.success is False
    assert "service" in result.user_message.lower()


async def test_semantic_booking_extraction_cannot_activate_disabled_module(monkeypatch):
    context = _context(enabled=False)
    called = {"value": False}

    async def fake_extract(ctx):
        called["value"] = True
        return _semantic_booking_extraction(field_updates={"booking_time": "10:00"})

    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", fake_extract)

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is None
    assert called["value"] is False
    assert context.session_data.state.get_task_step_data("booking") == {}


async def test_semantic_booking_extraction_rejects_unknown_unsafe_fields(monkeypatch):
    context = _context(step_data={"service_name": "Consultation"})
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_time": "10:00", "service_id": "not-allowed"},
            field_confidence={"booking_time": 0.95, "service_id": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking") == {"service_name": "Consultation"}


async def test_semantic_booking_tangent_does_not_overwrite_stable_service(monkeypatch):
    context = _context(step_data={"service_name": "Consultation", "preferred_time": "10:00"})
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            workflow_action="tangent",
            field_updates={"service_name": "Follow Up"},
            field_confidence={"service_name": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("booking")["service_name"] == "Consultation"


async def test_booking_quick_reply_text_uses_semantic_stub(monkeypatch):
    context = _context(step_data={"service_name": "Consultation", "preferred_date": "tomorrow"}, message_text="black-box quick reply")
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_time": "13:00"},
            field_confidence={"booking_time": 0.98},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is None
    assert context.session_data.state.get_task_step_data("booking")["preferred_time"] == "13:00"


@pytest.mark.parametrize("time_text", ["10:00", "10 AM", "10:00 AM"])
async def test_tenant_defined_date_time_fields_persist_in_separate_turns_and_reach_confirmation(monkeypatch, time_text):
    """Tenant-owned date/time keys must not depend on legacy preferred_* names."""
    fields = [
        {"key": "service_name", "label": "Service", "type": "string", "required": True},
        {"key": "guest_display_name", "label": "Guest", "type": "string", "required": True},
        {"key": "visit_date", "label": "Visit date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Visit time", "type": "time", "required": True},
    ]
    context = _context(
        config_data=_booking_config(
            services=[{"id": "retest", "name": "Retest Consultation", "duration_mins": 30, "price": 0}],
            fields=fields,
        ),
        step_data={"service_name": "Retest Consultation"},
        message_text="Alex",
    )
    context.session_data.state.set_prompt_focus(workflow="booking", requested_fields=["guest_display_name"])

    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"guest_display_name": "Alex"},
            field_confidence={"guest_display_name": 0.99},
        ),
    )
    await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))
    assert context.session_data.state.get_task_step_data("booking")["guest_display_name"] == "Alex"

    context.session_data.state.set_prompt_focus(workflow="booking", requested_fields=["visit_date"])
    context.incoming_message["text"] = _future_weekday().isoformat()
    context.shadow_turn_evidence = {}
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_date": _future_weekday().isoformat()},
            field_confidence={"booking_date": 0.99},
        ),
    )
    await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))
    persisted = context.session_data.state.model_dump(mode="json")
    context.session_data.state = SessionState.model_validate(persisted)
    assert context.session_data.state.get_task_step_data("booking")["visit_date"] == _future_weekday().isoformat()

    context.session_data.state.set_prompt_focus(workflow="booking", requested_fields=["visit_time"])
    context.incoming_message["text"] = time_text
    context.shadow_turn_evidence = {}
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_time": time_text},
            field_confidence={"booking_time": 0.99},
        ),
    )
    third = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert third is not None
    assert context.session_data.state.get_task_step_data("booking")["visit_time"] == "10:00"
    assert context.session_data.state.action_ledger.pending_action is not None
    assert third.quick_replies == ["Confirm", "Change", "Cancel"]


def test_tenant_defined_temporal_values_normalize_and_relative_dates_use_tenant_timezone(monkeypatch):
    config = _booking_config(fields=[
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
    ])
    config["timezone"] = "Asia/Kolkata"
    context = _context(config_data=config)
    monkeypatch.setattr(booking_module, "_tenant_today", lambda _: date(2026, 8, 19))

    assert booking_module.normalize_configured_field_value(
        {"type": "date"}, "tomorrow", tenant_config=config
    ) == "2026-08-20"
    assert booking_module.normalize_configured_field_value(
        {"type": "time"}, "10:00 AM", tenant_config=config
    ) == "10:00"
    assert booking_module.normalize_configured_field_value(
        {"type": "time"}, "10 PM", tenant_config=config
    ) == "22:00"
    context.session_data.state.update_task_step_data(
        "booking", {"visit_date": "2026-08-20", "visit_time": "10:00"}
    )
    assert booking_module._configured_temporal_value(context, "date") == "2026-08-20"
    assert booking_module._configured_temporal_value(context, "time") == "10:00"


def test_tenant_defined_temporal_validation_rejects_invalid_or_past_values():
    config = _booking_config()
    assert not booking_module.validate_input(
        {"key": "visit_date", "type": "date"}, "not a date", tenant_config=config
    ).is_valid
    assert not booking_module.validate_input(
        {"key": "visit_date", "type": "date"}, "2000-01-01", tenant_config=config
    ).is_valid
    assert not booking_module.validate_input(
        {"key": "visit_time", "type": "time"}, "not a time", tenant_config=config
    ).is_valid


async def test_tenant_defined_time_uses_existing_availability_authority(monkeypatch):
    service = SimpleNamespace(create_booking=AsyncMock())
    monkeypatch.setattr("backend.modules.booking_module.booking_service", service)
    fields = [
        {"key": "service_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
        {"key": "guest_display_name", "type": "string", "required": True},
    ]
    context = _context(
        config_data=_booking_config(fields=fields),
        step_data={
            "service_name": "Consultation",
            "visit_date": _future_weekday().isoformat(),
            "visit_time": "18:00",
            "guest_display_name": "Alex",
        },
    )

    result = await booking_module.execute(context)

    assert result.success is False
    assert "outside business hours" in result.user_message
    service.create_booking.assert_not_called()


async def test_unavailable_tenant_defined_time_is_not_persisted_during_collection(monkeypatch):
    fields = [
        {"key": "service_name", "type": "string", "required": True},
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
    ]
    context = _context(
        config_data=_booking_config(fields=fields),
        step_data={
            "service_name": "Consultation",
            "guest_display_name": "Alex",
            "visit_date": _future_weekday().isoformat(),
        },
        message_text="18:00",
    )
    context.session_data.state.set_prompt_focus(workflow="booking", requested_fields=["visit_time"])
    await _install_booking_extraction_stub(
        monkeypatch,
        _semantic_booking_extraction(
            field_updates={"booking_time": "18:00"},
            field_confidence={"booking_time": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, _booking_route(context))

    assert result is not None
    assert "time" in result.response_text.lower()
    assert "visit_time" not in context.session_data.state.get_task_step_data("booking")


async def test_service_ambiguity_does_not_expose_internal_ids():
    internal_a = str(uuid4())
    internal_b = str(uuid4())
    cfg = _booking_config(
        services=[
            {"id": internal_a, "name": "Consultation"},
            {"id": internal_b, "name": "Consultation Plus"},
        ]
    )
    context = _context(
        config_data=cfg,
        step_data={
            "service_name": "Consult",
            "preferred_date": _future_weekday().isoformat(),
            "preferred_time": "10:00",
            "customer_name": "Asha",
        },
    )

    result = await booking_module.execute(context)

    assert result.success is False
    assert "Consultation" in result.user_message
    assert internal_a not in result.user_message
    assert internal_b not in result.user_message
    assert not UUID_PATTERN.search(result.user_message)


async def test_booking_pending_action_confirmation_still_uses_slice2_semantics(monkeypatch):
    context = _context()
    args = {
        "service_name": "Consultation",
        "preferred_date": _future_weekday().isoformat(),
        "preferred_time": "10:00",
        "customer_name": "Asha",
    }
    context.session_data.state.update_task_step_data("booking", args)
    context.session_data.state.prepare_pending_action(
        tool_key="booking.execute",
        args=args,
        summary="Please review this booking request.",
        requires_confirmation=True,
        requires_verified_customer=False,
        channel_type="awa_chat",
    )

    async def fake_check_message_allowed(ctx):
        return None

    async def fake_hydrate(ctx):
        return None

    async def fake_decide(ctx):
        return PendingActionDecision(action="confirm", confidence=0.98)

    async def fake_execute_tool(tool_key, ctx):
        return ToolExecutionResult(tool_key=tool_key, success=True, user_message="Booking saved.")

    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", fake_check_message_allowed)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", fake_hydrate)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute_tool)

    result = await agentic_engine.execute_step(context)

    assert result.response_text == "Booking saved."
    assert context.session_data.state.action_ledger.pending_action is None


async def test_pending_booking_change_answers_grounded_price_without_losing_confirmation(monkeypatch):
    context = _context(
        config_data=_booking_config(services=[
            {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 30, "price": 1500},
        ]),
    )
    args = {
        "service_name": "Dental Cleaning",
        "preferred_date": _future_weekday().isoformat(),
        "preferred_time": "10:00",
        "customer_name": "Asha",
    }
    context.incoming_message["text"] = "Actually make it 11 AM, and by the way what is your price?"
    context.session_data.state.update_task_step_data("booking", args)
    context.session_data.state.prepare_pending_action(
        tool_key="booking.execute",
        args=args,
        summary="Please review this booking request.",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
        channel_type="awa_chat",
    )

    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", AsyncMock(return_value=None))
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "backend.logic.engine.core.decide_pending_action",
        AsyncMock(return_value=PendingActionDecision(
            action="change",
            confidence=0.98,
            field_updates={"preferred_time": "11:00"},
        )),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    pending = context.session_data.state.action_ledger.pending_action
    assert "Dental Cleaning is INR 1500" in result.response_text
    assert pending is not None
    assert pending.args["preferred_time"] == "11:00"
    assert pending.requires_confirmation is True
    assert pending.requires_verified_customer is True
    execute_tool.assert_not_awaited()


async def test_booking_field_extraction_prompt_omits_internal_ids(monkeypatch):
    from backend.logic.engine import field_extraction

    internal_id = str(uuid4())
    cfg = _booking_config(
        services=[
            {
                "id": internal_id,
                "name": f"Consultation {internal_id}",
                "duration_mins": 30,
            }
        ]
    )
    context = _context(
        config_data=cfg,
        step_data={"service_name": internal_id},
        message_text=f"black-box message {internal_id}",
    )
    captured = {}

    class FakeLLM:
        async def ainvoke(self, messages):
            captured["payload"] = str(messages[-1].content)
            return AIMessage(
                content=json.dumps(
                    {
                        "workflow": "booking",
                        "workflow_action": "continue",
                        "confidence": 0.9,
                        "field_updates": {},
                        "field_confidence": {},
                        "clarification_needed": False,
                        "clarification_fields": [],
                        "safe_response_hint": None,
                    }
                )
            )

    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: FakeLLM())

    await field_extraction.extract_booking_fields(context)

    payload = captured["payload"]
    assert internal_id not in payload
    assert "service_id" not in payload
    assert "tenant_id" not in payload
    assert "booking_id" not in payload
