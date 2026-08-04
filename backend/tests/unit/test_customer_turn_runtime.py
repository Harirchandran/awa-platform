"""Phase 4W-R executable contracts for customer turns and channel normalization."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from backend.api.webhook import _extract_message_text
from backend.core.states import ConversationState
from backend.logic.engine import agentic_engine
from backend.pipeline.message_router import message_router
from backend.tests.unit.test_conversation_ux import (
    FakeToolCallingLLM,
    install_agent_test_doubles,
    make_context,
)


pytestmark = [pytest.mark.anyio, pytest.mark.runtime, pytest.mark.customer_turn]


def _booking_config():
    return {
        "services": [
            {"id": "cleaning", "name": "Teeth Cleaning", "enabled": True},
            {"id": "filling", "name": "Cavity Filling", "enabled": True},
        ],
        "modules": {"booking": {"enabled": True}},
    }


def test_natural_first_turn_booking_request_preserves_booking_semantics():
    message = "I need teeth cleaning tomorrow evening"
    decision = message_router.route_message(message, _booking_config())

    assert decision.intent == "booking"
    assert decision.route_type in {"workflow", "active_task"}
    assert decision.intent not in {"greeting", "faq", "unknown"}
    assert all(hint in message.lower() for hint in ("teeth cleaning", "tomorrow", "evening"))


async def test_active_booking_multifield_continuation_preserves_state(monkeypatch):
    field_llm = MagicMock()
    field_llm.ainvoke = AsyncMock(return_value=AIMessage(content=(
        '{"detected_language":"English","workflow":"booking","workflow_action":"continue",'
        '"confidence":0.99,"field_updates":{"preferred_time":"16:00","customer_name":"Arjun",'
        '"phone":"9876543210"},"field_confidence":{"preferred_time":0.99,"customer_name":0.99,'
        '"phone":0.99},"clarification_needed":false,"clarification_fields":[]}'
    )))
    llm = FakeToolCallingLLM([AIMessage(content="Thanks Arjun. I have kept your booking details.")])
    await install_agent_test_doubles(monkeypatch, llm)
    monkeypatch.setattr(
        "backend.logic.engine.field_extraction._legacy_llm_or_default",
        lambda _injected: field_llm,
    )
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"service_name": "Dental Cleaning", "preferred_date": "tomorrow"},
        message={"text": "Around 4 pm. My name is Arjun and my phone is 9876543210."},
    )
    context.session_data.state.action_ledger.current_task = "booking"
    context.config.config_data["modules"]["booking"]["fields"].append(
        {"key": "phone", "label": "Phone", "type": "phone", "required": False}
    )

    result = await agentic_engine.execute_step(context)
    fields = context.session_data.state.get_task_step_data("booking")

    assert result.next_state == ConversationState.COLLECTING_DATA
    assert context.session_data.current_module == "booking"
    assert fields["service_name"] == "Dental Cleaning"
    assert fields["preferred_date"] == "tomorrow"
    assert fields["preferred_time"] == "16:00"
    assert fields["customer_name"] == "Arjun"
    assert fields["phone"] == "9876543210"
    assert "welcome" not in result.response_text.lower()


async def test_active_booking_service_answer_precedes_switch_conflict(monkeypatch):
    field_llm = MagicMock()
    field_llm.ainvoke = AsyncMock(return_value=AIMessage(content=(
        '{"detected_language":"English","workflow":"booking","workflow_action":"continue",'
        '"confidence":0.99,"field_updates":{"service_name":"Cavity Filling"},'
        '"field_confidence":{"service_name":0.99},"clarification_needed":false,"clarification_fields":[]}'
    )))
    llm = FakeToolCallingLLM([AIMessage(content="What date would you like for Cavity Filling?")])
    await install_agent_test_doubles(monkeypatch, llm)
    monkeypatch.setattr(
        "backend.logic.engine.field_extraction._legacy_llm_or_default",
        lambda _injected: field_llm,
    )
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={},
        message={"text": "Cavity Filling"},
    )
    context.config.config_data["services"].append(
        {"id": "filling", "name": "Cavity Filling", "duration_mins": 45, "price": 1200}
    )
    context.session_data.state.action_ledger.current_task = "booking"

    result = await agentic_engine.execute_step(context)
    fields = context.session_data.state.get_task_step_data("booking")

    # Exact configured booking options are coordinator-owned and must bypass
    # the legacy semantic field LLM.
    assert field_llm.ainvoke.await_count == 0
    assert fields["service_name"] == "Cavity Filling"
    assert context.session_data.current_module == "booking"
    assert not {"continue", "cancel", "change"}.issubset({r.lower() for r in result.quick_replies})


async def test_explicit_capability_request_differs_from_unknown_text(monkeypatch):
    llm = FakeToolCallingLLM([AIMessage(content="Could you tell me whether this is about an appointment or something else?")])
    await install_agent_test_doubles(monkeypatch, llm)

    capability = await agentic_engine.execute_step(make_context(message={"text": "What can you do?"}))
    unknown = await agentic_engine.execute_step(
        make_context(message={"text": "I was wondering about something unusual"})
    )

    assert capability.quick_replies == ["Book appointment", "Services & prices"]
    assert unknown.response_text != capability.response_text
    assert "welcome to smile clinic" not in unknown.response_text.lower()


@pytest.mark.channel
def test_browser_typed_and_quick_reply_use_visible_text_only():
    page_source = Path("frontend/src/routes/public/PublicChatPage.tsx").read_text(encoding="utf-8")
    api_source = Path("frontend/src/features/awa-chat/api.ts").read_text(encoding="utf-8")

    assert "handleSend(reply);" in page_source
    assert "submitSendAttempt(attempt);" in page_source
    payload = api_source.split("export async function sendPublicChatMessage", 1)[1].split("return response.data", 1)[0]
    assert "session_token: payload.sessionToken" in payload
    assert "message_text: payload.messageText" in payload
    assert "client_message_id: payload.clientMessageId" in payload
    for hidden_field in ("action_id", "tool_key", "workflow", "service_id", "intent", "field_assignment", "execution_payload"):
        assert hidden_field not in payload


@pytest.mark.channel
@pytest.mark.parametrize(
    ("message", "message_type", "visible_text"),
    [
        ({"text": {"body": "Cavity Filling"}}, "text", "Cavity Filling"),
        ({"interactive": {"type": "button_reply", "button_reply": {"id": "transport-only-42", "title": "Cavity Filling"}}}, "interactive", "Cavity Filling"),
        ({"interactive": {"type": "list_reply", "list_reply": {"id": "transport-only-99", "title": "Cavity Filling"}}}, "interactive", "Cavity Filling"),
    ],
)
def test_whatsapp_inbound_variants_normalize_to_visible_text(message, message_type, visible_text):
    semantic_text = _extract_message_text(message, message_type)
    assert semantic_text == visible_text
    assert "transport-only" not in semantic_text
