import os
from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.logic.engine.prompt import build_system_instructions
pytestmark = pytest.mark.anyio
def _live_smoke_enabled() -> bool:
    return os.getenv("RUN_LIVE_PROVIDER_SMOKE") == "1"
@pytest.mark.skipif(not _live_smoke_enabled(), reason="Set RUN_LIVE_PROVIDER_SMOKE=1 with live provider credentials to run.")
async def test_live_provider_returns_customer_safe_response():
    real_required = os.getenv("AWA_REAL_PROVIDER_REQUIRED", "").lower() in {"1", "true", "yes", "y"}
    if os.getenv("AWA_INTEGRATION_MODE") != "live" and not real_required:
        pytest.skip("AWA_INTEGRATION_MODE=live or AWA_REAL_PROVIDER_REQUIRED=true is required for real provider smoke.")
    provider = (os.getenv("AGENT_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "groq").lower()
    if provider in {"deterministic_fake", "fake_tool", "fake", "mock"}:
        pytest.fail("Real provider smoke is invalid with a fake provider.")
    if provider == "groq" and not os.getenv("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY is required for Groq live provider smoke.")
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for OpenAI live provider smoke.")
    from backend.logic.engine.llm import get_llm
    tenant_id = uuid4()
    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Live Smoke Clinic", plan_code="enterprise", status="active", ai_mode="concierge"),
        user=User(id=uuid4(), phone_number="+15550001111", name="Live Smoke"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Live Smoke Clinic",
                "business_type": "clinic",
                "ai_tone": "brief and professional",
                "modules": {},
                "services": [{"id": "checkup", "name": "General Checkup", "duration_mins": 30}],
            },
        ),
        incoming_message={"text": "Can I book a checkup tomorrow?", "channel_type": "awa_chat"},
    )
    system_instructions = build_system_instructions(
        context,
        memory_context="Recent conversation, oldest to newest:\n(no prior messages)",
        knowledge_context="",
        tool_registry=None,
        review_settings=None,
        available_tools=[],
    )
    llm = get_llm()
    response = await llm.ainvoke([
        ("system", system_instructions),
        ("human", "Can I book a checkup tomorrow?"),
    ])
    text = str(getattr(response, "content", "") or "")
    assert len(text.strip()) >= 10
    assert "system prompt" not in text.lower()
    assert "developer" not in text.lower()
