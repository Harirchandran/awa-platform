"""
test_order_workflow_recovery.py
Active Workflow Recovery – Slice 1 tests.

Tests are purely unit-level: no DB, no Redis, no network calls.
All tests use anyio so they work with the existing pytest-anyio setup.

Covered scenarios (A–I as specified):
  A. Active workflow typo resilience (\"2 picku\" continues order)
  B. Active workflow unknown input does not reset state
  C. Tenant-config fulfillment validation (custom values, disabled options, typo normalisation)
  D. Ambiguity before confirmation (disambiguation asked before confirmation)
  E. Numeric disambiguation (\"1\" selects first option, does not search DB)
  F. No UUID/internal ID leakage in disambiguation message or prompt
  G. Confirm executes once (no double-confirmation loop)
  H. Product detail tangent preservation (state preserved, not reset)
  I. Regression: existing inventory / Phase 3B behavior unchanged
"""
from __future__ import annotations

import re
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.core.config import settings
from backend.core.context import (
    ActionLedger,
    ConversationSession,
    ExecutionContext,
    PendingAction,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.domains.orders.tool_handlers import (
    build_pending_disambiguation,
    format_disambiguation_message,
    resolve_disambiguation_selection,
)
from backend.logic.engine.history import format_step_data_text
from backend.logic.engine.core import agentic_engine
from backend.logic.engine.field_extraction import OrderFieldExtraction
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.modules.order_module import OrderModule, order_module
from backend.pipeline.message_router import MessageRouter
from backend.tools.registry import ToolExecutionResult

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _router():
    return MessageRouter()


def _order_config(fulfillment_types=None, items=None):
    """Build a minimal tenant order config dict."""
    return {
        "modules": {
            "order": {
                "enabled": True,
                "currency": "USD",
                "fulfillment_types": fulfillment_types or ["pickup", "delivery"],
                "categories": [
                    {
                        "id": "cat1",
                        "name": "Category",
                        "items": items or [
                            {"id": "item1", "name": "Widget A", "price": 10, "enabled": True},
                        ],
                    }
                ],
            }
        }
    }


def _context(
    *,
    step_data: Optional[Dict[str, Any]] = None,
    scoped_step_data: Optional[Dict[str, Any]] = None,
    current_module: Optional[str] = "order",
    current_task: Optional[str] = "order",
    config_data: Optional[Dict[str, Any]] = None,
    fulfillment_types: Optional[List[str]] = None,
    items: Optional[List[Dict]] = None,
):
    tenant_id = uuid4()
    cfg = config_data or _order_config(
        fulfillment_types=fulfillment_types, items=items
    )
    sd = step_data or {}
    ssd = scoped_step_data or ({current_module or "order": dict(sd)} if sd else {})
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=sd,
        scoped_step_data=ssd,
    )
    if current_task:
        state.action_ledger.current_task = current_task
    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id, name="TestBiz", plan_code="enterprise", status="active"
        ),
        user=User(id=uuid4(), phone_number="+10000000000", name="Test User"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=current_module,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=cfg),
        incoming_message={"text": "test", "channel_type": "awa_chat"},
    )


# ---------------------------------------------------------------------------
# A. Active workflow typo resilience
# ---------------------------------------------------------------------------


