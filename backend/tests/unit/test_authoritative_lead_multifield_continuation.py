"""Iteration 7C authoritative non-terminal lead multi-field continuation tests.

Backend-validated complete-turn candidates for safe tenant-configured lead fields
are applied atomically when at least one applicable required field remains missing
afterward. Terminal batches (which would complete every required field) are deferred
to the existing lead runtime with zero authoritative writes. The Iteration 2 dynamic
configured-turn contract (``extract_global_workflow_turn``) is mocked; no live
provider is contacted.

These tests assert:
  * the apply flag defaults false and requires both lead flags;
  * a pre-existing active lead is required (no idle/booking/order/etc eligibility);
  * protected / consent-sensitive / unsupported-type keys never persist;
  * arbitrary exact tenant keys persist under their exact configured key;
  * application-time revalidation occurs independently of the 7B shadow validation;
  * a successful non-terminal batch uses exactly one atomic lead-scoped update;
  * a handled batch skips legacy lead extraction and the safe-update path;
  * a terminal batch is deferred (handled=False, zero writes);
  * an already-satisfied replay writes zero and is still handled;
  * uncertain writes propagate to the outer failure owner;
  * no tool, persistence, or lead.created event is added;
  * no saved-success customer text is generated;
  * telemetry is key-only and privacy-safe and failure reuses the shadow event.
"""
from __future__ import annotations

import copy
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
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.ops.logger import logger
from backend.modules.lead_capture_module import (
    _lead_required_missing_after_preview,
    lead_capture_module,
)

pytestmark = pytest.mark.anyio


PRIVATE_NAME_SENTINEL = "PRIVATE_NAME_SENTINEL"
PRIVATE_EMAIL_SENTINEL = "PRIVATE_EMAIL_SENTINEL"
PRIVATE_PHONE_SENTINEL = "PRIVATE_PHONE_SENTINEL"
PRIVATE_MESSAGE_SENTINEL = "PRIVATE_MESSAGE_SENTINEL"
PRIVATE_NOTES_SENTINEL = "PRIVATE_NOTES_SENTINEL"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _lead_fields() -> list[dict]:
    return [
        {"key": "name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
        {"key": "email", "label": "Email", "type": "email", "required": False},
        {"key": "interest", "label": "Interested In", "type": "string", "required": True},
        {"key": "message", "label": "Message", "type": "textarea", "required": False},
        {"key": "preferred_contact_time", "label": "Preferred Contact Time", "type": "string", "required": False},
        {"key": "company_name", "label": "Company", "type": "string", "required": False},
        {"key": "industry", "label": "Industry", "type": "string", "required": False},
        {"key": "current_software", "label": "Current Software", "type": "string", "required": False},
        {"key": "notes", "label": "Notes", "type": "textarea", "required": False},
    ]


def _make_context(
    *,
    fields=None,
    text="I'm Arjun from Acme. Email me at arjun@acme.com about an enterprise demo next week.",
    step_data=None,
    source_type="awa_chat",
    channel_type="awa_chat",
    current_module="lead_capture",
    pending_action=None,
    prompt_focus=None,
    enabled=True,
):
    import uuid

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
        # An already-active lead continuation needs a non-empty scoped state.
        state.update_task_step_data("lead_capture", {"interest": "enterprise demo"})
    if pending_action is not None:
        state.action_ledger.pending_action = pending_action
    if prompt_focus is not None:
        state.set_prompt_focus(
            workflow=prompt_focus[0],
            requested_fields=list(prompt_focus[1]),
            prompt_kind="missing_fields",
        )
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


def _rejected(reason: str = "boom", gateway_unavailable: bool = False) -> DynamicTurnValidationResult:
    return DynamicTurnValidationResult(
        accepted_candidates=[],
        rejected=True,
        rejection_reason=reason,
        gateway_unavailable=gateway_unavailable,
    )


def _seed_shadow(context: ExecutionContext, candidates: dict[str, Any]) -> None:
    """Seed a valid Iteration 7B lead shadow snapshot with a validated patch."""
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
        "pre_turn_safe_field_values": dict(context.session_data.state.get_task_step_data("lead_capture") or {}),
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


def _set_apply_flags(enabled: bool) -> None:
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = enabled
    settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = enabled


def _install_update_spy(context: ExecutionContext) -> list:
    """Patch SessionState.update_task_step_data to record call modules."""
    from backend.core.context import SessionState

    record: list = []
    real = SessionState.update_task_step_data

    def _spy(self, module, data):
        record.append(module)
        return real(self, module, data)

    patcher = patch.object(SessionState, "update_task_step_data", _spy)
    patcher.start()
    return record


def _make_engine():
    """Build an AgenticEngine instance with a mocked tool registry."""
    from backend.logic.engine.core import AgenticEngine
    from backend.tools.registry import ToolExecutionResult

    engine = AgenticEngine.__new__(AgenticEngine)
    registry = MagicMock()
    tool_def = MagicMock()
    tool_def.module = lead_capture_module
    registry.get_tool.return_value = tool_def
    registry.get_tool_key_for_module.return_value = "lead_capture.execute"
    registry.execute_calls = []

    async def _fake_execute(tool_key, ctx):
        registry.execute_calls.append(tool_key)
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="Prepared.",
            denied_reason=None,
            data={},
            validation_errors=None,
        )

    registry.execute_tool = _fake_execute
    engine.tool_registry = registry
    return engine


