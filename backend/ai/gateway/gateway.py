"""Provider-neutral gateway with one privacy-safe observable request lifecycle."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from pydantic import SecretStr

from backend.ai.gateway.errors import (
    LLMAllProvidersFailedError,
    LLMError,
    LLMInvalidRequestError,
    LLMProviderUnavailableError,
    LLMStructuredOutputError,
    LLMToolProtocolError,
    require_gateway_error,
)
from backend.ai.gateway.models import (
    LLMAttempt,
    LLMAttemptOutcome,
    LLMParseStatus,
    LLMPurpose,
    LLMRequest,
    LLMResult,
    ProviderInvocationRequest,
)
from backend.ai.gateway.normalization import normalize_messages_for_provider
from backend.ai.gateway.parsing import parse_structured_output
from backend.ai.gateway.policies import DEFAULT_PROVIDER_ORDER, TIMEOUT_SECONDS, get_purpose_policy
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.ai.gateway.telemetry import (
    LLMAttemptCompletedEvent,
    LLMFallbackTransitionEvent,
    LLMFinalOutcome,
    LLMGatewayTelemetryRecorder,
    LLMPolicyRejectionEvent,
    LLMRequestCompletedEvent,
    LLMRequestStartedEvent,
    LLMTelemetryParseStatus,
    NoOpLLMGatewayTelemetryRecorder,
    current_rollout_mode,
    deterministic_sample,
    record_safely,
    safe_correlation_fields,
    safe_operational_identifier,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _telemetry_parse_status(
    status: LLMParseStatus,
    error: LLMError | None = None,
) -> LLMTelemetryParseStatus:
    if isinstance(error, LLMToolProtocolError):
        return LLMTelemetryParseStatus.TOOL_PROTOCOL_FAILED
    if status is LLMParseStatus.SUCCESS:
        return LLMTelemetryParseStatus.SUCCESS
    if status is LLMParseStatus.NOT_REQUESTED:
        return LLMTelemetryParseStatus.NOT_REQUESTED
    reason = str((error.safe_metadata if error else {}).get("reason") or "")
    if reason == "oversized_output":
        return LLMTelemetryParseStatus.OVERSIZED_OUTPUT
    if reason in {"invalid_fence", "unsupported_fence", "not_an_object", "unsupported_output_type"}:
        return LLMTelemetryParseStatus.MALFORMED_JSON
    return LLMTelemetryParseStatus.SCHEMA_VALIDATION_FAILED


class LLMGateway:
    def __init__(
        self,
        registry: LLMProviderRegistry,
        *,
        provider_order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
        telemetry: LLMGatewayTelemetryRecorder | None = None,
        telemetry_enabled: bool | None = None,
        telemetry_sample_rate: float = 1.0,
        telemetry_include_usage: bool = True,
        telemetry_correlation_key: SecretStr | None = None,
    ) -> None:
        if not provider_order or len(set(provider_order)) != len(provider_order):
            raise ValueError("gateway provider order must be non-empty and unique")
        unknown = set(provider_order) - set(DEFAULT_PROVIDER_ORDER)
        if unknown:
            raise ValueError("gateway provider order contains unknown providers")
        if not 0.0 <= telemetry_sample_rate <= 1.0:
            raise ValueError("telemetry sample rate must be between zero and one")
        self.registry = registry
        self.provider_order = tuple(provider_order)
        self.telemetry = telemetry or NoOpLLMGatewayTelemetryRecorder()
        self.telemetry_enabled = bool(telemetry is not None) if telemetry_enabled is None else telemetry_enabled
        self.telemetry_sample_rate = float(telemetry_sample_rate)
        self.telemetry_include_usage = telemetry_include_usage
        self.telemetry_correlation_key = telemetry_correlation_key

    @staticmethod
    def _validate_against_policy(request: LLMRequest) -> None:
        policy = get_purpose_policy(request.purpose)
        if request.timeout_class != policy.timeout_class:
            raise LLMInvalidRequestError(safe_metadata={"reason": "timeout_policy_mismatch"})
        if request.deterministic_fallback != policy.deterministic_fallback:
            raise LLMInvalidRequestError(safe_metadata={"reason": "fallback_policy_mismatch"})
        if request.tools and not policy.tools_allowed:
            raise LLMInvalidRequestError(safe_metadata={"reason": "tools_forbidden"})
        if len(request.messages) > policy.maximum_messages:
            raise LLMInvalidRequestError(safe_metadata={"reason": "purpose_message_limit"})
        context_size = sum(len(message.content) for message in request.messages) + len(
            json.dumps(request.safe_context, ensure_ascii=True, sort_keys=True)
        )
        if context_size > policy.maximum_context_chars:
            raise LLMInvalidRequestError(safe_metadata={"reason": "purpose_context_limit"})
        if policy.structured_output and request.response_schema is None:
            raise LLMInvalidRequestError(safe_metadata={"reason": "response_schema_required"})
        if not policy.structured_output and request.response_schema is not None:
            raise LLMInvalidRequestError(safe_metadata={"reason": "response_schema_forbidden"})

    @staticmethod
    def _can_fallback(request: LLMRequest, error: LLMError) -> bool:
        if request.authoritative_side_effects_occurred:
            return False
        if isinstance(error, LLMToolProtocolError):
            return request.purpose == LLMPurpose.TOOL_SELECTION
        return error.fallback_next_provider

    async def invoke(self, request: LLMRequest) -> LLMResult:
        started = perf_counter()
        started_at = _now()
        request_id = uuid4().hex
        rollout_mode = current_rollout_mode()
        sampled = self.telemetry_enabled and deterministic_sample(request_id, self.telemetry_sample_rate)
        correlations = safe_correlation_fields(self.telemetry_correlation_key) if sampled else {
            "trace_id": None, "tenant_correlation": None, "conversation_correlation": None,
        }
        policy = get_purpose_policy(request.purpose)
        attempts: list[LLMAttempt] = []
        completed = False

        def emit(event) -> None:
            if sampled:
                record_safely(self.telemetry, event)

        def complete(
            *,
            outcome: LLMFinalOutcome,
            error_code: str | None,
            parse_status: LLMTelemetryParseStatus,
            provider: str | None = None,
            model: str | None = None,
        ) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            emit(LLMRequestCompletedEvent(
                gateway_request_id=request_id,
                purpose=request.purpose,
                rollout_mode=rollout_mode,
                completed_at=_now(),
                total_latency_ms=int((perf_counter() - started) * 1000),
                selected_provider=provider if outcome is LLMFinalOutcome.SUCCESS else None,
                selected_model=model if outcome is LLMFinalOutcome.SUCCESS else None,
                attempt_count=len(attempts),
                fallback_used=len(attempts) > 1,
                final_outcome=outcome,
                final_error_code=error_code,
                parse_status=parse_status,
                side_effect_boundary_declared=request.authoritative_side_effects_occurred,
                **correlations,
            ))

        emit(LLMRequestStartedEvent(
            gateway_request_id=request_id,
            purpose=request.purpose,
            rollout_mode=rollout_mode,
            timeout_class=request.timeout_class.value,
            tools_allowed=policy.tools_allowed,
            tool_count=len(request.tools),
            history_allowed=policy.maximum_messages > 1,
            message_count=len(request.messages),
            side_effect_boundary_declared=request.authoritative_side_effects_occurred,
            started_at=started_at,
            **correlations,
        ))

        try:
            try:
                self._validate_against_policy(request)
            except LLMError as error:
                reason = str(error.safe_metadata.get("reason") or "")
                code = "timeout_policy_mismatch" if reason == "timeout_policy_mismatch" else "invalid_request"
                emit(LLMPolicyRejectionEvent(
                    gateway_request_id=request_id,
                    purpose=request.purpose,
                    rollout_mode=rollout_mode,
                    normalized_error_code=code,
                    **correlations,
                ))
                complete(
                    outcome=LLMFinalOutcome.REJECTED,
                    error_code=error.code,
                    parse_status=LLMTelemetryParseStatus.NOT_REQUESTED,
                )
                return LLMResult(
                    content=None,
                    attempts=[],
                    latency_ms=int((perf_counter() - started) * 1000),
                    parse_status=LLMParseStatus.NOT_REQUESTED,
                    error=error.to_info(),
                )

            configured = [provider for provider in self.provider_order if provider in policy.provider_order]
            configured = configured[:policy.maximum_provider_attempts]
            last_error: LLMError | None = None

            for index, provider_name in enumerate(configured, start=1):
                adapter = self.registry.get(provider_name)
                model_name = getattr(adapter, "model_name", None) if adapter is not None else None
                parse_status = LLMParseStatus.NOT_REQUESTED
                attempt_started = perf_counter()
                try:
                    if adapter is None:
                        raise LLMProviderUnavailableError(safe_metadata={"provider": provider_name})
                    normalized_messages = normalize_messages_for_provider(request.messages, provider_name)
                    schema_contract = request.response_schema.model_json_schema() if request.response_schema else None
                    provider_result = await adapter.invoke(ProviderInvocationRequest(
                        gateway_request_id=request_id,
                        purpose=request.purpose,
                        messages=normalized_messages,
                        safe_context=request.safe_context,
                        selected_model=model_name,
                        timeout_seconds=TIMEOUT_SECONDS[policy.timeout_class],
                        response_schema=schema_contract,
                        tools=request.tools,
                        language=request.language,
                        attempt_number=index,
                    ))
                    if provider_result.tool_calls and not policy.tools_allowed:
                        raise LLMToolProtocolError(safe_metadata={"reason": "unexpected_tool_calls"})
                    if policy.structured_output:
                        content = parse_structured_output(provider_result.content, request.response_schema)
                        parse_status = LLMParseStatus.SUCCESS
                    else:
                        if provider_result.content is not None and not isinstance(provider_result.content, str):
                            raise LLMInvalidRequestError(safe_metadata={"reason": "text_output_required"})
                        content = provider_result.content
                    attempt = LLMAttempt(
                        provider=provider_name,
                        model=provider_result.model or model_name,
                        attempt_number=index,
                        latency_ms=int((perf_counter() - attempt_started) * 1000),
                        outcome=LLMAttemptOutcome.SUCCESS,
                        parse_status=parse_status,
                        usage=provider_result.usage,
                    )
                    attempts.append(attempt)
                    usage = provider_result.usage if self.telemetry_include_usage else None
                    telemetry_model = safe_operational_identifier(attempt.model)
                    emit(LLMAttemptCompletedEvent(
                        gateway_request_id=request_id,
                        purpose=request.purpose,
                        provider=provider_name,
                        model=telemetry_model,
                        attempt_number=index,
                        latency_ms=attempt.latency_ms,
                        outcome="success",
                        parse_status=_telemetry_parse_status(parse_status),
                        input_token_count=usage.input_tokens if usage else None,
                        output_token_count=usage.output_tokens if usage else None,
                        total_token_count=usage.total_tokens if usage else None,
                        usage_estimated=bool(usage and usage.estimated),
                        tool_call_count=len(provider_result.tool_calls),
                        **correlations,
                    ))
                    complete(
                        outcome=LLMFinalOutcome.SUCCESS,
                        error_code=None,
                        parse_status=_telemetry_parse_status(parse_status),
                        provider=provider_name,
                        model=telemetry_model,
                    )
                    return LLMResult(
                        content=content,
                        provider=provider_name,
                        model=attempt.model,
                        attempts=attempts,
                        latency_ms=int((perf_counter() - started) * 1000),
                        usage=provider_result.usage,
                        parse_status=parse_status,
                        fallback_used=len(attempts) > 1,
                        tool_calls=provider_result.tool_calls,
                        provider_metadata=provider_result.provider_metadata,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    try:
                        error = require_gateway_error(exc)
                    except TypeError:
                        raise
                    last_error = error
                    if isinstance(error, LLMStructuredOutputError):
                        parse_status = LLMParseStatus.FAILED
                    fallback_eligible = self._can_fallback(request, error)
                    next_provider = configured[index] if index < len(configured) and fallback_eligible else None
                    attempt = LLMAttempt(
                        provider=provider_name,
                        model=model_name,
                        attempt_number=index,
                        latency_ms=int((perf_counter() - attempt_started) * 1000),
                        outcome=LLMAttemptOutcome.ERROR,
                        normalized_error_code=error.code,
                        parse_status=parse_status,
                    )
                    attempts.append(attempt)
                    telemetry_parse = _telemetry_parse_status(parse_status, error)
                    telemetry_model = safe_operational_identifier(model_name)
                    emit(LLMAttemptCompletedEvent(
                        gateway_request_id=request_id,
                        purpose=request.purpose,
                        provider=provider_name,
                        model=telemetry_model,
                        attempt_number=index,
                        latency_ms=attempt.latency_ms,
                        outcome="error",
                        normalized_error_code=error.code,
                        parse_status=telemetry_parse,
                        tool_call_count=0,
                        fallback_next_provider=next_provider,
                        **correlations,
                    ))
                    if next_provider:
                        emit(LLMFallbackTransitionEvent(
                            gateway_request_id=request_id,
                            purpose=request.purpose,
                            attempt_number=index,
                            from_provider=provider_name,
                            to_provider=next_provider,
                            normalized_reason=error.code,
                            **correlations,
                        ))
                    if not fallback_eligible:
                        complete(
                            outcome=LLMFinalOutcome.ERROR,
                            error_code=error.code,
                            parse_status=telemetry_parse,
                        )
                        return LLMResult(
                            content=None,
                            provider=provider_name,
                            model=model_name,
                            attempts=attempts,
                            latency_ms=int((perf_counter() - started) * 1000),
                            parse_status=parse_status,
                            fallback_used=len(attempts) > 1,
                            error=error.to_info(),
                        )

            exhaustion = LLMAllProvidersFailedError(safe_metadata={"attempt_count": len(attempts)})
            if last_error is not None:
                exhaustion.__cause__ = last_error
            final_parse = attempts[-1].parse_status if attempts else LLMParseStatus.NOT_REQUESTED
            complete(
                outcome=LLMFinalOutcome.ERROR,
                error_code=exhaustion.code,
                parse_status=_telemetry_parse_status(final_parse, last_error),
            )
            return LLMResult(
                content=None,
                attempts=attempts,
                latency_ms=int((perf_counter() - started) * 1000),
                parse_status=final_parse,
                fallback_used=len(attempts) > 1,
                error=exhaustion.to_info(),
            )
        except asyncio.CancelledError:
            complete(
                outcome=LLMFinalOutcome.CANCELLED,
                error_code=None,
                parse_status=_telemetry_parse_status(
                    attempts[-1].parse_status if attempts else LLMParseStatus.NOT_REQUESTED
                ),
            )
            raise


__all__ = ["LLMGateway"]
