"""Iteration 4 booking candidate backend-validation shadow tests.

Adds a pure dry-run deterministic validation contract on top of Iteration 2's
already-strict semantic candidates. The existing authoritative ``_validated_patch``
remains the single deterministic validation authority. No real provider or LLM
is contacted; the Iteration 2 contract is mocked where the shadow path is exercised.

These tests assert:
  * exact configured missing keys validate;
  * unknown / protected / already-collected keys are rejected;
  * batch validation, date/time normalization, and select/number/email/phone
    validators are reused unchanged;
  * dry-run never mutates state, calls an LLM, or executes a tool;
  * the validated result lives only in ephemeral shadow evidence;
  * raw vs validated comparisons diverge where the deterministic validator rejects;
  * fail-open behavior on deterministic-validation failure;
  * privacy: no candidate or customer values reach telemetry.
"""
from __future__ import annotations

import copy
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
from backend.logic.engine.configured_field_collection import (
    ConfiguredCandidateValidationResult,
    configured_field_collection_coordinator,
)
from backend.logic.engine.field_extraction_gateway import (
    DynamicTurnFieldCandidate,
    DynamicTurnValidationResult,
)
from backend.logic.engine import global_configured_turn_shadow as shadow
from backend.logic.engine.global_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY,
    capture_global_configured_turn_shadow,
    observe_global_configured_turn_shadow,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module", autouse=True)
def _freeze_booking_clock():
    """Freeze datetime.now inside booking_module for deterministic test clock.

    The affected tests use visit_time=16:00 with visit_date=2026-07-18 in the
    Asia/Kolkata timezone. Without this fixture, validate_payload reads the
    real wall clock, causing pass/fail to change depending on whether the
    machine clock is before or after 16:00 IST.
    """
    import datetime as _real_dt
    from backend.modules import booking_module as _bm

    class _FrozenDateTime:
        _BASE = _real_dt.datetime(2026, 7, 18, 10, 0, 0)

        @staticmethod
        def now(tz=None):
            return _FrozenDateTime._BASE.replace(tzinfo=tz) if tz is not None else _FrozenDateTime._BASE

        combine = staticmethod(_real_dt.datetime.combine)
        fromisoformat = staticmethod(_real_dt.datetime.fromisoformat)

    _mp = pytest.MonkeyPatch()
    _mp.setattr(_bm, "datetime", _FrozenDateTime)
    yield
    _mp.undo()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _booking_fields() -> list[dict]:
    return [
        {"key": "visit_date", "label": "Visit Date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Visit Time", "type": "time", "required": True},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
        {"key": "insurance_policy_number", "label": "Policy", "type": "string", "required": False},
        {"key": "pet_breed", "label": "Breed", "type": "string", "required": False},
        {"key": "visit_type", "label": "Visit Type", "type": "select", "required": False,
         "options": ["consultation", "follow_up"]},
    ]


def _make_context(
    *,
    fields=None,
    text="Book a cleaning tomorrow evening",
    step_data=None,
    source_type="awa_chat",
    channel_type="awa_chat",
    current_module="booking",
    pending_action=None,
    prompt_focus=None,
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
    if step_data:
        state.update_task_step_data("booking", dict(step_data))
    if pending_action is not None:
        state.action_ledger.pending_action = pending_action
    if prompt_focus is not None:
        state.set_prompt_focus(
            workflow=prompt_focus[0],
            requested_fields=list(prompt_focus[1]),
            prompt_kind="missing_fields",
        )
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Clinic", plan_code="enterprise", status="active"),
        user=User(id=__import__("uuid").uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=__import__("uuid").uuid4(),
            tenant_id=tenant_id,
            conversation_id=__import__("uuid").uuid4(),
            config_version=1,
            current_module=current_module,
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={"timezone": "Asia/Kolkata", "modules": modules},
        ),
        incoming_message={"text": text, "channel_type": channel_type, "source_type": source_type},
    )


def _candidate(field_key: str, value: str) -> DynamicTurnFieldCandidate:
    return DynamicTurnFieldCandidate(
        field_key=field_key,
        candidate_value=value,
        confidence=0.9,
        evidence_text="evidence",
    )


def _accepted(*candidates) -> DynamicTurnValidationResult:
    return DynamicTurnValidationResult(
        accepted_candidates=list(candidates),
        rejected=False,
        gateway_unavailable=False,
    )


