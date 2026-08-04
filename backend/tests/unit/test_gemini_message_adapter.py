import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict
from langchain_openai import ChatOpenAI

from backend.ai.providers.gemini_chat_adapter import (
    GeminiChatOpenAI,
    PROVIDER_METADATA_KEY,
)
from backend.logic.engine.history import serialize_agentic_history


def _model(model_cls=GeminiChatOpenAI):
    return model_cls(
        model="gemini-test",
        api_key="test-key",
        base_url="https://example.invalid/v1/",
    )


def _response(*calls):
    return {
        "id": "response-1",
        "model": "gemini-test",
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": list(calls)},
        }],
    }


def _call(call_id: str, name: str, signature: str | None):
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"value": call_id})},
    }
    if signature is not None:
        call["extra_content"] = {"google": {"thought_signature": signature}}
    return call


def test_native_signature_survives_history_round_trip_and_is_not_customer_text():
    model = _model()
    message = model._create_chat_result(_response(_call("call-a", "booking_execute", "signature-A"))).generations[0].message

    metadata = message.additional_kwargs[PROVIDER_METADATA_KEY]
    assert metadata["tool_call_thought_signatures"] == {"call-a": "signature-A"}
    assert "signature-A" not in str(message.content)

    history = serialize_agentic_history([
        HumanMessage(content="Book a cleaning"),
        message,
        ToolMessage(content='{"success": false}', tool_call_id="call-a"),
    ])
    restored = messages_from_dict(history)
    payload = model._get_request_payload(restored)
    assistant = next(item for item in payload["messages"] if item["role"] == "assistant" and item.get("tool_calls"))

    assert assistant["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "signature-A"
    assert "signature-A" not in " ".join(str(item.get("content") or "") for item in payload["messages"])


def test_multiple_signatures_remain_bound_to_their_exact_call_ids():
    model = _model()
    message = model._create_chat_result(_response(
        _call("call-a", "booking_execute", "signature-A"),
        _call("call-b", "order_execute", "signature-B"),
    )).generations[0].message

    payload = model._get_request_payload([message])
    calls = {call["id"]: call for call in payload["messages"][0]["tool_calls"]}

    assert calls["call-a"]["extra_content"]["google"]["thought_signature"] == "signature-A"
    assert calls["call-b"]["extra_content"]["google"]["thought_signature"] == "signature-B"


def test_legacy_missing_signature_uses_bounded_context_without_fake_execution():
    model = _model()
    legacy_call = AIMessage(content="", tool_calls=[{
        "id": "legacy-call",
        "name": "order_execute",
        "args": {"item_name": "Toothpaste", "quantity": 1},
        "type": "tool_call",
    }])
    legacy_result = ToolMessage(
        content='{"success": false, "denied_reason": "confirmation_required"}',
        tool_call_id="legacy-call",
    )

    payload = model._get_request_payload([legacy_call, legacy_result])
    rendered = "\n".join(str(item.get("content") or "") for item in payload["messages"])

    assert all("tool_calls" not in item for item in payload["messages"])
    assert "order_execute" in rendered
    assert "confirmation_required" in rendered
    assert "does not prove execution or success" in rendered
    assert "skip_thought_signature_validator" not in json.dumps(payload)
    assert "context_engineering_is_the_way_to_go" not in json.dumps(payload)
    assert "thought_signature" not in json.dumps(payload)


def test_generic_openai_conversion_is_unchanged_for_unsigned_tool_history():
    generic = _model(ChatOpenAI)
    legacy_call = AIMessage(content="", tool_calls=[{
        "id": "openai-call",
        "name": "order_execute",
        "args": {"quantity": 1},
        "type": "tool_call",
    }])

    payload = generic._get_request_payload([legacy_call])

    assert payload["messages"][0]["tool_calls"][0]["id"] == "openai-call"
    assert "extra_content" not in payload["messages"][0]["tool_calls"][0]
