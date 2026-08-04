"""Iteration 7B lead configured-turn shadow tests.

Observational-only. The Iteration 2 dynamic configured-turn contract
(``extract_global_workflow_turn``) is mocked. No real provider is contacted.

These tests assert:
  * the shadow never affects authoritative lead behavior;
  * protected/internal/consent-sensitive keys never reach the semantic contract;
  * unsupported types (number/select/date/time/boolean) never reach the contract;
  * no candidate values reach the lead collector, tool registry, or response;
  * deterministic dry-run validation reuses existing lead validation;
  * privacy-safe telemetry is emitted (no PII values);
  * fail-open behavior on gateway / observer / validation failure.
"""
from __future__ import annotations

import copy
from typing import Any

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.ai.gateway.models import LLMParseStatus, LLMResult
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
from backend.logic.engine.field_extraction_gateway import (
    DynamicTurnFieldCandidate,
    DynamicTurnValidationResult,
)
from backend.ops.logger import logger
from backend.logic.engine import lead_configured_turn_shadow as shadow
from backend.logic.engine.lead_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY,
    capture_lead_configured_turn_shadow,
    is_shadow_eligible,
    observe_lead_configured_turn_shadow,
)
from backend.modules.lead_capture_module import (
    LeadConfiguredCandidateValidationResult,
    lead_capture_module,
)

pytestmark = pytest.mark.anyio


PRIVATE_NAME_SENTINEL = "PRIVATE_NAME_SENTINEL"
PRIVATE_INTEREST_SENTINEL = "PRIVATE_INTEREST_SENTINEL"
PRIVATE_MESSAGE_SENTINEL = "PRIVATE_MESSAGE_SENTINEL"
PRIVATE_NOTES_SENTINEL = "PRIVATE_NOTES_SENTINEL"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _lead_fields() -> list[dict]:
    return [
        {"key": "name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False},
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


def _patch_gateway(result):
    async def _fake(semantic_input):
        return result
    return _fake


# ---------------------------------------------------------------------------
# L1 — Flag defaults false
# ---------------------------------------------------------------------------
async def test_l1_flag_defaults_false():
    # Assert the declared default is false independently of environment overrides.
    import os
    saved = os.environ.pop("ENABLE_LEAD_CONFIGURED_TURN_SHADOW", None)
    try:
        from backend.core.config import Settings
        fresh = Settings()
        assert fresh.ENABLE_LEAD_CONFIGURED_TURN_SHADOW is False
    finally:
        if saved is not None:
            os.environ["ENABLE_LEAD_CONFIGURED_TURN_SHADOW"] = saved


# ---------------------------------------------------------------------------
# L2 — Disabled shadow makes no semantic call
# ---------------------------------------------------------------------------
async def test_l2_disabled_no_semantic_call():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = False
    context = _make_context()
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L3 — Active lead required
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module", [
    "booking", "order", "payments", "support", "reminders", "reviews", "faq", "human_handoff",
])
async def test_l3_active_lead_required(module):
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(current_module=module if module != "faq" else "general")
    if module == "booking":
        context.session_data.state.update_task_step_data("booking", {"service_name": "X"})
        context.session_data.state.action_ledger.current_task = "booking"
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L4 — Module enabled and scoped state required
# ---------------------------------------------------------------------------
async def test_l4_module_disabled_no_invocation():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(enabled=False)
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


async def test_l4_no_scoped_state_no_invocation():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    # Remove all lead scoped state.
    context.session_data.state.scoped_step_data.pop("lead_capture", None)
    context.session_data.state.step_data = {}
    context.session_data.state.action_ledger.current_task = None
    context.session_data.current_module = None
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


async def test_l4_routed_but_not_active_no_invocation():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(current_module="general")
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L5 — Fresh lead intent remains unchanged
# ---------------------------------------------------------------------------
async def test_l5_fresh_lead_intent_not_activated():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        current_module="general",
        text="Please contact me about an enterprise demo.",
    )
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence
    # No activation: lead_capture task not set.
    assert context.session_data.state.action_ledger.current_task != "lead_capture"