def _validate(context, candidates):
    return configured_field_collection_coordinator.validate_external_candidates(context, candidates)


# ---------------------------------------------------------------------------
# V1 — Exact configured missing keys validate
# ---------------------------------------------------------------------------
async def test_v1_exact_configured_missing_keys_validate():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {
        "visit_date": "2026-07-18",
        "visit_time": "16:00",
        "customer_name": "Arjun",
        "phone": "+919999999999",
        "insurance_policy_number": "POL123",
    })
    assert isinstance(result, ConfiguredCandidateValidationResult)
    assert result.accepted_field_keys == (
        "customer_name", "insurance_policy_number", "phone", "visit_date", "visit_time",
    )
    assert result.reason_code is None
    assert set(result.accepted_patch.keys()) == set(result.accepted_field_keys)


# ---------------------------------------------------------------------------
# V2 — Arbitrary configured key validates
# ---------------------------------------------------------------------------
async def test_v2_arbitrary_configured_key_validates():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {"pet_breed": "Golden Retriever"})
    assert "pet_breed" in result.accepted_field_keys
    assert result.accepted_patch.get("pet_breed") == "Golden Retriever"


# ---------------------------------------------------------------------------
# V3 — Unknown key rejected
# ---------------------------------------------------------------------------
async def test_v3_unknown_key_rejected():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {"vehicle_registration": "KA01XX1234"})
    assert result.accepted_patch == {}
    assert result.rejected_field_keys == ("vehicle_registration",)
    assert result.reason_code == "unknown_field"


# ---------------------------------------------------------------------------
# V4 — Protected service keys rejected
# ---------------------------------------------------------------------------
async def test_v4_protected_service_keys_rejected():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {"service_name": "Cleaning", "service_id": "cleaning"})
    assert result.accepted_patch == {}
    assert set(result.rejected_field_keys) == {"service_name", "service_id"}


# ---------------------------------------------------------------------------
# V5 — Already collected field is not overwritten
# ---------------------------------------------------------------------------
async def test_v5_already_collected_not_overwritten():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    before = {"customer_name": "Existing Customer"}
    context = _make_context(step_data=before)
    result = _validate(context, {"customer_name": "New Name"})
    assert context.session_data.state.get_task_step_data("booking") == before
    assert "customer_name" not in result.accepted_patch
    assert "customer_name" in result.rejected_field_keys
    assert result.reason_code == "field_already_collected"


# ---------------------------------------------------------------------------
# V6 — Multiple missing fields validate together
# ---------------------------------------------------------------------------
async def test_v6_multiple_missing_fields_validate_together():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {
        "visit_date": "2026-07-18",
        "visit_time": "16:00",
        "customer_name": "Arjun",
        "phone": "+919999999999",
    })
    assert result.accepted_field_keys == (
        "customer_name", "phone", "visit_date", "visit_time",
    )
    assert len(result.accepted_patch) == 4


# ---------------------------------------------------------------------------
# V7 — Invalid time rejected deterministically
# ---------------------------------------------------------------------------
async def test_v7_invalid_time_rejected():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {"visit_time": "25:00"})
    assert "visit_time" not in result.accepted_patch
    assert "visit_time" in result.rejected_field_keys


# ---------------------------------------------------------------------------
# V8 — Ambiguous time remains rejected
# ---------------------------------------------------------------------------
async def test_v8_ambiguous_time_rejected():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _validate(context, {"visit_time": "4"})
    assert "visit_time" not in result.accepted_patch
    assert "visit_time" in result.rejected_field_keys


# ---------------------------------------------------------------------------
# V9 — Date and time normalize through existing authority
# ---------------------------------------------------------------------------
async def test_v9_date_time_normalize_through_authority():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    from datetime import date
    from backend.modules.booking_module import booking_module

    # Freeze the authoritative tenant-local clock so "tomorrow" is deterministic
    # and independent of the machine's real date. The production path resolves
    # visit_date via booking_module._tenant_today() using the tenant timezone
    # (Asia/Kolkata in this context). Freezing that source keeps the assertion
    # fixed at 2026-07-18.
    fixed_tenant_today = date(2026, 7, 17)
    with patch.object(booking_module, "_tenant_today", return_value=fixed_tenant_today):
        result = _validate(context, {"visit_date": "tomorrow", "visit_time": "4 PM"})
    assert result.accepted_patch.get("visit_date") == "2026-07-18"
    assert result.accepted_patch.get("visit_time") == "16:00"