@pytest.fixture(autouse=True)
def _reset_lead_flags():
    """Restore the shared lead settings flags to their defaults after each test."""
    yield
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = False


# ---------------------------------------------------------------------------
# A1 — Apply flag defaults false
# ---------------------------------------------------------------------------
async def test_a1_apply_flag_defaults_false():
    from backend.core.config import Settings

    fresh = Settings()
    assert getattr(fresh, "ENABLE_LEAD_CONFIGURED_TURN_APPLY", None) is False


# ---------------------------------------------------------------------------
# A2 — Both flags required
# ---------------------------------------------------------------------------
async def test_a2_both_flags_required():
    from backend.logic.engine.core import _authoritative_lead_apply_enabled

    for shadow_on, apply_on in [(False, False), (True, False), (False, True)]:
        settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = shadow_on
        settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = apply_on
        assert _authoritative_lead_apply_enabled() is False


# ---------------------------------------------------------------------------
# A3 — Pre-existing active lead required
# ---------------------------------------------------------------------------
async def test_a3_active_lead_required():
    _set_apply_flags(True)
    from backend.logic.engine.core import _authoritative_lead_apply_eligible

    for module in ["booking", "order", "payments", "support", "reminders", "reviews", "faq", "human_handoff", None]:
        context = _make_context(current_module=module if module else "general")
        if module == "booking":
            context.session_data.state.update_task_step_data("booking", {"service_name": "X"})
            context.session_data.state.action_ledger.current_task = "booking"
        assert _authoritative_lead_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A4 — Module / scoped state required
# ---------------------------------------------------------------------------
async def test_a4_module_disabled_or_no_scoped_state():
    _set_apply_flags(True)
    from backend.logic.engine.core import _authoritative_lead_apply_eligible

    context = _make_context(enabled=False)
    assert _authoritative_lead_apply_eligible(context) is False

    context2 = _make_context()
    context2.session_data.state.scoped_step_data.pop("lead_capture", None)
    context2.session_data.state.step_data = {}
    context2.session_data.state.action_ledger.current_task = None
    context2.session_data.current_module = None
    assert _authoritative_lead_apply_eligible(context2) is False


# ---------------------------------------------------------------------------
# A5 — Pending action / internal event blocks
# ---------------------------------------------------------------------------
async def test_a5_pending_or_internal_blocks():
    _set_apply_flags(True)
    from backend.logic.engine.core import _authoritative_lead_apply_eligible

    pa = MagicMock()
    pa.tool_key = "lead_capture.execute"
    context = _make_context(pending_action=pa)
    assert _authoritative_lead_apply_eligible(context) is False

    context2 = _make_context(source_type="internal_event")
    assert _authoritative_lead_apply_eligible(context2) is False

    context3 = _make_context(text="enter the otp 123456")
    assert _authoritative_lead_apply_eligible(context3) is False


# ---------------------------------------------------------------------------
# A6 — Safe multi-field non-terminal batch applies once
# ---------------------------------------------------------------------------
async def test_a6_safe_multi_field_applies_once():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "enterprise demo"})
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    record = _install_update_spy(context)
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor, \
         patch.object(AgenticEngine, "_apply_safe_lead_field_updates", new=MagicMock(side_effect=AssertionError("safe-update ran"))):
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    assert result.prompt_focus_workflow == "lead_capture"
    assert result.prompt_focus_fields == ["phone"]
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("name") == "Arjun"
    assert lead.get("email") == "arjun@acme.com"
    assert lead.get("phone") != "arjun@acme.com"
    assert lead.get("interest") == "enterprise demo"
    # Exactly one atomic lead-scoped update.
    assert record.count("lead_capture") == 1
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A7 — Arbitrary safe tenant keys persist
# ---------------------------------------------------------------------------
async def test_a7_arbitrary_safe_keys_persist():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": True},
        {"key": "current_software", "type": "string", "required": False},
        {"key": "notes", "type": "textarea", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {
        "company_name": "Acme",
        "current_software": "SAP",
        "notes": "Needs onboarding",
    })
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    # Exact configured keys persisted without canonical renaming.
    assert lead.get("company_name") == "Acme"
    assert lead.get("industry") is None
    assert lead.get("current_software") == "SAP"
    assert lead.get("notes") == "Needs onboarding"
    assert "CompanyName" not in lead
    assert record.count("lead_capture") == 1


