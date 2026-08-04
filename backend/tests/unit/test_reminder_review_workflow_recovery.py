from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from backend.core.config import settings
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.core import agentic_engine
from backend.logic.engine.field_extraction import ReminderFieldExtraction, ReviewFieldExtraction
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.tools.registry import ToolExecutionResult


pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _reminder_config(*, enabled: bool = True, fields=None):
    return {
        "modules": {
            "reminders": {
                "enabled": enabled,
                "fields": fields or [
                    {"key": "scheduled_for", "label": "Schedule", "type": "string", "required": True},
                    {"key": "message_text", "label": "Message", "type": "string", "required": True},
                    {"key": "channel", "label": "Channel", "type": "string", "required": False},
                    {"key": "customer_name", "label": "Name", "type": "string", "required": False},
                    {"key": "phone", "label": "Phone", "type": "phone", "required": False},
                    {"key": "email", "label": "Email", "type": "email", "required": False},
                ],
            },
            "reviews": {"enabled": True},
        },
        "timezone": "UTC",
    }


def _review_config(*, enabled: bool = True, fields=None):
    return {
        "modules": {
            "reminders": {"enabled": True},
            "reviews": {
                "enabled": enabled,
                "fields": fields or [
                    {"key": "rating", "label": "Rating", "type": "number", "required": True},
                    {"key": "feedback", "label": "Feedback", "type": "string", "required": False},
                    {"key": "customer_name", "label": "Name", "type": "string", "required": False},
                    {"key": "phone", "label": "Phone", "type": "phone", "required": False},
                    {"key": "email", "label": "Email", "type": "email", "required": False},
                ],
            },
        },
        "timezone": "UTC",
    }


def _context(module_name: str, *, step_data=None, config_data=None, enabled: bool = True, message_text="black-box reply"):
    tenant_id = uuid4()
    config = config_data or (_reminder_config(enabled=enabled) if module_name == "reminders" else _review_config(enabled=enabled))
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=dict(step_data or {}),
        scoped_step_data={module_name: dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = module_name
    state.action_ledger.task_status = "collecting"
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="Test User"),
        session_data=ConversationSession(
            id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(), config_version=1,
            current_module=module_name, state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config),
        incoming_message={"text": message_text, "channel_type": "awa_chat"},
    )


def _route(context):
    from backend.pipeline.message_router import message_router
    return message_router.route_message(
        context.incoming_message.get("text", ""),
        context.config.config_data,
        current_task=context.session_data.state.action_ledger.current_task,
        current_module=context.session_data.current_module,
    )


def _reminder_extraction(**kwargs):
    values = {
        "detected_language": "black-box",
        "workflow": "reminder",
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": {},
        "field_confidence": {},
        "clarification_needed": False,
    }
    values.update(kwargs)
    return ReminderFieldExtraction(**values)


def _review_extraction(**kwargs):
    values = {
        "detected_language": "black-box",
        "workflow": "review",
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": {},
        "field_confidence": {},
        "clarification_needed": False,
    }
    values.update(kwargs)
    return ReviewFieldExtraction(**values)


async def _install_reminder_stub(monkeypatch, extraction=None, exc: Exception | None = None):
    async def fake_extract(_context):
        if exc:
            raise exc
        return extraction
    monkeypatch.setattr("backend.logic.engine.core.extract_reminder_fields", fake_extract)


async def _install_review_stub(monkeypatch, extraction=None, exc: Exception | None = None):
    async def fake_extract(_context):
        if exc:
            raise exc
        return extraction
    monkeypatch.setattr("backend.logic.engine.core.extract_review_fields", fake_extract)