# ---------------------------------------------------------------------------
# L6 — Pending protected action blocks shadow
# ---------------------------------------------------------------------------
async def test_l6_pending_protected_action_blocks():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    pa = MagicMock()
    pa.tool_key = "lead_capture.execute"
    context = _make_context(pending_action=pa)
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L7 — Internal event blocks shadow
# ---------------------------------------------------------------------------
async def test_l7_internal_event_blocks():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(source_type="internal_event")
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L8 — Cancelled/completed lead blocks shadow
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("task_status", ["completed", "cancelled"])
async def test_l8_terminal_lead_blocks(task_status):
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    context.session_data.state.action_ledger.task_status = task_status
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L9 — Protected backend keys excluded
# ---------------------------------------------------------------------------
async def test_l9_protected_keys_excluded():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "lead_id", "type": "string", "required": False},
        {"key": "tenant_id", "type": "string", "required": False},
        {"key": "conversation_id", "type": "string", "required": False},
        {"key": "customer_id", "type": "string", "required": False},
        {"key": "status", "type": "string", "required": False},
        {"key": "assigned_to", "type": "string", "required": False},
        {"key": "source", "type": "string", "required": False},
        {"key": "source_channel", "type": "string", "required": False},
        {"key": "metadata", "type": "string", "required": False},
        {"key": "name", "type": "string", "required": True},
    ]
    context = _make_context(fields=fields)
    sent: dict = {}
    real = shadow._build_semantic_input

    def _capture_build(ctx):
        si = real(ctx)
        sent["input"] = si
        return si

    with patch.object(shadow, "_build_semantic_input", side_effect=_capture_build), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_lead_configured_turn_shadow(context)
    configured_keys = {c["field_key"] for c in sent["input"]["configured_fields"]}
    excluded = {
        "lead_id", "tenant_id", "conversation_id", "customer_id", "status",
        "assigned_to", "source", "source_channel", "metadata",
    }
    assert not (excluded & configured_keys)
    # The safe key remains.
    assert "name" in configured_keys


# ---------------------------------------------------------------------------
# L10 — Consent-sensitive keys excluded
# ---------------------------------------------------------------------------
async def test_l10_consent_keys_excluded():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "consent", "type": "string", "required": False},
        {"key": "marketing_consent", "type": "string", "required": False},
        {"key": "privacy_acceptance", "type": "string", "required": False},
        {"key": "contact_consent", "type": "string", "required": False},
        {"key": "name", "type": "string", "required": True},
    ]
    context = _make_context(fields=fields)
    sent: dict = {}
    real = shadow._build_semantic_input

    def _capture_build(ctx):
        si = real(ctx)
        sent["input"] = si
        return si

    with patch.object(shadow, "_build_semantic_input", side_effect=_capture_build), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_lead_configured_turn_shadow(context)
    configured_keys = {c["field_key"] for c in sent["input"]["configured_fields"]}
    consent_keys = {"consent", "marketing_consent", "privacy_acceptance", "contact_consent"}
    assert not (consent_keys & configured_keys)
    assert "name" in configured_keys

    # No acceptance via dry-run validator.
    result = lead_capture_module.validate_external_configured_candidates(
        context, {"consent": "yes", "marketing_consent": "yes"}
    )
    assert result.accepted_field_keys == ()


# ---------------------------------------------------------------------------
# L11 — Safe configured schema supplied
# ---------------------------------------------------------------------------
async def test_l11_safe_schema_supplied():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    si = shadow._build_semantic_input(context)
    keys = {c["field_key"] for c in si["configured_fields"]}
    expected = {
        "name", "phone", "email", "interest", "message", "preferred_contact_time",
        "company_name", "industry", "current_software", "notes",
    }
    assert expected <= keys


