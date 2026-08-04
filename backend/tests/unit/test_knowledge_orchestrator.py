from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.knowledge.orchestrator import NO_MATCHED_ESCALATION_KNOWLEDGE, knowledge_orchestrator
pytestmark = pytest.mark.anyio
def make_context() -> ExecutionContext:
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Smile Clinic", plan_code="trial", status="trial"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={}),
        incoming_message={"text": "Are you open late?"},
    )
def test_matches_configured_faq_by_overlap():
    answer = knowledge_orchestrator.match_configured_faq(
        "What are your Friday hours?",
        [{"question": "What are your Friday hours?", "answer": "Open until 8 PM."}],
    )
    assert answer == "Open until 8 PM."
def test_direct_faq_context_is_customer_safe_language_not_rag_diagnostics():
    result = knowledge_orchestrator.get_direct_faq_context({
        "faqs": [{"question": "Do you accept walk-ins?", "answer": "Yes, before 5 PM."}]
    })
    assert result.mode == "direct_context"
    assert result.sources_used == ["tenant_config.faqs"]
    assert "Do you accept walk-ins?" in result.context_text
    assert "RAG" not in result.context_text
    assert "embedding" not in result.context_text.lower()
async def test_saved_knowledge_answer_extracts_tenant_answer(monkeypatch):
    async def fake_search(tenant_id, query, limit=1):
        return "Escalation reason/question: Late hours?\nTenant answer: We are open until 8 PM on Fridays."
    monkeypatch.setattr("backend.knowledge.orchestrator.rag_service.search_knowledge_base", fake_search)
    monkeypatch.setattr("backend.knowledge.orchestrator.rag_service.has_helpful_context", lambda context: True)
    answer = await knowledge_orchestrator.get_saved_knowledge_answer(make_context(), "Are you open late?")
    assert answer == "We are open until 8 PM on Fridays."
async def test_saved_knowledge_context_returns_no_match_without_helpful_context(monkeypatch):
    async def fake_search(tenant_id, query, limit=3):
        return "No helpful context found."
    monkeypatch.setattr("backend.knowledge.orchestrator.rag_service.search_knowledge_base", fake_search)
    monkeypatch.setattr("backend.knowledge.orchestrator.rag_service.has_helpful_context", lambda context: False)
    result = await knowledge_orchestrator.build_saved_knowledge_context(make_context(), "Unknown question")
    assert result.context_text == NO_MATCHED_ESCALATION_KNOWLEDGE
    assert result.mode == "saved_knowledge_no_match"
    assert result.sources_used == []