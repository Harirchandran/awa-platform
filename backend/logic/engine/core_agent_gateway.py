"""Narrow rollout bridge between the core graph and the central LLM gateway.

The bridge converts bounded messages and tool proposals only. Tool execution,
workflow state, business truth, confirmation, OTP, and persistence stay in the
existing core graph and ToolRegistry path.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from enum import Enum
from time import perf_counter
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    CanonicalToolCall,
    CanonicalToolDefinition,
    CanonicalToolResult,
    LLMFallbackContract,
    LLMMessageRole,
    LLMParseStatus,
    LLMPurpose,
    LLMRequest,
    LLMResult,
    LLMTimeoutClass,
)
from backend.ai.providers.gemini_chat_adapter import (
    PROVIDER_METADATA_KEY,
    sanitize_gemini_additional_kwargs,
)
from backend.ai.gateway.telemetry import (
    LLMCallerValidationOutcome,
    LLMFinalOutcome,
    LLMLegacyInvocationEvent,
    LLMShadowComparisonEvent,
    emit_configured_event,
    llm_rollout_context,
    normalize_legacy_error,
    safe_correlation_fields,
    safe_operational_identifier,
)
from backend.core.config import Settings, settings


class CoreAgentGatewayMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


def resolve_core_agent_gateway_mode(settings: Settings) -> CoreAgentGatewayMode:
    if not settings.LLM_GATEWAY_ENABLED:
        return CoreAgentGatewayMode.LEGACY
    return CoreAgentGatewayMode(settings.LLM_GATEWAY_CORE_AGENT_MODE)


def classify_core_invocation(*, tools_available: bool, authoritative_tool_result: bool) -> LLMPurpose:
    if authoritative_tool_result:
        return LLMPurpose.RESPONSE_COMPOSITION
    if tools_available:
        return LLMPurpose.TOOL_SELECTION
    return LLMPurpose.KNOWLEDGE_ANSWER


def _text(value: Any, *, maximum: int = 4_000) -> str:
    if isinstance(value, str):
        rendered = value
    elif isinstance(value, list):
        rendered = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in value
        )
    elif value is None:
        rendered = ""
    else:
        rendered = str(value)
    return rendered[:maximum]


def _bounded_system_parts(value: Any) -> list[str]:
    """Retain both platform-rule and trailing grounded-context regions."""
    rendered = _text(value, maximum=1_000_000)
    if len(rendered) <= 4_000:
        return [rendered] if rendered else []
    return [rendered[:4_000], rendered[-4_000:]]


def _canonical_call(raw: Any) -> CanonicalToolCall:
    if not isinstance(raw, dict):
        raise ValueError("tool call must be an object")
    arguments = raw.get("args", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return CanonicalToolCall(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        arguments=arguments,
    )


def langchain_messages_to_canonical(messages: list[Any]) -> list[CanonicalLLMMessage]:
    """Convert only the four graph message types and retain bounded correlation."""
    converted: list[CanonicalLLMMessage] = []
    call_names: dict[str, str] = {}
    for message in messages:
        if isinstance(message, SystemMessage):
            converted.extend(
                CanonicalLLMMessage(role=LLMMessageRole.SYSTEM, content=content)
                for content in _bounded_system_parts(message.content)
            )
        elif isinstance(message, HumanMessage):
            content = _text(message.content)
            if content:
                converted.append(CanonicalLLMMessage(role=LLMMessageRole.USER, content=content))
        elif isinstance(message, AIMessage):
            calls = [_canonical_call(call) for call in (message.tool_calls or [])[:4]]
            for call in calls:
                call_names[call.id] = call.name
            provider_metadata: dict[str, dict[str, Any]] = {}
            gemini_kwargs = sanitize_gemini_additional_kwargs(
                getattr(message, "additional_kwargs", None),
                allowed_call_ids=[call.id for call in calls],
            )
            wrapped = gemini_kwargs.get(PROVIDER_METADATA_KEY)
            if isinstance(wrapped, dict):
                signatures = wrapped.get("tool_call_thought_signatures")
                if signatures:
                    provider_metadata["gemini"] = {
                        "tool_call_thought_signatures": signatures,
                    }
            content = _text(message.content)
            if content or calls:
                converted.append(CanonicalLLMMessage(
                    role=LLMMessageRole.ASSISTANT,
                    content=content,
                    tool_calls=calls,
                    provider_metadata=provider_metadata,
                ))
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id or "")
            tool_name = str(getattr(message, "name", None) or call_names.get(call_id) or "unknown_tool")
            content = _text(message.content, maximum=1_000)
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                payload = {}
            success = payload.get("success") is True if isinstance(payload, dict) else False
            converted.append(CanonicalLLMMessage(
                role=LLMMessageRole.TOOL,
                tool_result=CanonicalToolResult(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    success=success,
                    message=content or None,
                ),
            ))
    if not converted:
        raise ValueError("core invocation has no supported messages")
    return converted


def _composition_messages(messages: list[CanonicalLLMMessage]) -> list[CanonicalLLMMessage]:
    """Keep the established system constraints and only the current tool turn."""
    system = [message for message in messages if message.role == LLMMessageRole.SYSTEM]
    last_user = max(
        (index for index, message in enumerate(messages) if message.role == LLMMessageRole.USER),
        default=0,
    )
    current = messages[last_user:]
    result = [message for message in system if message not in current] + current
    return result[-8:]


def langchain_tools_to_canonical(tools: list[Any]) -> list[CanonicalToolDefinition]:
    converted: list[CanonicalToolDefinition] = []
    names: set[str] = set()
    for raw in tools:
        if not isinstance(raw, dict) or raw.get("type") != "function":
            raise ValueError("unsupported tool schema")
        function = raw.get("function")
        if not isinstance(function, dict):
            raise ValueError("unsupported tool schema")
        name = str(function.get("name") or "")
        if name in names:
            raise ValueError("duplicate tool name")
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type", "object") != "object":
            raise ValueError("unsupported tool parameters")
        names.add(name)
        converted.append(CanonicalToolDefinition(
            name=name,
            description=str(function.get("description") or "")[:1_000],
            input_schema=parameters,
        ))
    return converted


def build_core_agent_gateway_request(
    messages: list[Any],
    tools: list[Any],
    *,
    authoritative_tool_result: bool,
) -> LLMRequest:
    purpose = classify_core_invocation(
        tools_available=bool(tools),
        authoritative_tool_result=authoritative_tool_result,
    )
    canonical_messages = langchain_messages_to_canonical(messages)
    if purpose == LLMPurpose.RESPONSE_COMPOSITION:
        canonical_messages = _composition_messages(canonical_messages)
        canonical_tools: list[CanonicalToolDefinition] = []
        timeout = LLMTimeoutClass.COMPOSITION_STANDARD
        fallback = LLMFallbackContract.ORIGINAL_BACKEND_TEXT
    elif purpose == LLMPurpose.TOOL_SELECTION:
        canonical_tools = langchain_tools_to_canonical(tools)
        timeout = LLMTimeoutClass.TOOL_SELECTION_STANDARD
        fallback = LLMFallbackContract.NO_TOOL
    else:
        canonical_tools = []
        timeout = LLMTimeoutClass.KNOWLEDGE_STANDARD
        fallback = LLMFallbackContract.SAFE_NOT_ENOUGH_INFORMATION
    return LLMRequest(
        purpose=purpose,
        messages=canonical_messages,
        safe_context={"authoritative_tool_result": authoritative_tool_result},
        tools=canonical_tools,
        timeout_class=timeout,
        deterministic_fallback=fallback,
        authoritative_side_effects_occurred=authoritative_tool_result,
    )


def gateway_result_to_ai_message(result: LLMResult, *, fallback_text: str) -> AIMessage:
    # The production graph has no approved parallel-business-write contract.
    # Reject multiple proposals before any can reach ToolRegistry.
    if result.error is not None or len(result.tool_calls) > 1:
        return AIMessage(content=fallback_text)
    if not result.tool_calls and not str(result.content or "").strip():
        return AIMessage(content=fallback_text)
    additional_kwargs: dict[str, Any] = {}
    if result.provider == "gemini" and result.tool_calls:
        owned = result.provider_metadata.get("gemini") if isinstance(result.provider_metadata, dict) else None
        if isinstance(owned, dict):
            sanitized = sanitize_gemini_additional_kwargs(
                {PROVIDER_METADATA_KEY: {"provider": "gemini", **owned}},
                allowed_call_ids=[call.id for call in result.tool_calls],
            )
            additional_kwargs.update(sanitized)
    return AIMessage(
        content=str(result.content or ""),
        tool_calls=[
            {"id": call.id, "name": call.name, "args": call.arguments, "type": "tool_call"}
            for call in result.tool_calls
        ],
        additional_kwargs=additional_kwargs,
        response_metadata={
            "awa_gateway_provider": result.provider,
            "model_name": result.model,
            "awa_gateway_fallback_used": result.fallback_used,
            "awa_gateway_parse_status": result.parse_status.value,
            "awa_gateway_usage": result.usage.model_dump() if result.usage else None,
        },
    )


def _result_kind(message: AIMessage) -> str:
    if message.tool_calls:
        return "tool_call"
    if str(message.content or "").strip():
        return "text"
    return "empty"


def record_core_agent_shadow_comparison(
    legacy: AIMessage,
    gateway_message: AIMessage,
    result: LLMResult | None,
    recorder: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    legacy_text = str(legacy.content or "")
    gateway_text = str(gateway_message.content or "")
    legacy_names = sorted(str(call.get("name") or "") for call in legacy.tool_calls)
    gateway_names = sorted(str(call.get("name") or "") for call in gateway_message.tool_calls)
    legacy_keys = sorted(sorted(str(key) for key in (call.get("args") or {})) for call in legacy.tool_calls)
    gateway_keys = sorted(sorted(str(key) for key in (call.get("args") or {})) for call in gateway_message.tool_calls)
    metadata = {
        "legacy_result_kind": _result_kind(legacy),
        "gateway_result_kind": _result_kind(gateway_message),
        "legacy_has_text": bool(legacy_text.strip()),
        "gateway_has_text": bool(gateway_text.strip()),
        "response_hash_equal": hashlib.sha256(legacy_text.encode()).digest()
        == hashlib.sha256(gateway_text.encode()).digest(),
        "legacy_tool_call_count": len(legacy.tool_calls),
        "gateway_tool_call_count": len(gateway_message.tool_calls),
        "gateway_provider": result.provider if result else None,
        "gateway_fallback_used": bool(result and result.fallback_used),
        "gateway_parse_status": result.parse_status.value if result else "failed",
        "legacy_tool_name_set": legacy_names,
        "gateway_tool_name_set": gateway_names,
        "tool_name_set_equal": legacy_names == gateway_names,
        "tool_count_equal": len(legacy.tool_calls) == len(gateway_message.tool_calls),
        "argument_key_set_equal": legacy_keys == gateway_keys,
    }
    try:
        if recorder is not None:
            recorder(metadata)
        else:
            purpose = (
                LLMPurpose.TOOL_SELECTION
                if legacy.tool_calls or gateway_message.tool_calls
                else LLMPurpose.KNOWLEDGE_ANSWER
            )
            emit_configured_event(LLMShadowComparisonEvent(
                purpose=purpose,
                legacy_result_kind=_result_kind(legacy),
                gateway_result_kind=_result_kind(gateway_message),
                result_kind_equal=_result_kind(legacy) == _result_kind(gateway_message),
                response_hash_equal=metadata["response_hash_equal"],
                tool_name_set_equal=metadata["tool_name_set_equal"],
                tool_count_equal=metadata["tool_count_equal"],
                argument_key_set_equal=metadata["argument_key_set_equal"],
                caller_validation=(
                    LLMCallerValidationOutcome.ACCEPTED
                    if result is not None and result.error is None
                    else LLMCallerValidationOutcome.REJECTED_BUSINESS_VALIDATION
                ),
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
    except Exception:
        return None


async def _invoke_gateway(
    messages: list[Any],
    tools: list[Any],
    *,
    authoritative_tool_result: bool,
    fallback_text: str,
    gateway: LLMGateway | None,
    rollout_mode: str,
) -> tuple[AIMessage, LLMResult | None]:
    try:
        request = build_core_agent_gateway_request(
            messages,
            tools,
            authoritative_tool_result=authoritative_tool_result,
        )
        if gateway is None:
            from backend.ai.gateway.runtime import get_runtime_llm_gateway

            gateway = get_runtime_llm_gateway()
        with llm_rollout_context(rollout_mode):
            result = await gateway.invoke(request)
        if authoritative_tool_result and result.tool_calls:
            return AIMessage(content=fallback_text), result
        return gateway_result_to_ai_message(result, fallback_text=fallback_text), result
    except asyncio.CancelledError:
        raise
    except Exception:
        return AIMessage(content=fallback_text), None


async def invoke_core_agent_model(
    messages: list[Any],
    tools: list[Any],
    *,
    mode: CoreAgentGatewayMode,
    authoritative_tool_result: bool,
    fallback_text: str,
    legacy_model: Any | None = None,
    gateway: LLMGateway | None = None,
    shadow_recorder: Callable[[dict[str, Any]], None] | None = None,
) -> AIMessage:
    purpose = classify_core_invocation(
        tools_available=bool(tools),
        authoritative_tool_result=authoritative_tool_result,
    )

    async def invoke_legacy(rollout_mode: str) -> AIMessage:
        if legacy_model is None:
            raise RuntimeError("legacy core-agent model is required")
        started = perf_counter()
        provider = safe_operational_identifier(getattr(legacy_model, "provider", None), maximum=40)
        model = safe_operational_identifier(
            getattr(legacy_model, "model_name", None) or getattr(legacy_model, "model", None)
        )
        try:
            response = await legacy_model.ainvoke(messages)
        except asyncio.CancelledError as exc:
            emit_configured_event(LLMLegacyInvocationEvent(
                purpose=purpose, rollout_mode=rollout_mode, provider=provider, model=model,
                latency_ms=int((perf_counter() - started) * 1000),
                outcome=LLMFinalOutcome.CANCELLED,
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
            raise
        except Exception as exc:
            emit_configured_event(LLMLegacyInvocationEvent(
                purpose=purpose, rollout_mode=rollout_mode, provider=provider, model=model,
                latency_ms=int((perf_counter() - started) * 1000), outcome=LLMFinalOutcome.ERROR,
                normalized_error_code=normalize_legacy_error(exc),
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
            raise
        emit_configured_event(LLMLegacyInvocationEvent(
            purpose=purpose, rollout_mode=rollout_mode, provider=provider, model=model,
            latency_ms=int((perf_counter() - started) * 1000), outcome=LLMFinalOutcome.SUCCESS,
            **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
        ), settings)
        return response

    if mode == CoreAgentGatewayMode.LEGACY:
        return await invoke_legacy("legacy")

    if mode == CoreAgentGatewayMode.SHADOW:
        legacy = await invoke_legacy("shadow")
        try:
            gateway_message, result = await _invoke_gateway(
                messages,
                tools,
                authoritative_tool_result=authoritative_tool_result,
                fallback_text=fallback_text,
                gateway=gateway,
                rollout_mode="shadow",
            )
            record_core_agent_shadow_comparison(
                legacy, gateway_message, result, shadow_recorder
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return legacy

    gateway_message, _result = await _invoke_gateway(
        messages,
        tools,
        authoritative_tool_result=authoritative_tool_result,
        fallback_text=fallback_text,
        gateway=gateway,
        rollout_mode="authoritative",
    )
    return gateway_message


__all__ = [
    "CoreAgentGatewayMode",
    "build_core_agent_gateway_request",
    "classify_core_invocation",
    "gateway_result_to_ai_message",
    "invoke_core_agent_model",
    "langchain_messages_to_canonical",
    "langchain_tools_to_canonical",
    "record_core_agent_shadow_comparison",
    "resolve_core_agent_gateway_mode",
]