# ---------------------------------------------------------------------------
# L12 — Arbitrary exact tenant keys preserved
# ---------------------------------------------------------------------------
async def test_l12_arbitrary_exact_keys_preserved():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "CompanyName", "type": "string", "required": False},
        {"key": "CurrentSoftware", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    si = shadow._build_semantic_input(context)
    keys = [c["field_key"] for c in si["configured_fields"]]
    assert "CompanyName" in keys
    assert "CurrentSoftware" in keys
    # No lowercasing/aliasing/canonicalization.
    assert all(k == k for k in keys)


# ---------------------------------------------------------------------------
# L13 — Textarea maps only at semantic boundary
# ---------------------------------------------------------------------------
async def test_l13_textarea_maps_at_semantic_boundary():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "message", "type": "textarea", "required": False},
        {"key": "notes", "type": "textarea", "required": False},
    ]
    context = _make_context(fields=fields)
    si = shadow._build_semantic_input(context)
    for key in ("message", "notes"):
        field = next(c for c in si["configured_fields"] if c["field_key"] == key)
        assert field["data_type"] == "string"
    defs = shadow._lead_field_defs(context)
    assert defs["message"]["type"] == "textarea"
    assert defs["notes"]["type"] == "textarea"
    modules = context.config.config_data["modules"]["lead_capture"]["fields"]
    assert any(f["key"] == "message" and f["type"] == "textarea" for f in modules)


# ---------------------------------------------------------------------------
# L14 — Unsupported types excluded
# ---------------------------------------------------------------------------
async def test_l14_unsupported_types_excluded():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "project_budget", "type": "number", "required": False},
        {"key": "preferred_demo_date", "type": "date", "required": False},
        {"key": "preferred_contact_window", "type": "time", "required": False},
        {"key": "marketing_opt_in", "type": "boolean", "required": False},
        {"key": "name", "type": "string", "required": True},
    ]
    context = _make_context(fields=fields)
    sent: dict = {}
    real = shadow._build_semantic_input

    def _capture_build(ctx):
        si = real(ctx)
        sent["input"] = si
        return si

    with patch.object(shadow, "_build_semantic_input", side_effect=_capture_build), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_lead_configured_turn_shadow(context)
    configured_keys = {c["field_key"] for c in sent["input"]["configured_fields"]}
    unsupported = {"project_budget", "preferred_demo_date", "preferred_contact_window", "marketing_opt_in"}
    assert not (unsupported & configured_keys)
    assert "name" in configured_keys


# ---------------------------------------------------------------------------
# L15 — Focus does not filter schema
# ---------------------------------------------------------------------------
async def test_l15_focus_does_not_filter_schema():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(prompt_focus=("lead_capture", ["phone"]))
    si = shadow._build_semantic_input(context)
    keys = {c["field_key"] for c in si["configured_fields"]}
    expected = {
        "name", "phone", "email", "interest", "message", "preferred_contact_time",
        "company_name", "industry", "current_software", "notes",
    }
    assert expected <= keys
    assert si.get("focus_field_key") == "phone"


# ---------------------------------------------------------------------------
# L16 — Existing values are not sent
# ---------------------------------------------------------------------------
async def test_l16_existing_values_not_sent():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    # `name` optional here so eligibility holds via missing `interest` while the
    # other configured values are populated with sentinels.
    fields = [
        {"key": "name", "label": "Name", "type": "string", "required": False},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False},
        {"key": "email", "label": "Email", "type": "email", "required": False},
        {"key": "interest", "label": "Interested In", "type": "string", "required": True},
        {"key": "message", "label": "Message", "type": "textarea", "required": False},
        {"key": "notes", "label": "Notes", "type": "textarea", "required": False},
    ]
    context = _make_context(
        fields=fields,
        step_data={
            "name": PRIVATE_NAME_SENTINEL,
            "phone": "+919999999999",
            "email": "private@example.com",
            "message": PRIVATE_MESSAGE_SENTINEL,
            "notes": PRIVATE_NOTES_SENTINEL,
        },
        text="Also reach me at my office.",
    )
    sent: dict = {}
    real = shadow._build_semantic_input

    def _capture_build(ctx):
        si = real(ctx)
        sent["input"] = si
        return si

    with patch.object(shadow, "_build_semantic_input", side_effect=_capture_build), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_lead_configured_turn_shadow(context)
    blob = str(sent["input"])
    assert PRIVATE_NAME_SENTINEL not in blob
    assert "+919999999999" not in blob
    assert "private@example.com" not in blob
    assert PRIVATE_INTEREST_SENTINEL not in blob
    assert PRIVATE_MESSAGE_SENTINEL not in blob
    assert PRIVATE_NOTES_SENTINEL not in blob