async def test_a7b_full_required_terminal_defers():
    """All required tenant fields supplied -> terminal batch, zero authoritative writes."""
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": True},
        {"key": "current_software", "type": "string", "required": True},
        {"key": "notes", "type": "textarea", "required": True},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {
        "company_name": "Acme",
        "industry": "Retail",
        "current_software": "SAP",
        "notes": "Needs onboarding",
    })
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # Terminal batch: deferred, no authoritative write.
    assert result is None
    assert record.count("lead_capture") == 0
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == {}


# ---------------------------------------------------------------------------
# A8 — Textarea persists under exact key
# ---------------------------------------------------------------------------
async def test_a8_textarea_persists_exact_key():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    fields = [{"key": "notes", "type": "textarea", "required": True},
              {"key": "company_name", "type": "string", "required": True}]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {"notes": PRIVATE_NOTES_SENTINEL})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("notes") == PRIVATE_NOTES_SENTINEL
    assert lead.get("company_name") is None


async def test_a8b_textarea_only_required_terminal_defers():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    fields = [{"key": "notes", "type": "textarea", "required": True}]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {"notes": PRIVATE_NOTES_SENTINEL})
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is None
    assert record.count("lead_capture") == 0


# ---------------------------------------------------------------------------
# A9 — Protected / internal keys never persist
# ---------------------------------------------------------------------------
async def test_a9_protected_keys_never_persist():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    protected = {
        "lead_id": "x", "tenant_id": "x", "conversation_id": "x",
        "customer_id": "x", "status": "x", "assigned_to": "x",
        "source": "x", "metadata": "x",
    }
    res = lead_capture_module.apply_external_configured_candidates(context, protected)
    assert res.handled is False
    assert res.accepted_field_keys == ()
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# A10 — Consent-sensitive keys never persist
# ---------------------------------------------------------------------------
async def test_a10_consent_keys_never_persist():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    consent = {"consent": "yes", "marketing_consent": "yes", "privacy_acceptance": "yes", "contact_consent": "yes"}
    res = lead_capture_module.apply_external_configured_candidates(context, consent)
    assert res.handled is False
    assert res.accepted_field_keys == ()
    # No telemetry key leakage.
    records = []

    def _spy(event, extra=None, **kwargs):
        if event in ("lead_configured_turn_shadow_failed", "lead_configured_turn_applied"):
            records.append((event, copy.deepcopy(extra["extra_data"])))
        return None

    with patch.object(logger, "warning", side_effect=_spy), patch.object(logger, "info", side_effect=_spy):
        res2 = lead_capture_module.apply_external_configured_candidates(context, consent)
    assert res2.handled is False
    blob = str(records)
    assert "consent" not in blob


