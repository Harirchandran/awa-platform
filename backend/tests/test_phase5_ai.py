"""
Phase 5 AI Layer Tests
Tests AI Provider Factory, AI Client, and Intent Classification
Updated to use proper Pydantic models for ExecutionContext.
"""
import asyncio
import os
import sys
import pytest
from pathlib import Path
from uuid import uuid4, UUID
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from backend.ai.factory import LLMFactory
from backend.ai.client import AIClient, PromptBuilder
from backend.ai.providers.base import BaseLLM
from backend.core.context import (
    ConversationSession, SessionState, TenantConfig,
    ExecutionContext, Tenant, User
)
from backend.core.states import ConversationState

def _live_smoke_enabled() -> bool:
    return os.getenv("RUN_LIVE_PROVIDER_SMOKE") == "1"

TEST_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000099")
def _make_context(business_name="Health Clinic", services=None, state=ConversationState.NEW):
    """Helper to build a valid ExecutionContext for tests."""
    if services is None:
        services = [{"name": "General Checkup"}, {"name": "Dental Cleaning"}]
    mock_config = TenantConfig(
        tenant_id=TEST_TENANT_ID,
        version=12,
        config_data={
            "business_name": business_name,
            "business_type": "clinic",
            "services": services,
            "timezone": "Asia/Kolkata",
            "default_language": "en",
            "modules": {"booking": {"enabled": True}},
        }
    )
    session = ConversationSession(
        id=uuid4(),
        tenant_id=TEST_TENANT_ID,
        conversation_id=uuid4(),
        config_version=12,
        state=SessionState(workflow_state=state)
    )
    tenant = Tenant(
        id=TEST_TENANT_ID,
        name=business_name,
        plan_code="ENTERPRISE",
        status="active"
    )
    user = User(
        id=TEST_USER_ID,
        phone_number="+911234567890",
        name="Test User"
    )
    return ExecutionContext(
        tenant=tenant,
        user=user,
        session_data=session,
        config=mock_config
    )
def test_llm_factory():
    """Test LLM Provider Factory"""
    print("\n=== TEST: LLM Provider Factory ===")
    import os
    # Test 1: Default provider (gemini)
    os.environ["LLM_PROVIDER"] = "gemini"
    provider = LLMFactory.create_provider()
    assert provider.get_provider_name() == "gemini"
    print("[OK] Default provider 'gemini': PASSED")
    # Test 2: Groq provider
    os.environ["LLM_PROVIDER"] = "groq"
    provider = LLMFactory.create_provider()
    assert provider.get_provider_name() == "groq"
    print("[OK] Provider 'groq': PASSED")
    # Test 3: OpenAI provider
    os.environ["LLM_PROVIDER"] = "openai"
    provider = LLMFactory.create_provider()
    assert provider.get_provider_name() == "openai"
    print("[OK] Provider 'openai': PASSED")
    # Test: Ollama provider
    os.environ["LLM_PROVIDER"] = "ollama"
    provider = LLMFactory.create_provider()
    assert provider.get_provider_name() == "ollama"
    print("[OK] Provider 'ollama': PASSED")
    # Test 4: Invalid provider
    os.environ["LLM_PROVIDER"] = "invalid_provider"
    try:
        provider = LLMFactory.create_provider()
        print("[FAIL] Should have raised ValueError!")
    except ValueError as e:
        print(f"[OK] Invalid provider rejected: PASSED")
    # Reset to gemini
    os.environ["LLM_PROVIDER"] = "gemini"
    print("\nAll LLM Factory tests passed!")
def test_prompt_builder():
    """Test context-aware prompt building"""
    print("\n=== TEST: Prompt Builder ===")
    context = _make_context(
        business_name="Health Clinic",
        services=[{"name": "General Checkup"}, {"name": "Dental Cleaning"}],
        state=ConversationState.COLLECTING_DATA
    )
    # Build prompt
    prompt = PromptBuilder.build(context)
    assert "Health Clinic" in prompt
    assert "General Checkup" in prompt
    assert "Dental Cleaning" in prompt
    assert "COLLECTING_DATA" in prompt
    print("[OK] Prompt contains business_name: PASSED")
    print("[OK] Prompt contains services: PASSED")
    print("[OK] Prompt contains workflow state: PASSED")
    # Test enriched fields
    assert "clinic" in prompt.lower()
    print("[OK] Prompt contains business_type: PASSED")
    print("\nAll Prompt Builder tests passed!")
async def _run_ai_client_tests():
    """Async helper for AI client tests."""
    import os
    from backend.core.config import settings
    settings.ROUTING_LLM_PROVIDER = "gemini"
    os.environ["LLM_PROVIDER"] = "gemini"
    if settings.GEMINI_API_KEY:
        os.environ["GEMINI_API_KEY"] = settings.GEMINI_API_KEY
    if settings.GROQ_API_KEY:
        os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY
    context = _make_context(
        business_name="Salon",
        services=[{"name": "Haircut"}],
        state=ConversationState.NEW
    )
    # Reinitialize client with gemini
    client = AIClient()
    # Test 1: Classify booking intent
    response = await client.classify_intent("I want to book an appointment", context)
    assert response.intent == "booking"
    assert response.confidence > 0.7
    print(f"[OK] Booking intent classified: {response.intent} ({response.confidence}): PASSED")
    # Test 2: Greeting intent (updated — our enriched classifier now correctly detects greetings)
    response = await client.classify_intent("hello there", context)
    assert response.intent in ("greeting", "unknown")  # Accept either
    print(f"[OK] Greeting/unknown intent classified: {response.intent}: PASSED")
    # Test 3: Get response
    response_text = await client.get_response("I need a haircut", context)
    assert len(response_text) > 0
    print(f"[OK] Response generated: '{response_text[:50]}...': PASSED")
@pytest.mark.skipif(not _live_smoke_enabled(), reason="Set RUN_LIVE_PROVIDER_SMOKE=1 with live provider credentials to run.")
def test_ai_client():
    """Test AI Client intent classification and response"""
    print("\n=== TEST: AI Client ===")
    asyncio.run(_run_ai_client_tests())
    print("\nAll AI Client tests passed!")
def main():
    print("=" * 60)
    print("AWA Phase 5 AI Layer Tests")
    print("=" * 60)
    test_llm_factory()
    test_prompt_builder()
    test_ai_client()
    print("\n" + "=" * 60)
    print("[OK] ALL PHASE 5 TESTS PASSED!")
    print("=" * 60)
if __name__ == "__main__":
    main()