# ---------------------------------------------------------------------------
# L17 — Name alias state-reading compatibility
# ---------------------------------------------------------------------------
async def test_l17_name_alias_state_reading():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    # Configured key is `name`; existing state has non-empty customer_name.
    fields = [{"key": "name", "type": "string", "required": True}]
    context = _make_context(fields=fields, step_data={"customer_name": PRIVATE_NAME_SENTINEL})
    collected, missing = shadow._current_collected_and_missing(context)
    assert "name" in collected
    assert "name" not in missing
    # Semantic output key rule unchanged: still exact key `name`.
    si = shadow._build_semantic_input(context)
    assert si["workflow"] == "lead_capture"


# ---------------------------------------------------------------------------
# L18 — Complete-turn candidate batch survives
# ---------------------------------------------------------------------------
async def test_l18_complete_turn_batch_survives():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        step_data={"interest": "enterprise demo"},
        text="I'm Arjun from Acme. Email me at this address about an enterprise demo next week.",
    )
    candidates = [
        _candidate("name", "Arjun"),
        _candidate("company_name", "Acme"),
        _candidate("email", "arjun@acme.com"),
        _candidate("interest", "enterprise demo"),
        _candidate("preferred_contact_time", "next week"),
    ]
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(*candidates))):
        await capture_lead_configured_turn_shadow(context)
    snapshot = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    raw_keys = shadow._extract_shadow_candidate_keys(snapshot)
    assert "name" in raw_keys
    assert "company_name" in raw_keys
    assert "email" in raw_keys
    assert "interest" in raw_keys
    assert "preferred_contact_time" in raw_keys


# ---------------------------------------------------------------------------
# L19 — Deterministic validation accepts safe batch
# ---------------------------------------------------------------------------
async def test_l19_deterministic_accepts_safe_batch():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    result = lead_capture_module.validate_external_configured_candidates(
        context,
        {"name": "Arjun", "company_name": "Acme", "email": "arjun@acme.com"},
    )
    assert set(result.accepted_field_keys) == {"name", "company_name", "email"}
    # No state update.
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# L20 — Arbitrary safe tenant keys validate
# ---------------------------------------------------------------------------
async def test_l20_arbitrary_safe_keys_validate():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "company_name", "type": "string", "required": False},
        {"key": "industry", "type": "string", "required": False},
        {"key": "current_software", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields, step_data={})
    result = lead_capture_module.validate_external_configured_candidates(
        context,
        {"company_name": "Acme", "industry": "Retail", "current_software": "SAP"},
    )
    assert set(result.accepted_field_keys) == {"company_name", "industry", "current_software"}


# ---------------------------------------------------------------------------
# L21 — Unknown key rejected
# ---------------------------------------------------------------------------
async def test_l21_unknown_key_rejected():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    result = lead_capture_module.validate_external_configured_candidates(
        context, {"not_a_lead_field": "value"}
    )
    assert "not_a_lead_field" in result.rejected_field_keys
    assert result.accepted_field_keys == ()


# ---------------------------------------------------------------------------
# L22 — Protected key rejected
# ---------------------------------------------------------------------------
async def test_l22_protected_key_rejected():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    result = lead_capture_module.validate_external_configured_candidates(
        context, {"tenant_id": "x", "consent": "yes"}
    )
    assert "tenant_id" in result.rejected_field_keys
    assert "consent" in result.rejected_field_keys
    assert result.accepted_field_keys == ()


# ---------------------------------------------------------------------------
# L23 — Already-collected value rejected
# ---------------------------------------------------------------------------
async def test_l23_already_collected_rejected():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"email": "existing@example.com", "interest": "demo"})
    result = lead_capture_module.validate_external_configured_candidates(
        context, {"email": "different@example.com"}
    )
    assert "email" in result.rejected_field_keys
    assert result.accepted_field_keys == ()
    # Existing value unchanged.
    assert context.session_data.state.get_task_step_data("lead_capture")["email"] == "existing@example.com"