# ---------------------------------------------------------------------------
# A11 — Unsupported types never persist
# ---------------------------------------------------------------------------
async def test_a11_unsupported_types_never_persist():
    _set_apply_flags(True)
    fields = [
        {"key": "project_budget", "type": "number", "required": False},
        {"key": "preferred_demo_date", "type": "date", "required": False},
        {"key": "preferred_contact_window", "type": "time", "required": False},
        {"key": "marketing_opt_in", "type": "boolean", "required": False},
        {"key": "tier", "type": "select", "required": False},
        {"key": "company_name", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    validation = lead_capture_module.validate_external_configured_candidates(context, {
        "project_budget": 5, "preferred_demo_date": "2026-01-01",
        "preferred_contact_window": "10:00", "marketing_opt_in": True,
        "tier": "gold", "company_name": "Acme",
    })
    assert set(validation.accepted_field_keys) == {"company_name"}
    assert "project_budget" in validation.rejected_field_keys
    assert "preferred_demo_date" in validation.rejected_field_keys
    assert "preferred_contact_window" in validation.rejected_field_keys
    assert "marketing_opt_in" in validation.rejected_field_keys
    assert "tier" in validation.rejected_field_keys
    # Applying a completed (no required remaining) batch is deferred with zero writes.
    res = lead_capture_module.apply_external_configured_candidates(context, {
        "company_name": "Acme",
    })
    assert res.handled is False
    assert res.reason_code == "terminal_batch_deferred"
    assert res.update_performed is False


# ---------------------------------------------------------------------------
# A12 — Unknown key falls back
# ---------------------------------------------------------------------------
async def test_a12_unknown_key_falls_back():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # A valid sibling applies; the unknown key is rejected at revalidation.
    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun", "not_a_lead_field": "value"})
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # The valid sibling is handled; the unknown key never persists.
    assert result is not None
    assert context.session_data.state.get_task_step_data("lead_capture").get("name") == "Arjun"
    assert "not_a_lead_field" not in context.session_data.state.get_task_step_data("lead_capture")
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A13 — Revalidation at application time
# ---------------------------------------------------------------------------
async def test_a13_revalidation_at_application_time():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo", "name": "Arjun"})
    # Field accepted at shadow validation time but already populated before apply.
    res = lead_capture_module.apply_external_configured_candidates(context, {"name": "Changed"})
    # No overwrite: the existing value is preserved and the candidate is rejected.
    assert res.applied is False
    assert res.accepted_field_keys == ()
    assert "name" in res.rejected_field_keys
    assert context.session_data.state.get_task_step_data("lead_capture").get("name") == "Arjun"


# ---------------------------------------------------------------------------
# A14 — Existing values not overwritten
# ---------------------------------------------------------------------------
async def test_a14_existing_values_not_overwritten():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo", "email": "existing@example.com"})
    res = lead_capture_module.apply_external_configured_candidates(context, {
        "email": "different@example.com", "company_name": "Acme",
    })
    assert res.handled is True
    assert set(res.accepted_field_keys) == {"company_name"}
    assert "email" in res.rejected_field_keys
    assert context.session_data.state.get_task_step_data("lead_capture").get("email") == "existing@example.com"
    assert context.session_data.state.get_task_step_data("lead_capture").get("company_name") == "Acme"


# ---------------------------------------------------------------------------
# A15 — One atomic update
# ---------------------------------------------------------------------------
async def test_a15_one_atomic_update():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # interest left missing so the batch is non-terminal (name/phone apply, interest
    # remains required and missing after the patch).
    context = _make_context(step_data={})
    _seed_shadow(context, {
        "name": "Arjun", "company_name": "Acme", "email": "arjun@acme.com",
        "phone": "+919999999999", "preferred_contact_time": "next week",
    })
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    # Exactly one lead-scoped update containing every accepted exact key.
    assert record.count("lead_capture") == 1
    lead = context.session_data.state.get_task_step_data("lead_capture")
    for key in ("name", "company_name", "email", "phone", "preferred_contact_time"):
        assert key in lead


# ---------------------------------------------------------------------------
# A16 — Successful handled application skips legacy extraction
# ---------------------------------------------------------------------------
async def test_a16_successful_skips_legacy_extraction():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor, \
         patch.object(AgenticEngine, "_apply_safe_lead_field_updates", new=MagicMock(side_effect=AssertionError("safe-update ran"))):
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A17 — Same message cannot populate next string field
# ---------------------------------------------------------------------------
async def test_a17_same_message_not_next_string_field():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    text = "I'm Arjun from Acme. Email me at arjun@acme.com."
    context = _make_context(step_data={"interest": "demo"}, text=text)
    _seed_shadow(context, {"name": "Arjun", "company_name": "Acme", "email": "arjun@acme.com"})
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("phone") != text
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A18 — Partial accepted patch owns the turn
# ---------------------------------------------------------------------------
async def test_a18_partial_accepted_patch_owns_turn():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun", "email": "not-an-email", "company_name": "Acme"})
    engine = _make_engine()
    record = _install_update_spy(context)
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("name") == "Arjun"
    assert lead.get("company_name") == "Acme"
    assert lead.get("email") != "not-an-email"
    assert record.count("lead_capture") == 1
    extractor.assert_not_called()
    # Clarification asks for the next missing field (email) per configured order.
    assert result.prompt_focus_workflow == "lead_capture"


# ---------------------------------------------------------------------------
# A19 — Already-satisfied replay writes zero
# ---------------------------------------------------------------------------
async def test_a19_already_satisfied_replay_writes_zero():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={
        "interest": "demo", "name": "Arjun", "email": "arjun@acme.com", "phone": "+919999999999",
    })
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    record = _install_update_spy(context)
    result1 = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result1 is not None
    assert record.count("lead_capture") == 0

    typed = lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun", "email": "arjun@acme.com"})
    assert typed.handled is True
    assert typed.applied is False
    assert typed.update_performed is False
    assert typed.reason_code == "already_satisfied"

    record.clear()
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
        result2 = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result2 is not None
    assert record.count("lead_capture") == 0
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A20 — Already-satisfied result handled
# ---------------------------------------------------------------------------
async def test_a20_already_satisfied_result_handled():
    _set_apply_flags(True)
    context = _make_context(step_data={
        "interest": "demo", "name": "Arjun", "email": "arjun@acme.com", "phone": "+919999999999",
    })
    res = lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun", "email": "arjun@acme.com"})
    assert res.handled is True
    assert res.applied is False
    assert res.update_performed is False
    assert res.reason_code == "already_satisfied"


# ---------------------------------------------------------------------------
# A21 — Terminal batch deferred
# ---------------------------------------------------------------------------
async def test_a21_terminal_batch_deferred():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    # required fields are name, phone, interest; phone + interest already present so
    # applying the remaining name + email completes every required field.
    context = _make_context(step_data={"interest": "enterprise demo", "phone": "+919999999999"})
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    record = _install_update_spy(context)
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # Outcome C: handled=False, zero authoritative writes, existing runtime owns turn.
    assert result is None
    assert record.count("lead_capture") == 0
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("name") != "Arjun"
    assert lead.get("email") != "arjun@acme.com"
    # typed result
    typed = lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun", "email": "arjun@acme.com"})
    assert typed.handled is False
    assert typed.applied is False
    assert typed.update_performed is False
    assert typed.reason_code == "terminal_batch_deferred"


# ---------------------------------------------------------------------------
# A22 — Terminal arbitrary-key batch deferred
# ---------------------------------------------------------------------------
async def test_a22_terminal_arbitrary_key_batch_deferred():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": True},
    ]
    # interest + all injected defaults (name/phone/email) present; company_name +
    # industry are the final required configured fields.
    context = _make_context(
        fields=fields,
        step_data={"interest": "demo", "name": "Arjun", "phone": "+919999999999", "email": "arjun@acme.com"},
    )
    _seed_shadow(context, {"company_name": "Acme", "industry": "Retail"})
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is None
    assert record.count("lead_capture") == 0
    typed = lead_capture_module.apply_external_configured_candidates(context, {"company_name": "Acme", "industry": "Retail"})
    assert typed.reason_code == "terminal_batch_deferred"