async def test_active_reminder_semantic_extraction_applies_safe_fields(monkeypatch):
    context = _context("reminders", message_text="black-box mixed-language reminder")
    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
    await _install_reminder_stub(monkeypatch, _reminder_extraction(
        field_updates={
            "reminder_message": "Please remember this.", "scheduled_for": scheduled_for,
            "channel": "whatsapp", "customer_name": "Test Person", "phone": "+10000000000",
            "email": "person@example.com",
        },
        field_confidence={key: 0.95 for key in ("reminder_message", "scheduled_for", "channel", "customer_name", "phone", "email")},
    ))

    result = await agentic_engine._handle_semantic_reminder_field_extraction(context, _route(context))

    assert result is None
    state = context.session_data.state.get_task_step_data("reminders")
    assert state["message_text"] == "Please remember this."
    assert state["scheduled_for"] == scheduled_for
    assert state["channel"] == "whatsapp"
    assert state["phone"] == "+10000000000"


async def test_active_review_semantic_extraction_applies_rating_feedback_and_contact(monkeypatch):
    context = _context("reviews", message_text="black-box review details")
    await _install_review_stub(monkeypatch, _review_extraction(
        field_updates={
            "rating": 5, "feedback": "Very helpful.", "customer_name": "Test Person",
            "phone": "+10000000000", "email": "person@example.com",
        },
        field_confidence={key: 0.95 for key in ("rating", "feedback", "customer_name", "phone", "email")},
    ))

    result = await agentic_engine._handle_semantic_review_field_extraction(context, _route(context))

    assert result is None
    state = context.session_data.state.get_task_step_data("reviews")
    assert state["rating"] == 5
    assert state["feedback"] == "Very helpful."
    assert state["email"] == "person@example.com"


async def test_non_english_and_mixed_inputs_use_semantic_stubs(monkeypatch):
    reminder_context = _context("reminders", message_text="നാളെ ഓർമ്മിപ്പിക്കൂ")
    review_context = _context("reviews", message_text="बहुत अच्छा mixed reply")
    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat()
    await _install_reminder_stub(monkeypatch, _reminder_extraction(
        field_updates={"scheduled_for": scheduled_for}, field_confidence={"scheduled_for": 0.96},
    ))
    await _install_review_stub(monkeypatch, _review_extraction(
        field_updates={"rating": 4}, field_confidence={"rating": 0.96},
    ))

    assert await agentic_engine._handle_semantic_reminder_field_extraction(reminder_context, _route(reminder_context)) is None
    assert await agentic_engine._handle_semantic_review_field_extraction(review_context, _route(review_context)) is None
    assert reminder_context.session_data.state.get_task_step_data("reminders")["scheduled_for"] == scheduled_for
    assert review_context.session_data.state.get_task_step_data("reviews")["rating"] == 4


async def test_low_confidence_or_provider_failure_preserves_active_state(monkeypatch):
    reminder_context = _context("reminders", step_data={"message_text": "Keep this"})
    review_context = _context("reviews", step_data={"rating": 4})
    await _install_reminder_stub(monkeypatch, _reminder_extraction(confidence=0.1, field_updates={"channel": "whatsapp"}))
    await _install_review_stub(monkeypatch, exc=ValueError("invalid semantic json"))

    reminder_result = await agentic_engine._handle_semantic_reminder_field_extraction(reminder_context, _route(reminder_context))
    review_result = await agentic_engine._handle_semantic_review_field_extraction(review_context, _route(review_context))

    assert reminder_result is not None and review_result is not None
    assert reminder_context.session_data.state.get_task_step_data("reminders") == {"message_text": "Keep this"}
    assert review_context.session_data.state.get_task_step_data("reviews") == {"rating": 4}
    assert reminder_context.session_data.state.action_ledger.current_task == "reminders"
    assert review_context.session_data.state.action_ledger.current_task == "reviews"