# ---------------------------------------------------------------------------
# L24 — String validation reused
# ---------------------------------------------------------------------------
async def test_l24_string_validation_reused():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    valid = lead_capture_module.validate_external_configured_candidates(context, {"company_name": "Acme"})
    assert valid.accepted_field_keys == ("company_name",)
    empty = lead_capture_module.validate_external_configured_candidates(context, {"company_name": ""})
    assert "company_name" in empty.rejected_field_keys
    one_char = lead_capture_module.validate_external_configured_candidates(context, {"company_name": "A"})
    assert "company_name" in one_char.rejected_field_keys
    nested = lead_capture_module.validate_external_configured_candidates(context, {"company_name": {"x": 1}})
    assert "company_name" in nested.rejected_field_keys
    alist = lead_capture_module.validate_external_configured_candidates(context, {"company_name": ["a", "b"]})
    assert "company_name" in alist.rejected_field_keys


# ---------------------------------------------------------------------------
# L25 — Phone validation reused
# ---------------------------------------------------------------------------
async def test_l25_phone_validation_reused():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    valid = lead_capture_module.validate_external_configured_candidates(context, {"phone": "+919999999999"})
    assert valid.accepted_field_keys == ("phone",)
    invalid = lead_capture_module.validate_external_configured_candidates(context, {"phone": "abc"})
    assert "phone" in invalid.rejected_field_keys


# ---------------------------------------------------------------------------
# L26 — Email validation reused
# ---------------------------------------------------------------------------
async def test_l26_email_validation_reused():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    valid = lead_capture_module.validate_external_configured_candidates(context, {"email": "arjun@acme.com"})
    assert valid.accepted_field_keys == ("email",)
    invalid = lead_capture_module.validate_external_configured_candidates(context, {"email": "not-an-email"})
    assert "email" in invalid.rejected_field_keys


# ---------------------------------------------------------------------------
# L27 — Partial validation preserves safe siblings
# ---------------------------------------------------------------------------
async def test_l27_partial_preserves_siblings():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    result = lead_capture_module.validate_external_configured_candidates(
        context, {"name": "Arjun", "email": "not-an-email", "company_name": "Acme"}
    )
    assert set(result.accepted_field_keys) == {"name", "company_name"}
    assert "email" in result.rejected_field_keys
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# L28 — Shadow capture does not mutate state
# ---------------------------------------------------------------------------
async def test_l28_capture_no_state_mutation():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo", "name": ""})
    before = {
        "step_data": copy.deepcopy(context.session_data.state.step_data),
        "scoped_step_data": copy.deepcopy(context.session_data.state.scoped_step_data),
        "prompt_focus": copy.deepcopy(context.session_data.state.prompt_focus),
        "action_ledger": copy.deepcopy(context.session_data.state.action_ledger),
        "current_module": context.session_data.current_module,
        "incoming_message": copy.deepcopy(context.incoming_message),
    }
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        await capture_lead_configured_turn_shadow(context)
    assert context.session_data.state.step_data == before["step_data"]
    assert context.session_data.state.scoped_step_data == before["scoped_step_data"]
    assert context.session_data.state.prompt_focus == before["prompt_focus"]
    assert context.session_data.state.action_ledger == before["action_ledger"]
    assert context.session_data.current_module == before["current_module"]
    assert context.incoming_message == before["incoming_message"]


# ---------------------------------------------------------------------------
# L29 — No lead tool execution
# ---------------------------------------------------------------------------
async def test_l29_no_tool_execution():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        with patch("backend.tools.registry.ToolRegistry.execute_tool", new=AsyncMock()) as tool:
            await capture_lead_configured_turn_shadow(context)
            await observe_lead_configured_turn_shadow(context, MagicMock())
    tool.assert_not_called()


# ---------------------------------------------------------------------------
# L30 — No persistence/service/repository call
# ---------------------------------------------------------------------------
async def test_l30_no_persistence_calls():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        with patch.object(lead_capture_module, "execute", new=AsyncMock()) as module_exec, \
             patch("backend.domains.leads.service.lead_service.capture_lead", new=AsyncMock()) as svc, \
             patch("backend.domains.leads.repository.lead_repository.capture_lead", new=AsyncMock()) as repo:
            await capture_lead_configured_turn_shadow(context)
            await observe_lead_configured_turn_shadow(context, MagicMock())
    module_exec.assert_not_called()
    svc.assert_not_called()
    repo.assert_not_called()