def test_typo_message_routes_to_active_task():
    """'2 picku' during an active order workflow must route to the active task."""
    router = _router()
    decision = router.route_message(
        "2 picku",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    assert decision.intent == "order"
    assert decision.route_type == "active_task"
    assert decision.should_clear_active_task is False


def test_typo_message_does_not_reset_to_greeting():
    """Typo input during active order must NOT produce intent='greeting'."""
    router = _router()
    decision = router.route_message(
        "2 picku",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    assert decision.intent != "greeting"


def test_partial_word_normalises_to_active_task_fulfillment():
    """'picku' should fuzzy-normalise to 'pickup' via resolve_fulfillment_type."""
    module = OrderModule()
    result = module.resolve_fulfillment_type("picku", _order_config())
    assert result == "pickup"


def test_partial_word_delivery_normalises():
    """'delive' should normalise to 'delivery'."""
    module = OrderModule()
    result = module.resolve_fulfillment_type("delive", _order_config())
    assert result == "delivery"


# ---------------------------------------------------------------------------
# B. Active workflow unknown input does not reset
# ---------------------------------------------------------------------------


def test_unknown_message_during_active_order_does_not_clear_task():
    """Sending 'xyzqrs' during active order keeps the order task alive."""
    router = _router()
    decision = router.route_message(
        "xyzqrs",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    assert decision.should_clear_active_task is False
    assert decision.intent == "order"


def test_unknown_message_no_active_task_returns_unknown():
    """Without an active task, an unrecognised message returns 'unknown'."""
    router = _router()
    decision = router.route_message("xyzqrs", _order_config())
    assert decision.intent == "unknown"


# ---------------------------------------------------------------------------
# C. Tenant-config fulfillment validation
# ---------------------------------------------------------------------------


def test_validate_configured_fulfillment_exact_match():
    """Exact configured value passes validation."""
    module = OrderModule()
    cfg = _order_config(fulfillment_types=["express", "locker"])
    field = {"key": "fulfillment_type", "type": "string"}
    result = module.validate_input(field, "express", cfg)
    assert result.is_valid is True


def test_validate_configured_fulfillment_typo_normalises():
    """Near-typo of a configured option normalises and passes validation."""
    module = OrderModule()
    cfg = _order_config(fulfillment_types=["pickup", "express"])
    field = {"key": "fulfillment_type", "type": "string"}
    # 'picku' should fuzzy-match 'pickup'
    result = module.validate_input(field, "picku", cfg)
    assert result.is_valid is True


def test_validate_unconfigured_fulfillment_fails():
    """An option not in the configured list fails, even if English word."""
    module = OrderModule()
    # Config only has 'express' and 'locker'; 'delivery' must NOT silently pass
    cfg = _order_config(fulfillment_types=["express", "locker"])
    field = {"key": "fulfillment_type", "type": "string"}
    result = module.validate_input(field, "delivery", cfg)
    assert result.is_valid is False
    # Error message must mention configured options
    assert "express" in result.error_message or "locker" in result.error_message


def test_validate_empty_fulfillment_fails():
    """Empty string fails regardless of config."""
    module = OrderModule()
    field = {"key": "fulfillment_type", "type": "string"}
    result = module.validate_input(field, "", _order_config())
    assert result.is_valid is False


def test_missing_config_uses_default_pickup_delivery():
    """When fulfillment_types is absent, defaults to pickup/delivery."""
    module = OrderModule()
    cfg = {"modules": {"order": {"enabled": True, "categories": []}}}
    options = module._configured_fulfillment_options(cfg)
    assert "pickup" in options
    assert "delivery" in options


# ---------------------------------------------------------------------------
# D. Ambiguity before confirmation
# ---------------------------------------------------------------------------


def test_build_pending_disambiguation_has_no_uuids_in_options():
    """Public options in pending_disambiguation must not contain UUIDs."""
    item_id_1 = str(uuid4())
    item_id_2 = str(uuid4())
    matches = [
        {"id": item_id_1, "name": "Widget Blue", "price": 20, "sku": "WB-01"},
        {"id": item_id_2, "name": "Widget Red", "price": 25, "sku": "WR-01"},
    ]
    pending = build_pending_disambiguation(matches)
    options_text = str(pending["options"])
    assert not UUID_PATTERN.search(options_text), "Options must not contain UUIDs"


def test_build_pending_disambiguation_backend_refs_present():
    """Backend refs must be present but only in _backend_refs."""
    item_id = str(uuid4())
    matches = [{"id": item_id, "name": "Widget", "price": 10}]
    pending = build_pending_disambiguation(matches)
    assert "_backend_refs" in pending
    assert pending["_backend_refs"][0]["item"]["id"] == item_id


def test_disambiguation_message_has_no_uuids():
    """format_disambiguation_message output must not contain UUIDs."""
    item_id = str(uuid4())
    matches = [
        {"id": item_id, "name": "Alpha", "price": 5},
        {"id": str(uuid4()), "name": "Beta", "price": 10},
    ]
    pending = build_pending_disambiguation(matches)
    msg = format_disambiguation_message(pending)
    assert not UUID_PATTERN.search(msg)
    assert "Alpha" in msg
    assert "Beta" in msg
    assert "1." in msg
    assert "2." in msg


async def test_execute_order_tool_with_ambiguous_item_stores_disambiguation(monkeypatch):
    """When item resolution is ambiguous, execute_order_tool stores pending_disambiguation."""
    from backend.domains.orders import tool_handlers

    item_id_1 = str(uuid4())
    item_id_2 = str(uuid4())

    async def fake_list_items(tenant_id, **kwargs):
        return [
            {"id": item_id_1, "name": "Cake Chocolate", "price": 30},
            {"id": item_id_2, "name": "Cake Vanilla", "price": 25},
        ]

    module = OrderModule()
    catalog_service = SimpleNamespace(
        list_items=AsyncMock(side_effect=fake_list_items),
        resolve_catalog_item=AsyncMock(return_value=None),
    )
    order_service = SimpleNamespace(
        create_order=AsyncMock(return_value={"id": str(uuid4()), "payment_status": "pending"})
    )
    ctx = _context(
        step_data={"item_name": "Cake", "quantity": 1, "fulfillment_type": "pickup"},
        config_data=_order_config(
            items=[
                {"id": item_id_1, "name": "Cake Chocolate", "price": 30, "enabled": True},
                {"id": item_id_2, "name": "Cake Vanilla", "price": 25, "enabled": True},
            ]
        ),
    )
    logger = MagicMock()
    result = await tool_handlers.execute_order_tool(ctx, module, order_service, catalog_service, logger)

    assert result.success is False
    # Must ask clarification, not generic failure
    assert "1." in result.user_message or "2." in result.user_message
    assert not UUID_PATTERN.search(result.user_message)
    # pending_disambiguation must be set in step_data
    assert "pending_disambiguation" in ctx.session_data.state.step_data
    pending = ctx.session_data.state.step_data["pending_disambiguation"]
    assert "options" in pending
    assert "_backend_refs" in pending


async def test_execute_order_tool_blocked_when_disambiguation_pending(monkeypatch):
    """execute_order_tool re-emits clarification when pending_disambiguation is still set."""
    from backend.domains.orders import tool_handlers

    pending = build_pending_disambiguation(
        [
            {"id": str(uuid4()), "name": "Item A", "price": 10},
            {"id": str(uuid4()), "name": "Item B", "price": 15},
        ]
    )
    module = OrderModule()
    catalog_service = SimpleNamespace(
        list_items=AsyncMock(return_value=[]),
        resolve_catalog_item=AsyncMock(return_value=None),
    )
    order_service = SimpleNamespace(create_order=AsyncMock())
    ctx = _context(
        step_data={
            "item_name": "Item",
            "quantity": 1,
            "fulfillment_type": "pickup",
            "pending_disambiguation": pending,
        },
    )
    logger = MagicMock()
    result = await tool_handlers.execute_order_tool(ctx, module, order_service, catalog_service, logger)

    assert result.success is False
    # Must contain the options list
    assert "Item A" in result.user_message or "1." in result.user_message
    # order_service.create_order must NOT have been called
    order_service.create_order.assert_not_called()


# ---------------------------------------------------------------------------
# E. Numeric disambiguation
# ---------------------------------------------------------------------------


def test_numeric_selection_resolves_to_correct_item():
    """'1' selects the first disambiguation option."""
    item_a = {"id": str(uuid4()), "name": "Widget A", "price": 10}
    item_b = {"id": str(uuid4()), "name": "Widget B", "price": 20}
    pending = build_pending_disambiguation([item_a, item_b])
    resolved = resolve_disambiguation_selection("1", pending)
    assert resolved is not None
    assert resolved["name"] == "Widget A"


def test_numeric_selection_second_option():
    """'2' selects the second disambiguation option."""
    item_a = {"id": str(uuid4()), "name": "Red", "price": 5}
    item_b = {"id": str(uuid4()), "name": "Blue", "price": 8}
    pending = build_pending_disambiguation([item_a, item_b])
    resolved = resolve_disambiguation_selection("2", pending)
    assert resolved is not None
    assert resolved["name"] == "Blue"


def test_out_of_range_numeric_returns_none():
    """'99' with only 2 options should return None, not silently pick wrong item."""
    matches = [
        {"id": str(uuid4()), "name": "Alpha", "price": 5},
        {"id": str(uuid4()), "name": "Beta", "price": 10},
    ]
    pending = build_pending_disambiguation(matches)
    resolved = resolve_disambiguation_selection("99", pending)
    assert resolved is None


def test_label_selection_resolves_by_name():
    """Exact name reply resolves the correct disambiguation option."""
    item_a = {"id": str(uuid4()), "name": "Galaxy Burger", "price": 12}
    item_b = {"id": str(uuid4()), "name": "Solar Burger", "price": 14}
    pending = build_pending_disambiguation([item_a, item_b])
    resolved = resolve_disambiguation_selection("Solar Burger", pending)
    assert resolved is not None
    assert resolved["name"] == "Solar Burger"


def test_disambiguation_clears_after_prefill_resolution():
    """After selecting an option via prefill_from_message, pending_disambiguation is cleared."""
    item_a = {"id": str(uuid4()), "name": "Widget A", "price": 10}
    item_b = {"id": str(uuid4()), "name": "Widget B", "price": 20}
    pending = build_pending_disambiguation([item_a, item_b])

    ctx = _context(
        step_data={"pending_disambiguation": pending},
        scoped_step_data={"order": {"pending_disambiguation": pending}},
    )
    module = OrderModule()
    # Customer sends "1" to select first option
    module.prefill_from_message("1", ctx)

    assert "pending_disambiguation" not in ctx.session_data.state.step_data
    assert ctx.session_data.state.step_data.get("item_name") == "Widget A"


def test_router_routes_numeric_to_active_task_when_disambiguation_pending():
    """When has_pending_disambiguation=True, numeric message '1' routes to active task."""
    router = _router()
    decision = router.route_message(
        "1",
        _order_config(),
        current_task="order",
        current_module="order",
        has_pending_disambiguation=True,
    )
    assert decision.intent == "order"
    assert decision.route_type == "active_task"
    assert decision.reason == "active_task_disambiguation_selection"


# ---------------------------------------------------------------------------
# F. No UUID/internal ID leakage
# ---------------------------------------------------------------------------


def test_format_step_data_text_hides_backend_refs():
    """format_step_data_text must never expose _backend_refs in its output."""
    item_id = str(uuid4())
    pending = build_pending_disambiguation(
        [{"id": item_id, "name": "Secret Item", "price": 5}]
    )
    step_data = {"pending_disambiguation": pending}
    rendered = format_step_data_text(step_data)
    assert item_id not in rendered
    assert "_backend_refs" not in rendered
    assert "Secret Item" in rendered


def test_format_step_data_text_pending_disambiguation_shows_names():
    """format_step_data_text renders item names and indices from options."""
    matches = [
        {"id": str(uuid4()), "name": "Alpha", "price": 5},
        {"id": str(uuid4()), "name": "Beta", "price": 10},
    ]
    pending = build_pending_disambiguation(matches)
    rendered = format_step_data_text({"pending_disambiguation": pending})
    assert "Alpha" in rendered
    assert "Beta" in rendered
    assert "1" in rendered
    assert "2" in rendered


def test_disambiguation_message_no_item_id_leakage():
    """format_disambiguation_message must not contain any UUID patterns."""
    uuid_val = str(uuid4())
    matches = [
        {"id": uuid_val, "name": "Red Widget", "price": 8},
        {"id": str(uuid4()), "name": "Blue Widget", "price": 12},
    ]
    pending = build_pending_disambiguation(matches)
    msg = format_disambiguation_message(pending)
    assert not UUID_PATTERN.search(msg)


def test_sku_uuid_excluded_from_public_options():
    """If an item's SKU is actually a UUID, it must NOT appear in public options."""
    sku_as_uuid = str(uuid4())
    matches = [{"id": str(uuid4()), "name": "Test Item", "sku": sku_as_uuid, "price": 5}]
    pending = build_pending_disambiguation(matches)
    options_str = str(pending["options"])
    assert sku_as_uuid not in options_str


# ---------------------------------------------------------------------------
# G. Confirm executes once (no ambiguous confirmation loop)
# ---------------------------------------------------------------------------


async def test_confirmation_blocked_when_disambiguation_pending():
    """execute_order_tool returns failure (not success) when disambiguation is pending."""
    from backend.domains.orders import tool_handlers

    pending = build_pending_disambiguation(
        [
            {"id": str(uuid4()), "name": "A", "price": 5},
            {"id": str(uuid4()), "name": "B", "price": 10},
        ]
    )
    module = OrderModule()
    catalog_service = SimpleNamespace(
        list_items=AsyncMock(return_value=[]),
        resolve_catalog_item=AsyncMock(return_value=None),
    )
    order_service = SimpleNamespace(create_order=AsyncMock())
    ctx = _context(
        step_data={"pending_disambiguation": pending, "fulfillment_type": "pickup"},
    )
    logger = MagicMock()

    result = await tool_handlers.execute_order_tool(ctx, module, order_service, catalog_service, logger)
    assert result.success is False
    assert "item_name" in result.validation_errors
    order_service.create_order.assert_not_called()


# ---------------------------------------------------------------------------
# H. Product detail tangent preservation
# ---------------------------------------------------------------------------


def test_active_order_faq_tangent_does_not_clear_task():
    """FAQ question during active order routes as tangent, not clearing task."""
    router = _router()
    decision = router.route_message(
        "what are your prices",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    # Should be either active_task or faq tangent — NOT clearing task
    assert decision.should_clear_active_task is False


def test_detail_question_during_active_order_does_not_reset():
    """'how much does it cost' during active order preserves order state."""
    router = _router()
    decision = router.route_message(
        "how much does it cost",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    assert decision.should_clear_active_task is False


# ---------------------------------------------------------------------------
# I. Regression: Fulfillment module resolve still returns correct canonical values
# ---------------------------------------------------------------------------


def test_resolve_fulfillment_pickup_exact():
    module = OrderModule()
    assert module.resolve_fulfillment_type("pickup", _order_config()) == "pickup"


def test_resolve_fulfillment_delivery_exact():
    module = OrderModule()
    assert module.resolve_fulfillment_type("delivery", _order_config()) == "delivery"


def test_resolve_fulfillment_unknown_returns_none():
    module = OrderModule()
    result = module.resolve_fulfillment_type("teleport", _order_config())
    assert result is None


def test_configured_fulfillment_options_reads_from_config():
    module = OrderModule()
    cfg = _order_config(fulfillment_types=["express", "locker", "curbside"])
    opts = module._configured_fulfillment_options(cfg)
    assert "express" in opts
    assert "locker" in opts
    assert "curbside" in opts


def test_router_explicit_cancel_clears_task():
    """Explicit 'cancel' still clears the active task."""
    router = _router()
    decision = router.route_message(
        "cancel",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    assert decision.intent == "cancel"
    assert decision.should_clear_active_task is True


def test_router_thanks_does_not_clear_active_task():
    """'thanks' does not clear active task (is_tangent=True instead)."""
    router = _router()
    decision = router.route_message(
        "thanks",
        _order_config(),
        current_task="order",
        current_module="order",
    )
    assert decision.intent == "thanks"
    # should_clear_active_task is False; is_tangent carries the tangent flag
    assert decision.should_clear_active_task is False


def test_disambiguation_numeric_message_not_routed_as_new_order():
    """'1' with pending_disambiguation must NOT be routed as a fresh order intent."""
    router = _router()
    decision = router.route_message(
        "1",
        _order_config(),
        current_task="order",
        current_module="order",
        has_pending_disambiguation=True,
    )
    # Must be active_task, not 'order' with should_clear_active_task
    assert decision.should_clear_active_task is False
    assert decision.route_type == "active_task"


# ---------------------------------------------------------------------------
# J. Slice 2: semantic pending-action decision handling
# ---------------------------------------------------------------------------


async def _install_pending_action_doubles(monkeypatch, context, decision):
    async def fake_check_message_allowed(ctx):
        return None

    async def fake_hydrate(ctx):
        return None

    async def fake_decide(ctx):
        return decision

    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", fake_check_message_allowed)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", fake_hydrate)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)


def _prepare_order_pending_action(context, args=None):
    args = args or {
        "item_name": "Widget A",
        "quantity": 1,
        "fulfillment_type": "pickup",
        "customer_name": "Test User",
        "phone": "+10000000000",
    }
    context.session_data.state.update_task_step_data("order", args)
    context.session_data.state.prepare_pending_action(
        tool_key="order.execute",
        args=args,
        summary="Please review this order request.",
        requires_confirmation=True,
        requires_verified_customer=False,
        channel_type="awa_chat",
    )
    return args


async def test_semantic_pending_order_confirm_executes_once(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "black-box customer confirmation"
    expected_args = _prepare_order_pending_action(context)
    pending_action = context.session_data.state.action_ledger.pending_action
    assert pending_action is not None
    expected_nonce = pending_action.action_nonce
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="confirm", confidence=0.98),
    )
    decide_pending_action = AsyncMock(
        return_value=PendingActionDecision(action="confirm", confidence=0.98)
    )
    monkeypatch.setattr(
        "backend.logic.engine.core.decide_pending_action",
        decide_pending_action,
    )
    calls = {"count": 0}

    async def fake_execute_tool(tool_key, ctx):
        calls["count"] += 1
        dispatched = ctx.session_data.state.action_ledger.pending_action
        assert dispatched is not None
        assert ctx.session_data.state.action_ledger.task_status == "executing"
        assert dispatched.action_nonce == expected_nonce
        assert dispatched.args == expected_args
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="Order placed.",
            created_resource_type="order",
            created_resource_id="order-public-ref",
        )

    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute_tool)

    result = await agentic_engine.execute_step(context)

    decide_pending_action.assert_awaited_once_with(context)
    assert result.response_text == "Order placed."
    assert calls["count"] == 1
    ledger = context.session_data.state.action_ledger
    assert ledger.task_status == "completed"
    assert ledger.pending_action is None
    assert ledger.last_tool_result is not None
    assert ledger.last_tool_result.success is True
    assert ledger.last_tool_result.created_resource_type == "order"
    assert ledger.last_tool_result.created_resource_id == "order-public-ref"
    assert ledger.last_tool_result.action_nonce is not None
    assert ledger.last_tool_result.args_hash is not None


async def test_repeated_confirm_after_execution_does_not_reexecute(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "black-box customer confirmation"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="confirm", confidence=0.99),
    )
    calls = {"count": 0}

    async def fake_execute_tool(tool_key, ctx):
        calls["count"] += 1
        return ToolExecutionResult(tool_key=tool_key, success=True, user_message="Order placed.")

    class QuietLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            from langchain_core.messages import AIMessage
            system_content = ""
            for msg in messages:
                if getattr(msg, "type", "") == "system":
                    system_content = str(getattr(msg, "content", ""))
                    break
            if "extract customer meaning" in system_content:
                return AIMessage(
                    content=json.dumps(
                        {
                            "workflow": "order",
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
            return AIMessage(content="There is no pending action to confirm.")

    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute_tool)
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: QuietLLM())
    monkeypatch.setattr(
        "backend.logic.engine.history.db_client.list_messages",
        AsyncMock(return_value={"items": [], "next_cursor": None}),
    )
    monkeypatch.setattr(
        "backend.logic.engine.core.db_client.get_review_settings",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "backend.knowledge.orchestrator.knowledge_orchestrator.build_saved_knowledge_context",
        AsyncMock(return_value=SimpleNamespace(context_text="No retrieved knowledge context loaded.")),
    )

    first = await agentic_engine.execute_step(context)
    second = await agentic_engine.execute_step(context)

    assert first.response_text == "Order placed."
    assert "no pending action" in second.response_text.lower()
    assert calls["count"] == 1


async def test_semantic_cancel_clears_pending_action_safely(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    context.incoming_message["text"] = "black-box customer cancellation"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="cancel", confidence=0.95),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    assert "cancelled" in result.response_text
    assert context.session_data.state.action_ledger.pending_action is None
    assert context.session_data.state.action_ledger.current_task is None
    execute_tool.assert_not_called()


async def test_semantic_change_preserves_workflow_and_asks_for_correction(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    context.incoming_message["text"] = "black-box customer correction"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="change", confidence=0.9),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    assert "change" in result.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action is not None
    assert context.session_data.state.action_ledger.current_task == "order"
    execute_tool.assert_not_called()


async def test_semantic_tangent_preserves_pending_action(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "black-box tangent"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="tangent", confidence=0.88),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    assert "please review this order request" in result.response_text.lower()
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]
    assert context.session_data.state.action_ledger.pending_action is not None
    execute_tool.assert_not_called()