async def test_invalid_past_reminder_time_unsupported_channel_and_rating_are_rejected(monkeypatch):
    reminder_context = _context("reminders", step_data={"message_text": "Keep this"})
    review_context = _context("reviews")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
    await _install_reminder_stub(monkeypatch, _reminder_extraction(
        field_updates={"scheduled_for": past}, field_confidence={"scheduled_for": 0.95},
    ))
    await _install_review_stub(monkeypatch, _review_extraction(
        field_updates={"rating": 9}, field_confidence={"rating": 0.95},
    ))

    reminder_result = await agentic_engine._handle_semantic_reminder_field_extraction(reminder_context, _route(reminder_context))
    review_result = await agentic_engine._handle_semantic_review_field_extraction(review_context, _route(review_context))

    assert reminder_result is not None and "future" in reminder_result.response_text.lower()
    assert review_result is not None and "rating" in review_result.response_text.lower()
    assert reminder_context.session_data.state.get_task_step_data("reminders") == {"message_text": "Keep this"}
    assert review_context.session_data.state.get_task_step_data("reviews") == {}


async def test_reminder_date_time_and_channel_are_validated_deterministically(monkeypatch):
    scheduled_date = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    valid_context = _context("reminders")
    unsupported_context = _context("reminders", step_data={"message_text": "Keep this"})
    await _install_reminder_stub(monkeypatch, _reminder_extraction(
        field_updates={
            "reminder_date": scheduled_date, "reminder_time": "10:30",
            "channel": "whatsapp",
        },
        field_confidence={"reminder_date": 0.95, "reminder_time": 0.95, "channel": 0.95},
    ))

    valid_result = await agentic_engine._handle_semantic_reminder_field_extraction(valid_context, _route(valid_context))

    assert valid_result is None
    assert valid_context.session_data.state.get_task_step_data("reminders")["scheduled_for"] == f"{scheduled_date}T10:30:00"

    await _install_reminder_stub(monkeypatch, _reminder_extraction(
        field_updates={"channel": "unsupported"}, field_confidence={"channel": 0.95},
    ))
    unsupported_result = await agentic_engine._handle_semantic_reminder_field_extraction(
        unsupported_context, _route(unsupported_context),
    )

    assert unsupported_result is not None
    assert "channel" in unsupported_result.response_text.lower()
    assert unsupported_context.session_data.state.get_task_step_data("reminders") == {"message_text": "Keep this"}


async def test_reminder_handler_rejects_past_schedule_before_persistence(monkeypatch):
    from backend.modules.reminders_module import reminders_module

    context = _context("reminders", step_data={
        "scheduled_for": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "message_text": "Keep this",
        "channel": "awa_chat",
    })
    service = SimpleNamespace(create_scheduled_message=AsyncMock())
    monkeypatch.setattr("backend.modules.reminders_module.reminder_service", service)

    result = await reminders_module.execute(context)

    assert result.success is False
    assert result.validation_errors == {"scheduled_for": "Reminder time must be in the future."}
    service.create_scheduled_message.assert_not_awaited()


async def test_disabled_modules_do_not_run_semantic_extraction(monkeypatch):
    reminder_context = _context("reminders", enabled=False)
    review_context = _context("reviews", enabled=False)
    called = {"reminder": False, "review": False}

    async def fake_reminder(_context):
        called["reminder"] = True
        return _reminder_extraction(field_updates={"channel": "whatsapp"})

    async def fake_review(_context):
        called["review"] = True
        return _review_extraction(field_updates={"rating": 5})

    monkeypatch.setattr("backend.logic.engine.core.extract_reminder_fields", fake_reminder)
    monkeypatch.setattr("backend.logic.engine.core.extract_review_fields", fake_review)

    assert await agentic_engine._handle_semantic_reminder_field_extraction(reminder_context, _route(reminder_context)) is None
    assert await agentic_engine._handle_semantic_review_field_extraction(review_context, _route(review_context)) is None
    assert called == {"reminder": False, "review": False}