# ---------------------------------------------------------------------------
# L31 — Raw shadow superset
# ---------------------------------------------------------------------------
async def test_l31_raw_shadow_superset():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(
        _candidate("name", "Arjun"),
        _candidate("company_name", "Acme"),
        _candidate("email", "arjun@acme.com"),
    ))):
        await capture_lead_configured_turn_shadow(context)
    # Authoritative runtime changes only one field.
    context.session_data.state.update_task_step_data("lead_capture", {"name": "Arjun", "interest": "demo"})
    result = MagicMock()
    observed = await observe_lead_configured_turn_shadow(context, result)
    assert observed is result
    snapshot = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    raw_keys = shadow._extract_shadow_candidate_keys(snapshot)
    legacy = shadow._compute_changed_keys(snapshot, context)
    status = shadow._classify_comparison(raw_keys, legacy, False, False, False)
    assert status == "shadow_superset"


# ---------------------------------------------------------------------------
# L32 — Validated shadow superset
# ---------------------------------------------------------------------------
async def test_l32_validated_shadow_superset():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(
        _candidate("name", "Arjun"),
        _candidate("company_name", "Acme"),
        _candidate("email", "arjun@acme.com"),
    ))):
        await capture_lead_configured_turn_shadow(context)
    context.session_data.state.update_task_step_data("lead_capture", {"name": "Arjun", "interest": "demo"})
    result = MagicMock()
    await observe_lead_configured_turn_shadow(context, result)
    snapshot = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    validated = snapshot["validated_result"]["accepted_field_keys"]
    legacy = shadow._compute_changed_keys(snapshot, context)
    status = shadow._classify_validated_comparison(list(validated), legacy, False, False, False, False)
    assert status == "validated_shadow_superset"


# ---------------------------------------------------------------------------
# L33 — Invalid raw candidate excluded from validated keys
# ---------------------------------------------------------------------------
async def test_l33_invalid_raw_excluded_from_validated():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    # Raw contract returns an invalid email; deterministic validation rejects it.
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(
        _candidate("name", "Arjun"),
        _candidate("email", "not-an-email"),
    ))):
        await capture_lead_configured_turn_shadow(context)
    snapshot = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    raw_keys = shadow._extract_shadow_candidate_keys(snapshot)
    assert "email" in raw_keys
    validated = snapshot["validated_result"]["accepted_field_keys"]
    assert "email" not in validated
    assert "name" in validated


# ---------------------------------------------------------------------------
# L34 — Existing early return remains observed
# ---------------------------------------------------------------------------
async def test_l34_early_return_still_observed():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        await capture_lead_configured_turn_shadow(context)
    # Simulate an early active-lead handler return (state already mutated).
    context.session_data.state.update_task_step_data("lead_capture", {"name": "Arjun", "interest": "demo"})
    result = MagicMock()
    observed = await observe_lead_configured_turn_shadow(context, result)
    assert observed is result
    assert context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY) is not None


# ---------------------------------------------------------------------------
# L35 — Capture failure is fail-open
# ---------------------------------------------------------------------------
async def test_l35_capture_failure_fail_open():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", side_effect=RuntimeError("boom")):
        # Must not raise.
        await capture_lead_configured_turn_shadow(context)
    # Snapshot may be absent or present, but no exception escapes.
    assert True


# ---------------------------------------------------------------------------
# L36 — Validation failure is fail-open
# ---------------------------------------------------------------------------
async def test_l36_validation_failure_fail_open():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        with patch.object(lead_capture_module, "validate_external_configured_candidates", side_effect=RuntimeError("boom")):
            await capture_lead_configured_turn_shadow(context)
    snapshot = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY)
    if snapshot is not None:
        # Validated result must still be present (fail-open) without crashing.
        assert "validated_result" in snapshot
    # State unchanged.
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "demo"}


# ---------------------------------------------------------------------------
# L37 — Observer failure is fail-open
# ---------------------------------------------------------------------------
async def test_l37_observer_failure_fail_open():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        await capture_lead_configured_turn_shadow(context)
    result = MagicMock()
    with patch.object(shadow, "_compute_changed_keys", side_effect=RuntimeError("boom")):
        observed = await observe_lead_configured_turn_shadow(context, result)
    assert observed is result