async def test_low_confidence_semantic_decision_does_not_execute(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "black-box ambiguous reply"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="confirm", confidence=0.2),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    assert "confirm" in result.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action is not None
    execute_tool.assert_not_called()


async def test_semantic_decision_failure_does_not_execute(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "black-box provider failure input"
    _prepare_order_pending_action(context)

    async def fake_check_message_allowed(ctx):
        return None

    async def fake_hydrate(ctx):
        return None

    async def failing_decide(ctx):
        raise ValueError("invalid semantic decision")

    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", fake_check_message_allowed)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", fake_hydrate)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", failing_decide)
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    assert "confirm" in result.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action is not None
    execute_tool.assert_not_called()


async def test_confirmation_no_longer_depends_on_english_word_list(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "Confirm"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="unknown", confidence=0.1),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    result = await agentic_engine.execute_step(context)

    assert "confirm" in result.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action is not None
    execute_tool.assert_not_called()


async def test_non_english_confirmation_uses_semantic_decision_stub(monkeypatch):
    context = _context()
    context.incoming_message["text"] = "black-box non-English affirmative"
    _prepare_order_pending_action(context)
    await _install_pending_action_doubles(
        monkeypatch,
        context,
        PendingActionDecision(action="confirm", confidence=0.96),
    )
    calls = {"count": 0}

    async def fake_execute_tool(tool_key, ctx):
        calls["count"] += 1
        return ToolExecutionResult(tool_key=tool_key, success=True, user_message="Order placed.")

    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute_tool)

    result = await agentic_engine.execute_step(context)

    assert result.response_text == "Order placed."
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# K. Slice 3A: active order semantic field extraction
# ---------------------------------------------------------------------------