async def test_unsafe_fields_and_tangents_do_not_overwrite_state(monkeypatch):
    reminder_context = _context("reminders", step_data={"message_text": "Keep this"})
    review_context = _context("reviews", step_data={"rating": 4, "feedback": "Keep this"})
    await _install_reminder_stub(monkeypatch, _reminder_extraction(
        field_updates={"channel": "whatsapp", "reminder_id": str(uuid4())},
        field_confidence={"channel": 0.95, "reminder_id": 0.95},
    ))
    reminder_result = await agentic_engine._handle_semantic_reminder_field_extraction(reminder_context, _route(reminder_context))
    await _install_review_stub(monkeypatch, _review_extraction(
        workflow_action="tangent", field_updates={"rating": 1}, field_confidence={"rating": 0.99},
    ))
    review_result = await agentic_engine._handle_semantic_review_field_extraction(review_context, _route(review_context))

    assert reminder_result is not None and review_result is not None
    assert reminder_context.session_data.state.get_task_step_data("reminders") == {"message_text": "Keep this"}
    assert review_context.session_data.state.get_task_step_data("reviews")["rating"] == 4


async def test_quick_reply_text_uses_the_same_semantic_path(monkeypatch):
    context = _context("reviews", step_data={"feedback": "Saved"}, message_text="5")
    await _install_review_stub(monkeypatch, _review_extraction(
        field_updates={"rating": 5}, field_confidence={"rating": 0.98},
    ))

    result = await agentic_engine._handle_semantic_review_field_extraction(context, _route(context))

    assert result is None
    assert context.session_data.state.get_task_step_data("reviews")["rating"] == 5


async def test_reminder_review_extraction_prompts_omit_internal_identifiers(monkeypatch):
    from backend.logic.engine import field_extraction

    internal_id = str(uuid4())
    captured: list[str] = []

    class FakeLLM:
        async def ainvoke(self, messages):
            captured.append(str(messages[-1].content))
            workflow = "reminder" if len(captured) == 1 else "review"
            return AIMessage(content=json.dumps({
                "workflow": workflow, "workflow_action": "continue", "confidence": 0.9,
                "field_updates": {}, "field_confidence": {}, "clarification_needed": False,
                "clarification_fields": [], "safe_response_hint": None,
            }))

    reminder_context = _context(
        "reminders",
        config_data=_reminder_config(fields=[
            {"key": "reminder_id", "label": f"Hidden {internal_id}", "type": "string", "required": False},
            {"key": "message_text", "label": "Message", "type": "string", "required": True},
        ]),
        step_data={"reminder_id": internal_id, "message_text": internal_id},
        message_text=f"black-box [tenant_id] [customer_id] [reminder_id] {internal_id}",
    )
    review_context = _context(
        "reviews",
        config_data=_review_config(fields=[
            {"key": "review_id", "label": f"Hidden {internal_id}", "type": "string", "required": False},
            {"key": "rating", "label": "Rating", "type": "number", "required": True},
        ]),
        step_data={"review_id": internal_id, "rating": 4},
        message_text=f"black-box [review_id] [order_id] {internal_id}",
    )
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: FakeLLM())

    await field_extraction.extract_reminder_fields(reminder_context)
    await field_extraction.extract_review_fields(review_context)

    payload = "\n".join(captured)
    for key in ("tenant_id", "customer_id", "reminder_id", "review_id", "order_id"):
        assert key not in payload
    assert internal_id not in payload
    assert not UUID_PATTERN.search(payload)


async def test_pending_action_confirmation_remains_semantic_and_single_path(monkeypatch):
    context = _context("reminders")
    args = {
        "scheduled_for": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "message_text": "Keep this", "channel": "awa_chat",
    }
    context.session_data.state.update_task_step_data("reminders", args)
    context.session_data.state.prepare_pending_action(
        tool_key="reminders.execute", args=args, summary="Review this reminder request.",
        requires_confirmation=True, requires_verified_customer=False, channel_type="awa_chat",
    )

    async def fake_allowed(_context):
        return None

    async def fake_hydrate(_context):
        return None

    async def fake_decide(_context):
        return PendingActionDecision(action="confirm", confidence=0.98)

    async def fake_execute(tool_key, _context):
        return ToolExecutionResult(tool_key=tool_key, success=True, user_message="Reminder saved.")

    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", fake_allowed)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", fake_hydrate)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute)

    result = await agentic_engine.execute_step(context)

    assert result.response_text == "Reminder saved."
    assert context.session_data.state.action_ledger.pending_action is None
