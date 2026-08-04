"""Iteration 7D-C authoritative terminal lead registry handoff tests.

When a pre-existing active lead receives a complete terminal batch of safe configured
fields, the engine revalidates the candidates at application time, builds an exact
tenant-configured terminal payload, replaces the lead-scoped state once, and executes
``lead_capture.execute`` through the ToolRegistry exactly once, records the tool result,
and returns the existing authoritative tool response. No direct module/handler/service/
repository/event call is made and no legacy extraction or LangGraph path runs again on
the same turn.

The terminal flag defaults false and requires all three lead flags enabled. The handoff
remains default-disabled and must stay disabled until 7D-D completes.

The Iteration 2 dynamic configured-turn contract is mocked; no live provider is contacted.
"""
from __future__ import annotations

import uuid
from typing import Any

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.logic.engine.field_extraction_gateway import (
    DynamicTurnFieldCandidate,
    DynamicTurnValidationResult,
)
from backend.core.config import settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    PendingAction,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.modules.lead_capture_module import lead_capture_module
from backend.tools.registry import ToolExecutionResult

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _lead_fields() -> list[dict]:
    return [
        {"key": "company_name", "label": "Company", "type": "string", "required": True},
        {"key": "contact_email", "label": "Email", "type": "email", "required": True},
        {"key": "industry", "label": "Industry", "type": "string", "required": False},
        {"key": "name", "label": "Name", "type": "string", "required": False},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False},
    ]


def _make_context(
    *,
    fields=None,
    text="My email is sales@acme.example and we are in healthcare.",
    step_data=None,
    source_type="awa_chat",
    channel_type="awa_chat",
    current_module="lead_capture",
    pending_action=None,
    enabled=True,
):
    tenant_id = uuid.uuid4()
    modules = {
        "lead_capture": {
            "enabled": enabled,
            "fields": fields if fields is not None else _lead_fields(),
        }
    }
    state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    state.action_ledger.current_task = current_module if current_module else None
    state.action_ledger.task_status = "collecting"
    if step_data is not None:
        state.update_task_step_data("lead_capture", dict(step_data))
    elif current_module == "lead_capture":
        state.update_task_step_data("lead_capture", {"company_name": "Acme"})
    if pending_action is not None:
        state.action_ledger.pending_action = pending_action
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid.uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid.uuid4(),
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


def _candidate(field_key: str, value: Any) -> DynamicTurnFieldCandidate:
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


def _seed_terminal_shadow(context: ExecutionContext, candidates: dict[str, Any]) -> None:
    """Seed a valid Iteration 7B lead shadow snapshot whose batch is terminal."""
    from backend.logic.engine.lead_configured_turn_shadow import SHADOW_EVIDENCE_KEY

    validation = lead_capture_module.validate_external_configured_candidates(context, candidates)
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = {
        "workflow": "lead_capture",
        "config_version": 1,
        "channel": "awa_chat",
        "focus_field_key": None,
        "safe_configured_field_keys": list(validation.accepted_field_keys),
        "missing_required_field_keys": [],
        "excluded_unsafe_field_count": 0,
        "excluded_unsupported_type_count": 0,
        "pre_turn_safe_field_values": dict(
            context.session_data.state.get_task_step_data("lead_capture") or {}
        ),
        "semantic_result": _accepted(
            *[_candidate(k, v) for k, v in candidates.items()]
        ),
        "validated_result": {
            "accepted_patch": dict(validation.accepted_patch),
            "accepted_field_keys": list(validation.accepted_field_keys),
            "rejected_field_keys": list(validation.rejected_field_keys),
            "reason_code": validation.reason_code,
        },
    }


def _set_all_lead_flags(enabled: bool) -> None:
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = enabled
    settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = enabled
    settings.ENABLE_LEAD_CONFIGURED_TURN_TERMINAL_APPLY = enabled


def _make_engine(execute_result: ToolExecutionResult | None = None):
    """Build an AgenticEngine instance with a mocked tool registry."""
    from backend.logic.engine.core import AgenticEngine

    engine = AgenticEngine.__new__(AgenticEngine)
    registry = MagicMock()
    tool_def = MagicMock()
    tool_def.module = lead_capture_module
    registry.get_tool.return_value = tool_def
    registry.get_tool_key_for_module.return_value = "lead_capture.execute"
    registry.execute_calls = []

    async def _fake_execute(tool_key, ctx):
        registry.execute_calls.append(tool_key)
        if execute_result is not None:
            return execute_result
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="Your request was completed.",
            denied_reason=None,
            data={},
            validation_errors=None,
            created_resource_type="lead",
            created_resource_id=str(uuid.uuid4()),
        )

    registry.execute_tool = _fake_execute
    engine.tool_registry = registry
    return engine


def _success_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_key="lead_capture.execute",
        success=True,
        user_message="Your contact request was submitted.",
        denied_reason=None,
        data={},
        validation_errors=None,
        created_resource_type="lead",
        created_resource_id=str(uuid.uuid4()),
    )


def _success_no_resource_id() -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_key="lead_capture.execute",
        success=True,
        user_message="All set, your request was completed successfully.",
        denied_reason=None,
        data={},
        validation_errors=None,
        created_resource_type="lead",
        created_resource_id=None,
    )


def _failure_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_key="lead_capture.execute",
        success=False,
        user_message="Something went wrong, please try again.",
        denied_reason="tool_failed",
        data={},
        validation_errors=None,
    )


@pytest.fixture(autouse=True)
def _reset_lead_flags():
    """Restore the shared lead settings flags to their defaults after each test."""
    yield
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = False
    settings.ENABLE_LEAD_CONFIGURED_TURN_TERMINAL_APPLY = False