def _order_route(context):
    return _router().route_message(
        context.incoming_message.get("text", ""),
        context.config.config_data,
        current_task=context.session_data.state.action_ledger.current_task,
        current_module=context.session_data.current_module,
    )


def _semantic_order_extraction(**kwargs):
    defaults = {
        "detected_language": "black-box",
        "workflow": "order",
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": {},
        "field_confidence": {},
        "clarification_needed": False,
    }
    defaults.update(kwargs)
    return OrderFieldExtraction(**defaults)


async def _install_order_extraction_stub(monkeypatch, extraction=None, exc: Exception | None = None):
    async def fake_extract(ctx):
        if exc:
            raise exc
        return extraction

    monkeypatch.setattr("backend.logic.engine.core.extract_order_fields", fake_extract)


async def test_active_order_semantic_stub_updates_quantity_and_fulfillment(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    context.incoming_message["text"] = "black-box mixed-language quantity fulfillment"
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            field_updates={"quantity": 2, "fulfillment_type": "delivery"},
            field_confidence={"quantity": 0.96, "fulfillment_type": 0.94},
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is None
    step_data = context.session_data.state.get_task_step_data("order")
    assert step_data["quantity"] == 2
    assert step_data["fulfillment_type"] == "delivery"


async def test_complete_validated_order_is_prepared_before_confirmation_prose(monkeypatch):
    context = _context(step_data={
        "item_name": "Widget A",
        "quantity": 2,
        "fulfillment_type": "pickup",
        "customer_name": "Asha",
        "phone": "+10000000000",
    })
    context.config.config_data["modules"]["order"]["fields"] = [
        {"key": "item_id", "type": "string", "required": True},
        {"key": "quantity", "type": "number", "required": True},
        {"key": "fulfillment_type", "type": "select", "required": True},
        {"key": "customer_name", "type": "string", "required": True},
        {"key": "phone", "type": "phone", "required": True},
    ]
    context.session_data.state.action_ledger.task_status = "collecting"
    await _install_order_extraction_stub(monkeypatch, _semantic_order_extraction())

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    pending = context.session_data.state.action_ledger.pending_action
    assert pending is not None
    assert pending.tool_key == "order.execute"
    assert pending.requires_confirmation is True
    assert pending.requires_verified_customer is True
    assert context.session_data.state.action_ledger.last_tool_result.success is False
    assert context.session_data.state.action_ledger.last_tool_result.denied_reason == "confirmation_required:order"
    assert result is not None
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]

    context.session_data.state.action_ledger.pending_action = None
    context.session_data.state.action_ledger.task_status = "completed"
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    repeated = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert repeated is None
    execute_tool.assert_not_awaited()