# ---------------------------------------------------------------------------
# A23 — Invalid result falls back
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scenario", ["gateway", "invalid_payload", "invalid_phone", "invalid_email", "empty"])
async def test_a23_invalid_result_falls_back(scenario):
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine
    from backend.logic.engine.lead_configured_turn_shadow import SHADOW_EVIDENCE_KEY

    context = _make_context(step_data={"interest": "demo"})
    if scenario == "gateway":
        context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = {
            "semantic_result": _rejected(gateway_unavailable=True),
            "validated_result": {"accepted_patch": {}, "accepted_field_keys": [], "rejected_field_keys": [], "reason_code": None},
        }
    elif scenario == "invalid_payload":
        context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = None
    elif scenario == "invalid_phone":
        _seed_shadow(context, {"phone": "abc"})
    elif scenario == "invalid_email":
        _seed_shadow(context, {"email": "not-an-email"})
    elif scenario == "empty":
        _seed_shadow(context, {})
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is None
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# A24 — String validation reused
# ---------------------------------------------------------------------------
async def test_a24_string_validation_reused():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    valid = lead_capture_module.apply_external_configured_candidates(context, {"company_name": "Acme"})
    assert valid.accepted_field_keys == ("company_name",)
    empty = lead_capture_module.apply_external_configured_candidates(context, {"company_name": ""})
    assert "company_name" in empty.rejected_field_keys
    one_char = lead_capture_module.apply_external_configured_candidates(context, {"company_name": "A"})
    assert "company_name" in one_char.rejected_field_keys
    nested = lead_capture_module.apply_external_configured_candidates(context, {"company_name": {"x": 1}})
    assert "company_name" in nested.rejected_field_keys
    alist = lead_capture_module.apply_external_configured_candidates(context, {"company_name": ["a", "b"]})
    assert "company_name" in alist.rejected_field_keys


# ---------------------------------------------------------------------------
# A25 — Phone validation reused
# ---------------------------------------------------------------------------
async def test_a25_phone_validation_reused():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    valid = lead_capture_module.apply_external_configured_candidates(context, {"phone": "+919999999999"})
    assert valid.accepted_field_keys == ("phone",)
    invalid = lead_capture_module.apply_external_configured_candidates(context, {"phone": "abc"})
    assert "phone" in invalid.rejected_field_keys


# ---------------------------------------------------------------------------
# A26 — Email validation reused
# ---------------------------------------------------------------------------
async def test_a26_email_validation_reused():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    valid = lead_capture_module.apply_external_configured_candidates(context, {"email": "arjun@acme.com"})
    assert valid.accepted_field_keys == ("email",)
    invalid = lead_capture_module.apply_external_configured_candidates(context, {"email": "not-an-email"})
    assert "email" in invalid.rejected_field_keys


# ---------------------------------------------------------------------------
# A27 — Name alias compatibility
# ---------------------------------------------------------------------------
async def test_a27_name_alias_compatibility():
    _set_apply_flags(True)
    # configured key is `name`; existing state has non-empty customer_name -> treated collected.
    fields = [{"key": "name", "type": "string", "required": True}]
    context = _make_context(fields=fields, step_data={"customer_name": PRIVATE_NAME_SENTINEL, "interest": "demo"})
    res = lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun"})
    # Already satisfied: no duplicate write.
    assert res.handled is True
    assert res.applied is False
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("customer_name") == PRIVATE_NAME_SENTINEL
    assert lead.get("name") != "Arjun"


async def test_a27_customer_name_alias_compatibility():
    _set_apply_flags(True)
    fields = [{"key": "customer_name", "type": "string", "required": True}]
    context = _make_context(fields=fields, step_data={"name": PRIVATE_NAME_SENTINEL, "interest": "demo"})
    res = lead_capture_module.apply_external_configured_candidates(context, {"customer_name": "Arjun"})
    assert res.handled is True
    assert res.applied is False
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("name") == PRIVATE_NAME_SENTINEL
    assert lead.get("customer_name") != "Arjun"


