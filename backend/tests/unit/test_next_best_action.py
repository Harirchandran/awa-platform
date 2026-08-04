from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.core import WorkflowResult, agentic_engine
from backend.logic.engine.nba import append_next_best_actions, generate_next_best_actions
from backend.logic.engine.response_composer import CustomerResponseComposition
from backend.tools.registry import ToolExecutionResult

pytestmark = pytest.mark.anyio


def _context(*, modules=None, state=ConversationState.COLLECTING_DATA, include_catalog=True):
    tenant_id = uuid4()
    config_modules = {
        "booking": {"enabled": True, "fields": []},
        "reminders": {"enabled": True, "fields": []},
        "payments": {"enabled": True, "fields": []},
        "order": {
            "enabled": True,
            "fields": [],
            "categories": [
                {
                    "id": "food",
                    "name": "Food",
                    "items": [{"id": "tea", "name": "Masala Tea", "price": 30, "enabled": True}],
                }
            ]
            if include_catalog
            else [],
        },
        "reviews": {"enabled": True, "fields": []},
        "lead_capture": {"enabled": True, "fields": []},
        "human_handoff": {"enabled": True, "fields": []},
        "support": {"enabled": True, "fields": []},
    }
    if modules is not None:
        config_modules.update(modules)
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Smile Clinic", plan_code="enterprise", status="active", ai_mode="standard"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(workflow_state=state),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Smile Clinic",
                "services": [{"id": "cleaning", "name": "Dental Cleaning", "price": 800}],
                "modules": config_modules,
            },
        ),
        incoming_message={"text": "done", "channel_type": "awa_chat"},
    )


async def _allow_all_tools(monkeypatch):
    async def no_visibility_denial(context, tool_key):
        return None

    monkeypatch.setattr(agentic_engine.tool_registry.guard, "get_tool_visibility_denial", no_visibility_denial)


def _record(context, result):
    context.session_data.state.record_tool_result(result, channel_type="awa_chat")


