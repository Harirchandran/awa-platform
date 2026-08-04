import logging
from uuid import uuid4

import pytest

from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    PendingAction,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.logic.engine.llm import (
    build_llm_input_trace,
    final_output_type,
    log_llm_input_trace,
)


def _context() -> ExecutionContext:
    tenant_id = uuid4()
    state = SessionState()
    state.agentic_history = [
        {"type": "human", "data": {"content": "hello"}},
        {"type": "ai", "data": {"content": "hi"}},
    ]
    state.action_ledger.pending_action = PendingAction(tool_key="order.execute", args={"phone": "+919999999999"})
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Trace Clinic", plan_code="trial", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "modules": {
                    "order": {
                        "enabled": True,
                        "categories": [
                            {"items": [{"id": "toothpaste"}, {"id": "floss"}]},
                        ],
                    }
                }
            },
        ),
        incoming_message={
            "text": "My phone is +919999999999 and OTP is 123456. sk-test-secret token abcdefghijklmnop",
            "channel_type": "awa_chat",
            "route_path": "/api/v1/public-chat/sessions/x/messages",
        },
    )


def test_trace_logs_provider_model_integration_mode_and_fake_flag(caplog):
    context = _context()
    trace = build_llm_input_trace(
        context=context,
        provider="groq",
        model="llama-test",
        integration_mode="real",
        system_instructions="System rules\nBusiness type: clinic\nRecent conversation, oldest to newest:\nCustomer: hi",
        tools=[{"function": {"name": "order_execute"}}],
        memory_context="Recent conversation, oldest to newest:\nCustomer: hi",
        knowledge_context="Knowledge reference title=FAQ\nHours",
        latency_ms=12,
        final_output_type="tool_call",
    )

    with caplog.at_level(logging.INFO):
        log_llm_input_trace(trace)

    assert trace["provider"] == "groq"
    assert trace["model"] == "llama-test"
    assert trace["integration_mode"] == "real"
    assert trace["is_fake_provider"] is False
    assert any("LLM input trace" in record.message for record in caplog.records)


def test_trace_records_counts_tools_history_rag_catalog_and_pending_action():
    trace = build_llm_input_trace(
        context=_context(),
        provider="deterministic_fake",
        model="fake",
        integration_mode="test",
        system_instructions="abc",
        tools=[{"function": {"name": "order_execute"}}, {"function": {"name": "booking_execute"}}],
        memory_context="Customer: hi\nAssistant: hello",
        knowledge_context="Knowledge reference one\n\nKnowledge reference two",
    )

    assert trace["is_fake_provider"] is True
    assert "tool_names" not in trace
    assert trace["tool_count"] == 2
    assert trace["history_message_count"] == 2
    assert trace["agentic_history_count"] == 2
    assert trace["rag_chunk_count"] == 2
    assert trace["catalog_item_count"] == 2
    assert trace["has_pending_action"] is True


def test_trace_redacts_sensitive_values_and_does_not_include_raw_prompt():
    trace = build_llm_input_trace(
        context=_context(),
        provider="openai",
        model="gpt-test",
        integration_mode="real",
        system_instructions="FULL RAW PROMPT WITH PHONE +919999999999 AND OTP 123456",
        tools=[{"function": {"name": "order_execute", "parameters": {"secret": "unit-test-not-a-real-provider-key"}}}],
        memory_context="Customer phone +919999999999 OTP 123456 token abcdefghijklmnop",
        knowledge_context="",
    )
    dumped = str(trace)

    assert "FULL RAW PROMPT" not in dumped
    assert "+919999999999" not in dumped
    assert "123456" not in dumped
    assert "unit-test-not-a-real-provider-key" not in dumped
    assert "abcdefghijklmnop" not in dumped
    assert trace["prompt_sections"]["history_chars"] > 0


def test_trace_records_fallback_event_safely():
    trace = build_llm_input_trace(
        context=_context(),
        provider="openai",
        model="gpt-test",
        integration_mode="real",
        system_instructions="system",
        fallback_used=True,
        provider_failure={"provider": "groq", "error_type": "rate_limit", "api_key": "sk-secret"},
        final_output_type="safe_error",
    )

    assert trace["fallback_used"] is True
    assert trace["error_code"] == "rate_limit"
    assert "provider_failure" not in trace
    assert "sk-secret" not in str(trace)


def test_final_output_type_detects_tool_calls_and_text():
    class ToolResponse:
        tool_calls = [{"name": "order_execute"}]
        content = ""

    class TextResponse:
        tool_calls = []
        content = "hello"

    assert final_output_type(ToolResponse()) == "tool_call"
    assert final_output_type(TextResponse()) == "text"
    assert final_output_type(None) == "safe_error"
