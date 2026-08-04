from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.configured_field_collection import ConfiguredFieldCollectionCoordinator
from backend.logic.engine.field_extraction import BookingFieldExtraction
from backend.modules.booking_module import booking_module


pytestmark = pytest.mark.anyio


def _context(fields, text, state_values=None):
    tenant_id = uuid4()
    state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    state.update_task_step_data("booking", dict(state_values or {}))
    state.action_ledger.task_status = "collecting"
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Configured Fields", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(), config_version=17,
            current_module="booking", state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=17, config_data={
            "timezone": "UTC",
            "modules": {"booking": {"enabled": True, "fields": fields}},
        }),
        incoming_message={"text": text, "channel_type": "awa_chat", "source_type": "awa_chat"},
    )


async def _no_semantic(_context):
    return BookingFieldExtraction(workflow="unknown", confidence=0.0)


@pytest.mark.parametrize(("field", "text", "expected"), [
    ({"key": "any_string", "type": "string", "required": True}, "Alex", "Alex"),
    ({"key": "party_size", "type": "number", "required": True}, "3", 3),
    ({"key": "visit_date", "type": "date", "required": True}, (date.today() + timedelta(days=7)).isoformat(), (date.today() + timedelta(days=7)).isoformat()),
    ({"key": "visit_time", "type": "time", "required": True}, "10 AM", "10:00"),
    ({"key": "contact_email", "type": "email", "required": True}, "ALEX@Example.COM", "alex@example.com"),
    ({"key": "contact_phone", "type": "phone", "required": True}, "+1 555 012 3456", "+1 555 012 3456"),
    ({"key": "seat", "type": "select", "required": True, "options": ["Window", "Aisle"]}, "window", "Window"),
])
async def test_collects_arbitrary_configured_types_under_exact_key(field, text, expected):
    context = _context([field], text)
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.accepted is True
    assert result.patch == {field["key"]: expected}
    assert context.session_data.state.get_task_step_data("booking") == {field["key"]: expected}


@pytest.mark.parametrize(("key", "text", "expected"), [
    ("visit_time", "10:00", "10:00"),
    ("tenant_slot", "10 AM", "10:00"),
    ("visit_time", "10:00 AM", "10:00"),
    ("tenant_slot", "2 PM", "14:00"),
    ("visit_time", "2:30 PM", "14:30"),
    ("tenant_slot", "noon", "12:00"),
    ("visit_time", "morning", "10:00"),
    ("visit_time", "afternoon", "14:00"),
    ("visit_time", "evening", "17:00"),
])
async def test_configured_time_normalizes_under_any_tenant_key(key, text, expected):
    context = _context([{"key": key, "type": "time", "required": True}], text)
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.accepted is True
    assert result.patch == {key: expected}
    assert booking_module.normalize_configured_field_value({"type": "time"}, text) == expected


@pytest.mark.parametrize("text", ["25:00", "13 PM"])
async def test_configured_time_rejects_invalid_syntax(text):
    context = _context([{"key": "tenant_slot", "type": "time", "required": True}], text)
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.reason_code == "invalid_format"
    assert context.session_data.state.get_task_step_data("booking") == {}


@pytest.mark.parametrize("text", ["", "when are you available?"])
async def test_configured_time_leaves_empty_or_non_time_prose_uncommitted(text):
    context = _context([{"key": "tenant_slot", "type": "time", "required": True}], text)
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.accepted is False
    assert booking_module.validate_input({"type": "time"}, text).is_valid is False
    assert context.session_data.state.get_task_step_data("booking") == {}


@pytest.mark.parametrize("text", ["4", "around 4"])
async def test_configured_time_preserves_ambiguous_hour_policy(text):
    context = _context([{"key": "tenant_slot", "type": "time", "required": True}], text)
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.reason_code == "ambiguous_value"
    assert context.session_data.state.get_task_step_data("booking") == {}


async def test_one_field_focus_does_not_put_date_into_preceding_string_field():
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
    ]
    context = _context(fields, (date.today() + timedelta(days=7)).isoformat())
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.handled is False
    assert context.session_data.state.get_task_step_data("booking") == {}