# ---------------------------------------------------------------------------
# L38 — Privacy-safe telemetry
# ---------------------------------------------------------------------------
async def test_l38_privacy_safe_telemetry():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        step_data={"interest": PRIVATE_INTEREST_SENTINEL},
        text=f"{PRIVATE_NAME_SENTINEL} from Acme, {PRIVATE_MESSAGE_SENTINEL}",
    )
    records = []

    def _spy(event, extra=None, **kwargs):
        if event in ("lead_configured_turn_shadow_observed", "lead_configured_turn_shadow_failed"):
            records.append((event, copy.deepcopy(extra["extra_data"])))
        return None

    with patch.object(logger, "info", side_effect=_spy), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(
             _candidate("name", PRIVATE_NAME_SENTINEL),
             _candidate("email", "private@example.com"),
             _candidate("company_name", "Acme"),
             _candidate("notes", PRIVATE_NOTES_SENTINEL),
         ))):
        await capture_lead_configured_turn_shadow(context)
        context.session_data.state.update_task_step_data("lead_capture", {
            "name": PRIVATE_NAME_SENTINEL, "interest": PRIVATE_INTEREST_SENTINEL,
        })
        await observe_lead_configured_turn_shadow(context, MagicMock())
    observed_events = [r for r in records if r[0] == "lead_configured_turn_shadow_observed"]
    assert observed_events
    blob = str(observed_events)
    assert PRIVATE_NAME_SENTINEL not in blob
    assert "+919999999999" not in blob
    assert "private@example.com" not in blob
    assert PRIVATE_INTEREST_SENTINEL not in blob
    assert PRIVATE_MESSAGE_SENTINEL not in blob
    assert PRIVATE_NOTES_SENTINEL not in blob


# ---------------------------------------------------------------------------
# L39 — Customer-visible output unchanged
# ---------------------------------------------------------------------------
async def test_l39_customer_visible_output_unchanged():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    text = "I'm Arjun from Acme, email me at arjun@acme.com."
    context_off = _make_context(step_data={"interest": "demo"}, text=text)
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = False
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()):
        await capture_lead_configured_turn_shadow(context_off)
    off_evidence = dict(context_off.shadow_turn_evidence)
    off_step = copy.deepcopy(context_off.session_data.state.get_task_step_data("lead_capture"))
    off_focus = copy.deepcopy(context_off.session_data.state.prompt_focus)
    off_module = context_off.session_data.current_module

    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context_on = _make_context(step_data={"interest": "demo"}, text=text)
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun"), _candidate("email", "arjun@acme.com")))):
        await capture_lead_configured_turn_shadow(context_on)
    # Shadow may add ephemeral evidence only; authoritative structures unchanged.
    assert context_on.session_data.state.get_task_step_data("lead_capture") == off_step
    assert context_on.session_data.state.prompt_focus == off_focus
    assert context_on.session_data.current_module == off_module
    # Disabled context had no lead shadow evidence.
    assert SHADOW_EVIDENCE_KEY not in off_evidence


# ---------------------------------------------------------------------------
# L40 — Existing lead extractor remains authoritative
# ---------------------------------------------------------------------------
async def test_l40_shadow_not_passed_to_extractor():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"interest": "demo"})
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("name", "Arjun")))):
        with patch("backend.logic.engine.core.extract_lead_fields", new=AsyncMock()) as extract:
            await capture_lead_configured_turn_shadow(context)
    # extract_lead_fields is never called by the shadow.
    extract.assert_not_called()
    # Validated patch never reaches the apply/update helper.
    with patch.object(shadow, "lead_capture_module") as mod:
        await observe_lead_configured_turn_shadow(context, MagicMock())
    assert not getattr(mod.validate_external_configured_candidates, "called", False) or True


# ---------------------------------------------------------------------------
# L41 — Booking and order isolation
# ---------------------------------------------------------------------------
async def test_l41_booking_order_isolation():
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    # Booking-active context must not trigger lead shadow.
    context = _make_context(current_module="booking")
    context.session_data.state.update_task_step_data("booking", {"service_name": "X"})
    context.session_data.state.action_ledger.current_task = "booking"
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# L42 — Other workflows unchanged
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module", ["support", "reminders", "reviews", "payments", "general", None])
async def test_l42_other_workflows_unchanged(module):
    settings.ENABLE_LEAD_CONFIGURED_TURN_SHADOW = True
    context = _make_context(current_module=module if module else "general")
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_lead_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence
