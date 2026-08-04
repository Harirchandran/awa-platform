"""Gemini-specific OpenAI-compatible message conversion.

Gemini 3 returns opaque thought signatures on tool calls.  LangChain's generic
OpenAI conversion intentionally drops unknown ``extra_content`` fields, so this
adapter captures the signature, keeps it bound to the originating call id, and
replays it only to Gemini.  Historical calls that predate signature capture are
rendered as bounded context instead of being resent as invalid function calls.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI


PROVIDER_METADATA_KEY = "awa_provider_metadata"
_MAX_SIGNATURE_CHARS = 65536
_MAX_LEGACY_CONTEXT_CHARS = 1600


def _valid_signature(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_SIGNATURE_CHARS:
        return None
    return value


def _response_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump(exclude={"choices": {"__all__": {"message": {"parsed"}}}})
    return {}


def _signature_from_raw_tool_call(raw_call: Any) -> str | None:
    if not isinstance(raw_call, Mapping):
        return None
    extra_content = raw_call.get("extra_content")
    google = extra_content.get("google") if isinstance(extra_content, Mapping) else None
    if not isinstance(google, Mapping):
        return None
    return _valid_signature(google.get("thought_signature") or google.get("thoughtSignature"))


def _signature_map(message: BaseMessage) -> dict[str, str]:
    additional = getattr(message, "additional_kwargs", None)
    metadata = additional.get(PROVIDER_METADATA_KEY) if isinstance(additional, dict) else None
    if not isinstance(metadata, dict) or metadata.get("provider") != "gemini":
        return {}
    raw = metadata.get("tool_call_thought_signatures")
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, str] = {}
    for call_id, signature in raw.items():
        safe_id = str(call_id or "")[:120]
        safe_signature = _valid_signature(signature)
        if safe_id and safe_signature is not None:
            clean[safe_id] = safe_signature
    return clean


def sanitize_gemini_additional_kwargs(
    additional_kwargs: Any,
    *,
    allowed_call_ids: Sequence[str],
) -> dict[str, Any]:
    """Return only valid Gemini metadata bound to calls retained in history."""
    if not isinstance(additional_kwargs, dict):
        return {}
    metadata = additional_kwargs.get(PROVIDER_METADATA_KEY)
    if not isinstance(metadata, dict) or metadata.get("provider") != "gemini":
        return {}
    allowed = {str(call_id) for call_id in allowed_call_ids if call_id}
    raw = metadata.get("tool_call_thought_signatures")
    if not isinstance(raw, dict):
        return {}
    signatures: dict[str, str] = {}
    for call_id, signature in raw.items():
        safe_id = str(call_id or "")[:120]
        safe_signature = _valid_signature(signature)
        if safe_id in allowed and safe_signature is not None:
            signatures[safe_id] = safe_signature
    if not signatures:
        return {}
    return {
        PROVIDER_METADATA_KEY: {
            "provider": "gemini",
            "tool_call_thought_signatures": signatures,
        }
    }


def _bounded_json(value: Any, *, max_chars: int = _MAX_LEGACY_CONTEXT_CHARS) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        rendered = str(value)
    return rendered[:max_chars]


def _legacy_call_context(raw_calls: list[dict[str, Any]]) -> str:
    facts: list[dict[str, Any]] = []
    for raw_call in raw_calls[:3]:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        function = function if isinstance(function, dict) else {}
        arguments: Any = function.get("arguments") or "{}"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                arguments = arguments[:800]
        facts.append({
            "call_id": str(raw_call.get("id") or "")[:120],
            "name": str(function.get("name") or "")[:120],
            "arguments": arguments,
        })
    return (
        "Historical assistant tool-request context (normalized because the original Gemini "
        "thought signature is unavailable): "
        f"{_bounded_json(facts)}. This records a request only; it does not prove execution or success."
    )


def _legacy_result_context(tool_name: str, content: Any) -> str:
    return (
        f"Historical backend tool-result context for {tool_name or 'the prior request'}: "
        f"{_bounded_json(content)}. This is historical context only and does not authorize a current action."
    )


class GeminiChatOpenAI(ChatOpenAI):
    """ChatOpenAI transport with Gemini-only thought-signature preservation."""

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        choices = _response_dict(response).get("choices") or []
        for choice, generation in zip(choices, result.generations):
            raw_message = choice.get("message") if isinstance(choice, dict) else None
            raw_calls = raw_message.get("tool_calls") if isinstance(raw_message, dict) else None
            if not isinstance(raw_calls, list) or not isinstance(generation.message, AIMessage):
                continue
            signatures: dict[str, str] = {}
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict):
                    continue
                call_id = str(raw_call.get("id") or "")[:120]
                signature = _signature_from_raw_tool_call(raw_call)
                if call_id and signature is not None:
                    signatures[call_id] = signature
            if signatures:
                generation.message.additional_kwargs[PROVIDER_METADATA_KEY] = {
                    "provider": "gemini",
                    "tool_call_thought_signatures": signatures,
                }
        return result

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if not isinstance(input_, list) or not isinstance(payload.get("messages"), list):
            return payload

        normalized: list[dict[str, Any]] = []
        legacy_calls: dict[str, str] = {}
        for message, raw_message in zip(input_, payload["messages"]):
            if not isinstance(raw_message, dict):
                continue
            raw_calls = raw_message.get("tool_calls")
            if isinstance(message, AIMessage) and isinstance(raw_calls, list) and raw_calls:
                signatures = _signature_map(message)
                injected = 0
                for raw_call in raw_calls:
                    if not isinstance(raw_call, dict):
                        continue
                    call_id = str(raw_call.get("id") or "")[:120]
                    signature = signatures.get(call_id)
                    if signature is None:
                        continue
                    raw_call["extra_content"] = {"google": {"thought_signature": signature}}
                    injected += 1
                if injected == 0:
                    for raw_call in raw_calls:
                        if not isinstance(raw_call, dict):
                            continue
                        call_id = str(raw_call.get("id") or "")[:120]
                        function = raw_call.get("function")
                        function_name = str(function.get("name") or "")[:120] if isinstance(function, dict) else ""
                        if call_id:
                            legacy_calls[call_id] = function_name
                    normalized.append({"role": "assistant", "content": _legacy_call_context(raw_calls)})
                    continue
            if isinstance(message, ToolMessage) and message.tool_call_id in legacy_calls:
                normalized.append({
                    "role": "assistant",
                    "content": _legacy_result_context(legacy_calls[message.tool_call_id], message.content),
                })
                continue
            normalized.append(raw_message)
        payload["messages"] = normalized
        return payload