# ---------------------------------------------------------------------------
# A28 — Failure before write falls back
# ---------------------------------------------------------------------------
async def test_a28_failure_before_write_falls_back():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun"})
    engine = _make_engine()
    with patch.object(lead_capture_module, "apply_external_configured_candidates", side_effect=RuntimeError("boom")):
        with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
            result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is None
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# A29 — Uncertain-write protection
# ---------------------------------------------------------------------------
async def test_a29_uncertain_write_protection():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun"})

    def _mutate_then_raise(*args, **kwargs):
        lead = context.session_data.state.get_task_step_data("lead_capture")
        merged = dict(lead)
        merged.update({"name": "Arjun"})
        context.session_data.state.update_task_step_data("lead_capture", merged)
        raise RuntimeError("post-write failure")

    engine = _make_engine()
    with patch.object(lead_capture_module, "apply_external_configured_candidates", side_effect=_mutate_then_raise):
        with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor:
            with pytest.raises(RuntimeError):
                await engine._try_authoritative_lead_configured_turn_apply(context)
        extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A30 — Apply method causes no tool call
# ---------------------------------------------------------------------------
async def test_a30_apply_no_tool_call():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    with patch("backend.tools.registry.ToolRegistry.execute_tool", new=AsyncMock()) as tool:
        lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun", "company_name": "Acme"})
    tool.assert_not_called()


# ---------------------------------------------------------------------------
# A31 — Apply method causes no persistence
# ---------------------------------------------------------------------------
async def test_a31_apply_no_persistence():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(lead_capture_module, "execute", new=AsyncMock()) as module_exec, \
         patch("backend.domains.leads.service.lead_service.capture_lead", new=AsyncMock()) as svc, \
         patch("backend.domains.leads.repository.lead_repository.capture_lead", new=AsyncMock()) as repo:
        lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun", "company_name": "Acme"})
    module_exec.assert_not_called()
    svc.assert_not_called()
    repo.assert_not_called()


# ---------------------------------------------------------------------------
# A32 — No lead.created event
# ---------------------------------------------------------------------------
async def test_a32_no_lead_created_event():
    _set_apply_flags(True)
    context = _make_context(step_data={"interest": "demo"})
    records = []

    def _spy(event, extra=None, **kwargs):
        records.append(event)
        return None

    with patch.object(logger, "info", side_effect=_spy), patch.object(logger, "warning", side_effect=_spy):
        lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun", "company_name": "Acme"})
    assert "lead.created" not in records


# ---------------------------------------------------------------------------
# A33 — No saved-success response
# ---------------------------------------------------------------------------
async def test_a33_no_saved_success_response():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    text = result.response_text.lower()
    for forbidden in ("saved", "submitted", "created", "team will contact", "lead created"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# A34 — Application telemetry contains keys only
# ---------------------------------------------------------------------------
async def test_a34_telemetry_keys_only():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(
        step_data={"interest": PRIVATE_MESSAGE_SENTINEL},
        text=f"{PRIVATE_NAME_SENTINEL} from Acme, {PRIVATE_EMAIL_SENTINEL}",
    )
    _seed_shadow(context, {"name": PRIVATE_NAME_SENTINEL, "email": "arjun@acme.com"})
    engine = _make_engine()
    records = []

    def _spy(
        _ctx, *, config_version=None, channel=None, applied_field_keys=None,
        remaining_required_field_keys=None, focus_field_key_before=None,
        focus_field_key_after=None,
    ):
        records.append({
            "event": "lead_configured_turn_applied",
            "applied_field_keys": list(applied_field_keys),
            "remaining_required_field_keys": list(remaining_required_field_keys),
        })
        return None

    with patch("backend.logic.engine.core._emit_lead_applied_event", side_effect=_spy, autospec=True):
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    assert records
    blob = str(records)
    assert PRIVATE_NAME_SENTINEL not in blob
    assert PRIVATE_EMAIL_SENTINEL not in blob
    assert PRIVATE_MESSAGE_SENTINEL not in blob
    ed = records[0]
    assert ed["event"] == "lead_configured_turn_applied"
    assert set(ed["applied_field_keys"]) == {"name", "email"}


# ---------------------------------------------------------------------------
# A35 — Failure telemetry reuses shadow event
# ---------------------------------------------------------------------------
async def test_a35_failure_telemetry_reuses_shadow_event():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun"})
    engine = _make_engine()
    records = []

    def _spy(event, extra=None, **kwargs):
        records.append((event, copy.deepcopy(extra["extra_data"])))
        return None

    with patch.object(logger, "warning", side_effect=_spy):
        with patch.object(lead_capture_module, "apply_external_configured_candidates", side_effect=RuntimeError("boom")):
            await engine._try_authoritative_lead_configured_turn_apply(context)
    failed = [r for r in records if r[0] == "lead_configured_turn_shadow_failed"]
    assert failed
    assert failed[0][1]["event"] == "lead_configured_turn_shadow_failed"
    assert failed[0][1]["stage"] == "authoritative_lead_apply"
    assert "lead_configured_turn_apply_failed" not in [r[0] for r in records]


# ---------------------------------------------------------------------------
# A36 — Default-disabled behavior unchanged
# ---------------------------------------------------------------------------
async def test_a36_default_disabled_unchanged():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_LEAD_CONFIGURED_TURN_APPLY = False
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "demo"})
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # No authoritative handling when disabled.
    assert result is None
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# A37 — Fresh lead intent unchanged
# ---------------------------------------------------------------------------
async def test_a37_fresh_lead_intent_unchanged():
    _set_apply_flags(True)
    from backend.logic.engine.core import _authoritative_lead_apply_eligible

    context = _make_context(current_module="general", text="Please contact me about an enterprise demo.")
    assert _authoritative_lead_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A38 — Booking and order behavior unchanged
