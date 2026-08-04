import pytest
from types import SimpleNamespace
from langchain_core.messages import HumanMessage

from backend.ai.factory import LLMFactory
from backend.logic.engine import llm as llm_module
from backend.ai.providers.base import ProviderConfigurationError
from backend.ai.providers.groq_provider import GroqProvider


pytestmark = pytest.mark.anyio


async def test_fake_provider_rejected_when_real_provider_required(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "test")
    monkeypatch.setattr(llm_module.settings, "AWA_REAL_PROVIDER_REQUIRED", True, raising=False)
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")

    with pytest.raises(RuntimeError, match="real provider is required"):
        llm_module.get_llm()


async def test_fake_provider_rejected_in_live_mode(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "live")
    monkeypatch.setattr(llm_module.settings, "AWA_REAL_PROVIDER_REQUIRED", False, raising=False)
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")

    with pytest.raises(RuntimeError, match="not allowed in live"):
        llm_module.get_llm()


async def test_provider_missing_key_does_not_return_mock_success_when_real_required(monkeypatch):
    monkeypatch.setenv("AWA_REAL_PROVIDER_REQUIRED", "true")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    provider = GroqProvider()

    with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
        await provider.generate_response("You are helpful.", "hello")


async def test_deterministic_fake_still_works_in_explicit_test_mode(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "test")
    monkeypatch.setattr(llm_module.settings, "AWA_REAL_PROVIDER_REQUIRED", False, raising=False)
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")

    model = llm_module.get_llm().bind_tools([
        {
            "type": "function",
            "function": {
                "name": "booking_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ])
    response = await model.ainvoke([HumanMessage(content="Book cleaning tomorrow at 10 AM for Asha")])

    assert response.tool_calls
    assert response.tool_calls[0]["name"] == "booking_execute"


async def test_provider_fallback_all_failures_do_not_return_mock_success(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "live")
    monkeypatch.setattr(llm_module.settings, "AWA_REAL_PROVIDER_REQUIRED", True, raising=False)
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "groq")

    candidates: list[str] = []

    def fail_create_provider(provider_name: str):
        candidates.append(provider_name)
        raise ProviderConfigurationError(f"{provider_name} unavailable")

    monkeypatch.setattr(LLMFactory, "create_provider", fail_create_provider)

    context = SimpleNamespace(
        session_data=SimpleNamespace(
            state=SimpleNamespace(
                workflow_state="collecting_data",
                action_ledger=SimpleNamespace(pending_action=None, last_tool_result=None),
            )
        ),
        record_llm_usage=lambda usage: None,
    )

    result = await llm_module.run_provider_fallback(context, "You are helpful.", "hello")

    assert candidates == ["openai", "gemini"]
    assert "try again" in result.response_text.lower()
    assert "mock" not in result.response_text.lower()
    assert "fake" not in result.response_text.lower()
    assert "completed" not in result.response_text.lower()
    assert context.session_data.state.action_ledger.last_error.provider_error["error_type"] == "all_fallback_providers_failed"