# ---------------------------------------------------------------------------
# V10 — Configured select option enforced
# ---------------------------------------------------------------------------
async def test_v10_select_option_enforced():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    ok = _validate(context, {"visit_type": "consultation"})
    assert "visit_type" in ok.accepted_field_keys
    bad = _validate(context, {"visit_type": "surgery"})
    assert "visit_type" not in bad.accepted_patch


# ---------------------------------------------------------------------------
# V11 — Number, email, and phone use existing validators
# ---------------------------------------------------------------------------
async def test_v11_number_email_phone_existing_validators():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "quantity", "label": "Qty", "type": "number", "required": False},
        {"key": "email", "label": "Email", "type": "email", "required": False},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False},
    ]
    context = _make_context(fields=fields)
    ok = _validate(context, {
        "quantity": "5",
        "email": "a@b.com",
        "phone": "+919999999999",
    })
    assert set(ok.accepted_field_keys) == {"quantity", "email", "phone"}
    bad = _validate(context, {
        "quantity": "notanumber",
        "email": "notanemail",
        "phone": "abc",
    })
    assert bad.accepted_patch == {}


# ---------------------------------------------------------------------------
# V12 — Partial date/time salvage matches existing behavior
# ---------------------------------------------------------------------------
async def test_v12_partial_datetime_salvage_matches_authority():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    candidates = {
        "visit_date": "2026-07-18",
        "visit_time": "25:00",
    }
    coordinator = configured_field_collection_coordinator
    fields = coordinator.resolve_fields(context)
    fields_by_key = {f.key: f for f in fields}
    current = context.session_data.state.get_task_step_data("booking")
    raw = {
        k: (fields_by_key[k], v)
        for k, v in candidates.items()
        if v not in (None, "") and current.get(k) in (None, "")
    }
    expected_patch, _ = coordinator._validated_patch(context, raw)
    result = _validate(context, candidates)
    assert result.accepted_patch == expected_patch
    assert "visit_time" not in result.accepted_patch
    assert "visit_date" in result.accepted_patch


# ---------------------------------------------------------------------------
# V13 — Dry-run does not mutate state
# ---------------------------------------------------------------------------
async def test_v13_dry_run_does_not_mutate_state():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"customer_name": "Asha"})
    before = {
        "step_data": copy.deepcopy(context.session_data.state.step_data),
        "scoped": copy.deepcopy(context.session_data.state.scoped_step_data),
        "focus": context.session_data.state.prompt_focus,
        "ledger": context.session_data.state.action_ledger,
        "module": context.session_data.current_module,
        "incoming": context.incoming_message,
        "config": context.config,
    }
    _validate(context, {"visit_date": "2026-07-18", "visit_time": "16:00"})
    after = {
        "step_data": context.session_data.state.step_data,
        "scoped": context.session_data.state.scoped_step_data,
        "focus": context.session_data.state.prompt_focus,
        "ledger": context.session_data.state.action_ledger,
        "module": context.session_data.current_module,
        "incoming": context.incoming_message,
        "config": context.config,
    }
    assert before["step_data"] == after["step_data"]
    assert before["scoped"] == after["scoped"]
    assert before["focus"] is after["focus"]
    assert before["ledger"] is after["ledger"]
    assert before["module"] is after["module"]
    assert before["incoming"] is after["incoming"]
    assert before["config"] is after["config"]


# ---------------------------------------------------------------------------
# V14 — No LLM call
# ---------------------------------------------------------------------------
async def test_v14_no_llm_call():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()), \
         patch("backend.ai.gateway.runtime.get_runtime_llm_gateway") as gw, \
         patch("backend.logic.engine.field_extraction.extract_booking_fields", new=AsyncMock()):
        _validate(context, {"visit_date": "2026-07-18"})
    gw.assert_not_called()


# ---------------------------------------------------------------------------
# V15 — No tool execution
# ---------------------------------------------------------------------------
async def test_v15_no_tool_execution():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    executed = {"count": 0}

    async def spy_execute(tool_key, ctx):
        executed["count"] += 1
        return MagicMock(success=True)

    with patch("backend.logic.engine.core.ToolRegistry.execute_tool", new=AsyncMock(side_effect=spy_execute)):
        _validate(context, {"visit_date": "2026-07-18", "visit_time": "16:00"})
    assert executed["count"] == 0