# ---------------------------------------------------------------------------
async def test_a38_booking_order_unchanged():
    _set_apply_flags(True)
    from backend.logic.engine.core import _authoritative_lead_apply_eligible

    for module in ("booking", "order"):
        context = _make_context(current_module=module)
        if module == "booking":
            context.session_data.state.update_task_step_data("booking", {"service_name": "X"})
            context.session_data.state.action_ledger.current_task = "booking"
        assert _authoritative_lead_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A39 — Other workflows unchanged
# ---------------------------------------------------------------------------
async def test_a39_other_workflows_unchanged():
    _set_apply_flags(True)
    from backend.logic.engine.core import _authoritative_lead_apply_eligible

    for module in ("support", "reminders", "reviews", "payments", "general", None):
        context = _make_context(current_module=module if module else "general")
        assert _authoritative_lead_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A40 — Terminal completion remains existing-runtime-owned
# ---------------------------------------------------------------------------
async def test_a40_terminal_completion_existing_runtime_owned():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine

    context = _make_context(step_data={"interest": "enterprise demo", "phone": "+919999999999"})
    _seed_shadow(context, {"name": "Arjun", "email": "arjun@acme.com"})
    engine = _make_engine()
    original_message = dict(context.incoming_message)
    record = _install_update_spy(context)
    with patch("backend.logic.engine.field_extraction.extract_lead_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # Authoritative application method neither writes nor persists; existing
    # runtime receives the original complete message unchanged.
    assert result is None
    assert record.count("lead_capture") == 0
    assert context.incoming_message == original_message


# ===========================================================================
# Iteration 7C-V2 — Tenant lead field authority correction (F1-F15)
# ===========================================================================
def _effective_keys(context, required_only=False):
    fields = lead_capture_module.get_effective_configured_fields_for_context(context)
    keys = [str(f.get("key")) for f in fields]
    if required_only:
        keys = [k for f, k in zip(fields, keys) if f.get("required", False)]
    return keys


def _required_keys_from_apply(context, candidates):
    res = lead_capture_module.apply_external_configured_candidates(context, candidates)
    # Simulate preview-missing computation via the same authority the apply uses.
    from backend.modules.lead_capture_module import _lead_required_missing_after_preview
    preview = dict(context.session_data.state.get_task_step_data("lead_capture") or {})
    preview.update(dict(res.accepted_patch))
    return _lead_required_missing_after_preview(context, preview)


async def test_f1_empty_tenant_schema_default_configured_fields():
    context = _make_context(fields=[])
    keys = _effective_keys(context)
    assert keys == ["name", "interest"]


async def test_f2_empty_tenant_schema_required_keys():
    context = _make_context(fields=[])
    required = _effective_keys(context, required_only=True)
    assert required == ["name", "interest"]


async def test_f3_explicit_tenant_schema_not_augmented():
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    keys = _effective_keys(context)
    assert keys == ["company_name", "industry"]
    for forbidden in ("name", "interest", "phone", "email", "preferred_contact_time", "message", "notes"):
        assert forbidden not in keys


async def test_f4_explicit_tenant_required_keys_exact():
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    missing = _lead_required_missing_after_preview(context, {})
    assert missing == ["company_name"]
    # No hidden required keys leak in.
    assert "name" not in missing and "interest" not in missing


async def test_f5_tool_compat_properties_not_configured_fields():
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    from backend.logic.engine.lead_configured_turn_shadow import (
        _safe_field_keys, _configured_lead_fields,
    )
    shadow_keys, _ = _safe_field_keys(context)
    assert "phone" not in shadow_keys and "email" not in shadow_keys
    raw = [str(f.get("key")) for f in _configured_lead_fields(context)]
    assert raw == ["company_name", "industry"]


async def test_f6_shadow_exact_tenant_schema():
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    from backend.logic.engine.lead_configured_turn_shadow import _build_bounded_field_schema
    schema, _ = _build_bounded_field_schema(context)
    assert [s["field_key"] for s in schema] == ["company_name", "industry"]


async def test_f7_shadow_and_apply_authority_identical():
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
        {"key": "contact_email", "type": "email", "required": True},
    ]
    context = _make_context(fields=fields)
    from backend.logic.engine.lead_configured_turn_shadow import _lead_field_defs
    shadow_keys = sorted(_lead_field_defs(context).keys())
    apply_keys = sorted(
        str(f.get("key")) for f in lead_capture_module.get_effective_configured_fields_for_context(context)
    )
    assert shadow_keys == apply_keys

    shadow_missing, _ = (lambda c: (sorted(k for k, f in _lead_field_defs(c).items() if f.get("required", False)), 0))(context)
    apply_missing = _lead_required_missing_after_preview(context, {})
    assert sorted(apply_missing) == shadow_missing