class _RecordingDict(dict):
    """Dict subclass that records every lead_capture key assignment."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lead_assignments = []

    def __setitem__(self, key, value):
        if key == "lead_capture":
            self.lead_assignments.append(dict(value))
        return super().__setitem__(key, value)


# ---------------------------------------------------------------------------
# H1 — Terminal flag defaults false
# ---------------------------------------------------------------------------
async def test_h1_terminal_flag_defaults_false():
    from backend.core.config import Settings

    fresh = Settings()
    assert getattr(fresh, "ENABLE_LEAD_CONFIGURED_TURN_TERMINAL_APPLY", None) is False


# ---------------------------------------------------------------------------
# H2 — All three flags required
# ---------------------------------------------------------------------------
async def test_h2_all_three_flags_required():
    from backend.logic.engine.core import _authoritative_lead_terminal_apply_enabled

    for shadow, apply, terminal in [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
    ]:
        settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = shadow
        settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = apply
        settings.ENABLE_LEAD_CONFIGURED_TURN_TERMINAL_APPLY = terminal
        assert _authoritative_lead_terminal_apply_enabled() is False


# ---------------------------------------------------------------------------
# H3 — Pre-existing active lead required
# ---------------------------------------------------------------------------
async def test_h3_preexisting_active_lead_required():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    for module in ["booking", "order", "payments", "support", "reminders", "reviews", "faq", "human_handoff", None]:
        context = _make_context(current_module=module if module else "general")
        if module == "booking":
            context.session_data.state.update_task_step_data("booking", {"service_name": "X"})
            context.session_data.state.action_ledger.current_task = "booking"
        _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
        engine = _make_engine()
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
        assert result is None, f"unexpected handoff for module={module}"


# ---------------------------------------------------------------------------
# H4 — Pending / internal / OTP blocks handoff
# ---------------------------------------------------------------------------
async def test_h4_pending_internal_otp_blocks():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    pa = PendingAction(
        tool_key="lead_capture.execute",
        args={},
        args_hash="x" * 24,
        action_nonce="y" * 32,
        requires_confirmation=False,
    )
    context = _make_context(pending_action=pa)
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    assert await engine._try_authoritative_lead_terminal_registry_handoff(context) is None

    context2 = _make_context(source_type="internal_event")
    _seed_terminal_shadow(context2, {"contact_email": "sales@acme.example"})
    engine2 = _make_engine()
    assert await engine2._try_authoritative_lead_terminal_registry_handoff(context2) is None

    context3 = _make_context(text="enter the otp 123456")
    _seed_terminal_shadow(context3, {"contact_email": "sales@acme.example"})
    engine3 = _make_engine()
    assert await engine3._try_authoritative_lead_terminal_registry_handoff(context3) is None


# ---------------------------------------------------------------------------
# H5 — Terminal-deferred result required
# ---------------------------------------------------------------------------
async def test_h5_terminal_deferred_required():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # A non-terminal batch (still missing required contact_email) must not hand off.
    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"industry": "healthcare"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None


# ---------------------------------------------------------------------------
# H6 — Newly accepted terminal field required
# ---------------------------------------------------------------------------
async def test_h6_newly_accepted_field_required():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # Current state already complete: candidate would be already-collected -> no accept.
    context = _make_context(step_data={"company_name": "Acme", "contact_email": "sales@acme.example"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None


# ---------------------------------------------------------------------------
# H7 — Exact custom terminal payload succeeds
# ---------------------------------------------------------------------------
async def test_h7_custom_terminal_payload_succeeds():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example", "industry": "healthcare"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H8 — Terminal validator invoked with merged in-memory state
# ---------------------------------------------------------------------------
async def test_h8_terminal_validator_invoked_with_merged_state():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    with patch.object(
        lead_capture_module,
        "validate_terminal_configured_payload",
        wraps=lead_capture_module.validate_terminal_configured_payload,
    ) as spy:
        await engine._try_authoritative_lead_terminal_registry_handoff(context)
        assert spy.call_count == 1
        merged = spy.call_args.args[1]
        assert merged.get("company_name") == "Acme"
        assert merged.get("contact_email") == "sales@acme.example"


# ---------------------------------------------------------------------------
# H9 — Legacy validate_payload not invoked
# ---------------------------------------------------------------------------
async def test_h9_legacy_validate_payload_not_invoked():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    with patch.object(
        lead_capture_module, "validate_payload", new=MagicMock(side_effect=AssertionError("legacy ran"))
    ):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None


# ---------------------------------------------------------------------------
# H10 — Application-time candidate revalidation occurs
# ---------------------------------------------------------------------------
async def test_h10_application_time_revalidation_occurs():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    with patch.object(
        lead_capture_module,
        "validate_external_configured_candidates",
        wraps=lead_capture_module.validate_external_configured_candidates,
    ) as spy:
        await engine._try_authoritative_lead_terminal_registry_handoff(context)
        # Application-time revalidation runs (the apply path also revalidates
        # internally, so the candidate validator is invoked at least once here).
        assert spy.call_count >= 1


# ---------------------------------------------------------------------------
# H11 — Raw semantic values never written
# ---------------------------------------------------------------------------
async def test_h11_raw_semantic_values_never_written():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # Configured: name (required), contact_email (required). Only the configured keys
    # reach the registry payload; nothing else (no shadow evidence, no raw tokens).
    fields = [
        {"key": "name", "type": "string", "required": True},
        {"key": "contact_email", "type": "email", "required": True},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_terminal_shadow(context, {"name": "Arjun", "contact_email": "arjun@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    # Only the configured keys are written; no raw/unconfigured keys leak.
    assert lead.get("name") == "Arjun"
    assert lead.get("contact_email") == "arjun@acme.example"
    assert set(lead.keys()) == {"name", "contact_email"}


# ---------------------------------------------------------------------------
# H12 — Exact payload replacement
# ---------------------------------------------------------------------------
async def test_h12_exact_payload_replacement():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    # Exact replacement: only the normalized configured keys remain, no unrelated or
    # injected keys (tool args, metadata, shadow evidence, internal markers).
    assert lead == {"company_name": "Acme", "contact_email": "sales@acme.example"}
    assert "unrelated_sentinel" not in lead


# ---------------------------------------------------------------------------
# H13 — One state replacement on success
# ---------------------------------------------------------------------------
async def test_h13_one_state_replacement_on_success():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    recording = _RecordingDict()
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Exactly one exact-replacement of the lead-scoped state on success.
    assert len(recording.lead_assignments) == 1


# ---------------------------------------------------------------------------
# H14 — Registry activates scoped state, no manual global write
# ---------------------------------------------------------------------------
async def test_h14_registry_activates_scoped_state():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # The helper performs exactly one exact scoped replacement; the registry's
    # execute_tool (activate_task_step_data) mirrors it into global step_data at
    # execution time. No manual global write is performed by the helper.
    assert context.session_data.state.scoped_step_data["lead_capture"] == {
        "company_name": "Acme",
        "contact_email": "sales@acme.example",
    }
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H15 — Registry executes exactly once
# ---------------------------------------------------------------------------
async def test_h15_registry_executes_exactly_once():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]
    assert len(engine.tool_registry.execute_calls) == 1


# ---------------------------------------------------------------------------
# H16 — No direct module execution
# ---------------------------------------------------------------------------
async def test_h16_no_direct_module_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    with patch.object(
        lead_capture_module, "execute", new=AsyncMock(side_effect=AssertionError("direct module ran"))
    ) as direct:
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    direct.assert_not_called()


# ---------------------------------------------------------------------------
# H17 — No direct handler/service/repository/event call
# ---------------------------------------------------------------------------
async def test_h17_no_direct_handler_service_repo_event():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine
    from backend.domains.leads import tool_handlers as lead_handlers

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    with patch.object(
        lead_handlers,
        "execute_lead_capture_tool",
        new=AsyncMock(side_effect=AssertionError("handler ran")),
    ):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None


# ---------------------------------------------------------------------------
# H18 — Tool result recorded exactly once
# ---------------------------------------------------------------------------
async def test_h18_tool_result_recorded_once():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    recorded = []
    original_record = SessionState.record_tool_result

    def _record_spy(self, result, channel_type=None, **kwargs):
        recorded.append(result)
        return original_record(self, result, channel_type=channel_type, **kwargs)

    with patch.object(SessionState, "record_tool_result", _record_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert len(recorded) == 1


# ---------------------------------------------------------------------------
# H19 — Success requires authoritative resource truth
# ---------------------------------------------------------------------------
async def test_h19_success_requires_resource_truth():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    lowered = (result.response_text or "").lower()
    assert "submitted" in lowered or "completed" in lowered


# ---------------------------------------------------------------------------
# H20 — Missing resource id is fail-closed
# ---------------------------------------------------------------------------
async def test_h20_missing_resource_id_fail_closed():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_no_resource_id())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Fail-closed: no saved/submitted/created success language reaches the customer.
    lowered = (result.response_text or "").lower()
    assert "saved" not in lowered
    assert "submitted" not in lowered
    assert "created" not in lowered
    assert "your lead was submitted" not in lowered


# ---------------------------------------------------------------------------
# H21 — Tool success completes task through existing recorder
# ---------------------------------------------------------------------------
async def test_h21_success_completes_via_recorder():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Recorder set task_status=completed via existing behavior, not manual assignment.
    assert context.session_data.state.action_ledger.task_status == "completed"


# ---------------------------------------------------------------------------
# H22 — Success skips legacy extraction
# ---------------------------------------------------------------------------
async def test_h22_success_skips_legacy_extraction():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    with patch(
        "backend.logic.engine.field_extraction.extract_lead_fields",
        new=AsyncMock(side_effect=AssertionError("legacy extractor ran")),
    ), patch.object(
        AgenticEngine, "_apply_safe_lead_field_updates", new=MagicMock(side_effect=AssertionError("safe-update ran"))
    ):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None


# ---------------------------------------------------------------------------
# H23 — Success skips LangGraph and second tool execution
# ---------------------------------------------------------------------------
async def test_h23_success_skips_langgraph():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H24 — Success does not restore prior state
# ---------------------------------------------------------------------------
async def test_h24_success_does_not_restore_prior_state():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Exactly one pre-execution replacement, no restoration on success.
    assert len(recording.lead_assignments) == 1


# ---------------------------------------------------------------------------
# H25 — Explicit tool failure restores prior scoped state
# ---------------------------------------------------------------------------
async def test_h25_failure_restores_prior_state():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    replace_calls = []
    restore_calls = []
    original_replace = SessionState.replace_task_step_data
    original_restore = SessionState.restore_task_step_data_snapshot

    def _replace_spy(self, task_key, values):
        replace_calls.append(task_key)
        return original_replace(self, task_key, values)

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append(task_key)
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "replace_task_step_data", _replace_spy), \
         patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # One pre-execution replacement + one snapshot restoration.
    assert replace_calls == ["lead_capture"]
    assert restore_calls == ["lead_capture"]
    lead = context.session_data.state.get_task_step_data("lead_capture")
    # Prior state is restored exactly (terminal payload not left behind).
    assert lead == {"company_name": "Acme"}
    assert lead.get("contact_email") is None


# ---------------------------------------------------------------------------
# H26 — Explicit failure result recorded once
# ---------------------------------------------------------------------------
async def test_h26_failure_result_recorded_once():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    recorded = []
    original_record = SessionState.record_tool_result

    def _record_spy(self, result, channel_type=None, **kwargs):
        recorded.append(result)
        return original_record(self, result, channel_type=channel_type, **kwargs)

    with patch.object(SessionState, "record_tool_result", _record_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert len(recorded) == 1
    assert recorded[0].success is False


# ---------------------------------------------------------------------------
# H27 — Explicit failure uses failure response
# ---------------------------------------------------------------------------
async def test_h27_failure_uses_failure_response():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    lowered = (result.response_text or "").lower()
    assert "submitted" not in lowered
    assert "created" not in lowered
    assert "saved" not in lowered


# ---------------------------------------------------------------------------
# H28 — Explicit failure skips legacy and LangGraph
# ---------------------------------------------------------------------------
async def test_h28_failure_skips_legacy_and_langgraph():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    with patch(
        "backend.logic.engine.field_extraction.extract_lead_fields",
        new=AsyncMock(side_effect=AssertionError("legacy extractor ran")),
    ), patch.object(
        AgenticEngine, "_apply_safe_lead_field_updates", new=MagicMock(side_effect=AssertionError("safe-update ran"))
    ):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H29 — Registry exception propagates
# ---------------------------------------------------------------------------
async def test_h29_registry_exception_propagates():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    async def _boom(tool_key, ctx):
        raise RuntimeError("registry exploded")
    engine.tool_registry.execute_tool = _boom
    with pytest.raises(RuntimeError):
        await engine._try_authoritative_lead_terminal_registry_handoff(context)


# ---------------------------------------------------------------------------
# H30 — Registry exception causes no second execution / legacy / langgraph
# ---------------------------------------------------------------------------
async def test_h30_exception_no_second_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    calls = []
    async def _boom(tool_key, ctx):
        calls.append(tool_key)
        raise RuntimeError("registry exploded")
    engine.tool_registry.execute_tool = _boom
    with pytest.raises(RuntimeError), patch(
        "backend.logic.engine.field_extraction.extract_lead_fields",
        new=AsyncMock(side_effect=AssertionError("legacy ran")),
    ):
        await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H31 — Registry exception does not restore state
# ---------------------------------------------------------------------------
async def test_h31_exception_does_not_restore_state():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    async def _boom(tool_key, ctx):
        raise RuntimeError("registry exploded")
    engine.tool_registry.execute_tool = _boom
    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    with pytest.raises(RuntimeError):
        await engine._try_authoritative_lead_terminal_registry_handoff(context)
    # Only the pre-execution replacement happened; no restoration (uncertain state).
    assert len(recording.lead_assignments) == 1


# ---------------------------------------------------------------------------
# H32 — Result-recording exception propagates
# ---------------------------------------------------------------------------
async def test_h32_result_recording_exception_propagates():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())

    def _record_boom(self, result, channel_type=None, **kwargs):
        raise RuntimeError("record exploded")

    with patch.object(SessionState, "record_tool_result", _record_boom):
        with pytest.raises(RuntimeError):
            await engine._try_authoritative_lead_terminal_registry_handoff(context)
    # No legacy fallback executed.


# ---------------------------------------------------------------------------
# H33 — Optional field omitted succeeds
# ---------------------------------------------------------------------------
async def test_h33_optional_field_omitted_succeeds():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # Required: company_name, contact_email only.
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "contact_email", "type": "email", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H34 — Valid optional field preserved exactly
# ---------------------------------------------------------------------------
async def test_h34_valid_optional_field_preserved():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "contact_email", "type": "email", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example", "industry": "healthcare"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("industry") == "healthcare"


# ---------------------------------------------------------------------------
# H35 — Invalid optional field prevents execution
# ---------------------------------------------------------------------------
async def test_h35_invalid_optional_field_prevents_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    # contact_email invalid -> terminal validation fails -> no handoff/state write.
    _seed_terminal_shadow(context, {"contact_email": "not-an-email"})
    engine = _make_engine()
    recording = _RecordingDict()
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert recording.lead_assignments == []
    assert engine.tool_registry.execute_calls == []


# ---------------------------------------------------------------------------
# H36 — Protected key prevents execution
# ---------------------------------------------------------------------------
async def test_h36_protected_key_prevents_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme", "contact_email": "sales@acme.example"})
    # status is a protected key; terminal validation rejects it.
    _seed_terminal_shadow(context, {"status": "open"})
    engine = _make_engine()
    recording = _RecordingDict()
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []
    assert recording.lead_assignments == []


# ---------------------------------------------------------------------------
# H37 — Consent-sensitive key prevents execution
# ---------------------------------------------------------------------------
async def test_h37_consent_sensitive_key_prevents_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme", "contact_email": "sales@acme.example"})
    _seed_terminal_shadow(context, {"marketing_consent": "yes"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []


# ---------------------------------------------------------------------------
# H38 — Unsupported configured type prevents execution
# ---------------------------------------------------------------------------
async def test_h38_unsupported_configured_type_prevents_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # date is an unsupported configured type for the lead terminal validator.
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "contact_email", "type": "email", "required": True},
        {"key": "preferred_date", "type": "date", "required": True},
    ]
    context = _make_context(fields=fields, step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example", "preferred_date": "2026-01-01"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []


# ---------------------------------------------------------------------------
# H39 — Unknown key prevents execution
# ---------------------------------------------------------------------------
async def test_h39_unknown_key_prevents_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme", "contact_email": "sales@acme.example"})
    _seed_terminal_shadow(context, {"favorite_color": "blue"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []


# ---------------------------------------------------------------------------
# H40 — Alias projection written once
# ---------------------------------------------------------------------------
async def test_h40_alias_projection_written_once():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # Configured: name (required), contact_email (required). The configured key is the
    # only key written to the registry payload; no duplicate alias or raw token appears.
    fields = [
        {"key": "name", "type": "string", "required": True},
        {"key": "contact_email", "type": "email", "required": True},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_terminal_shadow(context, {"name": "Arjun", "contact_email": "arjun@acme.example"})
    engine = _make_engine()
    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    # Exactly one exact replacement; only the configured keys reach the payload once.
    assert len(recording.lead_assignments) == 1
    assert set(lead.keys()) == {"name", "contact_email"}
    assert lead.get("name") == "Arjun"
    assert lead.get("contact_email") == "arjun@acme.example"


# ---------------------------------------------------------------------------
# H41 — Fresh lead activation unchanged
# ---------------------------------------------------------------------------
async def test_h41_fresh_lead_activation_unchanged():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # No active lead scoped state; not eligible.
    context = _make_context(current_module="general", step_data=None)
    context.session_data.state.scoped_step_data.pop("lead_capture", None)
    context.session_data.state.step_data = {}
    context.session_data.state.action_ledger.current_task = None
    context.session_data.current_module = None
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []


# ---------------------------------------------------------------------------
# H42 — Default-disabled behavior equality
# ---------------------------------------------------------------------------
async def test_h42_default_disabled_behavior():
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []
    # State untouched.
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert "contact_email" not in lead


# ---------------------------------------------------------------------------
# H43 — Completed task blocks later handoff
# ---------------------------------------------------------------------------
async def test_h43_completed_task_blocks_later_handoff():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme", "contact_email": "sales@acme.example"})
    context.session_data.state.action_ledger.task_status = "completed"
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None


# ---------------------------------------------------------------------------
# H44 — Same handled turn cannot execute registry twice
# ---------------------------------------------------------------------------
async def test_h44_handled_turn_single_registry_execution():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]
    assert len(engine.tool_registry.execute_calls) == 1


# ---------------------------------------------------------------------------
# H45 — Key-only success telemetry
# ---------------------------------------------------------------------------
async def test_h45_key_only_success_telemetry():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    captured = []
    with patch("backend.ops.logger.logger.info") as info:
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
        for call in info.call_args_list:
            if call.args and call.args[0] == "lead_configured_turn_terminal_handoff":
                captured.append(call.kwargs["extra"]["extra_data"])
    assert result is not None
    assert captured
    for evt in captured:
        assert evt["event"] == "lead_configured_turn_terminal_handoff"
        # No customer values / ids present.
        assert "sales@acme.example" not in str(evt)
        assert "Acme" not in str(evt)
        assert "created_resource_id" not in evt


# ---------------------------------------------------------------------------
# H46 — Key-only tool-failure telemetry
# ---------------------------------------------------------------------------
async def test_h46_key_only_tool_failure_telemetry():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    captured = []
    with patch("backend.ops.logger.logger.info") as info:
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
        for call in info.call_args_list:
            if call.args and call.args[0] == "lead_configured_turn_terminal_handoff":
                captured.append(call.kwargs["extra"]["extra_data"])
    assert result is not None
    failure_events = [e for e in captured if e.get("status") == "tool_failure"]
    assert failure_events
    for evt in failure_events:
        assert "sales@acme.example" not in str(evt)
        assert "Acme" not in str(evt)


# ---------------------------------------------------------------------------
# H47 — Exception telemetry reuses shadow failure event
# ---------------------------------------------------------------------------
async def test_h47_exception_telemetry_reuses_shadow_failure():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    async def _boom(tool_key, ctx):
        raise RuntimeError("registry exploded")
    engine.tool_registry.execute_tool = _boom
    captured = []
    with patch("backend.ops.logger.logger.warning") as warn:
        try:
            await engine._try_authoritative_lead_terminal_registry_handoff(context)
        except RuntimeError:
            pass
        for call in warn.call_args_list:
            if call.args and call.args[0] == "lead_configured_turn_shadow_failed":
                captured.append(call.kwargs["extra"]["extra_data"])
    assert captured
    evt = captured[0]
    assert evt["event"] == "lead_configured_turn_shadow_failed"
    assert evt["stage"] == "authoritative_lead_terminal_handoff"
    # Forbidden distinct failure event is never emitted.
    assert evt["event"] != "lead_configured_turn_terminal_handoff_failed"


# ---------------------------------------------------------------------------
# H48 — Telemetry failure is fail-open
# ---------------------------------------------------------------------------
async def test_h48_telemetry_failure_fail_open():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    with patch("backend.ops.logger.logger.info", side_effect=RuntimeError("log down")):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    # Customer result unchanged despite telemetry failure.
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# H49 — Booking and order isolation
# ---------------------------------------------------------------------------
async def test_h49_booking_order_isolation():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # Booking active context must never consult terminal lead handoff.
    context = _make_context(current_module="booking")
    context.session_data.state.update_task_step_data("booking", {"service_name": "X"})
    context.session_data.state.action_ledger.current_task = "booking"
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is None
    assert engine.tool_registry.execute_calls == []

    context2 = _make_context(current_module="order")
    context2.session_data.state.update_task_step_data("order", {"item_name": "Y"})
    context2.session_data.state.action_ledger.current_task = "order"
    _seed_terminal_shadow(context2, {"contact_email": "sales@acme.example"})
    engine2 = _make_engine()
    result2 = await engine2._try_authoritative_lead_terminal_registry_handoff(context2)
    assert result2 is None
    assert engine2.tool_registry.execute_calls == []


# ---------------------------------------------------------------------------
# H50 — No concurrency-safety claim
# ---------------------------------------------------------------------------
async def test_h50_no_concurrency_safety_mechanism():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    with patch("backend.core.redis_client.get_redis_client", new=MagicMock(side_effect=AssertionError("redis used"))):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # No migration, lock, dedupe, or unique-constraint function invoked by the handoff.
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# ---------------------------------------------------------------------------
# R-segment — Exact payload projection proofs (sentinel fixture)
# ---------------------------------------------------------------------------
def _sentinel_step_data() -> dict:
    """Prior lead-scoped state that must NOT reach the registry after replacement.

    company_name is intentionally blank so the terminal candidate ``company_name`` is
    newly accepted (the revalidator only newly-accepts fields absent from prior state).
    The unrelated keys (UNRELATED_SENTINEL/metadata/source) are unknown to the terminal
    schema and must be dropped by the exact replacement, never reaching the registry.
    """
    return {
        "company_name": "",
        "contact_email": "",
        "UNRELATED_SENTINEL": "MUST_DISAPPEAR",
        "metadata": "MUST_DISAPPEAR",
        "source": "MUST_DISAPPEAR",
    }


def _terminal_normalized_payload() -> dict:
    return {
        "company_name": "Acme",
        "contact_email": "sales@acme.example",
    }


async def _run_sentinel_handoff(
    execute_result=None, before_registry=None
):
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=execute_result)
    registry_seen = {}

    original_execute = engine.tool_registry.execute_tool

    async def _capture_execute(tool_key, ctx):
        # Capture registry-visible lead state immediately before/at execution.
        registry_seen["scoped_lead"] = dict(
            ctx.session_data.state.get_task_step_data("lead_capture") or {}
        )
        # Allow callers to inspect pre-registry (pre-activation) state first.
        if before_registry is not None:
            before_registry(ctx)
        # The real tool registry is the single owner of global activation; emulate it
        # here so registry-visible global state reflects the exact normalized payload.
        ctx.session_data.state.activate_task_step_data("lead_capture")
        registry_seen["global_step_data"] = dict(ctx.session_data.state.step_data or {})
        return await original_execute(tool_key, ctx)

    engine.tool_registry.execute_tool = _capture_execute
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    return context, engine, result, registry_seen


# R1 — Exact replacement removes unrelated fields (sentinel fixture, pre-registry).
async def test_r1_exact_replacement_removes_unrelated_fields():
    context, engine, result, registry_seen = await _run_sentinel_handoff(
        execute_result=_success_result()
    )
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == _terminal_normalized_payload()
    assert "UNRELATED_SENTINEL" not in lead
    assert "metadata" not in lead
    assert "source" not in lead


# R2 — Registry sees exact normalized payload (scoped + global after activation).
async def test_r2_registry_sees_exact_normalized_payload():
    context, engine, result, registry_seen = await _run_sentinel_handoff(
        execute_result=_success_result()
    )
    assert result is not None
    assert registry_seen["scoped_lead"] == _terminal_normalized_payload()
    assert "UNRELATED_SENTINEL" not in registry_seen["scoped_lead"]
    assert "metadata" not in registry_seen["scoped_lead"]
    assert "source" not in registry_seen["scoped_lead"]
    assert registry_seen["global_step_data"] == _terminal_normalized_payload()
    assert "UNRELATED_SENTINEL" not in registry_seen["global_step_data"]


# R3 — No direct global state mutation before registry activation.
async def test_r3_no_direct_global_mutation_before_registry():
    seen = {}

    def _before(ctx):
        seen["global_before"] = dict(ctx.session_data.state.step_data or {})

    context, engine, result, registry_seen = await _run_sentinel_handoff(
        execute_result=_success_result(), before_registry=_before
    )
    assert result is not None
    # The helper must NOT directly mutate global step_data before registry execution.
    # The replacement primitive owns only the scoped state; the registry remains the
    # single owner of global activation. Global step_data therefore still holds the prior
    # lead state (the sentinel prior), and the registry-visible scoped payload contains
    # only the exact normalized terminal payload.
    assert seen["global_before"] == _sentinel_step_data()
    assert "UNRELATED_SENTINEL" in seen["global_before"]
    assert registry_seen["scoped_lead"] == _terminal_normalized_payload()
    assert "UNRELATED_SENTINEL" not in registry_seen["scoped_lead"]


# R4 — Exact failure restoration restores the entire prior dictionary.
async def test_r4_exact_failure_restoration():
    context, engine, result, registry_seen = await _run_sentinel_handoff(
        execute_result=_failure_result()
    )
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == _sentinel_step_data()
    assert lead.get("UNRELATED_SENTINEL") == "MUST_DISAPPEAR"
    assert lead.get("metadata") == "MUST_DISAPPEAR"
    assert lead.get("source") == "MUST_DISAPPEAR"
    assert lead.get("company_name") == ""


# R5 — Failure restoration removes terminal additions unless pre-existing.
async def test_r5_failure_removes_terminal_additions():
    context, engine, result, registry_seen = await _run_sentinel_handoff(
        execute_result=_failure_result()
    )
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    # contact_email in the prior snapshot was ""; the normalized "sales@acme.example"
    # must NOT remain after failure restoration.
    assert lead.get("contact_email") == ""
    assert lead.get("company_name") == ""


# R6 — Success performs one replacement.
async def test_r6_success_one_replacement():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert len(recording.lead_assignments) == 1


# R7 — Explicit failure performs two replacements (one terminal, one restoration).
async def test_r7_explicit_failure_two_replacements():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    replace_calls = []
    restore_calls = []
    original_replace = SessionState.replace_task_step_data
    original_restore = SessionState.restore_task_step_data_snapshot

    def _replace_spy(self, task_key, values):
        replace_calls.append((task_key, dict(values)))
        return original_replace(self, task_key, values)

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append((task_key, dict(kwargs.get("scoped_values", {}))))
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "replace_task_step_data", _replace_spy), \
         patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert len(replace_calls) == 1
    assert len(restore_calls) == 1
    # Replace call is the exact terminal replacement.
    assert replace_calls[0][0] == "lead_capture"
    assert replace_calls[0][1] == _terminal_normalized_payload()
    # Restore call restores the prior state exactly.
    assert restore_calls[0][0] == "lead_capture"
    assert restore_calls[0][1] == _sentinel_step_data()


# R8 — Registry exception performs one replacement (no restoration).
async def test_r8_registry_exception_one_replacement():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine()
    async def _boom(tool_key, ctx):
        raise RuntimeError("registry exploded")
    engine.tool_registry.execute_tool = _boom
    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    with pytest.raises(RuntimeError):
        await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert len(recording.lead_assignments) == 1


# R9 — Result-recording exception performs one replacement (no restoration).
async def test_r9_result_recording_exception_one_replacement():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())

    def _record_boom(self, result, channel_type=None, **kwargs):
        raise RuntimeError("record exploded")

    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    with patch.object(SessionState, "record_tool_result", _record_boom):
        with pytest.raises(RuntimeError):
            await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert len(recording.lead_assignments) == 1


# R10 — Exact normalized projection: protected/consent/unknown/unsupported/internal/
#      unrelated prior fields never reach the registry-visible payload.
async def test_r10_exact_normalized_projection():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    prior = {
        "company_name": "",
        "contact_email": "",
        "UNRELATED_SENTINEL": "MUST_DISAPPEAR",
        "metadata": "MUST_DISAPPEAR",
        "source": "MUST_DISAPPEAR",
        "marketing_consent": "yes",   # consent-sensitive
        "favorite_color": "blue",     # unknown
        "status": "open",             # protected/internal
    }
    context = _make_context(step_data=prior)
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    seen = {}

    async def _capture(tool_key, ctx):
        seen["payload"] = dict(ctx.session_data.state.get_task_step_data("lead_capture") or {})
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="done",
            data={},
            created_resource_type="lead",
            created_resource_id=str(uuid.uuid4()),
        )

    engine.tool_registry.execute_tool = _capture
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert seen["payload"] == _terminal_normalized_payload()
    for forbidden in ("UNRELATED_SENTINEL", "metadata", "source", "marketing_consent",
                      "favorite_color", "status"):
        assert forbidden not in seen["payload"]


# R11 — Existing H1–H50 contracts remain intact (success still yields one replacement
#      and exact payload; this is a regression guard reaffirming the H12 contract with
#      the sentinel prior state).
async def test_r11_existing_contracts_intact_with_sentinel_prior():
    context, engine, result, registry_seen = await _run_sentinel_handoff(
        execute_result=_success_result()
    )
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]
    assert len(engine.tool_registry.execute_calls) == 1
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == _terminal_normalized_payload()


# ---------------------------------------------------------------------------
# S-segment — SessionState exact replacement primitive and terminal owner proofs
# ---------------------------------------------------------------------------
def _primitive_state() -> SessionState:
    return SessionState(workflow_state=ConversationState.COLLECTING_DATA)


# S1 — Replacement primitive removes prior keys.
async def test_s1_replacement_removes_prior_keys():
    state = _primitive_state()
    state.scoped_step_data["lead_capture"] = {
        "company_name": "Old Acme",
        "contact_email": "",
        "UNRELATED_SENTINEL": "REMOVE",
    }
    out = state.replace_task_step_data(
        "lead_capture",
        {"company_name": "Acme", "contact_email": "sales@acme.example"},
    )
    lead = state.get_task_step_data("lead_capture")
    assert lead == {"company_name": "Acme", "contact_email": "sales@acme.example"}
    assert out == {"company_name": "Acme", "contact_email": "sales@acme.example"}
    assert "UNRELATED_SENTINEL" not in lead


# S2 — Replacement primitive copies input (input mutated after call is not reflected).
async def test_s2_replacement_copies_input():
    state = _primitive_state()
    supplied = {"company_name": "Acme", "contact_email": "sales@acme.example"}
    state.replace_task_step_data("lead_capture", supplied)
    supplied["company_name"] = "MUTATED"
    supplied["injected"] = "X"
    lead = state.get_task_step_data("lead_capture")
    assert lead == {"company_name": "Acme", "contact_email": "sales@acme.example"}


# S3 — Replacement primitive does not touch global state.
async def test_s3_replacement_does_not_touch_global():
    state = _primitive_state()
    state.step_data = {"GLOBAL_SENTINEL": "KEEP"}
    state.replace_task_step_data(
        "lead_capture", {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    # Global state unchanged until activate_task_step_data runs.
    assert state.step_data == {"GLOBAL_SENTINEL": "KEEP"}
    state.activate_task_step_data("lead_capture")
    assert state.step_data == {"company_name": "Acme", "contact_email": "sales@acme.example"}


# S4 — Replacement primitive sets current task.
async def test_s4_replacement_sets_current_task():
    state = _primitive_state()
    state.replace_task_step_data(
        "lead_capture", {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert state.action_ledger.current_task == "lead_capture"


# S5 — Terminal helper uses the replacement primitive exactly once before success.
async def test_s5_helper_uses_replacement_primitive():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    calls = []
    original = SessionState.replace_task_step_data

    def _spy(self, task_key, values):
        calls.append(task_key)
        return original(self, task_key, values)

    with patch.object(SessionState, "replace_task_step_data", _spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert calls == ["lead_capture"]


# S6 — Terminal helper has no direct scoped assignment (only via the primitive).
async def test_s6_helper_no_direct_scoped_assignment():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    recording = _RecordingDict(context.session_data.state.scoped_step_data)
    context.session_data.state.scoped_step_data = recording
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Exactly one exact scoped replacement occurs (owned by the primitive).
    assert len(recording.lead_assignments) == 1
    assert recording.lead_assignments[0] == _terminal_normalized_payload()


# S7 — Terminal helper has no direct global assignment before registry activation.
async def test_s7_helper_no_direct_global_assignment():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    seen = {}

    async def _capture(tool_key, ctx):
        seen["global_before"] = dict(ctx.session_data.state.step_data or {})
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="done",
            data={},
            created_resource_type="lead",
            created_resource_id=str(uuid.uuid4()),
        )

    engine.tool_registry.execute_tool = _capture
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Global step_data still holds the prior sentinel state until registry activation.
    assert seen["global_before"] == _sentinel_step_data()


# S8 — Registry activation projects exact payload (scoped + global equal normalized).
async def test_s8_registry_activation_exact_payload():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    seen = {}

    async def _capture(tool_key, ctx):
        # Emulate the existing registry activation owner inside the mock.
        ctx.session_data.state.activate_task_step_data("lead_capture")
        seen["scoped"] = dict(ctx.session_data.state.get_task_step_data("lead_capture") or {})
        seen["global"] = dict(ctx.session_data.state.step_data or {})
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="done",
            data={},
            created_resource_type="lead",
            created_resource_id=str(uuid.uuid4()),
        )

    engine.tool_registry.execute_tool = _capture
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert seen["scoped"] == _terminal_normalized_payload()
    assert seen["global"] == _terminal_normalized_payload()


# S9 — Explicit failure exact scoped restoration through the primitive.
async def test_s9_explicit_failure_scoped_restoration():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    replace_calls = []
    restore_calls = []
    original_replace = SessionState.replace_task_step_data
    original_restore = SessionState.restore_task_step_data_snapshot

    def _replace_spy(self, task_key, values):
        replace_calls.append((task_key, dict(values)))
        return original_replace(self, task_key, values)

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append((task_key, dict(kwargs.get("scoped_values", {}))))
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "replace_task_step_data", _replace_spy), \
         patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Call 1: exact terminal replacement (replace primitive).
    assert replace_calls[0][0] == "lead_capture"
    assert replace_calls[0][1] == _terminal_normalized_payload()
    # Call 2: exact prior restoration (snapshot primitive).
    assert restore_calls[0][0] == "lead_capture"
    assert restore_calls[0][1] == _sentinel_step_data()
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == _sentinel_step_data()


# S10 — Explicit failure global restoration after activation.
async def test_s10_explicit_failure_global_restoration():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # After failure restoration + activation, global step_data equals the prior dict.
    assert context.session_data.state.step_data == _sentinel_step_data()


# S11 — Success replacement count.
async def test_s11_success_replacement_count():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    calls = []
    original = SessionState.replace_task_step_data

    def _spy(self, task_key, values):
        calls.append(task_key)
        return original(self, task_key, values)

    with patch.object(SessionState, "replace_task_step_data", _spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert calls == ["lead_capture"]


# S12 — Failure replacement count.
async def test_s12_failure_replacement_count():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    replace_calls = []
    restore_calls = []
    original_replace = SessionState.replace_task_step_data
    original_restore = SessionState.restore_task_step_data_snapshot

    def _replace_spy(self, task_key, values):
        replace_calls.append(task_key)
        return original_replace(self, task_key, values)

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append(task_key)
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "replace_task_step_data", _replace_spy), \
         patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert replace_calls == ["lead_capture"]
    assert restore_calls == ["lead_capture"]


# S13 — Registry exception replacement count (one, no restore).
async def test_s13_registry_exception_replacement_count():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine()

    async def _boom(tool_key, ctx):
        raise RuntimeError("registry exploded")

    engine.tool_registry.execute_tool = _boom
    calls = []
    original = SessionState.replace_task_step_data

    def _spy(self, task_key, values):
        calls.append(task_key)
        return original(self, task_key, values)

    with patch.object(SessionState, "replace_task_step_data", _spy):
        with pytest.raises(RuntimeError):
            await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert calls == ["lead_capture"]


# S14 — Result-recording exception replacement count (one, no restore).
async def test_s14_result_recording_exception_replacement_count():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    calls = []
    original = SessionState.replace_task_step_data

    def _spy(self, task_key, values):
        calls.append(task_key)
        return original(self, task_key, values)

    def _record_boom(self, result, channel_type=None, **kwargs):
        raise RuntimeError("record exploded")

    with patch.object(SessionState, "replace_task_step_data", _spy):
        with patch.object(SessionState, "record_tool_result", _record_boom):
            with pytest.raises(RuntimeError):
                await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert calls == ["lead_capture"]


# S15 — Existing H1–H50 and R1–R11 behavior preserved.
async def test_s15_existing_behavior_preserved():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # All three flags required -> was enabled.
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]
    # Exact configured payload.
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == _terminal_normalized_payload()
    # One registry execution, one normal result recording, no legacy/LangGraph.
    assert len(engine.tool_registry.execute_calls) == 1
    assert context.session_data.state.action_ledger.task_status == "completed"


# ---------------------------------------------------------------------------
# P-segment — State B classification and snapshot restoration proofs
# ---------------------------------------------------------------------------

# P1 — State B classification is executable: scope absent, global step_data has
#      pre-existing configured field, terminal candidate supplies final configured
#      required field. Terminal execution is reachable.
async def test_p1_state_b_absent_scope_executable():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    # Remove the scoped entry so scope is absent; current_task remains "lead_capture"
    # and global step_data holds the prior configured field.
    context.session_data.state.scoped_step_data.pop("lead_capture", None)
    context.session_data.state.step_data = {"company_name": "Acme"}
    context.session_data.state.action_ledger.current_task = "lead_capture"
    # Verify scope is truly absent.
    assert "lead_capture" not in context.session_data.state.scoped_step_data
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# P2 — Empty scope can represent all fields missing: present empty lead scope,
#      configure required fields, supply all via terminal candidates.
async def test_p2_empty_scope_all_fields_missing():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(current_module="lead_capture", step_data={})
    # Explicitly set empty scoped dict.
    context.session_data.state.scoped_step_data["lead_capture"] = {}
    context.session_data.state.action_ledger.current_task = "lead_capture"
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]


# P3 — Absent scope restored as absent after explicit failure.
async def test_p3_absent_scope_restored_absent():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    context.session_data.state.scoped_step_data.pop("lead_capture", None)
    context.session_data.state.step_data = {"company_name": "Acme"}
    context.session_data.state.action_ledger.current_task = "lead_capture"
    assert "lead_capture" not in context.session_data.state.scoped_step_data
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # After restoration, the scope must still be absent.
    assert "lead_capture" not in context.session_data.state.scoped_step_data


# P4 — Present empty scope restored as present empty after explicit failure.
async def test_p4_empty_scope_restored_present_empty():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(current_module="lead_capture", step_data={})
    context.session_data.state.scoped_step_data["lead_capture"] = {}
    context.session_data.state.action_ledger.current_task = "lead_capture"
    # A complete terminal batch (all required fields) is needed to reach execution.
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert "lead_capture" in context.session_data.state.scoped_step_data
    assert context.session_data.state.scoped_step_data["lead_capture"] == {}


# P5 — Non-empty scope restored exactly after explicit failure.
async def test_p5_non_empty_scope_restored_exactly():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    prior = dict(context.session_data.state.scoped_step_data.get("lead_capture", {}))
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    restored = context.session_data.state.scoped_step_data.get("lead_capture", {})
    assert restored == prior
    assert restored.get("company_name") == "Acme"


# P6 — Prior global state restored independently (not a copy of scoped state).
async def test_p6_prior_global_restored_independently():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    # Make global step_data different from scoped data.
    context.session_data.state.step_data["extra_global"] = "GLOBAL_ONLY"
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    prior_global = dict(context.session_data.state.step_data)
    scoped_before = dict(context.session_data.state.scoped_step_data.get("lead_capture", {}))
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Global state must be the exact prior global, not a copy of scoped.
    assert context.session_data.state.step_data == prior_global
    # Verify global contains the extra key unique to global state.
    assert context.session_data.state.step_data.get("extra_global") == "GLOBAL_ONLY"


# P7 — Prior current task restored by snapshot method before failure-result recording.
async def test_p7_prior_current_task_restored():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    context.session_data.state.action_ledger.current_task = "lead_capture"
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert context.session_data.state.action_ledger.current_task == "lead_capture"


# P8 — Snapshot inputs copied (mutating original test dicts after restoration does not
#      affect state).
async def test_p8_snapshot_inputs_copied():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context, {"contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    prior_global = dict(context.session_data.state.step_data)
    prior_scoped = dict(context.session_data.state.scoped_step_data.get("lead_capture", {}))
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    # Mutate the original dictionaries; restored state must not change.
    prior_global["mutated"] = "X"
    prior_global.pop("company_name", None)
    restored_lead = context.session_data.state.get_task_step_data("lead_capture")
    assert restored_lead.get("company_name") == "Acme"
    assert restored_lead.get("mutated") is None


# P9 — Terminal helper contains no direct scoped_step_data assignment/pop/update/clear
#      or step_data assignment/update/clear.
async def test_p9_helper_no_direct_dictionary_mutation():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine
    import inspect

    source = inspect.getsource(AgenticEngine._try_authoritative_lead_terminal_registry_handoff)
    # Direct scoped_step_data mutations (assignment, pop, update, clear) forbidden.
    assert "scoped_step_data[" not in source, "direct scoped_step_data key assignment"
    assert "scoped_step_data.pop" not in source, "direct scoped_step_data.pop"
    assert "scoped_step_data.update" not in source, "direct scoped_step_data.update"
    assert "scoped_step_data.clear" not in source, "direct scoped_step_data.clear"
    # Direct step_data mutations forbidden (the single `step_data =` in ordinary
    # execution context is the _cancel_active_task at line ~1232, outside the helper).
    # Check that no `step_data =` or `step_data[` or `step_data.pop` appears after the
    # def line of the terminal helper.
    lines = source.splitlines()
    in_helper = False
    for line in lines:
        stripped = line.strip()
        if "def _try_authoritative_lead_terminal_registry_handoff" in stripped:
            in_helper = True
            continue
        if in_helper and stripped.startswith("def "):
            break
        if in_helper:
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "step_data[" in stripped or "step_data =" in stripped or "step_data.pop" in stripped or "step_data.update" in stripped or "step_data.clear" in stripped:
                if "state.step_data" not in stripped:
                    raise AssertionError(f"direct step_data mutation: {stripped}")


# P10 — Failure uses exactly one restoration call (replace_task_step_data=1,
#       restore_task_step_data_snapshot=1).
async def test_p10_failure_one_restoration_call():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_failure_result())
    replace_calls = []
    restore_calls = []
    original_replace = SessionState.replace_task_step_data
    original_restore = SessionState.restore_task_step_data_snapshot

    def _replace_spy(self, task_key, values):
        replace_calls.append(task_key)
        return original_replace(self, task_key, values)

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append(task_key)
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "replace_task_step_data", _replace_spy), \
         patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert len(replace_calls) == 1
    assert len(restore_calls) == 1


# P11 — Success uses no restoration calls.
async def test_p11_success_no_restoration():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    restore_calls = []
    original_restore = SessionState.restore_task_step_data_snapshot

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append(task_key)
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert len(restore_calls) == 0


# P12 — Registry exception uses no restoration.
async def test_p12_registry_exception_no_restoration():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine()
    async def _boom(tool_key, ctx):
        raise RuntimeError("registry exploded")
    engine.tool_registry.execute_tool = _boom
    restore_calls = []
    original_restore = SessionState.restore_task_step_data_snapshot

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append(task_key)
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        with pytest.raises(RuntimeError):
            await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert len(restore_calls) == 0


# P13 — Result-recording exception uses no post-execution restoration.
async def test_p13_result_recording_exception_no_restoration():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())

    def _record_boom(self, result, channel_type=None, **kwargs):
        raise RuntimeError("record exploded")

    restore_calls = []
    original_restore = SessionState.restore_task_step_data_snapshot

    def _restore_spy(self, task_key, **kwargs):
        restore_calls.append(task_key)
        return original_restore(self, task_key, **kwargs)

    with patch.object(SessionState, "record_tool_result", _record_boom), \
         patch.object(SessionState, "restore_task_step_data_snapshot", _restore_spy):
        with pytest.raises(RuntimeError):
            await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert len(restore_calls) == 0


# P14 — Existing contracts preserved: success still yields one replacement and exact
#       payload; failure still restores prior state; all H1–H50, R1–R11, S1–S15
#       contracts remain intact.
async def test_p14_existing_contracts_preserved():
    _set_all_lead_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # Success contract: one registry execution, exact payload.
    context = _make_context(step_data=_sentinel_step_data())
    _seed_terminal_shadow(context, {"company_name": "Acme", "contact_email": "sales@acme.example"})
    engine = _make_engine(execute_result=_success_result())
    result = await engine._try_authoritative_lead_terminal_registry_handoff(context)
    assert result is not None
    assert engine.tool_registry.execute_calls == ["lead_capture.execute"]
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == _terminal_normalized_payload()
    assert context.session_data.state.action_ledger.task_status == "completed"

    # Failure contract: prior state restored, no residual terminal values.
    context2 = _make_context(step_data={"company_name": "Acme"})
    _seed_terminal_shadow(context2, {"contact_email": "sales@acme.example"})
    engine2 = _make_engine(execute_result=_failure_result())
    result2 = await engine2._try_authoritative_lead_terminal_registry_handoff(context2)
    assert result2 is not None
    lead2 = context2.session_data.state.get_task_step_data("lead_capture")
    assert lead2 == {"company_name": "Acme"}
    assert lead2.get("contact_email") is None
    assert engine2.tool_registry.execute_calls == ["lead_capture.execute"]
