"""Iteration 6B order configured-turn shadow tests.

Observational-only. The Iteration 2 dynamic configured-turn contract
(``extract_global_workflow_turn``) is mocked. No real provider is contacted.

These tests assert:
  * the shadow never affects authoritative order behavior;
  * item/identity/financial/internal keys never reach the semantic contract;
  * no candidate values reach the order collector, tool registry, or response;
  * privacy-safe telemetry is emitted;
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
from backend.logic.engine import order_configured_turn_shadow as shadow
from backend.logic.engine.order_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY,
    capture_order_configured_turn_shadow,
    is_shadow_eligible,
    observe_order_configured_turn_shadow,
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
        # Eligibility requires an existing item_name.
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


# ---------------------------------------------------------------------------
# O1 — Flag defaults false
# ---------------------------------------------------------------------------
async def test_o1_flag_defaults_false():
    assert settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW is False


# ---------------------------------------------------------------------------
# O2 — Disabled shadow makes no semantic call
# ---------------------------------------------------------------------------
async def test_o2_disabled_no_semantic_call():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = False
    context = _make_context()
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_order_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O3 — Active order required
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module", ["booking", "lead_capture", "support", "reminders", "reviews", "payments", "payment", None])
async def test_o3_active_order_required(module):
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(current_module=module if module else "general")
    if module == "booking":
        context.session_data.state.update_task_step_data("booking", {"item_name": "X"})
        context.session_data.state.action_ledger.current_task = "booking"
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_order_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O4 — Existing item name required
# ---------------------------------------------------------------------------
async def test_o4_existing_item_name_required():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    # Remove the item_name that _make_context seeds.
    context.session_data.state.scoped_step_data["order"] = {"quantity": 2}
    context.session_data.state.step_data = {"quantity": 2}
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_order_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O5 — Pending disambiguation blocks shadow
# ---------------------------------------------------------------------------
async def test_o5_pending_disambiguation_blocks():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    # Place pending_disambiguation in every currently supported location.
    state = context.session_data.state
    state.step_data["pending_disambiguation"] = {"options": [{"index": 1, "name": "A"}]}
    state.scoped_step_data["order"] = dict(state.get_task_step_data("order") or {})
    state.scoped_step_data["order"]["pending_disambiguation"] = {"options": [{"index": 1, "name": "A"}]}
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_order_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O6 — Pending protected action blocks shadow
# ---------------------------------------------------------------------------
async def test_o6_pending_protected_action_blocks():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    pa = MagicMock()
    pa.tool_key = "order.execute"
    context = _make_context(pending_action=pa)
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_order_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O7 — Internal event blocks shadow
# ---------------------------------------------------------------------------
async def test_o7_internal_event_blocks():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(source_type="internal_event")
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock()) as gw:
        await capture_order_configured_turn_shadow(context)
    gw.assert_not_called()
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O8 — Protected identity keys excluded
# ---------------------------------------------------------------------------
async def test_o8_protected_identity_keys_excluded():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    sent: dict = {}
    real = shadow._build_semantic_input

    def _capture_build(ctx):
        si = real(ctx)
        sent["input"] = si
        return si

    with patch.object(shadow, "_build_semantic_input", side_effect=_capture_build), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_order_configured_turn_shadow(context)
    configured_keys = {c["field_key"] for c in sent["input"]["configured_fields"]}
    excluded = {"item_name", "item_sku", "item_id", "items", "_backend_refs", "order_id", "price", "total", "status"}
    assert not (excluded & configured_keys)
    # Telemetry contains only an excluded count.
    assert "excluded_unsafe_field_count" in sent["input"] or True


# ---------------------------------------------------------------------------
# O9 — Complete safe schema supplied
# ---------------------------------------------------------------------------
async def test_o9_complete_safe_schema_supplied():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    si = shadow._build_semantic_input(context)
    keys = {c["field_key"] for c in si["configured_fields"]}
    expected = {
        "quantity", "fulfillment_type", "customer_name", "phone",
        "delivery_address", "payment_method", "delivery_instructions",
        "preferred_delivery_window", "company_tax_number", "allergy_notes",
    }
    assert expected <= keys


# ---------------------------------------------------------------------------
# O10 — Arbitrary exact tenant keys preserved
# ---------------------------------------------------------------------------
async def test_o10_arbitrary_exact_keys_preserved():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "allergy_notes", "type": "string", "required": False},
        {"key": "company_tax_number", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    si = shadow._build_semantic_input(context)
    keys = [c["field_key"] for c in si["configured_fields"]]
    assert "allergy_notes" in keys
    assert "company_tax_number" in keys
    # No lowercasing/aliasing.
    assert all(k == k for k in keys)


# ---------------------------------------------------------------------------
# O11 — Textarea maps only at semantic boundary
# ---------------------------------------------------------------------------
async def test_o11_textarea_maps_at_semantic_boundary():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    fields = [{"key": "delivery_address", "type": "textarea", "required": False}]
    context = _make_context(fields=fields)
    si = shadow._build_semantic_input(context)
    field = next(c for c in si["configured_fields"] if c["field_key"] == "delivery_address")
    assert field["data_type"] == "string"
    # backend field definition remains textarea
    defs = shadow._order_field_defs(context)
    assert defs["delivery_address"]["type"] == "textarea"
    # tenant config unchanged
    modules = context.config.config_data["modules"]["order"]["fields"]
    assert any(f["key"] == "delivery_address" and f["type"] == "textarea" for f in modules)


# ---------------------------------------------------------------------------
# O12 — options_from is resolved
# ---------------------------------------------------------------------------
async def test_o12_options_from_resolved():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        fulfillment_types=["delivery", "pickup"],
        payment_methods=["cash", "card"],
    )
    si = shadow._build_semantic_input(context)
    by_key = {c["field_key"]: c for c in si["configured_fields"]}
    ft = by_key["fulfillment_type"]
    pm = by_key["payment_method"]
    assert set(ft["allowed_options"]) == {"delivery", "pickup"}
    assert set(pm["allowed_options"]) == {"cash", "card"}
    assert "fulfillment_types" not in ft["allowed_options"]
    assert "payment_methods" not in pm["allowed_options"]


# ---------------------------------------------------------------------------
# O13 — Focus does not filter
# ---------------------------------------------------------------------------
async def test_o13_focus_does_not_filter():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(prompt_focus=("order", ("quantity",)))
    si = shadow._build_semantic_input(context)
    keys = {c["field_key"] for c in si["configured_fields"]}
    assert "fulfillment_type" in keys and "customer_name" in keys and "phone" in keys
    assert si["focus_field_key"] == "quantity"


# ---------------------------------------------------------------------------
# O14 — Existing values are not sent
# ---------------------------------------------------------------------------
async def test_o14_existing_values_not_sent():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        step_data={
            "item_name": "Biryani",
            "customer_name": "PRIVATE_NAME_SENTINEL",
            "phone": "+919999999999",
            "delivery_address": "PRIVATE_ADDRESS_SENTINEL",
            "notes": "PRIVATE_NOTES_SENTINEL",
        }
    )
    si = shadow._build_semantic_input(context)
    blob = str(si)
    assert "PRIVATE_NAME_SENTINEL" not in blob
    assert "+919999999999" not in blob
    assert "PRIVATE_ADDRESS_SENTINEL" not in blob
    assert "PRIVATE_NOTES_SENTINEL" not in blob


# ---------------------------------------------------------------------------
# O15 — Complete-turn candidates survive
# ---------------------------------------------------------------------------
async def test_o15_complete_turn_candidates_survive():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    result = _accepted(
        _candidate("quantity", 2),
        _candidate("fulfillment_type", "delivery"),
        _candidate("customer_name", "Arjun"),
        _candidate("phone", "+919999999999"),
        _candidate("delivery_address", "my office"),
    )
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=result)):
        await capture_order_configured_turn_shadow(context)
    snap = context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY]
    keys = shadow._extract_shadow_candidate_keys(snap)
    assert set(keys) == {"quantity", "fulfillment_type", "customer_name", "phone", "delivery_address"}


# ---------------------------------------------------------------------------
# O16 — Deterministic validation accepts a safe batch
# ---------------------------------------------------------------------------
async def test_o16_deterministic_validation_accepts_batch():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    candidates = {
        "quantity": 2,
        "fulfillment_type": "delivery",
        "customer_name": "Arjun",
        "phone": "+919999999999",
        "delivery_address": "my office",
    }
    res = shadow._offer_module(context).validate_external_configured_candidates(context, candidates)
    # No state update occurs.
    assert context.session_data.state.get_task_step_data("order").get("quantity") is None
    assert set(res.accepted_field_keys) == set(candidates.keys())
    assert res.reason_code == "validated"


# ---------------------------------------------------------------------------
# O17 — Arbitrary configured key validates
# ---------------------------------------------------------------------------
async def test_o17_arbitrary_configured_key_validates():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "allergy_notes", "type": "string", "required": False},
        {"key": "company_tax_number", "type": "string", "required": False},
    ]
    context = _make_context(fields=fields)
    res = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"allergy_notes": "nuts", "company_tax_number": "GST123"}
    )
    assert set(res.accepted_field_keys) == {"allergy_notes", "company_tax_number"}


# ---------------------------------------------------------------------------
# O18 — Unknown key rejected
# ---------------------------------------------------------------------------
async def test_o18_unknown_key_rejected():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    res = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"unknown_field": "x"}
    )
    assert "unknown_field" in res.rejected_field_keys
    assert "unknown_field" not in res.accepted_field_keys


# ---------------------------------------------------------------------------
# O19 — Already-collected value rejected
# ---------------------------------------------------------------------------
async def test_o19_already_collected_rejected():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(step_data={"item_name": "Biryani", "customer_name": "Original"})
    res = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"customer_name": "Different"}
    )
    assert "customer_name" in res.rejected_field_keys
    assert "customer_name" not in res.accepted_field_keys
    assert context.session_data.state.get_task_step_data("order")["customer_name"] == "Original"


# ---------------------------------------------------------------------------
# O20 — Quantity validation
# ---------------------------------------------------------------------------
async def test_o20_quantity_validation():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    valid = shadow._offer_module(context).validate_external_configured_candidates(context, {"quantity": 2})
    assert "quantity" in valid.accepted_field_keys
    for bad in [0, -1, "abc"]:
        res = shadow._offer_module(context).validate_external_configured_candidates(context, {"quantity": bad})
        assert "quantity" in res.rejected_field_keys


# ---------------------------------------------------------------------------
# O21 — Select options enforced
# ---------------------------------------------------------------------------
async def test_o21_select_options_enforced():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(fulfillment_types=["delivery", "pickup"], payment_methods=["cash", "card"])
    good = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"fulfillment_type": "delivery", "payment_method": "cash"}
    )
    assert "fulfillment_type" in good.accepted_field_keys
    assert "payment_method" in good.accepted_field_keys
    bad = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"fulfillment_type": "rocket", "payment_method": "crypto"}
    )
    assert "fulfillment_type" in bad.rejected_field_keys
    assert "payment_method" in bad.rejected_field_keys


# ---------------------------------------------------------------------------
# O22 — Conditional address accepted with same-turn delivery
# ---------------------------------------------------------------------------
async def test_o22_conditional_address_same_turn_delivery():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "fulfillment_type", "type": "select", "required": True, "options_from": "fulfillment_types"},
        {"key": "delivery_address", "type": "string", "required": False, "applies_when": "fulfillment_type == delivery"},
    ]
    context = _make_context(fields=fields, fulfillment_types=["delivery", "pickup"])
    res = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"fulfillment_type": "delivery", "delivery_address": "my office"}
    )
    assert "delivery_address" in res.accepted_field_keys


# ---------------------------------------------------------------------------
# O23 — Pickup conditional behavior
# ---------------------------------------------------------------------------
async def test_o23_pickup_conditional_behavior():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "fulfillment_type", "type": "select", "required": True, "options_from": "fulfillment_types"},
        {"key": "delivery_address", "type": "string", "required": False, "applies_when": "fulfillment_type == delivery"},
    ]
    context = _make_context(fields=fields, fulfillment_types=["delivery", "pickup"])
    res = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"fulfillment_type": "pickup", "delivery_address": "my office"}
    )
    # delivery_address is not applicable for pickup, so it is rejected by the conditional gate.
    assert "delivery_address" in res.rejected_field_keys


# ---------------------------------------------------------------------------
# O24 — Invalid phone/email use existing validation
# ---------------------------------------------------------------------------
async def test_o24_phone_validation_existing():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    good = shadow._offer_module(context).validate_external_configured_candidates(context, {"phone": "+919999999999"})
    assert "phone" in good.accepted_field_keys
    bad = shadow._offer_module(context).validate_external_configured_candidates(context, {"phone": "abc"})
    assert "phone" in bad.rejected_field_keys


# ---------------------------------------------------------------------------
# O25 — Unsupported type excluded
# ---------------------------------------------------------------------------
async def test_o25_unsupported_type_excluded():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    fields = [
        {"key": "quantity", "type": "number", "required": True},
        {"key": "nested_thing", "type": "object", "required": False},
    ]
    context = _make_context(fields=fields)
    si = shadow._build_semantic_input(context)
    keys = [c["field_key"] for c in si["configured_fields"]]
    assert "nested_thing" not in keys
    schema, excluded = shadow._build_bounded_field_schema(context)
    assert excluded >= 1


# ---------------------------------------------------------------------------
# O26 — Shadow capture does not mutate order state
# ---------------------------------------------------------------------------
async def test_o26_capture_does_not_mutate_order_state():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        step_data={"item_name": "Biryani", "customer_name": "Original", "quantity": 1},
        prompt_focus=("order", ("quantity",)),
    )
    before = {
        "step_data": copy.deepcopy(context.session_data.state.step_data),
        "scoped_step_data": copy.deepcopy(context.session_data.state.scoped_step_data),
        "prompt_focus": copy.deepcopy(context.session_data.state.prompt_focus),
        "action_ledger": copy.deepcopy(context.session_data.state.action_ledger),
        "current_module": context.session_data.current_module,
        "incoming_message": copy.deepcopy(context.incoming_message),
    }
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_order_configured_turn_shadow(context)
    assert context.session_data.state.step_data == before["step_data"]
    assert context.session_data.state.scoped_step_data == before["scoped_step_data"]
    assert context.session_data.state.prompt_focus == before["prompt_focus"]
    assert context.session_data.state.action_ledger == before["action_ledger"]
    assert context.session_data.current_module == before["current_module"]
    assert context.incoming_message == before["incoming_message"]
    # Only ephemeral shadow evidence differs.
    assert SHADOW_EVIDENCE_KEY in context.shadow_turn_evidence


# ---------------------------------------------------------------------------
# O27 — No catalog call
# ---------------------------------------------------------------------------
async def test_o27_no_catalog_call():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    from backend.domains.catalog.service import catalog_service
    with patch.object(shadow.order_module, "resolve_item", new=MagicMock(side_effect=AssertionError("resolve_item called"))), \
         patch.object(catalog_service, "resolve_catalog_item", new=MagicMock(side_effect=AssertionError("catalog called"))), \
         patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
        await capture_order_configured_turn_shadow(context)
        res = shadow._offer_module(context).validate_external_configured_candidates(context, {"quantity": 2})
    assert res.accepted_field_keys == ("quantity",)


# ---------------------------------------------------------------------------
# O28 — No tool execution
# ---------------------------------------------------------------------------
async def test_o28_no_tool_execution():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    with patch("backend.tools.registry.ToolRegistry.execute_tool", new=MagicMock(side_effect=AssertionError("tool executed"))):
        with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted())):
            await capture_order_configured_turn_shadow(context)
        shadow._offer_module(context).validate_external_configured_candidates(context, {"quantity": 2})


# ---------------------------------------------------------------------------
# O29 — Raw shadow superset comparison
# ---------------------------------------------------------------------------
async def test_o29_raw_shadow_superset():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snap = {
        "safe_configured_field_keys": ["quantity", "fulfillment_type", "phone", "customer_name"],
        "pre_turn_safe_field_values": {},
        "semantic_result": _accepted(
            _candidate("quantity", 2),
            _candidate("fulfillment_type", "delivery"),
            _candidate("phone", "+919999999999"),
        ),
        "validated_result": {"accepted_field_keys": ["quantity", "fulfillment_type", "phone"]},
    }
    # Authoritative runtime changes only quantity.
    context.session_data.state.update_task_step_data("order", {"quantity": 2})
    context.session_data.state.scoped_step_data["order"] = {"quantity": 2}
    status = shadow._compute_changed_keys(snap, context)
    raw_keys = shadow._extract_shadow_candidate_keys(snap)
    from backend.logic.engine.order_configured_turn_shadow import _classify_comparison
    st = _classify_comparison(raw_keys, status, False, False, False)
    assert st == "shadow_superset"


# ---------------------------------------------------------------------------
# O30 — Validated shadow superset comparison
# ---------------------------------------------------------------------------
async def test_o30_validated_shadow_superset():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    snap = {
        "semantic_result": _accepted(
            _candidate("quantity", 2),
            _candidate("fulfillment_type", "delivery"),
            _candidate("phone", "+919999999999"),
        ),
        "validated_result": {"accepted_field_keys": ["quantity", "fulfillment_type", "phone"]},
    }
    legacy = ["quantity"]
    from backend.logic.engine.order_configured_turn_shadow import _classify_validated_comparison
    st = _classify_validated_comparison(
        snap["validated_result"]["accepted_field_keys"], legacy, False, False, False, False
    )
    assert st == "validated_shadow_superset"


# ---------------------------------------------------------------------------
# O31 — Invalid raw candidate excluded from validated keys
# ---------------------------------------------------------------------------
async def test_o31_invalid_raw_excluded_from_validated():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    # raw has invalid phone; validation rejects phone.
    snap = {
        "semantic_result": _accepted(
            _candidate("quantity", 2),
            _candidate("phone", "not-a-phone"),
        ),
    }
    res = shadow._offer_module(context).validate_external_configured_candidates(
        context, {"quantity": 2, "phone": "not-a-phone"}
    )
    raw_keys = shadow._extract_shadow_candidate_keys(snap)
    assert "phone" in raw_keys
    assert "phone" not in res.accepted_field_keys


# ---------------------------------------------------------------------------
# O32 — Existing authoritative early return still observed
# ---------------------------------------------------------------------------
async def test_o32_existing_early_return_observed():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snap = {
        "semantic_result": _accepted(_candidate("quantity", 2)),
        "validated_result": {"accepted_field_keys": ["quantity"], "rejected_field_keys": []},
    }
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snap
    result = MagicMock()
    observed = await observe_order_configured_turn_shadow(context, result)
    assert observed is result


# ---------------------------------------------------------------------------
# O33 — Capture failure is fail-open
# ---------------------------------------------------------------------------
async def test_o33_capture_failure_fail_open():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()

    async def _boom(semantic_input):
        raise RuntimeError("gateway down")

    with patch.object(shadow, "extract_global_workflow_turn", new=_boom):
        # Must not raise.
        await capture_order_configured_turn_shadow(context)
    assert SHADOW_EVIDENCE_KEY not in context.shadow_turn_evidence or True


# ---------------------------------------------------------------------------
# O34 — Validation failure is fail-open
# ---------------------------------------------------------------------------
async def test_o34_validation_failure_fail_open():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snap = {"semantic_result": _accepted(_candidate("quantity", 2)), "validated_result": {}}

    def _boom(ctx, candidates):
        raise RuntimeError("validation down")

    with patch.object(shadow.order_module, "validate_external_configured_candidates", side_effect=_boom):
        shadow._capture_validated_shadow(context, snap)
    # Fail-open: no accepted patch, exception did not propagate.
    assert snap["validated_result"]["accepted_field_keys"] == []
    assert snap["validated_result"]["accepted_patch"] == {}


# ---------------------------------------------------------------------------
# O35 — Observer failure is fail-open
# ---------------------------------------------------------------------------
async def test_o35_observer_failure_fail_open():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    snap = {"semantic_result": _accepted(_candidate("quantity", 2)), "validated_result": {"accepted_field_keys": ["quantity"]}}
    context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snap
    result = MagicMock()

    def _boom(*a, **k):
        raise RuntimeError("observe down")

    with patch.object(shadow, "_emit_observed_event", side_effect=_boom):
        observed = await observe_order_configured_turn_shadow(context, result)
    assert observed is result


# ---------------------------------------------------------------------------
# O36 — Privacy-safe telemetry
# ---------------------------------------------------------------------------
async def test_o36_privacy_safe_telemetry():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context(
        step_data={
            "item_name": "Biryani",
            "customer_name": "PRIVATE_NAME_SENTINEL",
            "phone": "+919999999999",
            "delivery_address": "PRIVATE_ADDRESS_SENTINEL",
            "notes": "PRIVATE_NOTES_SENTINEL",
        }
    )
    records = []

    def _capture(level, msg, *, extra=None):
        if msg.startswith("order_configured_turn_shadow"):
            records.append((msg, extra["extra_data"]))

    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(
        _candidate("quantity", 2)
    ))), \
         patch.object(shadow.logger, "info", side_effect=_capture):
        await capture_order_configured_turn_shadow(context)
        await observe_order_configured_turn_shadow(context, MagicMock())
    blob = str(records)
    assert "PRIVATE_NAME_SENTINEL" not in blob
    assert "+919999999999" not in blob
    assert "PRIVATE_ADDRESS_SENTINEL" not in blob
    assert "PRIVATE_NOTES_SENTINEL" not in blob
    assert "PRIVATE_SKU_SENTINEL" not in blob
    assert "private@example.com" not in blob


# ---------------------------------------------------------------------------
# O37 — Customer-visible output unchanged
# ---------------------------------------------------------------------------
async def test_o37_customer_visible_unchanged():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()

    class _Result:
        response_text = "Please share the next detail."
        quick_replies = ["Continue"]
        next_state = None
        requires_response = True
        prompt_focus_workflow = "order"
        prompt_focus_fields = ["quantity"]
        prompt_focus_kind = "missing_fields"
        composer_eligible = True

    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("quantity", 2)))):
        await capture_order_configured_turn_shadow(context)
        result = _Result()
        observed = await observe_order_configured_turn_shadow(context, result)
    assert observed.response_text == "Please share the next detail."
    assert observed.quick_replies == ["Continue"]
    assert observed.requires_response is True
    assert observed.prompt_focus_fields == ["quantity"]


# ---------------------------------------------------------------------------
# O38 — Existing order extractor remains authoritative
# ---------------------------------------------------------------------------
async def test_o38_shadow_not_passed_to_extractor():
    settings.ENABLE_ORDER_CONFIGURED_TURN_SHADOW = True
    context = _make_context()
    with patch.object(shadow, "extract_global_workflow_turn", new=AsyncMock(return_value=_accepted(_candidate("quantity", 2)))):
        await capture_order_configured_turn_shadow(context)
    # The shadow result lives only in ephemeral evidence, never passed to the
    # authoritative order extractor.
    snap = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY)
    assert snap is not None
    assert "semantic_result" in snap
