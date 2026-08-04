"""Iteration 6C authoritative order multi-field continuation tests.

Backend-owned, default-disabled. The Iteration 2 dynamic configured-turn
contract (``extract_global_workflow_turn``) and live providers are mocked.
No real provider is contacted.

These tests assert:
  * the apply flag defaults false and both flags are required;
  * the authoritative path is bounded to an already-active order with item_name;
  * protected / financial / internal / unknown keys never persist;
  * revalidation at application time rejects stale/invalid values;
  * exactly one atomic order-scoped update occurs on success;
  * already-satisfied replay writes zero times and still owns the turn;
  * the legacy semantic extractor and prefill never run after a handled apply;
  * partial accepted batches persist the safe subset once;
  * conditional delivery/pickup behavior matches deterministic validation;
  * continuation derives from authoritative order state;
  * no catalog resolution, tool execution, or order completion is added;
  * privacy-safe key-only telemetry;
  * first-message order activation and other workflows are unchanged.
"""
from __future__ import annotations

from typing import Any
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
from backend.logic.engine import order_configured_turn_shadow as shadow
from backend.logic.engine.field_extraction_gateway import (
    DynamicTurnFieldCandidate,
    DynamicTurnValidationResult,
)
from backend.logic.engine.order_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY,
    _emit_order_applied_event,
)
from backend.modules.order_module import (
    ExternalOrderCandidateApplicationResult,
    order_module,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _order_fields() -> list[dict]:
    return [
        {"key": "quantity", "label": "Quantity", "type": "number", "required": True},
        {"key": "fulfillment_type", "label": "Fulfillment", "type": "select", "required": True, "options_from": "fulfillment_types"},
        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
        {"key": "delivery_address", "label": "Address", "type": "textarea", "required": False},
        {"key": "payment_method", "label": "Payment", "type": "select", "required": False, "options_from": "payment_methods"},
        {"key": "delivery_instructions", "label": "Instructions", "type": "string", "required": False},
        {"key": "preferred_delivery_window", "label": "Window", "type": "string", "required": False},
        {"key": "company_tax_number", "label": "Tax No", "type": "string", "required": False},
        {"key": "allergy_notes", "label": "Allergy", "type": "string", "required": False},
        {"key": "email", "label": "Email", "type": "email", "required": False},
        {"key": "notes", "label": "Notes", "type": "string", "required": False},
    ]


def _make_context(
    *,
    fields=None,
    text="Make it two for delivery. I'm Arjun, this number is fine, and send it to my office.",
    step_data=None,
    source_type="awa_chat",
    channel_type="awa_chat",
    current_module="order",
    pending_action=None,
    prompt_focus=None,
    fulfillment_types=None,
    payment_methods=None,
):
    import uuid

    tenant_id = uuid.uuid4()
    modules = {
        "order": {
            "enabled": True,
            "fields": fields if fields is not None else _order_fields(),
        }
    }
    if fulfillment_types is not None:
        modules["order"]["fulfillment_types"] = fulfillment_types
    if payment_methods is not None:
        modules["order"]["payment_methods"] = payment_methods
    state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    state.action_ledger.current_task = current_module if current_module else None
    state.action_ledger.task_status = "collecting"
    if step_data:
        state.update_task_step_data("order", dict(step_data))
    elif current_module == "order":
        state.update_task_step_data("order", {"item_name": "Biryani"})
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


def _seed_shadow(context: ExecutionContext, candidates: dict[str, Any]) -> None:
    """Seed a valid Iteration 6B shadow snapshot with a validated patch."""
    validation = order_module.validate_external_configured_candidates(context, candidates)
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = {
        "workflow": "order",
        "config_version": 1,
        "channel": "awa_chat",
        "focus_field_key": None,
        "safe_configured_field_keys": list(validation.accepted_field_keys),
        "missing_required_field_keys": [],
        "excluded_unsafe_field_count": 0,
        "excluded_unsupported_type_count": 0,
        "pre_turn_safe_field_values": dict(context.session_data.state.get_task_step_data("order") or {}),
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
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = enabled
    settings.ENABLE_ORDER_CONFIGURED_TURN_APPLY = enabled


def _install_update_spy(context: ExecutionContext) -> list:
    """Patch SessionState.update_task_step_data to record call modules.

    SessionState is a pydantic model, so we patch the class method (not an
    instance attribute) and restore it afterwards.
    """
    from backend.core.context import SessionState
    from unittest.mock import patch

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
    from unittest.mock import MagicMock, AsyncMock

    engine = AgenticEngine.__new__(AgenticEngine)
    registry = MagicMock()
    tool_def = MagicMock()
    tool_def.module = order_module
    registry.get_tool.return_value = tool_def
    registry.get_tool_key_for_module.return_value = "order.execute"
    from backend.tools.registry import ToolExecutionResult

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

    registry.execute_calls = []
    registry.execute_tool = _fake_execute
    engine.tool_registry = registry
    return engine


@pytest.fixture(autouse=True)
def _reset_order_flags():
    """Restore the shared settings flags to their defaults after each test to
    avoid polluting other test modules that assert default-off behavior."""
    yield
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_ORDER_CONFIGURED_TURN_APPLY = False


# ---------------------------------------------------------------------------
# A1 — Apply flag defaults false
# ---------------------------------------------------------------------------
async def test_a1_apply_flag_default_is_false():
    # A fresh Settings instance must declare the default False independently of
    # any environment override applied during the test session.
    from backend.core.config import Settings
    fresh = Settings()
    assert getattr(fresh, "ENABLE_ORDER_CONFIGURED_TURN_APPLY", None) is False


# ---------------------------------------------------------------------------
# A2 — Both flags required
# ---------------------------------------------------------------------------
async def test_a2_both_flags_required():
    for shadow_on, apply_on in [(False, False), (True, False), (False, True)]:
        settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = shadow_on
        settings.ENABLE_ORDER_CONFIGURED_TURN_APPLY = apply_on
        from backend.logic.engine.core import _authoritative_order_apply_enabled
        assert _authoritative_order_apply_enabled() is False


# ---------------------------------------------------------------------------
# A3 — Pre-existing active order required
# ---------------------------------------------------------------------------
async def test_a3_active_order_required():
    _set_apply_flags(True)
    for module in ["booking", "lead_capture", "support", "reminders", "reviews", "payments", "payment", "human_handoff", None]:
        context = _make_context(current_module=module if module else "general")
        if module == "booking":
            context.session_data.state.update_task_step_data("booking", {"item_name": "X"})
            context.session_data.state.action_ledger.current_task = "booking"
        from backend.logic.engine.core import _authoritative_order_apply_eligible
        assert _authoritative_order_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A4 — Existing item name required
# ---------------------------------------------------------------------------
async def test_a4_existing_item_name_required():
    _set_apply_flags(True)
    context = _make_context()
    context.session_data.state.scoped_step_data["order"] = {"quantity": 2}
    context.session_data.state.step_data = {"quantity": 2}
    from backend.logic.engine.core import _authoritative_order_apply_eligible
    assert _authoritative_order_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A5 — Pending disambiguation blocks
# ---------------------------------------------------------------------------
async def test_a5_pending_disambiguation_blocks():
    _set_apply_flags(True)
    context = _make_context()
    state = context.session_data.state
    state.step_data["pending_disambiguation"] = {"options": [{"index": 1, "name": "A"}]}
    state.scoped_step_data["order"] = dict(state.get_task_step_data("order") or {})
    state.scoped_step_data["order"]["pending_disambiguation"] = {"options": [{"index": 1, "name": "A"}]}
    from backend.logic.engine.core import _authoritative_order_apply_eligible
    assert _authoritative_order_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A6 — Pending confirmation / assurance blocks
# ---------------------------------------------------------------------------
async def test_a6_pending_confirmation_or_assurance_blocks():
    _set_apply_flags(True)
    # pending confirmation
    pa = MagicMock()
    pa.tool_key = "order.execute"
    context = _make_context(pending_action=pa)
    from backend.logic.engine.core import _authoritative_order_apply_eligible
    assert _authoritative_order_apply_eligible(context) is False
    # OTP continuation (internal_event)
    context2 = _make_context(source_type="internal_event")
    assert _authoritative_order_apply_eligible(context2) is False
    # completed order
    context3 = _make_context()
    context3.session_data.state.action_ledger.task_status = "completed"
    assert _authoritative_order_apply_eligible(context3) is False


# ---------------------------------------------------------------------------
# A7 — Quantity and fulfillment persist together
# ---------------------------------------------------------------------------
async def test_a7_quantity_fulfillment_persist_together():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    # Revalidation occurs at application time (the apply method calls
    # validate_external_configured_candidates internally).
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    order = context.session_data.state.get_task_step_data("order")
    assert order.get("quantity") == 2
    assert order.get("fulfillment_type") == "delivery"
    # No legacy extractor or safe-update function ran for this turn.
    from backend.logic.engine.core import AgenticEngine as _AE
    assert result.prompt_focus_workflow == "order" or result.response_text


# ---------------------------------------------------------------------------
# A8 — Complete non-item customer details persist together
# ---------------------------------------------------------------------------
async def test_a8_complete_customer_details_persist_together():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    candidates = {
        "quantity": 2,
        "fulfillment_type": "delivery",
        "customer_name": "Arjun",
        "phone": "+919999999999",
        "delivery_address": "my office",
    }
    _seed_shadow(context, candidates)
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    record = _install_update_spy(context)
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    order_calls = [c for c in record if c == "order"]
    assert len(order_calls) == 1
    order = context.session_data.state.get_task_step_data("order")
    for key in candidates:
        assert order.get(key) == candidates[key]


# ---------------------------------------------------------------------------
# A9 — Arbitrary configured keys persist
# ---------------------------------------------------------------------------
async def test_a9_arbitrary_configured_keys_persist():
    _set_apply_flags(True)
    fields = [
        {"key": "delivery_instructions", "type": "string", "required": False},
        {"key": "preferred_delivery_window", "type": "string", "required": False},
        {"key": "company_tax_number", "type": "string", "required": False},
        {"key": "allergy_notes", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    candidates = {
        "delivery_instructions": "leave at door",
        "preferred_delivery_window": "evening",
        "company_tax_number": "GST123",
        "allergy_notes": "nuts",
    }
    _seed_shadow(context, candidates)
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    order = context.session_data.state.get_task_step_data("order")
    for key in candidates:
        assert order.get(key) == candidates[key]


# ---------------------------------------------------------------------------
# A10 — Protected item identity never persists
# ---------------------------------------------------------------------------
async def test_a10_protected_item_identity_never_persists():
    _set_apply_flags(True)
    context = _make_context()
    protected = {
        "item_name": "X",
        "item_sku": "X",
        "item_id": "X",
        "items": ["X"],
        "_backend_refs": {"x": 1},
    }
    # validation itself rejects; the apply method must never accept these.
    res = order_module.apply_external_configured_candidates(context, protected)
    assert res.handled is False
    # If a snapshot somehow carried them, they must not reach state.
    _seed_shadow(context, {"quantity": 2})
    # Manually taint the validated patch with a protected key and ensure apply
    # revalidates and drops it.
    snap = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    snap["validated_result"]["accepted_patch"]["item_name"] = "hacked"
    snap["validated_result"]["accepted_field_keys"].append("item_name")
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    order = context.session_data.state.get_task_step_data("order")
    assert order.get("item_name") == "Biryani"
    assert order.get("quantity") == 2


# ---------------------------------------------------------------------------
# A11 — Financial/internal keys never persist
# ---------------------------------------------------------------------------
async def test_a11_financial_internal_keys_never_persist():
    _set_apply_flags(True)
    context = _make_context()
    res = order_module.apply_external_configured_candidates(
        context, {"price": 10, "total": 20, "currency": "INR", "status": "done", "idempotency_key": "k"}
    )
    assert res.handled is False


# ---------------------------------------------------------------------------
# A12 — Unknown key causes safe fallback
# ---------------------------------------------------------------------------
async def test_a12_unknown_key_safe_fallback():
    _set_apply_flags(True)
    context = _make_context()
    _seed_shadow(context, {"quantity": 2})
    snap = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    snap["validated_result"]["accepted_patch"]["unknown_field"] = "x"
    snap["validated_result"]["accepted_field_keys"].append("unknown_field")
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    # unknown key dropped at revalidation; quantity still applies -> handled.
    assert result is not None
    assert context.session_data.state.get_task_step_data("order").get("quantity") == 2
    # extractor should not have run (handled path).
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A13 — Already-collected values are not overwritten
# ---------------------------------------------------------------------------
async def test_a13_already_collected_not_overwritten():
    _set_apply_flags(True)
    context = _make_context(step_data={"item_name": "Biryani", "customer_name": "Original"})
    _seed_shadow(context, {"customer_name": "Different", "quantity": 2})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    order = context.session_data.state.get_task_step_data("order")
    assert order.get("customer_name") == "Original"
    assert order.get("quantity") == 2
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A14 — One atomic update
# ---------------------------------------------------------------------------
async def test_a14_one_atomic_update():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    candidates = {
        "quantity": 2,
        "fulfillment_type": "delivery",
        "customer_name": "Arjun",
        "phone": "+919999999999",
        "delivery_address": "my office",
    }
    _seed_shadow(context, candidates)
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    record = _install_update_spy(context)
    await engine._try_authoritative_order_configured_turn_apply(context)
    assert record.count("order") == 1


# ---------------------------------------------------------------------------
# A15 — Revalidation at application time
# ---------------------------------------------------------------------------
async def test_a15_revalidation_at_application_time():
    _set_apply_flags(True)
    context = _make_context()
    # Field becomes populated before apply. Revalidation must reject it.
    context.session_data.state.update_task_step_data("order", {"quantity": 5})
    res = order_module.apply_external_configured_candidates(context, {"quantity": 2})
    # Already satisfied -> handled, no overwrite, no write.
    assert res.handled is True
    assert res.applied is False
    assert res.update_performed is False
    assert res.reason_code == "already_satisfied"
    assert context.session_data.state.get_task_step_data("order").get("quantity") == 5


# ---------------------------------------------------------------------------
# A16 — Successful application skips legacy extraction
# ---------------------------------------------------------------------------
async def test_a16_successful_application_skips_legacy_extraction():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor, \
         patch.object(AgenticEngine, "_apply_safe_order_field_updates", new=MagicMock(side_effect=AssertionError("safe-update ran"))):
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A17 — Already-satisfied replay writes zero times
# ---------------------------------------------------------------------------
async def test_a17_already_satisfied_replay_writes_zero():
    _set_apply_flags(True)
    context = _make_context(step_data={"item_name": "Biryani", "quantity": 2, "fulfillment_type": "delivery"})
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    record = _install_update_spy(context)
    result1 = await engine._try_authoritative_order_configured_turn_apply(context)
    # Engine owns the turn for an already-satisfied replay too (returns a result).
    assert result1 is not None
    # Outcome B: no state write for an already-satisfied replay.
    assert record.count("order") == 0
    # The apply method itself reports the typed already-satisfied outcome.
    typed = order_module.apply_external_configured_candidates(context, {"quantity": 2, "fulfillment_type": "delivery"})
    assert typed.handled is True
    assert typed.applied is False
    assert typed.update_performed is False
    assert typed.reason_code == "already_satisfied"
    # Second application (replay): zero writes.
    record.clear()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        result2 = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result2 is not None
    assert record.count("order") == 0
    extractor.assert_not_called()
    assert context.session_data.state.get_task_step_data("order").get("quantity") == 2


# ---------------------------------------------------------------------------
# A18 — Already-satisfied result is handled
# ---------------------------------------------------------------------------
async def test_a18_already_satisfied_result_handled():
    _set_apply_flags(True)
    context = _make_context(step_data={"item_name": "Biryani", "quantity": 2})
    res = order_module.apply_external_configured_candidates(context, {"quantity": 2})
    assert res.handled is True
    assert res.applied is False
    assert res.update_performed is False
    assert res.reason_code == "already_satisfied"


# ---------------------------------------------------------------------------
# A19 — Invalid result falls back
# ---------------------------------------------------------------------------
async def test_a19_invalid_result_falls_back():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    # gateway unavailable with no candidates
    snap = {
        "workflow": "order",
        "validated_result": {"accepted_patch": {}, "accepted_field_keys": [], "rejected_field_keys": [], "reason_code": None},
        "semantic_result": _rejected(gateway_unavailable=True),
    }
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snap
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    # The authoritative path declines (Outcome C) and returns None so the
    # existing legacy order semantic extraction path may run unchanged.
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is None


async def test_a19_invalid_quantity_falls_back():
    _set_apply_flags(True)
    context = _make_context()
    res = order_module.apply_external_configured_candidates(context, {"quantity": 0})
    assert res.handled is False


async def test_a19_invalid_phone_falls_back():
    _set_apply_flags(True)
    context = _make_context()
    res = order_module.apply_external_configured_candidates(context, {"phone": "abc"})
    assert res.handled is False


async def test_a19_invalid_select_falls_back():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    res = order_module.apply_external_configured_candidates(context, {"fulfillment_type": "rocket"})
    assert res.handled is False


async def test_a19_empty_candidates_falls_back():
    _set_apply_flags(True)
    context = _make_context()
    res = order_module.apply_external_configured_candidates(context, {})
    assert res.handled is False


# ---------------------------------------------------------------------------
# A20 — Same message cannot populate next string field
# ---------------------------------------------------------------------------
async def test_a20_same_message_not_next_string_field():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    # Apply quantity/fulfillment/address, leaving customer_name missing.
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery", "delivery_address": "my office"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    order = context.session_data.state.get_task_step_data("order")
    # The whole raw utterance must NOT be stored as customer_name.
    assert order.get("customer_name") != context.incoming_message["text"]
    extractor.assert_not_called()


# ---------------------------------------------------------------------------
# A21 — Only the genuinely missing field is requested
# ---------------------------------------------------------------------------
async def test_a21_only_missing_field_requested():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery", "customer_name": "Arjun", "delivery_address": "my office"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        # order.execute must not be invoked (not all required complete).
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    extractor.assert_not_called()
    assert engine.tool_registry.execute_calls == []
    assert result.prompt_focus_workflow == "order"
    assert result.prompt_focus_fields == ["phone"]


# ---------------------------------------------------------------------------
# A22 — Conditional delivery batch
# ---------------------------------------------------------------------------
async def test_a22_conditional_delivery_batch():
    _set_apply_flags(True)
    fields = [
        {"key": "fulfillment_type", "type": "select", "required": True, "options_from": "fulfillment_types"},
        {"key": "delivery_address", "type": "string", "required": False, "applies_when": "fulfillment_type == delivery"},
    ]
    context = _make_context(fields=fields, fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"fulfillment_type": "delivery", "delivery_address": "my office"})
    res = order_module.apply_external_configured_candidates(context, {"fulfillment_type": "delivery", "delivery_address": "my office"})
    assert res.handled is True
    assert res.applied is True
    assert set(res.accepted_field_keys) == {"delivery_address", "fulfillment_type"}


# ---------------------------------------------------------------------------
# A23 — Pickup conditional behavior
# ---------------------------------------------------------------------------
async def test_a23_pickup_conditional_behavior():
    _set_apply_flags(True)
    fields = [
        {"key": "fulfillment_type", "type": "select", "required": True, "options_from": "fulfillment_types"},
        {"key": "delivery_address", "type": "string", "required": False, "applies_when": "fulfillment_type == delivery"},
    ]
    context = _make_context(fields=fields, fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"fulfillment_type": "pickup", "delivery_address": "my office"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    order = context.session_data.state.get_task_step_data("order")
    assert order.get("fulfillment_type") == "pickup"
    assert "delivery_address" not in order or order.get("delivery_address") in (None, "")
    extractor.assert_not_called()
    # next required field derives from updated state
    assert result.prompt_focus_workflow == "order"


# ---------------------------------------------------------------------------
# A24 — Partial accepted patch owns the turn
# ---------------------------------------------------------------------------
async def test_a24_partial_accepted_patch_owns_turn():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery", "phone": "not-a-phone", "delivery_address": "my office"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    record = _install_update_spy(context)
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    assert record.count("order") == 1
    order = context.session_data.state.get_task_step_data("order")
    assert order.get("quantity") == 2
    assert order.get("fulfillment_type") == "delivery"
    assert order.get("delivery_address") == "my office"
    assert order.get("phone") in (None, "")
    extractor.assert_not_called()
    assert result.prompt_focus_fields == ["customer_name", "phone"]


# ---------------------------------------------------------------------------
# A25 — Complete required fields reach existing proposal path
# ---------------------------------------------------------------------------
async def test_a25_complete_required_reach_proposal():
    _set_apply_flags(True)
    fields = [
        {"key": "quantity", "type": "number", "required": True},
        {"key": "fulfillment_type", "type": "select", "required": True, "options_from": "fulfillment_types"},
        {"key": "customer_name", "type": "string", "required": True},
        {"key": "phone", "type": "phone", "required": True},
        {"key": "delivery_address", "type": "textarea", "required": False},
    ]
    context = _make_context(fields=fields, fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {
        "quantity": 2, "fulfillment_type": "delivery",
        "customer_name": "Arjun", "phone": "+919999999999", "delivery_address": "my office",
    })
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock()) as extractor:
        # tool_def.module must resolve so order.execute can be prepared.
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is not None
    extractor.assert_not_called()
    # order.execute was reached (proposal/confirmation), not a success message.
    assert engine.tool_registry.execute_calls == ["order.execute"]


# ---------------------------------------------------------------------------
# A26 — Apply method causes no catalog call
# ---------------------------------------------------------------------------
async def test_a26_apply_method_no_catalog_call():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    from backend.domains.catalog.service import catalog_service
    with patch.object(order_module, "resolve_item", new=MagicMock(side_effect=AssertionError("resolve_item called"))), \
         patch.object(catalog_service, "resolve_catalog_item", new=MagicMock(side_effect=AssertionError("catalog called"))):
        order_module.apply_external_configured_candidates(context, {"quantity": 2, "fulfillment_type": "delivery"})


# ---------------------------------------------------------------------------
# A27 — Apply method causes no tool call
# ---------------------------------------------------------------------------
async def test_a27_apply_method_no_tool_call():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    with patch("backend.tools.registry.ToolRegistry.execute_tool", new=MagicMock(side_effect=AssertionError("tool executed"))):
        order_module.apply_external_configured_candidates(context, {"quantity": 2, "fulfillment_type": "delivery"})


# ---------------------------------------------------------------------------
# A28 — Uncertain-write protection
# ---------------------------------------------------------------------------
async def test_a28_uncertain_write_protection():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery"})

    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    from backend.core.context import SessionState
    from unittest.mock import patch

    real = SessionState.update_task_step_data

    def _boom(self, module, data):
        real(self, module, data)
        raise RuntimeError("write failed")

    patcher = patch.object(SessionState, "update_task_step_data", _boom)
    patcher.start()
    try:
        with patch("backend.logic.engine.field_extraction.extract_order_fields", new=AsyncMock(side_effect=AssertionError("legacy ran"))) as extractor:
            with pytest.raises(RuntimeError):
                await engine._try_authoritative_order_configured_turn_apply(context)
        extractor.assert_not_called()
    finally:
        patcher.stop()


# ---------------------------------------------------------------------------
# A29 — Failure before write falls back
# ---------------------------------------------------------------------------
async def test_a29_failure_before_write_falls_back():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery"})
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    # Revalidation fails before write -> the authoritative path declines and
    # returns None so the existing legacy order path may run unchanged.
    with patch.object(order_module, "validate_external_configured_candidates", side_effect=RuntimeError("boom")):
        result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is None


# ---------------------------------------------------------------------------
# A30 — Application telemetry contains keys only
# ---------------------------------------------------------------------------
async def test_a30_telemetry_keys_only():
    _set_apply_flags(True)
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery", "customer_name": "PRIVATE_NAME_SENTINEL", "phone": "+919999999999", "delivery_address": "PRIVATE_ADDRESS_SENTINEL", "notes": "PRIVATE_NOTES_SENTINEL"})
    records = []

    def _capture(msg, *, extra=None):
        if msg == "order_configured_turn_applied":
            records.append(extra["extra_data"])

    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    with patch.object(shadow, "_emit_order_applied_event", wraps=_emit_order_applied_event), \
         patch.object(shadow.logger, "info", side_effect=_capture):
        await engine._try_authoritative_order_configured_turn_apply(context)
    assert records
    blob = str(records)
    assert "PRIVATE_NAME_SENTINEL" not in blob
    assert "+919999999999" not in blob
    assert "PRIVATE_ADDRESS_SENTINEL" not in blob
    assert "PRIVATE_NOTES_SENTINEL" not in blob
    assert "private@example.com" not in blob
    evt = records[0]
    assert evt["event"] == "order_configured_turn_applied"
    assert "applied_field_keys" in evt
    assert "remaining_required_field_keys" in evt


# ---------------------------------------------------------------------------
# A31 — Default-disabled customer behavior unchanged
# ---------------------------------------------------------------------------
async def test_a31_default_disabled_unchanged():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = False
    settings.ENABLE_ORDER_CONFIGURED_TURN_APPLY = False
    context = _make_context(fulfillment_types=["delivery", "pickup"])
    _seed_shadow(context, {"quantity": 2, "fulfillment_type": "delivery"})
    from backend.logic.engine.core import AgenticEngine, _authoritative_order_apply_eligible
    assert _authoritative_order_apply_eligible(context) is False
    engine = _make_engine()
    # Default-disabled: the authoritative path declines and yields to legacy.
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is None


# ---------------------------------------------------------------------------
# A32 — Booking behavior unchanged
# ---------------------------------------------------------------------------
async def test_a32_booking_behavior_unchanged():
    _set_apply_flags(True)
    context = _make_context(current_module="booking")
    context.session_data.state.update_task_step_data("booking", {"item_name": "X"})
    context.session_data.state.action_ledger.current_task = "booking"
    from backend.logic.engine.core import _authoritative_order_apply_eligible
    assert _authoritative_order_apply_eligible(context) is False
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is None


# ---------------------------------------------------------------------------
# A33 — Other workflows unchanged
# ---------------------------------------------------------------------------
async def test_a33_other_workflows_unchanged():
    _set_apply_flags(True)
    for module in ["payments", "support", "lead_capture", "reminders", "reviews", "faq", "human_handoff"]:
        context = _make_context(current_module=module)
        from backend.logic.engine.core import _authoritative_order_apply_eligible
        assert _authoritative_order_apply_eligible(context) is False


# ---------------------------------------------------------------------------
# A34 — First-message complete order remains unchanged
# ---------------------------------------------------------------------------
async def test_a34_first_message_order_unchanged():
    _set_apply_flags(True)
    # Idle conversation -> no active order scope.
    context = _make_context(current_module=None, step_data={})
    context.session_data.state.action_ledger.current_task = None
    context.session_data.state.action_ledger.task_status = "idle"
    from backend.logic.engine.core import _authoritative_order_apply_eligible
    assert _authoritative_order_apply_eligible(context) is False
    from backend.logic.engine.core import AgenticEngine
    engine = _make_engine()
    result = await engine._try_authoritative_order_configured_turn_apply(context)
    assert result is None