async def test_f8_final_custom_required_field_terminal():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {"company_name": "Acme"})
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is None
    assert record.count("lead_capture") == 0
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead == {}


async def test_f9_remaining_custom_required_permits_apply():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "contact_email", "type": "email", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {"company_name": "Acme"})
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    assert result is not None
    assert record.count("lead_capture") == 1
    lead = context.session_data.state.get_task_step_data("lead_capture")
    assert lead.get("company_name") == "Acme"
    assert lead.get("contact_email") is None
    assert result.prompt_focus_fields == ["contact_email"]


async def test_f10_optional_tenant_field_does_not_block_deferral():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    _seed_shadow(context, {"company_name": "Acme"})
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # Optional industry left empty must not make this non-terminal.
    assert result is None
    assert record.count("lead_capture") == 0


async def test_f11_tenant_can_require_standard_fields():
    fields = [
        {"key": "phone", "type": "phone", "required": True},
        {"key": "email", "type": "email", "required": False},
    ]
    context = _make_context(fields=fields)
    required = _effective_keys(context, required_only=True)
    assert required == ["phone"]
    all_keys = _effective_keys(context)
    assert "name" not in all_keys and "interest" not in all_keys


async def test_f12_tenant_can_omit_standard_defaults():
    fields = [
        {"key": "company_name", "type": "string", "required": True},
        {"key": "industry", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    missing = _lead_required_missing_after_preview(context, {})
    assert "name" not in missing and "interest" not in missing
    assert missing == ["company_name"]


async def test_f13_name_alias_only_when_configured():
    # No `name` in schema -> explicit schema without name does not inject it.
    fields = [{"key": "company_name", "type": "string", "required": True}]
    context = _make_context(fields=fields, step_data={"customer_name": "Arjun"})
    keys = _effective_keys(context)
    assert keys == ["company_name"]
    assert "name" not in keys
    # Configured `name` may use customer_name for collected lookup.
    fields2 = [{"key": "name", "type": "string", "required": True}]
    context2 = _make_context(fields=fields2, step_data={"customer_name": "Arjun"})
    res = lead_capture_module.apply_external_configured_candidates(context2, {"name": "New"})
    assert res.handled is True and res.applied is False


async def test_f14_default_lead_behavior_preserved():
    context = _make_context(fields=[])
    keys = _effective_keys(context)
    assert keys == ["name", "interest"]


async def test_f15_existing_7c_guarantees_preserved():
    _set_apply_flags(True)
    from backend.logic.engine.core import AgenticEngine
    # Default schema: name(req), phone(req), interest(req).
    context = _make_context(step_data={})
    _seed_shadow(context, {"name": "Arjun", "phone": "+919999999999"})
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_lead_configured_turn_apply(context)
    # One atomic non-terminal update (interest still required/missing).
    assert result is not None
    assert record.count("lead_capture") == 1
    # Revalidation at application time occurs (covered by none-overwrite path).
    res = lead_capture_module.apply_external_configured_candidates(
        context, {"name": "Arjun", "phone": "+919999999999"}
    )
    assert res.reason_code == "already_satisfied"
    assert res.update_performed is False
    # Terminal deferral when all required supplied.
    context2 = _make_context(step_data={"phone": "+919999999999", "interest": "demo"})
    _seed_shadow(context2, {"name": "Arjun"})
    engine2 = _make_engine()
    record2 = _install_update_spy(context2)
    result2 = await engine2._try_authoritative_lead_configured_turn_apply(context2)
    assert result2 is None
    assert record2.count("lead_capture") == 0
    # No tool, no persistence, no lead.created event.
    with patch("backend.tools.registry.ToolRegistry.execute_tool", new=AsyncMock()) as tool:
        lead_capture_module.apply_external_configured_candidates(context, {"name": "Arjun"})
    tool.assert_not_called()