# ---------------------------------------------------------------------------
# V16 — Shadow stores validated result ephemerally
# ---------------------------------------------------------------------------
async def test_v16_shadow_stores_validated_result_ephemerally():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    shadow_result = _accepted(_candidate("visit_date", "2026-07-18"), _candidate("visit_time", "16:00"))
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=shadow_result)):
        await capture_global_configured_turn_shadow(context)
    snapshot = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY, {})
    assert snapshot.get("validated_shadow_candidate_keys") == ["visit_date", "visit_time"]
    assert snapshot.get("validated_shadow_patch")
    # authoritative booking state unchanged
    assert context.session_data.state.get_task_step_data("booking") == {}


# ---------------------------------------------------------------------------
# V17 — Raw candidate accepted but deterministic validation rejects
# ---------------------------------------------------------------------------
async def test_v17_raw_accepted_deterministic_rejects():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    shadow_result = _accepted(_candidate("visit_time", "25:00"))
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=shadow_result)):
        await capture_global_configured_turn_shadow(context)
    snapshot = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY, {})
    raw_keys = set(shadow._extract_shadow_candidate_keys(snapshot))
    validated_keys = set(snapshot.get("validated_shadow_candidate_keys") or [])
    assert "visit_time" in raw_keys
    assert "visit_time" not in validated_keys


# ---------------------------------------------------------------------------
# V18 — Validated shadow superset
# ---------------------------------------------------------------------------
async def test_v18_validated_shadow_superset():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snapshot = shadow._make_snapshot(context, shadow._build_semantic_input(context))
    snapshot["semantic_shadow_result"] = _accepted(
        _candidate("visit_date", "2026-07-18"),
        _candidate("visit_time", "16:00"),
    )
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snapshot
    shadow._capture_validated_shadow(context, snapshot)
    # authoritative runtime changes only visit_date
    context.session_data.state.update_task_step_data("booking", {"visit_date": "2026-07-18"})
    observed = {}
    with patch.object(shadow, "_emit_observed_event", side_effect=lambda ctx, **kw: observed.update(kw)):
        await observe_global_configured_turn_shadow(context, MagicMock())
    assert observed["validated_comparison_status"] == "validated_shadow_superset"
    assert observed["validated_shadow_only_keys"] == ["visit_time"]


# ---------------------------------------------------------------------------
# V19 — Validated exact match
# ---------------------------------------------------------------------------
async def test_v19_validated_match():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snapshot = shadow._make_snapshot(context, shadow._build_semantic_input(context))
    snapshot["semantic_shadow_result"] = _accepted(
        _candidate("visit_date", "2026-07-18"),
        _candidate("visit_time", "16:00"),
    )
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snapshot
    shadow._capture_validated_shadow(context, snapshot)
    context.session_data.state.update_task_step_data("booking", {"visit_date": "2026-07-18", "visit_time": "16:00"})
    observed = {}
    with patch.object(shadow, "_emit_observed_event", side_effect=lambda ctx, **kw: observed.update(kw)):
        await observe_global_configured_turn_shadow(context, MagicMock())
    assert observed["validated_comparison_status"] == "validated_match"


# ---------------------------------------------------------------------------
# V20 — Validation failure is fail-open
# ---------------------------------------------------------------------------
async def test_v20_validation_failure_fail_open():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snapshot = shadow._make_snapshot(context, shadow._build_semantic_input(context))
    snapshot["semantic_shadow_result"] = _accepted(_candidate("visit_date", "2026-07-18"))
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snapshot

    class _Boom:
        def validate_external_candidates(self, context, candidates):
            raise RuntimeError("validation down")

    boom = _Boom()
    with patch.object(shadow, "configured_field_collection_coordinator", new=boom), \
         patch.object(shadow, "_emit_failure_event", new=MagicMock()) as fail:
        shadow._capture_validated_shadow(context, snapshot)
    # snapshot still stored, validated fields empty, no exception escaped
    assert snapshot.get("validated_shadow_candidate_keys") == []
    assert context.session_data.state.get_task_step_data("booking") == {}
    fail.assert_called()
    assert fail.call_args.kwargs["stage"] == "deterministic_validation"


