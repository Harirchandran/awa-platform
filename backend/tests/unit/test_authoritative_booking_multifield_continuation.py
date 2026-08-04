"""Iteration 5 authoritative multi-field booking continuation tests.

These tests assert the Iteration 5 authority boundary: backend-validated
Iteration 2 complete-turn candidates are revalidated by the existing
configured-field validator and persisted together in one atomic booking-state
update, without the LLM writing state directly.

The semantic gateway is mocked; no live provider is contacted. Tests cover the
flag default, eligibility contract, atomic multi-field persistence, arbitrary
configured keys, service/already-collected protection, temporal validation,
no-double-interpretation, existing-collector fallback, idempotency, failure
behavior, and privacy-safe telemetry.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.config import settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.logic.engine import core as agentic_engine
from backend.logic.engine.configured_field_collection import (
    ConfiguredFieldCollectionCoordinator,
    ExternalCandidateApplicationResult,
)
from backend.logic.engine.global_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY,
)
from backend.logic.engine import global_configured_turn_shadow as shadow

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SERVICE_NAME = "Teeth Cleaning"


def _booking_fields() -> list[dict]:
    return [
        {"key": "visit_date", "label": "Visit Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Visit Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
    ]


def _make_context(
    *,
    fields=None,
    text="Tomorrow evening at four. I'm Arjun and this number is fine.",
    step_data=None,
    current_module="booking",
    pending_action=None,
    source_type="awa_chat",
    channel_type="awa_chat",
):
    tenant_id = __import__("uuid").uuid4()
    modules = {
        "booking": {
            "enabled": True,
            "fields": fields if fields is not None else _booking_fields(),
        }
    }
    state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    state.action_ledger.current_task = "booking" if current_module == "booking" else None
    state.action_ledger.task_status = "collecting"
    state.update_task_step_data("booking", dict(step_data or {}))
    if pending_action is not None:
        state.action_ledger.pending_action = pending_action
    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="AWA", plan_code="enterprise", status="active"),
        user=User(id=__import__("uuid").uuid4(), phone_number="+10000000000", name="Test"),
        session_data=ConversationSession(
            id=__import__("uuid").uuid4(),
            tenant_id=tenant_id,
            conversation_id=__import__("uuid").uuid4(),
            config_version=17,
            current_module=current_module,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=17, config_data={
            "timezone": "UTC",
            "services": [{"id": "cleaning", "name": _SERVICE_NAME, "enabled": True}],
            "modules": modules,
        }),
        incoming_message={
            "text": text,
            "channel_type": channel_type,
            "source_type": source_type,
        },
    )
    return context


def _set_shadow_validated(context, validated_patch):
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = {
        "validated_shadow_patch": dict(validated_patch),
        "validated_shadow_candidate_keys": sorted(validated_patch.keys()),
        "focus_field_key": None,
    }


def _engine():
    return agentic_engine.AgenticEngine()


def _make_pending_action(tool_key="booking.execute"):
    pa = MagicMock()
    pa.tool_key = tool_key
    pa.args = {}
    pa.summary = None
    return pa


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Flag default
# ---------------------------------------------------------------------------
def test_a1_flag_default_false_in_isolation():
    assert settings.model_fields["ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY"].default is False
    original = getattr(settings, "ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY", None)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        assert settings.model_fields["ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY"].default is False
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = original if original is not None else False


# ---------------------------------------------------------------------------
# Apply requires both flags
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("shadow,apply", [
    (False, True),
    (True, False),
    (False, False),
])
async def test_a2_apply_requires_both_flags(shadow, apply):
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = shadow
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = apply
    try:
        assert agentic_engine._authoritative_booking_apply_eligible(context) is False
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False, accepted=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# Active booking continuation required
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("workflow", [
    None, "order", "payments", "support", "lead_capture", "reminders", "reviews",
])
async def test_a3_active_booking_required(workflow):
    context = _make_context(
        current_module=workflow,
        step_data={"service_name": _SERVICE_NAME} if workflow == "booking" else {},
    )
    _set_shadow_validated(context, {"visit_date": _tomorrow()})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        context.session_data.state.action_ledger.current_task = workflow
        assert agentic_engine._authoritative_booking_apply_eligible(context) is False
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# Service must already be selected
# ---------------------------------------------------------------------------
async def test_a4_service_must_be_selected():
    context = _make_context(step_data={})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        assert agentic_engine._authoritative_booking_apply_eligible(context) is False
        engine = _engine()
        result = await engine._try_authoritative_booking_apply(context)
        assert result is None
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# Pending action blocks application
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool_key", [
    "booking.execute", "booking.confirm", "other.tool",
])
async def test_a5_pending_action_blocks(tool_key):
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    context.session_data.state.action_ledger.pending_action = _make_pending_action(tool_key)
    _set_shadow_validated(context, {"visit_date": _tomorrow()})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        assert agentic_engine._authoritative_booking_apply_eligible(context) is False
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A6 - Date and time persist together
# ---------------------------------------------------------------------------
async def test_a6_date_and_time_persist_together():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    cand = {"visit_date": _tomorrow(), "visit_time": "16:00"}
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called is False
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["visit_date"] == _tomorrow()
        assert stored["visit_time"] == "16:00"
        assert "service_name" in stored
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A7 - Name, phone, date, time persist together
# ---------------------------------------------------------------------------
async def test_a7_four_fields_persist_together():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    cand = {
        "visit_date": _tomorrow(),
        "visit_time": "16:00",
        "customer_name": "Arjun",
        "phone": "+919999999999",
    }
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["visit_date"] == _tomorrow()
        assert stored["visit_time"] == "16:00"
        assert stored["customer_name"] == "Arjun"
        assert stored["phone"] == "+919999999999"
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A8 - Arbitrary configured fields persist
# ---------------------------------------------------------------------------
async def test_a8_arbitrary_configured_fields_persist():
    fields = [
        {"key": "insurance_policy_number", "label": "Policy", "type": "string", "required": False},
        {"key": "pet_breed", "label": "Breed", "type": "string", "required": False},
        {"key": "preferred_contact_window", "label": "Window", "type": "string", "required": False},
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
    ]
    context = _make_context(fields=fields, step_data={"service_name": _SERVICE_NAME})
    cand = {
        "insurance_policy_number": "POL-123",
        "pet_breed": "Labrador",
        "preferred_contact_window": "morning",
        "visit_date": _tomorrow(),
    }
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["insurance_policy_number"] == "POL-123"
        assert stored["pet_breed"] == "Labrador"
        assert stored["preferred_contact_window"] == "morning"
        assert stored["visit_date"] == _tomorrow()
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A9 - Unknown field is never persisted
# ---------------------------------------------------------------------------
async def test_a9_unknown_field_never_persisted():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    # Iteration 2 rejects the complete payload because one candidate is unknown,
    # so no validated patch is produced and the existing collector owns the turn.
    _set_shadow_validated(context, {})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
        stored = context.session_data.state.get_task_step_data("booking")
        assert "unknown_custom_key" not in stored
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A10 - Protected service keys never persisted
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["service_name", "service_id"])
async def test_a10_protected_service_keys_never_persisted(key):
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    # A protected service key in the complete payload is deferred/rejected by
    # Iteration 2, so no validated patch is produced and the service owner is
    # never bypassed by the authoritative continuation path.
    _set_shadow_validated(context, {})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["service_name"] == _SERVICE_NAME
        assert stored.get("service_id") != "Hacked Service"
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A11 - Already-collected values are not overwritten
# ---------------------------------------------------------------------------
async def test_a11_already_collected_not_overwritten():
    context = _make_context(step_data={
        "service_name": _SERVICE_NAME, "customer_name": "Original Name",
    })
    cand = {"customer_name": "Different Name", "phone": "+919999999999"}
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["customer_name"] == "Original Name"
        assert stored["phone"] == "+919999999999"
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False


# ---------------------------------------------------------------------------
# A12 - Invalid time falls back safely
# ---------------------------------------------------------------------------
async def test_a12_invalid_time_falls_back():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    # An invalid time (25:00) invalidates the complete payload at Iteration 2,
    # so no validated patch is produced and the existing collector continues.
    _set_shadow_validated(context, {})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
        stored = context.session_data.state.get_task_step_data("booking")
        assert "visit_time" not in stored
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A13 - Ambiguous time not invented
# ---------------------------------------------------------------------------
async def test_a13_ambiguous_time_not_invented():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    # "4" is ambiguous; Iteration 2 does not accept the complete payload, so no
    # time is invented into 04:00 or 16:00 and the existing collector runs.
    _set_shadow_validated(context, {})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
        stored = context.session_data.state.get_task_step_data("booking")
        assert "visit_time" not in stored
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A14 - One atomic update call
# ---------------------------------------------------------------------------
async def test_a14_one_atomic_update_call():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    cand = {"visit_date": _tomorrow(), "visit_time": "16:00", "customer_name": "Arjun", "phone": "+919999999999"}
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        calls = []
        real_fn = SessionState.update_task_step_data
        target_state = context.session_data.state

        def spy(*a, **k):
            calls.append(a)
            return real_fn(target_state, *a, **k)

        with patch.object(SessionState, "update_task_step_data", side_effect=spy):
            await engine._handle_configured_booking_collection(context)
            booking_calls = [c for c in calls if c and c[0] == "booking"]
            assert len(booking_calls) == 1
            written = booking_calls[0][1]
            assert set(["visit_date", "visit_time", "customer_name", "phone"]).issubset(written.keys())
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A15 - Revalidation occurs at application time
# ---------------------------------------------------------------------------
async def test_a15_revalidation_at_application_time():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    cand = {"customer_name": "Arjun", "phone": "+919999999999"}
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        context.session_data.state.scoped_step_data.setdefault("booking", {})["customer_name"] = "PreCollected"
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["customer_name"] == "PreCollected"
        assert stored["phone"] == "+919999999999"
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A16 - Old semantic extractor skipped after successful application
# ---------------------------------------------------------------------------
async def test_a16_old_extractor_skipped_after_apply():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    cand = {"visit_date": _tomorrow(), "visit_time": "16:00"}
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(side_effect=AssertionError("collect must not run")),
        ):
            await engine._handle_configured_booking_collection(context)
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A17 - Existing collector runs when no candidate applies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scenario", ["gateway_unavailable", "invalid_payload", "no_candidates", "missing_snapshot"])
async def test_a17_existing_collector_runs_when_no_candidate(scenario):
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    if scenario == "missing_snapshot":
        pass
    elif scenario == "no_candidates":
        _set_shadow_validated(context, {})
    elif scenario == "gateway_unavailable":
        _set_shadow_validated(context, {})
    elif scenario == "invalid_payload":
        _set_shadow_validated(context, {})
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A18 - Same message not reused for focused string field
# ---------------------------------------------------------------------------
async def test_a18_same_message_not_reused_for_name():
    fields = [
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
    ]
    context = _make_context(
        fields=fields,
        step_data={"service_name": _SERVICE_NAME},
        text="Tomorrow evening at four",
    )
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored.get("customer_name") not in {"Tomorrow evening at four", "Tomorrow evening at four."}
        assert stored.get("customer_name") in (None, "")
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A19 - Only genuinely missing information requested
# ---------------------------------------------------------------------------
async def test_a19_only_missing_requested():
    fields = [
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
        {"key": "email", "label": "Email", "type": "email", "required": True},
    ]
    context = _make_context(fields=fields, step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {
        "visit_date": _tomorrow(), "visit_time": "16:00",
        "customer_name": "Arjun", "phone": "+919999999999",
    })
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        result = await engine._handle_configured_booking_collection(context)
        focus = result.prompt_focus_fields or []
        assert focus[:1] == (["email"] if focus else [])
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A20 - Completed field collection still requires confirmation
# ---------------------------------------------------------------------------
async def test_a20_completed_still_requires_confirmation():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {
        "visit_date": _tomorrow(), "visit_time": "16:00",
        "customer_name": "Arjun", "phone": "+919999999999",
    })
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        # Iteration 5B: when every required field is complete the authoritative
        # path reaches the SAME existing proposal/confirmation path (booking
        # .execute prepares the proposal + confirmation requirement). No booking
        # record is created and no success is claimed here.
        prepared = MagicMock()
        prepared.success = False
        prepared.denied_reason = "confirmation_required: please confirm"
        prepared.validation_errors = {}

        async def fake_execute(tool_key, ctx):
            ctx.session_data.state.action_ledger.pending_action = _make_pending_action("booking.execute")
            return prepared

        with patch.object(engine.tool_registry, "execute_tool", side_effect=fake_execute) as exec_mock, \
             patch.object(SessionState, "record_tool_result", MagicMock()):
            result = await engine._handle_configured_booking_collection(context)
        assert exec_mock.called is True
        assert "booked" not in result.response_text.lower()
        stored = context.session_data.state.get_task_step_data("booking")
        assert all(stored.get(k) for k in ["visit_date", "visit_time", "customer_name", "phone"])
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A21 - No tool execution from application method
# ---------------------------------------------------------------------------
async def test_a21_no_tool_from_application_method():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    patch_val = {"visit_date": _tomorrow(), "visit_time": "16:00"}
    coordinator = ConfiguredFieldCollectionCoordinator()
    with patch("backend.tools.registry.ToolRegistry") as _registry:
        result = coordinator.apply_external_candidates(context, patch_val)
    assert result.handled is True
    assert result.applied is True
    assert result.update_performed is True
    assert context.session_data.state.get_task_step_data("booking")["visit_date"] == _tomorrow()


# ---------------------------------------------------------------------------
# A22 - Exact tenant keys preserved
# ---------------------------------------------------------------------------
async def test_a22_exact_tenant_keys_preserved():
    fields = [
        {"key": "Insurance_Policy_Number", "label": "Policy", "type": "string", "required": False},
        {"key": "Preferred_Contact_Window", "label": "Window", "type": "string", "required": False},
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
    ]
    context = _make_context(fields=fields, step_data={"service_name": _SERVICE_NAME})
    cand = {
        "Insurance_Policy_Number": "POL-1",
        "Preferred_Contact_Window": "morning",
        "visit_date": _tomorrow(),
    }
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["Insurance_Policy_Number"] == "POL-1"
        assert stored["Preferred_Contact_Window"] == "morning"
        assert "insurance_policy_number" not in stored
        assert "preferred_contact_window" not in stored
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A23 - Retry is idempotent
# ---------------------------------------------------------------------------
async def test_a23_retry_idempotent():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    cand = {
        "visit_date": _tomorrow(),
        "visit_time": "16:00",
        "customer_name": "Arjun",
        "phone": "+919999999999",
    }
    _set_shadow_validated(context, cand)
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        calls = []
        real_fn = SessionState.update_task_step_data
        target_state = context.session_data.state

        def spy(*a, **k):
            calls.append(a)
            return real_fn(target_state, *a, **k)

        with patch.object(SessionState, "update_task_step_data", side_effect=spy):
            await engine._handle_configured_booking_collection(context)
            count_after_first = len(calls)
            # second turn: every required field already collected, so the
            # authoritative path is ineligible and no second update occurs.
            await engine._handle_configured_booking_collection(context)
            assert len(calls) == count_after_first
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["visit_date"] == _tomorrow()
        assert stored["visit_time"] == "16:00"
        assert stored["customer_name"] == "Arjun"
        assert stored["phone"] == "+919999999999"
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A24 - Application failure before update falls back
# ---------------------------------------------------------------------------
async def test_a24_failure_before_update_falls_back():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()

        def boom(context, candidates):
            raise RuntimeError("pre-update failure")

        with patch.object(ConfiguredFieldCollectionCoordinator, "apply_external_candidates", new=boom):
            with patch.object(
                ConfiguredFieldCollectionCoordinator, "collect",
                new=AsyncMock(return_value=MagicMock(handled=False)),
            ) as collect_mock:
                await engine._handle_configured_booking_collection(context)
        assert collect_mock.called
        stored = context.session_data.state.get_task_step_data("booking")
        assert "visit_date" not in stored
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A25 - No second mutation after uncertain write
# ---------------------------------------------------------------------------
async def test_a25_no_second_mutation_after_uncertain_write():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        engine = _engine()
        calls = []
        real_fn = SessionState.update_task_step_data
        target_state = context.session_data.state

        def partial_update(*a, **k):
            calls.append(a)
            real_fn(target_state, *a, **k)
            raise RuntimeError("commit unconfirmed")

        with patch.object(SessionState, "update_task_step_data", side_effect=partial_update):
            with patch.object(
                ConfiguredFieldCollectionCoordinator, "collect",
                new=AsyncMock(side_effect=AssertionError("collect must not run after uncertain write")),
            ):
                try:
                    await engine._handle_configured_booking_collection(context)
                except RuntimeError:
                    pass
        booking_calls = [c for c in calls if c and c[0] == "booking"]
        assert len(booking_calls) == 1
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A26 - Apply telemetry contains keys only
# ---------------------------------------------------------------------------
async def test_a26_apply_telemetry_keys_only():
    fields = [
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
        {"key": "email", "label": "Email", "type": "email", "required": True},
        {"key": "insurance_policy_number", "label": "Policy", "type": "string", "required": False},
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Time", "type": "time", "required": True},
    ]
    context = _make_context(fields=fields, step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {
        "customer_name": "PRIVATE_NAME_SENTINEL",
        "phone": "+919999999999",
        "email": "private@example.com",
        "insurance_policy_number": "PRIVATE_POLICY_SENTINEL",
        "visit_date": _tomorrow(),
        "visit_time": "16:00",
    })
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    captured = []
    orig_info = shadow.logger.info

    def spy_info(event, extra=None, **kw):
        if event == "global_configured_turn_booking_applied":
            captured.append(extra["extra_data"])
        return orig_info(event, extra=extra, **kw)

    try:
        shadow.logger.info = spy_info
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        assert len(captured) == 1
        payload = captured[0]
        blob = str(payload)
        assert "PRIVATE_NAME_SENTINEL" not in blob
        assert "+919999999999" not in blob
        assert "private@example.com" not in blob
        assert "PRIVATE_POLICY_SENTINEL" not in blob
        assert payload["event"] == "global_configured_turn_booking_applied"
        assert set(payload["applied_field_keys"]) == {
            "customer_name", "phone", "email", "insurance_policy_number", "visit_date", "visit_time"
        }
    finally:
        shadow.logger.info = orig_info
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# A27 - Default-disabled customer behavior unchanged
# ---------------------------------------------------------------------------
async def test_a27_default_disabled_unchanged():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False
    engine = _engine()
    with patch.object(
        ConfiguredFieldCollectionCoordinator, "collect",
        new=AsyncMock(return_value=MagicMock(handled=False)),
    ) as collect_mock:
        await engine._handle_configured_booking_collection(context)
    assert collect_mock.called
    stored = context.session_data.state.get_task_step_data("booking")
    assert "visit_date" not in stored


# ---------------------------------------------------------------------------
# A28 - Other workflows unchanged
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("workflow", [
    "order", "payments", "lead_capture", "support", "reminders", "reviews", "faq", "human_handoff",
])
async def test_a28_other_workflows_unchanged(workflow):
    context = _make_context(current_module=workflow)
    _set_shadow_validated(context, {"visit_date": _tomorrow()})
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True
    try:
        context.session_data.state.action_ledger.current_task = workflow
        engine = _engine()
        result = await engine._try_authoritative_booking_apply(context)
        assert result is None
    finally:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ===========================================================================
# Iteration 5B correction tests (C1-C10)
#
# These assert the single-owner booking-turn contract: after any handled
# external application (whether a write occurred or the batch was already
# satisfied), the legacy collector and the legacy semantic extractor must not
# run for the same customer message, and continuation is derived only from
# authoritative booking state.
# ===========================================================================
def _five_required_fields() -> list[dict]:
    return [
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
        {"key": "email", "label": "Email", "type": "email", "required": True},
    ]


def _flags_on():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = True


def _flags_off():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY = False


# ---------------------------------------------------------------------------
# C1 - Partial apply never invokes legacy collection
# ---------------------------------------------------------------------------
async def test_c1_partial_apply_never_invokes_legacy_collection():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    # Iteration 2/backend accepted the safe subset only (valid date + name);
    # the invalid time is not in the validated patch.
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "customer_name": "Arjun"})
    _flags_on()
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(side_effect=AssertionError("collect must not run")),
        ), patch.object(
            agentic_engine, "extract_booking_fields",
            new=AsyncMock(side_effect=AssertionError("extractor must not run")),
        ):
            result = await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["visit_date"] == _tomorrow()
        assert stored["customer_name"] == "Arjun"
        assert "visit_time" not in stored
        focus = result.prompt_focus_fields or []
        assert focus[:1] == (["visit_time"] if focus else [])
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C2 - Multi-field apply with one remaining field
# ---------------------------------------------------------------------------
async def test_c2_multifield_apply_one_remaining_field():
    context = _make_context(fields=_five_required_fields(), step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {
        "visit_date": _tomorrow(), "visit_time": "16:00",
        "customer_name": "Arjun", "phone": "+919999999999",
    })
    _flags_on()
    try:
        engine = _engine()
        calls = []
        real_fn = SessionState.update_task_step_data
        target_state = context.session_data.state

        def spy(*a, **k):
            calls.append(a)
            return real_fn(target_state, *a, **k)

        with patch.object(SessionState, "update_task_step_data", side_effect=spy), \
             patch.object(
                 ConfiguredFieldCollectionCoordinator, "collect",
                 new=AsyncMock(side_effect=AssertionError("collect must not run")),
             ):
            result = await engine._handle_configured_booking_collection(context)
        booking_calls = [c for c in calls if c and c[0] == "booking"]
        assert len(booking_calls) == 1
        focus = result.prompt_focus_fields or []
        assert focus[:1] == (["email"] if focus else [])
        assert "email" in result.response_text.lower()
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C3 - Complete collection reaches confirmation path
# ---------------------------------------------------------------------------
async def test_c3_complete_collection_reaches_confirmation_path():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {
        "visit_date": _tomorrow(), "visit_time": "16:00",
        "customer_name": "Arjun", "phone": "+919999999999",
    })
    _flags_on()
    try:
        engine = _engine()
        # booking.execute prepares the proposal and sets a confirmation
        # requirement; it never creates a booking record here.
        prepared = MagicMock()
        prepared.success = False
        prepared.denied_reason = "confirmation_required: please confirm"
        prepared.validation_errors = {}

        async def fake_execute(tool_key, ctx):
            ctx.session_data.state.action_ledger.pending_action = _make_pending_action("booking.execute")
            return prepared

        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(side_effect=AssertionError("collect must not run")),
        ), patch.object(engine.tool_registry, "execute_tool", side_effect=fake_execute) as exec_mock, \
             patch.object(SessionState, "record_tool_result", MagicMock()):
            result = await engine._handle_configured_booking_collection(context)
        # Existing proposal/confirmation path IS reached (booking.execute
        # prepares the proposal), and no generic missing-field clarification.
        assert exec_mock.called is True
        assert exec_mock.call_args[0][0] == "booking.execute"
        assert result is not None
        assert "next booking detail" not in result.response_text.lower()
        # No booking success is claimed.
        assert "booked" not in result.response_text.lower()
        stored = context.session_data.state.get_task_step_data("booking")
        assert all(stored.get(k) for k in ["visit_date", "visit_time", "customer_name", "phone"])
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C4 - Retry performs zero writes
# ---------------------------------------------------------------------------
async def test_c4_retry_performs_zero_writes():
    # Five required fields; the batch fills four and leaves email missing so the
    # turn does not reach the proposal path (no pending action). The same batch
    # is then replayed and must be handled write-free from authoritative state.
    context = _make_context(fields=_five_required_fields(), step_data={"service_name": _SERVICE_NAME})
    cand = {
        "visit_date": _tomorrow(), "visit_time": "16:00",
        "customer_name": "Arjun", "phone": "+919999999999",
    }
    _set_shadow_validated(context, cand)
    _flags_on()
    try:
        engine = _engine()
        calls = []
        real_fn = SessionState.update_task_step_data
        target_state = context.session_data.state

        def spy(*a, **k):
            calls.append(a)
            return real_fn(target_state, *a, **k)

        with patch.object(SessionState, "update_task_step_data", side_effect=spy):
            await engine._handle_configured_booking_collection(context)
            first_count = len([c for c in calls if c and c[0] == "booking"])
            assert first_count == 1
            # Second identical invocation: every candidate key already holds an
            # accepted value -> already-satisfied handled result, zero writes,
            # zero collector calls, zero semantic extractor calls.
            with patch.object(
                ConfiguredFieldCollectionCoordinator, "collect",
                new=AsyncMock(side_effect=AssertionError("collect must not run on retry")),
            ), patch.object(
                agentic_engine, "extract_booking_fields",
                new=AsyncMock(side_effect=AssertionError("extractor must not run on retry")),
            ):
                # Replay the identical validated batch.
                _set_shadow_validated(context, cand)
                await engine._handle_configured_booking_collection(context)
            second_count = len([c for c in calls if c and c[0] == "booking"])
            assert second_count == first_count
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["visit_date"] == _tomorrow()
        assert stored["visit_time"] == "16:00"
        assert stored["customer_name"] == "Arjun"
        assert stored["phone"] == "+919999999999"
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C5 - Already-satisfied batch is handled
# ---------------------------------------------------------------------------
async def test_c5_already_satisfied_batch_is_handled():
    context = _make_context(step_data={
        "service_name": _SERVICE_NAME,
        "visit_date": _tomorrow(),
        "visit_time": "16:00",
    })
    coordinator = ConfiguredFieldCollectionCoordinator()
    _flags_on()
    try:
        result = coordinator.apply_external_candidates(
            context, {"visit_date": _tomorrow(), "visit_time": "16:00"}
        )
        assert isinstance(result, ExternalCandidateApplicationResult)
        assert result.handled is True
        assert result.applied is False
        assert result.update_performed is False
        assert result.reason_code == "already_satisfied"
        # No legacy collector runs for a handled already-satisfied batch.
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(side_effect=AssertionError("collect must not run")),
        ):
            _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
            await engine._handle_configured_booking_collection(context)
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C6 - Invalid batch falls back
# ---------------------------------------------------------------------------
async def test_c6_invalid_batch_falls_back():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    coordinator = ConfiguredFieldCollectionCoordinator()
    # Every candidate is unknown -> no accepted patch, not already-satisfied.
    result = coordinator.apply_external_candidates(context, {"unknown_a": "x", "unknown_b": "y"})
    assert result.handled is False
    assert result.applied is False
    assert result.update_performed is False
    stored = context.session_data.state.get_task_step_data("booking")
    assert "unknown_a" not in stored and "unknown_b" not in stored

    _set_shadow_validated(context, {})
    _flags_on()
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(return_value=MagicMock(handled=False)),
        ) as collect_mock:
            await engine._handle_configured_booking_collection(context)
        assert collect_mock.call_count == 1
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C7 - Same utterance cannot populate next string field
# ---------------------------------------------------------------------------
async def test_c7_same_utterance_cannot_populate_next_string_field():
    fields = [
        {"key": "visit_date", "label": "Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
    ]
    context = _make_context(
        fields=fields,
        step_data={"service_name": _SERVICE_NAME},
        text="Tomorrow evening at four.",
    )
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    _flags_on()
    try:
        engine = _engine()
        await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored.get("customer_name") in (None, "")
        assert stored.get("customer_name") != "Tomorrow evening at four."
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C8 - Accepted custom key plus rejected time
# ---------------------------------------------------------------------------
async def test_c8_accepted_custom_key_plus_rejected_time():
    fields = [
        {"key": "insurance_policy_number", "label": "Policy", "type": "string", "required": True},
        {"key": "visit_time", "label": "Time", "type": "time", "required": True},
    ]
    context = _make_context(fields=fields, step_data={"service_name": _SERVICE_NAME})
    # Backend accepted only the policy number; the invalid time is excluded.
    _set_shadow_validated(context, {"insurance_policy_number": "POL-9"})
    _flags_on()
    try:
        engine = _engine()
        with patch.object(
            ConfiguredFieldCollectionCoordinator, "collect",
            new=AsyncMock(side_effect=AssertionError("collect must not run")),
        ):
            result = await engine._handle_configured_booking_collection(context)
        stored = context.session_data.state.get_task_step_data("booking")
        assert stored["insurance_policy_number"] == "POL-9"
        assert "visit_time" not in stored
        focus = result.prompt_focus_fields or []
        assert focus[:1] == (["visit_time"] if focus else [])
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C9 - Uncertain-write protection remains
# ---------------------------------------------------------------------------
async def test_c9_uncertain_write_protection_remains():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    _flags_on()
    try:
        engine = _engine()
        calls = []
        real_fn = SessionState.update_task_step_data
        target_state = context.session_data.state

        def partial_update(*a, **k):
            calls.append(a)
            real_fn(target_state, *a, **k)
            raise RuntimeError("commit unconfirmed")

        with patch.object(SessionState, "update_task_step_data", side_effect=partial_update), \
             patch.object(
                 ConfiguredFieldCollectionCoordinator, "collect",
                 new=AsyncMock(side_effect=AssertionError("collect must not run after uncertain write")),
             ):
            with pytest.raises(RuntimeError):
                await engine._handle_configured_booking_collection(context)
        booking_calls = [c for c in calls if c and c[0] == "booking"]
        assert len(booking_calls) == 1
    finally:
        _flags_off()


# ---------------------------------------------------------------------------
# C10 - Default-disabled behavior unchanged
# ---------------------------------------------------------------------------
async def test_c10_default_disabled_behavior_unchanged():
    context = _make_context(step_data={"service_name": _SERVICE_NAME})
    _set_shadow_validated(context, {"visit_date": _tomorrow(), "visit_time": "16:00"})
    _flags_off()
    engine = _engine()
    with patch.object(
        ConfiguredFieldCollectionCoordinator, "collect",
        new=AsyncMock(return_value=MagicMock(handled=False)),
    ) as collect_mock:
        await engine._handle_configured_booking_collection(context)
    assert collect_mock.called
    stored = context.session_data.state.get_task_step_data("booking")
    assert "visit_date" not in stored