async def test_rich_order_cart_is_preserved_in_the_single_pending_action(monkeypatch):
    context = _context(step_data={})
    context.session_data.state.action_ledger.task_status = "collecting"
    context.incoming_message["text"] = "two products in one customer turn"
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            field_updates={
                "items": [
                    {"item_name": "Widget A", "quantity": 2},
                    {"item_name": "Widget B", "quantity": 1},
                ],
                "fulfillment_type": "pickup",
                "customer_name": "Asha",
                "phone": "+10000000000",
            },
            field_confidence={
                "items": 0.96,
                "fulfillment_type": 0.96,
                "customer_name": 0.96,
                "phone": 0.96,
            },
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    pending = context.session_data.state.action_ledger.pending_action
    assert result is not None and pending is not None
    assert pending.tool_key == "order.execute"
    assert pending.args["items"] == [
        {"item_name": "Widget A", "quantity": 2},
        {"item_name": "Widget B", "quantity": 1},
    ]
    assert pending.args["item_name"] == "Widget A"
    assert "Widget A" in pending.summary and "Widget B" in pending.summary
    assert "quantity': 2" in pending.summary and "quantity': 1" in pending.summary
    assert "Widget A: quantity 2" in result.response_text
    assert "Widget B: quantity 1" in result.response_text
    assert context.session_data.state.action_ledger.last_tool_result.denied_reason == "confirmation_required:order"


