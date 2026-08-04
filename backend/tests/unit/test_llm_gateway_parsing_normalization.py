import pytest
from pydantic import BaseModel, ConfigDict

from backend.ai.gateway.errors import LLMStructuredOutputError
from backend.ai.gateway.models import (
    MAX_STRUCTURED_OUTPUT_CHARS,
    CanonicalLLMMessage,
    CanonicalToolCall,
    CanonicalToolResult,
)
from backend.ai.gateway.normalization import normalize_messages_for_provider
from backend.ai.gateway.parsing import parse_structured_output


class StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    confidence: float


def _gemini_history():
    call = CanonicalToolCall(id="call_1", name="booking.check", arguments={"date": "2026-07-15"})
    return [
        CanonicalLLMMessage(
            role="assistant",
            tool_calls=[call],
            provider_metadata={
                "gemini": {"tool_call_thought_signatures": {"call_1": "opaque-signature", "removed": "wrong"}},
                "openai": {"response_id": "response-1"},
            },
        ),
        CanonicalLLMMessage(
            role="tool",
            tool_result=CanonicalToolResult(
                tool_call_id="call_1",
                tool_name="booking.check",
                success=True,
                message="Available",
                safe_data={"available": True},
            ),
        ),
    ]


def test_valid_and_fenced_json_parse_to_pydantic_model():
    plain = parse_structured_output('{"action":"confirm","confidence":0.9}', StrictResult)
    fenced = parse_structured_output('```json\n{"action":"cancel","confidence":0.8}\n```', StrictResult)
    assert plain.action == "confirm"
    assert fenced.action == "cancel"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"action":"confirm","confidence":"not-number"}',
        '{"action":"confirm","confidence":0.9,"extra":true}',
        "```python\n{}\n```",
        "x" * (MAX_STRUCTURED_OUTPUT_CHARS + 1),
    ],
)
def test_invalid_structured_outputs_raise_typed_safe_error(raw):
    with pytest.raises(LLMStructuredOutputError) as caught:
        parse_structured_output(raw, StrictResult)
    assert raw not in caught.value.to_info().model_dump_json()


def test_gemini_metadata_is_stripped_for_groq_and_openai_without_text_leakage():
    history = _gemini_history()
    for provider in ("groq", "openai"):
        normalized = normalize_messages_for_provider(history, provider)
        assert "gemini" not in normalized[0].provider_metadata
        assert "opaque-signature" not in "\n".join(message.content for message in normalized)
        assert normalized[0].tool_calls[0].id == normalized[1].tool_result.tool_call_id


def test_gemini_retains_only_matching_signature_metadata():
    normalized = normalize_messages_for_provider(_gemini_history(), "gemini")
    signatures = normalized[0].provider_metadata["gemini"]["tool_call_thought_signatures"]
    assert signatures == {"call_1": "opaque-signature"}


def test_openai_tool_history_to_gemini_keeps_canonical_factual_correlation():
    history = _gemini_history()
    history[0] = history[0].model_copy(update={"provider_metadata": {"openai": {"response_id": "r1"}}})
    normalized = normalize_messages_for_provider(history, "gemini")
    assert normalized[0].provider_metadata == {}
    assert normalized[0].tool_calls[0].id == normalized[1].tool_result.tool_call_id
    assert normalized[1].tool_result.safe_data == {"available": True}
