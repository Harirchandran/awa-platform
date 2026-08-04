from datetime import datetime, timedelta, timezone
from importlib import import_module
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.modules.booking_module import booking_module
from backend.modules.faq_module import faq_module
from backend.modules.human_handoff_module import human_handoff_module
from backend.modules.lead_capture_module import lead_capture_module
from backend.modules.order_module import order_module
from backend.modules.payments_module import payments_module
from backend.modules.reminders_module import reminders_module
from backend.modules.reviews_module import reviews_module
from backend.modules.support_module import support_module
from backend.tools.registry import ToolExecutionResult, ToolRegistry


pytestmark = pytest.mark.anyio

HANDLER_BRIDGES = [
    ("faq", "faq.execute", "backend.domains.knowledge.tool_handlers", "execute_faq_tool", faq_module),
    ("lead_capture", "lead_capture.execute", "backend.domains.leads.tool_handlers", "execute_lead_capture_tool", lead_capture_module),
    ("support", "support.execute", "backend.domains.support.tool_handlers", "execute_support_tool", support_module),
    (
        "human_handoff",
        "human_handoff.execute",
        "backend.domains.support.tool_handlers",
        "execute_human_handoff_tool",
        human_handoff_module,
    ),
    ("reviews", "reviews.execute", "backend.domains.reviews.tool_handlers", "execute_reviews_tool", reviews_module),
    ("reminders", "reminders.execute", "backend.domains.reminders.tool_handlers", "execute_reminders_tool", reminders_module),
    ("booking", "booking.execute", "backend.domains.bookings.tool_handlers", "execute_booking_tool", booking_module),
    ("order", "order.execute", "backend.domains.orders.tool_handlers", "execute_order_tool", order_module),
    ("payments", "payments.execute", "backend.domains.payments.tool_handlers", "execute_payments_tool", payments_module),
]


def make_context(module_name="support", step_data=None, modules=None):
    tenant_id = uuid4()
    state = SessionState(workflow_state=ConversationState.EXECUTING)
    state.step_data.update(step_data or {})
    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id,
            name="Phase 3 Tenant",
            plan_code="enterprise",
            status="active",
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        user=User(id=uuid4(), phone_number="+15550000000", name="Customer"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=module_name,
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Phase 3 Tenant",
                "modules": modules or {module_name: {"enabled": True, "fields": []}},
            },
        ),
        incoming_message={"text": "test", "channel_type": "awa_chat"},
    )


def import_handler_module(path, function_name):
    try:
        handler_module = import_module(path)
    except ModuleNotFoundError:
        pytest.skip(f"{path} is not migrated yet")
    if not hasattr(handler_module, function_name):
        pytest.skip(f"{path}.{function_name} is not migrated yet")
    return handler_module


def test_wrapper_modules_still_register_same_tools():
    registry = ToolRegistry()
    for _, _, _, _, module in HANDLER_BRIDGES:
        registry.register_module(module)

    expected_keys = {tool_key for _, tool_key, *_ in HANDLER_BRIDGES}
    assert set(registry._tools) == expected_keys
    for module_name, tool_key, _, _, _ in HANDLER_BRIDGES:
        assert registry.get_tool_key_for_module(module_name) == tool_key
        assert registry.get_tool_key_for_llm_name(f"{module_name}_execute") == tool_key


@pytest.mark.parametrize(("module_name", "tool_key", "handler_path", "function_name", "module"), HANDLER_BRIDGES)
async def test_wrapper_execute_delegates_to_domain_handler(monkeypatch, module_name, tool_key, handler_path, function_name, module):
    handler_module = import_handler_module(handler_path, function_name)
    sentinel = ToolExecutionResult(
        tool_key=tool_key,
        success=True,
        user_message=f"{module_name} delegated",
        data={"module_name": module_name},
    )
    calls = []

    async def fake_handler(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(handler_module, function_name, fake_handler)
    context = make_context(module_name)

    result = await module.execute(context)

    assert result is sentinel
    assert calls
    assert calls[0][0][0] is context


@pytest.mark.parametrize(("module_name", "tool_key", "handler_path", "function_name", "module"), HANDLER_BRIDGES)
def test_wrapper_metadata_and_schema_survive_handler_migration(module_name, tool_key, handler_path, function_name, module):
    import_handler_module(handler_path, function_name)
    config = {"modules": {module_name: {"enabled": True, "fields": []}}}

    assert module.get_module_name() == module_name
    assert isinstance(module.get_tool_schema(config), dict)
    metadata = module.get_tool_metadata(config)
    assert metadata.risk_level in {"write", "sensitive_write"}
    assert isinstance(metadata.requires_confirmation, bool)
    assert isinstance(metadata.requires_verified_customer, bool)


async def test_migrated_faq_handler_preserves_boolean_result():
    import_handler_module("backend.domains.knowledge.tool_handlers", "execute_faq_tool")

    result = await faq_module.execute(make_context("faq"))

    assert result is False