async def test_non_english_item_phrase_semantic_stub_uses_backend_resolver(monkeypatch):
    context = _context(step_data={"quantity": 1, "fulfillment_type": "pickup"})
    context.incoming_message["text"] = "black-box non-English item phrase"
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            field_updates={"item_name": "Widget A"},
            field_confidence={"item_name": 0.93},
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is None
    step_data = context.session_data.state.get_task_step_data("order")
    assert step_data["item_name"] == "Widget A"
    assert order_module.resolve_item(step_data["item_name"], context.config.config_data)["name"] == "Widget A"


async def test_typo_active_order_low_confidence_does_not_reset_to_greeting(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    context.incoming_message["text"] = "black-box typo fragment"
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(confidence=0.2, field_updates={"quantity": 2}),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is not None
    assert "quantity" in result.response_text.lower() or "detail" in result.response_text.lower()
    assert context.session_data.state.action_ledger.current_task == "order"
    assert context.session_data.current_module == "order"
    assert context.session_data.state.get_task_step_data("order")["item_name"] == "Widget A"


async def test_low_confidence_order_extraction_preserves_state_and_clarifies(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(confidence=0.1, field_updates={"fulfillment_type": "delivery"}),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("order") == {"item_name": "Widget A"}


async def test_provider_failure_order_extraction_preserves_state_and_clarifies(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    await _install_order_extraction_stub(monkeypatch, exc=ValueError("invalid semantic json"))

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("order") == {"item_name": "Widget A"}


async def test_extracted_unconfigured_fulfillment_is_rejected(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"}, fulfillment_types=["pickup"])
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            field_updates={"fulfillment_type": "not-configured"},
            field_confidence={"fulfillment_type": 0.95},
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is not None
    assert "pickup" in result.response_text
    assert "fulfillment_type" not in context.session_data.state.get_task_step_data("order")


async def test_semantic_extraction_cannot_activate_disabled_order_module(monkeypatch):
    cfg = _order_config()
    cfg["modules"]["order"]["enabled"] = False
    context = _context(step_data={}, config_data=cfg, current_module="order", current_task="order")
    called = {"value": False}

    async def fake_extract(ctx):
        called["value"] = True
        return _semantic_order_extraction(field_updates={"quantity": 2})

    monkeypatch.setattr("backend.logic.engine.core.extract_order_fields", fake_extract)

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is None
    assert called["value"] is False
    assert context.session_data.state.get_task_step_data("order") == {}


async def test_semantic_extraction_rejects_unknown_unsafe_fields(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            field_updates={"quantity": 2, "tenant_id": "not-allowed"},
            field_confidence={"quantity": 0.95, "tenant_id": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("order") == {"item_name": "Widget A"}


async def test_semantic_tangent_does_not_overwrite_stable_item(monkeypatch):
    context = _context(step_data={"item_name": "Widget A", "quantity": 1})
    context.incoming_message["text"] = "black-box tangent"
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            workflow_action="tangent",
            field_updates={"item_name": "Other Item"},
            field_confidence={"item_name": 0.99},
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("order")["item_name"] == "Widget A"


async def test_quick_reply_text_uses_semantic_order_stub(monkeypatch):
    context = _context(step_data={"item_name": "Widget A"})
    context.incoming_message["text"] = "black-box quick reply label"
    await _install_order_extraction_stub(
        monkeypatch,
        _semantic_order_extraction(
            field_updates={"fulfillment_type": "pickup"},
            field_confidence={"fulfillment_type": 0.98},
        ),
    )

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))

    assert result is None
    assert context.session_data.state.get_task_step_data("order")["fulfillment_type"] == "pickup"


async def test_pending_disambiguation_bypasses_semantic_extraction_and_keeps_uuid_hidden(monkeypatch):
    item_id = str(uuid4())
    pending = build_pending_disambiguation([
        {"id": item_id, "name": "Widget A", "price": 10},
    ])
    context = _context(step_data={"pending_disambiguation": pending})
    called = {"value": False}

    async def fake_extract(ctx):
        called["value"] = True
        return _semantic_order_extraction(field_updates={"item_name": "Widget A"})

    monkeypatch.setattr("backend.logic.engine.core.extract_order_fields", fake_extract)

    result = await agentic_engine._handle_semantic_order_field_extraction(context, _order_route(context))
    order_module.prefill_from_message("1", context)
    message = format_disambiguation_message(pending)

    assert result is None
    assert called["value"] is False
    assert context.session_data.state.get_task_step_data("order")["item_name"] == "Widget A"
    assert item_id not in message
    assert not UUID_PATTERN.search(message)


async def test_order_field_extraction_prompt_omits_internal_ids(monkeypatch):
    from langchain_core.messages import AIMessage
    from backend.logic.engine import field_extraction

    internal_id = str(uuid4())
    cfg = _order_config(
        items=[
            {
                "id": internal_id,
                "sku": internal_id,
                "name": f"Widget {internal_id}",
                "price": 10,
                "enabled": True,
            }
        ]
    )
    pending = build_pending_disambiguation([
        {"id": internal_id, "sku": internal_id, "name": f"Widget {internal_id}", "price": 10},
    ])
    context = _context(
        step_data={
            "item_name": internal_id,
            "pending_disambiguation": pending,
        },
        config_data=cfg,
    )
    captured = {}

    class FakeLLM:
        async def ainvoke(self, messages):
            captured["payload"] = str(messages[-1].content)
            return AIMessage(
                content=json.dumps(
                    {
                        "workflow": "order",
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

    await field_extraction.extract_order_fields(context)

    payload = captured["payload"]
    assert internal_id not in payload
    assert "_backend_refs" not in payload
    assert "item_id" not in payload
    assert "tenant_id" not in payload
