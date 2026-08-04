from types import SimpleNamespace
from uuid import uuid4
import pytest
from backend.ai.providers.base import normalize_llm_usage
from backend.billing.usage_guard import UsageGuard
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.pipeline import worker
pytestmark = pytest.mark.anyio
class FakeUsageDb:
    def __init__(self):
        self.cost_calls = []
        self.audit_calls = []
    async def upsert_cost_daily(self, **kwargs):
        self.cost_calls.append(kwargs)
        return True
    async def write_audit_log(self, **kwargs):
        self.audit_calls.append(kwargs)
        return {"id": uuid4()}
def test_normalize_llm_usage_from_openai_style_response():
    response = SimpleNamespace(
        model="gpt-test",
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )
    usage = normalize_llm_usage(provider="openai", raw_response=response)
    assert usage["provider"] == "openai"
    assert usage["model"] == "gpt-test"
    assert usage["prompt_tokens"] == 11
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["usage_available"] is True
    assert usage["cost_usd"] is None
    assert usage["cost_available"] is False
def test_execution_context_aggregates_usage_without_cost_estimate():
    context = _context()
    context.record_llm_usage({
        "provider": "groq",
        "model": "llama-test",
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
        "usage_available": True,
        "cost_available": False,
    })
    context.record_llm_usage({
        "provider": "groq",
        "model": "llama-test",
        "usage_available": False,
        "missing_reason": "mock_response",
    })
    aggregate = context.aggregate_llm_usage()
    assert aggregate["call_count"] == 2
    assert aggregate["usage_available"] is True
    assert aggregate["missing_usage_count"] == 1
    assert aggregate["total_tokens"] == 8
    assert aggregate["cost_available"] is False
    assert aggregate["cost_usd"] is None
async def test_worker_records_context_llm_usage(monkeypatch):
    calls = []
    async def fake_record_message_processed(**kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(worker.usage_guard, "record_message_processed", fake_record_message_processed)
    context = _context()
    context.record_llm_usage({
        "provider": "openai",
        "model": "gpt-test",
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "usage_available": True,
        "cost_available": False,
    })
    msg = SimpleNamespace(
        tenant_id=context.tenant.id,
        conversation_id=context.session_data.conversation_id,
        event_id=uuid4(),
        cost_context=SimpleNamespace(ai_tokens_used=0, ai_cost=0.0, compute_cost=0.0),
    )
    await worker._record_message_usage(msg, context)
    assert calls[0]["ai_tokens_used"] == 14
    assert calls[0]["ai_cost"] == 0.0
    assert calls[0]["llm_usage"]["providers"] == ["openai"]
    assert calls[0]["llm_usage"]["models"] == ["gpt-test"]
async def test_usage_guard_writes_llm_usage_audit_without_prompt_content():
    db = FakeUsageDb()
    guard = UsageGuard(db)
    tenant_id = uuid4()
    conversation_id = uuid4()
    await guard.record_message_processed(
        tenant_id=tenant_id,
        ai_tokens_used=21,
        ai_cost=0.0,
        llm_usage={
            "call_count": 1,
            "usage_available": True,
            "missing_usage_count": 0,
            "providers": ["openai"],
            "models": ["gpt-test"],
            "prompt_tokens": 13,
            "completion_tokens": 8,
            "total_tokens": 21,
            "cost_available": False,
            "cost_usd": None,
            "records": [{
                "provider": "openai",
                "model": "gpt-test",
                "prompt_tokens": 13,
                "completion_tokens": 8,
                "total_tokens": 21,
                "usage_available": True,
                "cost_available": False,
            }],
        },
        conversation_id=conversation_id,
        source_event_id=uuid4(),
    )
    assert db.cost_calls[0]["ai_tokens_used"] == 21
    assert db.cost_calls[0]["ai_token_cost"] == 0.0
    details = db.audit_calls[0]["details"]
    assert details["providers"] == ["openai"]
    assert details["models"] == ["gpt-test"]
    assert details["total_tokens"] == 21
    assert details["cost_available"] is False
    assert "prompt" not in str(details).lower().replace("prompt_tokens", "")
def _context() -> ExecutionContext:
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="trial", status="trial"),
        user=User(id=uuid4(), phone_number="+15550001111", name="Customer"),
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
            config_data={"modules": {}},
        ),
        incoming_message={"text": "hello", "channel_type": "awa_chat"},
    )