async def test_rich_turn_commits_name_date_and_time_in_one_atomic_patch(monkeypatch):
    booking_date = (date.today() + timedelta(days=7)).isoformat()
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
    ]
    context = _context(fields, "Arjun, tomorrow at four")

    async def grouped(_context):
        return BookingFieldExtraction(
            workflow="booking",
            workflow_action="continue",
            confidence=0.99,
            field_updates={
                "customer_name": "Arjun",
                "booking_date": booking_date,
                "booking_time": "4 PM",
            },
            field_confidence={
                "customer_name": 0.99,
                "booking_date": 0.99,
                "booking_time": 0.99,
            },
        )

    state = context.session_data.state
    original_update = type(state).update_task_step_data
    committed_patches = []

    def track_update(instance, task_key, values):
        if instance is state:
            committed_patches.append(dict(values))
        return original_update(instance, task_key, values)

    monkeypatch.setattr(type(state), "update_task_step_data", track_update)
    result = await ConfiguredFieldCollectionCoordinator().collect(
        context,
        semantic_extractor=grouped,
    )

    expected = {
        "guest_display_name": "Arjun",
        "visit_date": booking_date,
        "visit_time": "16:00",
    }
    assert result.accepted is True
    assert result.ready_for_confirmation is True
    assert result.patch == expected
    assert committed_patches == [expected]
    assert state.get_task_step_data("booking") == expected


async def test_rich_string_turn_without_structured_evidence_never_captures_whole_utterance():
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
    ]
    context = _context(fields, "Arjun, tomorrow at four")

    result = await ConfiguredFieldCollectionCoordinator().collect(
        context,
        semantic_extractor=_no_semantic,
    )

    assert result.handled is False
    assert context.session_data.state.get_task_step_data("booking") == {}


async def test_atomic_patch_preserves_legacy_unscoped_booking_state():
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
    ]
    context = _context(fields, "Arjun")
    state = context.session_data.state
    state.scoped_step_data.pop("booking", None)
    state.step_data = {"service_name": "Dental Cleaning"}

    result = await ConfiguredFieldCollectionCoordinator().collect(
        context,
        semantic_extractor=_no_semantic,
    )

    assert result.patch == {"guest_display_name": "Arjun"}
    assert state.get_task_step_data("booking") == {
        "service_name": "Dental Cleaning",
        "guest_display_name": "Arjun",
    }


@pytest.mark.parametrize("case", ["unknown", "low_confidence", "invalid", "protected_service"])
async def test_unsafe_member_preserves_valid_semantic_batch_fields(case, monkeypatch):
    booking_date = (date.today() + timedelta(days=7)).isoformat()
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
    ]
    context = _context(fields, "Arjun, tomorrow at four")
    updates = {
        "customer_name": "Arjun",
        "booking_date": booking_date,
        "booking_time": "4 PM",
    }
    confidences = {key: 0.99 for key in updates}

    expected_patch = {
        "guest_display_name": "Arjun",
        "visit_date": booking_date,
    }

    if case == "unknown":
        updates["invented_key"] = "invented"
        confidences["invented_key"] = 0.99
        expected_patch["visit_time"] = "16:00"
    elif case == "low_confidence":
        confidences["booking_time"] = 0.79
    elif case == "invalid":
        updates["booking_time"] = "25:00"
    elif case == "protected_service":
        updates["service_name"] = "Protected service"
        confidences["service_name"] = 0.99
        expected_patch["visit_time"] = "16:00"

    async def unsafe_batch(_context):
        return BookingFieldExtraction(
            workflow="booking",
            workflow_action="continue",
            confidence=0.99,
            field_updates=updates,
            field_confidence=confidences,
        )

    state = context.session_data.state
    original_update = type(state).update_task_step_data
    committed_patches = []

    def track_update(instance, task_key, values):
        if instance is state:
            committed_patches.append(dict(values))
        return original_update(instance, task_key, values)

    monkeypatch.setattr(type(state), "update_task_step_data", track_update)
    result = await ConfiguredFieldCollectionCoordinator().collect(
        context,
        semantic_extractor=unsafe_batch,
    )

    if case == "protected_service":
        assert result.handled is False
        assert result.reason_code == "no_candidate"
        return

    assert result.handled is True
    assert result.accepted is True
    assert result.patch == expected_patch
    assert len(committed_patches) == 1
    assert state.get_task_step_data("booking") == expected_patch