# ---------------------------------------------------------------------------
# V21 — No values in telemetry
# ---------------------------------------------------------------------------
async def test_v21_no_values_in_telemetry():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        text="PRIVATE_NAME_SENTINEL +919999999999 private@example.com PRIVATE_POLICY_SENTINEL",
    )
    snapshot = shadow._make_snapshot(context, shadow._build_semantic_input(context))
    snapshot["semantic_shadow_result"] = _accepted(
        _candidate("customer_name", "PRIVATE_NAME_SENTINEL"),
        _candidate("phone", "+919999999999"),
        _candidate("email", "private@example.com"),
        _candidate("insurance_policy_number", "PRIVATE_POLICY_SENTINEL"),
    )
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snapshot
    shadow._capture_validated_shadow(context, snapshot)

    captured_extras = []

    def spy_log(*args, **kwargs):
        extra = kwargs.get("extra")
        if extra and isinstance(extra, dict) and "extra_data" in extra:
            captured_extras.append(extra["extra_data"])

    with patch.object(shadow.logger, "info", side_effect=spy_log), \
         patch.object(shadow.logger, "warning", side_effect=spy_log):
        await observe_global_configured_turn_shadow(context, MagicMock())

    blob = __import__("json").dumps(captured_extras, default=str)
    for secret in ["PRIVATE_NAME_SENTINEL", "+919999999999", "private@example.com", "PRIVATE_POLICY_SENTINEL"]:
        assert secret not in blob


# ---------------------------------------------------------------------------
# V22 — Customer output unchanged
# ---------------------------------------------------------------------------
async def test_v22_customer_output_unchanged():
    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    from backend.logic.engine import core as agentic_engine
    from backend.tests.unit.test_conversation_ux import install_agent_test_doubles, FakeToolCallingLLM
    from langchain_core.messages import AIMessage
    import pytest as _pytest

    async def run(flag_enabled: bool) -> Any:
        settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = flag_enabled
        context = _make_context(text="Book a cleaning tomorrow evening")
        mp = _pytest.MonkeyPatch()
        llm = FakeToolCallingLLM([AIMessage(content="I can help with that.")])
        await install_agent_test_doubles(mp, llm)
        shadow_result = _accepted(
            _candidate("visit_date", "2026-07-18"),
            _candidate("visit_time", "16:00"),
        )
        with patch.object(settings, "LLM_GATEWAY_ENABLED", False), \
             patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=shadow_result)), \
             patch("backend.logic.engine.core.message_router") as router:
            router.route_message.return_value = MagicMock(
                intent="booking", route_type="active_task", reason="active_task_continuation",
                should_clear_active_task=False, is_tangent=False,
            )
            engine = agentic_engine.AgenticEngine()
            result = await engine.execute_step(context)
        mp.undo()
        return result

    r_disabled = await run(False)
    r_ok = await run(True)
    assert r_disabled.response_text == r_ok.response_text
    assert r_disabled.quick_replies == r_ok.quick_replies
    assert r_disabled.next_state == r_ok.next_state
    assert r_disabled.requires_response == r_ok.requires_response
    assert r_disabled.prompt_focus_fields == r_ok.prompt_focus_fields


# ---------------------------------------------------------------------------
# V23 — Same-day past time rejected with explicit late clock
# ---------------------------------------------------------------------------
async def test_same_day_past_visit_time_remains_rejected_with_explicit_late_clock():
    """Prove production past-time rejection is preserved with an explicit late clock.

    The module-scoped fixture freezes the clock at 10:00 IST. Overriding it to
    17:00 IST makes 16:00 unambiguously past, confirming that the production
    validation still rejects past appointment times when the clock is advanced.
    """
    import datetime as _real_dt
    from backend.modules import booking_module as _bm

    class _LateFrozenDateTime:
        _BASE = _real_dt.datetime(2026, 7, 18, 17, 0, 0)

        @staticmethod
        def now(tz=None):
            return _LateFrozenDateTime._BASE.replace(tzinfo=tz) if tz is not None else _LateFrozenDateTime._BASE

        combine = staticmethod(_real_dt.datetime.combine)
        fromisoformat = staticmethod(_real_dt.datetime.fromisoformat)

    settings.ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    with patch.object(_bm, "datetime", _LateFrozenDateTime):
        result = _validate(context, {
            "visit_date": "2026-07-18",
            "visit_time": "16:00",
        })
    assert "visit_date" in result.accepted_field_keys
    assert "visit_time" not in result.accepted_field_keys
    assert "visit_time" in result.rejected_field_keys
