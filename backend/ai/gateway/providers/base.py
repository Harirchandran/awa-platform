"""Shared OpenAI-compatible adapter mechanics and safe error normalization."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from backend.ai.gateway.errors import (
    LLMAuthenticationError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidModelError,
    LLMInvalidRequestError,
    LLMNetworkError,
    LLMProviderServerError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMSafetyRejectionError,
    LLMTimeoutError,
    LLMToolProtocolError,
)
from backend.ai.gateway.models import (
    CanonicalToolCall,
    LLMMessageRole,
    LLMUsage,
    ProviderInvocationRequest,
    ProviderInvocationResult,
)
from backend.ai.providers.gemini_chat_adapter import PROVIDER_METADATA_KEY
from backend.ai.gateway.providers.capabilities import ProviderCapabilities


class AsyncChatClient(Protocol):
    async def ainvoke(self, messages: list[BaseMessage], **kwargs: Any) -> Any: ...


def _safe_metadata(provider: str, model: str, *, status: int | None, reason: str, exc: Exception) -> dict[str, Any]:
    metadata: dict[str, Any] = {"provider": provider, "model": model, "reason": reason}
    if status is not None:
        metadata["http_status_category"] = f"{status // 100}xx"
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        request_id = headers.get("x-request-id") or headers.get("request-id")
        if request_id:
            metadata["provider_request_id"] = str(request_id)[:120]
    return metadata


def normalize_provider_exception(exc: Exception, *, provider: str, model: str) -> LLMError:
    """Map provider/transport evidence without retaining raw bodies or exception text."""
    name = type(exc).__name__.lower()
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    status = int(status) if isinstance(status, int) else None
    code = str(getattr(exc, "code", "") or getattr(exc, "type", "") or "").lower()
    # Text is used only as bounded classification evidence and is never retained.
    evidence = f"{name} {code} {str(exc)[:500].lower()}"

    def mapped(kind: type[LLMError], reason: str) -> LLMError:
        return kind(safe_metadata=_safe_metadata(provider, model, status=status, reason=reason, exc=exc))

    if status == 429 or "ratelimit" in name or "resourceexhausted" in name:
        return mapped(LLMRateLimitError, "rate_limited")
    if status in (401, 403) or any(token in name for token in ("authentication", "unauthenticated", "permissiondenied")):
        return mapped(LLMAuthenticationError, "authentication_failed")
    if status == 408 or any(token in name for token in ("timeout", "deadlineexceeded")):
        return mapped(LLMTimeoutError, "deadline_exceeded")
    if any(token in name for token in ("connection", "connecterror", "network", "requesterror")):
        return mapped(LLMNetworkError, "connectivity_failure")
    if status is not None and 500 <= status <= 599:
        return mapped(LLMProviderServerError, "provider_5xx")
    if any(token in evidence for token in ("context_length", "context window", "maximum context", "too many tokens")):
        return mapped(LLMContextLengthError, "context_length_exceeded")
    if any(token in evidence for token in ("safety", "blocked_reason", "content_filter", "prohibited_content")):
        return mapped(LLMSafetyRejectionError, "safety_rejection")
    if any(token in evidence for token in ("model_not_found", "invalid model", "does not exist", "unknown model")):
        return mapped(LLMInvalidModelError, "invalid_or_unavailable_model")
    if status == 400 or "badrequest" in name or "invalidargument" in name:
        return mapped(LLMInvalidRequestError, "invalid_request")
    return mapped(LLMProviderServerError, "unclassified_provider_failure")


def canonical_messages_to_langchain(request: ProviderInvocationRequest, *, provider: str) -> list[BaseMessage]:
    converted: list[BaseMessage] = []
    for message in request.messages:
        if message.role == LLMMessageRole.SYSTEM:
            converted.append(SystemMessage(content=message.content))
        elif message.role == LLMMessageRole.USER:
            converted.append(HumanMessage(content=message.content))
        elif message.role == LLMMessageRole.ASSISTANT:
            additional_kwargs: dict[str, Any] = {}
            if provider == "gemini" and message.tool_calls:
                owned = message.provider_metadata.get("gemini")
                if owned:
                    additional_kwargs[PROVIDER_METADATA_KEY] = {"provider": "gemini", **owned}
            converted.append(AIMessage(
                content=message.content,
                tool_calls=[{"id": call.id, "name": call.name, "args": call.arguments, "type": "tool_call"} for call in message.tool_calls],
                additional_kwargs=additional_kwargs,
            ))
        else:
            result = message.tool_result
            assert result is not None
            content = json.dumps({
                "success": result.success,
                "message": result.message,
                "public_reference": result.public_reference,
                "safe_data": result.safe_data,
            }, ensure_ascii=True, separators=(",", ":"))
            converted.append(ToolMessage(content=content, tool_call_id=result.tool_call_id, name=result.tool_name))
    if request.response_schema:
        instruction = SystemMessage(content=(
            "Return exactly one JSON object matching this JSON Schema; do not add commentary: "
            + json.dumps(request.response_schema, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        ))
        insertion = 0
        while insertion < len(converted) and isinstance(converted[insertion], SystemMessage):
            insertion += 1
        converted.insert(insertion, instruction)
    return converted


def canonical_tool_schemas(request: ProviderInvocationRequest) -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    } for tool in request.tools]


def _usage(response: Any) -> LLMUsage | None:
    raw = getattr(response, "usage_metadata", None)
    if not isinstance(raw, dict):
        metadata = getattr(response, "response_metadata", None)
        raw = metadata.get("token_usage") if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return None
    input_tokens = raw.get("input_tokens", raw.get("prompt_tokens"))
    output_tokens = raw.get("output_tokens", raw.get("completion_tokens"))
    total_tokens = raw.get("total_tokens")
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens, estimated=False)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") in ("text", "output_text")]
        return "".join(str(part) for part in parts)
    return "" if content is None else str(content)[:32_000]


class OpenAICompatibleGatewayAdapter:
    provider_name = ""

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str | None,
        capabilities: ProviderCapabilities,
        client_factory: Callable[[float], AsyncChatClient],
    ) -> None:
        self.model_name = model_name
        self.capabilities = capabilities
        self._api_key = api_key
        self._client_factory = client_factory
        self.client_construction_count = 0

    def _provider_metadata(self, response: Any, calls: list[CanonicalToolCall]) -> dict[str, Any]:
        return {}

    async def invoke(self, request: ProviderInvocationRequest) -> ProviderInvocationResult:
        model = request.selected_model or self.model_name
        if not self._api_key:
            raise LLMProviderUnavailableError(safe_metadata={"provider": self.provider_name, "model": model, "reason": "missing_api_key"})
        if request.tools and not self.capabilities.supports_tools_for(model):
            raise LLMProviderUnavailableError(safe_metadata={"provider": self.provider_name, "model": model, "reason": "tools_unsupported"})
        messages = canonical_messages_to_langchain(request, provider=self.provider_name)
        tools = canonical_tool_schemas(request)
        self.client_construction_count += 1
        client = self._client_factory(request.timeout_seconds)
        try:
            response = await asyncio.wait_for(
                client.ainvoke(messages, **({"tools": tools} if tools else {})),
                timeout=request.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise LLMTimeoutError(safe_metadata={"provider": self.provider_name, "model": model, "reason": "gateway_deadline"}) from exc
        except LLMError:
            raise
        except Exception as exc:
            raise normalize_provider_exception(exc, provider=self.provider_name, model=model) from exc

        invalid_calls = getattr(response, "invalid_tool_calls", None)
        if invalid_calls:
            raise LLMToolProtocolError(safe_metadata={"provider": self.provider_name, "model": model, "reason": "malformed_tool_arguments"})
        calls: list[CanonicalToolCall] = []
        try:
            for call in getattr(response, "tool_calls", None) or []:
                arguments = call.get("args", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments are not an object")
                calls.append(CanonicalToolCall(id=str(call.get("id") or ""), name=str(call.get("name") or ""), arguments=arguments))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMToolProtocolError(safe_metadata={"provider": self.provider_name, "model": model, "reason": "malformed_tool_arguments"}) from exc

        response_metadata = getattr(response, "response_metadata", None)
        response_model = response_metadata.get("model_name") if isinstance(response_metadata, dict) else None
        return ProviderInvocationResult(
            content=_text_content(getattr(response, "content", "")),
            model=str(response_model or model),
            usage=_usage(response),
            tool_calls=calls,
            provider_metadata=self._provider_metadata(response, calls),
        )


__all__ = [
    "OpenAICompatibleGatewayAdapter",
    "ProviderCapabilities",
    "canonical_messages_to_langchain",
    "canonical_tool_schemas",
    "normalize_provider_exception",
]