async def test_rich_turn_keeps_time_when_service_was_already_configured_authoritatively():
    booking_date = (date.today() + timedelta(days=7)).isoformat()
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
        {"key": "visit_time", "type": "time", "required": True},
        {"key": "phone", "type": "phone", "required": True},
    ]
    context = _context(
        fields,
        f"Asha, {booking_date} at 10 AM, +1 555 010 2002",
        {"service_name": "Configuration-owned service", "service_id": "svc-1"},
    )

    async def rich_extraction(_context):
        return BookingFieldExtraction(
            workflow="booking", workflow_action="continue", confidence=0.99,
            field_updates={
                "service_name": "Configuration-owned service",
                "booking_date": booking_date,
                "booking_time": "10 AM",
                "customer_name": "Asha",
                "phone": "+1 555 010 2002",
            },
            field_confidence={
                "service_name": 0.99, "booking_date": 0.99, "booking_time": 0.99,
                "customer_name": 0.99, "phone": 0.99,
            },
        )

    result = await ConfiguredFieldCollectionCoordinator().collect(
        context, semantic_extractor=rich_extraction,
    )

    assert result.accepted is True
    assert result.patch == {
        "guest_display_name": "Asha", "visit_date": booking_date,
        "visit_time": "10:00", "phone": "+1 555 010 2002",
    }
    assert context.session_data.state.get_task_step_data("booking") == {
        "service_name": "Configuration-owned service", "service_id": "svc-1",
        **result.patch,
    }


async def test_date_candidate_is_not_consumed_by_resume_when_focus_is_date():
    fields = [
        {"key": "guest_display_name", "type": "string", "required": True},
        {"key": "visit_date", "type": "date", "required": True},
    ]
    value = (date.today() + timedelta(days=7)).isoformat()
    context = _context(fields, value, {"guest_display_name": "Alex"})
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.accepted is True
    assert context.session_data.state.get_task_step_data("booking") == {"guest_display_name": "Alex", "visit_date": value}


async def test_invalid_select_leaves_state_unchanged_and_removed_field_is_ignored():
    fields = [
        {"key": "choice", "type": "select", "required": True, "options": ["A"]},
        {"key": "removed", "type": "string", "required": False, "enabled": False},
    ]
    context = _context(fields, "B")
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)

    assert result.reason_code == "option_not_allowed"
    assert context.session_data.state.get_task_step_data("booking") == {}


async def test_legacy_alias_maps_to_one_exact_date_key_and_ambiguous_alias_fails_closed():
    value = (date.today() + timedelta(days=7)).isoformat()

    async def alias(_context):
        return BookingFieldExtraction(workflow="booking", workflow_action="continue", confidence=0.99, field_updates={"booking_date": value}, field_confidence={"booking_date": 0.99})

    one = _context([{"key": "visit_date", "type": "date", "required": True}], "July 20")
    assert (await ConfiguredFieldCollectionCoordinator().collect(one, semantic_extractor=alias)).accepted is True
    assert one.session_data.state.get_task_step_data("booking")["visit_date"] == value

    ambiguous = _context([
        {"key": "first_date", "type": "date", "required": False},
        {"key": "visit_date", "type": "date", "required": True},
    ], "July 20")
    result = await ConfiguredFieldCollectionCoordinator().collect(ambiguous, semantic_extractor=alias)
    assert result.handled is True
    assert result.reason_code == "unmapped_field"
    assert ambiguous.session_data.state.get_task_step_data("booking") == {}


async def test_explicit_cancel_preempts_collection():
    context = _context([{"key": "guest_display_name", "type": "string", "required": True}], "cancel")
    result = await ConfiguredFieldCollectionCoordinator().collect(context, semantic_extractor=_no_semantic)
    assert result.control_disposition == "cancel"
    assert result.patch == {}