async def test_booking_success_adds_safe_next_best_actions(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context()
    _record(
        context,
        ToolExecutionResult(
            tool_key="booking.execute",
            success=True,
            user_message="I've booked that appointment.",
            created_resource_type="booking",
            created_resource_id=str(uuid4()),
        ),
    )

    result = await append_next_best_actions(context, WorkflowResult(response_text="Booked."), agentic_engine.tool_registry)

    assert result.quick_replies == ["Reschedule", "Cancel booking", "Set reminder"]
    assert result.response_text == "Booked.\n\nWould you like me to set a reminder for this booking?"
    assert len(result.quick_replies) <= 3


async def test_response_composer_cannot_drop_persisted_booking_reminder_offer(monkeypatch):
    from backend.logic.engine import core as core_module

    await _allow_all_tools(monkeypatch)
    context = _context()

    async def booking_success(inner_context):
        inner_context.session_data.state.record_tool_result(
            ToolExecutionResult(
                tool_key="booking.execute",
                success=True,
                user_message="Booked.",
                created_resource_type="booking",
                created_resource_id=str(uuid4()),
            ),
            channel_type="awa_chat",
        )
        return WorkflowResult(response_text="Booked.")

    async def composer_drops_offer(*_args, **_kwargs):
        return CustomerResponseComposition(
            response_text="Your booking is confirmed.",
            quick_replies=["Reschedule", "Cancel booking", "Set reminder"],
            composed=True,
        )

    async def preserve_localized_result(_context, result, **_kwargs):
        return result

    monkeypatch.setattr(agentic_engine, "_execute_step_inner", booking_success)
    monkeypatch.setattr(agentic_engine, "_localize_active_workflow_result", preserve_localized_result)
    monkeypatch.setattr(core_module, "compose_customer_response", composer_drops_offer)
    monkeypatch.setattr(core_module, "_shadow_interpretation_enabled", lambda: False)

    result = await agentic_engine.execute_step(context)

    assert result.quick_replies == ["Reschedule", "Cancel booking", "Set reminder"]
    assert result.response_text == (
        "Your booking is confirmed.\n\n"
        "Would you like me to set a reminder for this booking?"
    )
    assert result.response_text.lower().count("reminder") == 1


async def test_reworded_booking_reminder_question_is_not_duplicated(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context()
    _record(
        context,
        ToolExecutionResult(
            tool_key="booking.execute",
            success=True,
            created_resource_type="booking",
            created_resource_id=str(uuid4()),
        ),
    )
    original = "Booked. Would you like a reminder?"

    result = await append_next_best_actions(
        context,
        WorkflowResult(response_text=original),
        agentic_engine.tool_registry,
    )

    assert result.response_text == original
    assert result.response_text.lower().count("reminder") == 1


async def test_booking_failure_does_not_add_success_dependent_suggestions(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context()
    _record(
        context,
        ToolExecutionResult(
            tool_key="booking.execute",
            success=False,
            denied_reason="slot_unavailable",
            user_message="That slot is unavailable.",
        ),
    )

    result = await append_next_best_actions(context, WorkflowResult(response_text="That slot is unavailable."), agentic_engine.tool_registry)

    assert "Reschedule" not in result.quick_replies
    assert "Cancel booking" not in result.quick_replies
    assert result.quick_replies == []


async def test_order_success_suggests_payment_only_when_payments_enabled(monkeypatch):
    await _allow_all_tools(monkeypatch)
    enabled_context = _context()
    _record(
        enabled_context,
        ToolExecutionResult(
            tool_key="order.execute",
            success=True,
            created_resource_type="order",
            data={"payment_status": "not_collected"},
        ),
    )
    enabled = await generate_next_best_actions(enabled_context, tool_registry=agentic_engine.tool_registry)

    disabled_context = _context(modules={"payments": {"enabled": False, "fields": []}})
    _record(
        disabled_context,
        ToolExecutionResult(
            tool_key="order.execute",
            success=True,
            created_resource_type="order",
            data={"payment_status": "not_collected"},
        ),
    )
    disabled = await generate_next_best_actions(disabled_context, tool_registry=agentic_engine.tool_registry)

    assert "Make payment" in enabled
    assert "Make payment" not in disabled


async def test_payment_suggestions_require_actual_success(monkeypatch):
    await _allow_all_tools(monkeypatch)
    success_context = _context()
    _record(
        success_context,
        ToolExecutionResult(tool_key="payments.execute", success=True, created_resource_type="payment_request"),
    )
    failure_context = _context()
    _record(
        failure_context,
        ToolExecutionResult(tool_key="payments.execute", success=False, denied_reason="provider_failed"),
    )

    assert await generate_next_best_actions(success_context, tool_registry=agentic_engine.tool_registry) == ["Contact support"]
    assert await generate_next_best_actions(failure_context, tool_registry=agentic_engine.tool_registry) == []


async def test_escalated_handoff_suggests_only_support_state_options(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context(state=ConversationState.ESCALATED)
    _record(
        context,
        ToolExecutionResult(
            tool_key="human_handoff.execute",
            success=True,
            created_resource_type="conversation_escalation",
        ),
    )

    suggestions = await generate_next_best_actions(context, tool_registry=agentic_engine.tool_registry)

    assert suggestions == ["Wait for agent", "Add more details"]
    assert not any(term in option.lower() for option in suggestions for term in ("book", "order", "pay", "reminder"))


async def test_suggestions_are_deduplicated_against_existing_quick_replies(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context(modules={"payments": {"enabled": False, "fields": []}})
    _record(
        context,
        ToolExecutionResult(tool_key="order.execute", success=True, created_resource_type="order"),
    )

    result = await append_next_best_actions(
        context,
        WorkflowResult(response_text="Order placed.", quick_replies=["Contact support"]),
        agentic_engine.tool_registry,
    )

    assert result.quick_replies == ["Contact support", "View menu", "Set reminder"]
    assert len({option.lower() for option in result.quick_replies}) == len(result.quick_replies)


async def test_suggestions_are_bounded_short_and_internal_id_free(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context()
    internal_id = str(uuid4())
    _record(
        context,
        ToolExecutionResult(
            tool_key="booking.execute",
            success=True,
            created_resource_type="booking",
            created_resource_id=internal_id,
        ),
    )

    suggestions = await generate_next_best_actions(context, tool_registry=agentic_engine.tool_registry)

    assert len(suggestions) <= 3
    assert all(len(option) <= 20 for option in suggestions)
    assert all(internal_id not in option for option in suggestions)
    assert all("uuid" not in option.lower() and "id:" not in option.lower() for option in suggestions)


async def test_suggestions_respect_disabled_tenant_tools(monkeypatch):
    await _allow_all_tools(monkeypatch)
    booking_context = _context(modules={"reminders": {"enabled": False, "fields": []}})
    _record(
        booking_context,
        ToolExecutionResult(tool_key="booking.execute", success=True, created_resource_type="booking"),
    )
    order_context = _context(modules={"payments": {"enabled": False, "fields": []}})
    _record(
        order_context,
        ToolExecutionResult(tool_key="order.execute", success=True, created_resource_type="order"),
    )
    review_context = _context(modules={"booking": {"enabled": False, "fields": []}})
    _record(
        review_context,
        ToolExecutionResult(tool_key="reviews.execute", success=True, created_resource_type="review_request"),
    )

    assert "Set reminder" not in await generate_next_best_actions(booking_context, tool_registry=agentic_engine.tool_registry)
    assert "Make payment" not in await generate_next_best_actions(order_context, tool_registry=agentic_engine.tool_registry)
    assert "Book again" not in await generate_next_best_actions(review_context, tool_registry=agentic_engine.tool_registry)


async def test_booking_success_does_not_offer_disabled_reminder(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context(modules={"reminders": {"enabled": False, "fields": []}})
    _record(
        context,
        ToolExecutionResult(
            tool_key="booking.execute",
            success=True,
            created_resource_type="booking",
            created_resource_id=str(uuid4()),
        ),
    )

    result = await append_next_best_actions(
        context,
        WorkflowResult(response_text="Booked."),
        agentic_engine.tool_registry,
    )

    assert "Set reminder" not in result.quick_replies
    assert result.response_text == "Booked."


async def test_booking_success_does_not_offer_undeliverable_configured_reminder(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context(modules={
        "reminders": {
            "enabled": True,
            "fields": [
                {
                    "key": "channel",
                    "label": "Channel",
                    "type": "select",
                    "required": True,
                    "options": ["awa_chat"],
                }
            ],
        }
    })
    _record(
        context,
        ToolExecutionResult(
            tool_key="booking.execute",
            success=True,
            created_resource_type="booking",
            created_resource_id=str(uuid4()),
        ),
    )

    result = await append_next_best_actions(
        context,
        WorkflowResult(response_text="Booked."),
        agentic_engine.tool_registry,
    )

    assert "Set reminder" not in result.quick_replies
    assert result.response_text == "Booked."


async def test_same_milestone_suggestions_are_not_repeated(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context()
    _record(
        context,
        ToolExecutionResult(tool_key="booking.execute", success=True, created_resource_type="booking"),
    )

    first = await generate_next_best_actions(context, tool_registry=agentic_engine.tool_registry)
    second = await generate_next_best_actions(context, tool_registry=agentic_engine.tool_registry)

    assert first == ["Reschedule", "Cancel booking", "Set reminder"]
    assert second == []


async def test_lead_capture_success_uses_safe_followup_options(monkeypatch):
    await _allow_all_tools(monkeypatch)
    context = _context()
    _record(
        context,
        ToolExecutionResult(tool_key="lead_capture.execute", success=True, created_resource_type="lead"),
    )

    suggestions = await generate_next_best_actions(context, tool_registry=agentic_engine.tool_registry)

    assert suggestions == ["Ask a question", "View menu", "Contact support"]
