import pytest

from backend.ai.gateway.errors import (
    LLMAuthenticationError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMSafetyRejectionError,
    LLMStructuredOutputError,
    require_gateway_error,
)
from backend.ai.gateway.models import LLMFallbackContract, LLMPurpose
from backend.ai.gateway.policies import DEFAULT_PROVIDER_ORDER, PURPOSE_POLICIES
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.tests.fakes.fake_llm_gateway import ScriptedLLMProviderAdapter


def test_every_bounded_purpose_has_an_immutable_policy():
    assert set(PURPOSE_POLICIES) == set(LLMPurpose)
    assert all(policy.provider_order == DEFAULT_PROVIDER_ORDER for policy in PURPOSE_POLICIES.values())


def test_policy_safety_boundaries():
    localization = PURPOSE_POLICIES[LLMPurpose.LOCALIZATION]
    pending = PURPOSE_POLICIES[LLMPurpose.PENDING_ACTION_DECISION]
    composition = PURPOSE_POLICIES[LLMPurpose.RESPONSE_COMPOSITION]
    tools = PURPOSE_POLICIES[LLMPurpose.TOOL_SELECTION]
    assert localization.maximum_provider_attempts == 3
    assert pending.deterministic_fallback is LLMFallbackContract.CLARIFY
    assert not pending.tools_allowed
    assert not composition.tools_allowed
    assert tools.tools_allowed and tools.deterministic_fallback is LLMFallbackContract.NO_TOOL


def test_error_flags_and_serialization_are_safe():
    rate = LLMRateLimitError(safe_metadata={"status_code": 429})
    auth = LLMAuthenticationError()
    safety = LLMSafetyRejectionError()
    assert rate.retry_same_provider and rate.fallback_next_provider
    assert not auth.retry_same_provider and auth.fallback_next_provider
    assert not safety.fallback_next_provider
    assert "raw provider failure" not in rate.to_info().model_dump_json()


def test_original_exception_is_chained_but_not_serialized():
    original = RuntimeError("secret provider response")
    try:
        raise LLMStructuredOutputError(safe_metadata={"reason": "validation_failed"}) from original
    except LLMStructuredOutputError as error:
        assert error.__cause__ is original
        assert "secret provider response" not in error.to_info().model_dump_json()


def test_business_or_tool_errors_cannot_be_normalized_accidentally():
    with pytest.raises(TypeError):
        require_gateway_error(RuntimeError("business validation failed"))


def test_registry_order_duplicate_missing_and_instance_isolation():
    first = LLMProviderRegistry()
    gemini = ScriptedLLMProviderAdapter("gemini", ["ok"])
    first.register("GEMINI", gemini)
    with pytest.raises(ValueError):
        first.register("gemini", gemini)
    ordered = first.ordered(("gemini", "groq"))
    assert ordered == (("gemini", gemini), ("groq", None))
    second = LLMProviderRegistry()
    assert second.registered_names() == ()


def test_invalid_request_is_terminal_by_contract():
    assert not LLMInvalidRequestError().fallback_next_provider
