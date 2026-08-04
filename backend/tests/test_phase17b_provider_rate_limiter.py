import pytest
import asyncio
import time
from uuid import uuid4
from backend.observability.provider_rate_limiter import (
    acquire_provider_capacity,
    get_provider_limits,
    estimate_llm_request_tokens,
    ProviderRateLimitDecision,
)
from backend.core.config import settings
from backend.ai.factory import ObservedLLM
from backend.ai.providers.base import BaseLLM, LLMResponse
from backend.observability.latency import latency_metrics_var
from backend.api.routes.public_chat import _public_chat_message_record

pytestmark = pytest.mark.anyio

class FakeRedis:
    def __init__(self):
        self.store = {}
    async def hmget(self, key, fields):
        data = self.store.get(key, {})
        return [data.get(field) for field in fields]
    async def hmset(self, key, mapping):
        if key not in self.store:
            self.store[key] = {}
        self.store[key].update(mapping)
        return True

# 1. Single atomic decision consumes all buckets or none
async def test_atomic_decision_consumes_all_or_none(monkeypatch):
    redis_client = FakeRedis()
    test_id = uuid4().hex[:8]
    provider = f"test_prov_{test_id}"
    model = f"test_mod_{test_id}"

    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    # Monkeypatch get_provider_limits to return 1 RPM
    def mock_limits(p, m):
        return 1, 100
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_provider_limits", mock_limits)

    # First call: allowed
    dec1 = await acquire_provider_capacity(redis_client, provider, model, None, 10, 100)
    assert dec1.allowed is True

    # Second call: denied (1 RPM limit exhausted)
    dec2 = await acquire_provider_capacity(redis_client, provider, model, None, 10, 100)
    assert dec2.allowed is False
    assert dec2.wait_ms > 0
    assert dec2.reason == "Rate limit wait timeout"

# 2. Independent provider and model buckets
async def test_provider_and_model_buckets_are_independent(monkeypatch):
    redis_client = FakeRedis()
    test_id = uuid4().hex[:8]
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    dec1 = await acquire_provider_capacity(redis_client, f"p1_{test_id}", f"m1_{test_id}", None, 10, 100)
    assert dec1.allowed is True

    dec2 = await acquire_provider_capacity(redis_client, f"p2_{test_id}", f"m2_{test_id}", None, 10, 100)
    assert dec2.allowed is True

# 3. Tenant buckets are applied safely when tenant_id exists
async def test_tenant_buckets_applied_safely(monkeypatch):
    redis_client = FakeRedis()
    test_id = uuid4().hex[:8]
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    dec = await acquire_provider_capacity(redis_client, f"p3_{test_id}", f"m3_{test_id}", f"t1_{test_id}", 10, 100)
    assert dec.allowed is True

# 4. tenant_id missing does not crash
async def test_tenant_id_missing_no_crash(monkeypatch):
    redis_client = FakeRedis()
    test_id = uuid4().hex[:8]
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    dec = await acquire_provider_capacity(redis_client, f"p4_{test_id}", f"m4_{test_id}", None, 10, 100)
    assert dec.allowed is True

# 5. Redis unavailable fail-open/fail-closed behavior works
class BrokenRedis:
    async def hmget(self, *args, **kwargs):
        raise ConnectionError("Redis connection failure")
    async def hmset(self, *args, **kwargs):
        raise ConnectionError("Redis connection failure")

async def test_redis_unavailable_fail_open_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)
    broken = BrokenRedis()

    # Fail closed (default)
    monkeypatch.setattr(settings, "PROVIDER_RATE_LIMIT_FAIL_OPEN", False)
    dec_closed = await acquire_provider_capacity(broken, "gemini", "flash", None, 10, 100)
    assert dec_closed.allowed is False
    assert "Redis error, failed closed" in dec_closed.reason

    # Fail open
    monkeypatch.setattr(settings, "PROVIDER_RATE_LIMIT_FAIL_OPEN", True)
    dec_open = await acquire_provider_capacity(broken, "gemini", "flash", None, 10, 100)
    assert dec_open.allowed is True
    assert "Redis error, failed open" in dec_open.reason

# 6. Wait timeout returns safe fallback and provider_error
class MockProvider(BaseLLM):
    def get_provider_name(self) -> str:
        return "mock_llm"
    @property
    def model_name(self) -> str:
        return "mock_model"
    async def generate_response(self, system_prompt: str, user_message: str, temperature: float = 0.7) -> LLMResponse:
        return LLMResponse("Real response", provider="mock_llm", model="mock_model")

async def test_wait_timeout_returns_safe_fallback(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_redis_client", mock_get_redis)

    def mock_limits(p, m):
        return 0, 0
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_provider_limits", mock_limits)

    provider = ObservedLLM(MockProvider())
    metrics = {"provider_throttle_wait_ms": 0}
    token = latency_metrics_var.set(metrics)

    try:
        res = await provider.generate_response("system", "user")
        assert "I couldn't complete that request right now" in res.content
        assert res.provider_error is not None
        assert res.provider_error["error_type"] == "rate_limit_wait_timeout"
        assert metrics["provider_throttle_wait_ms"] >= 0
    finally:
        latency_metrics_var.reset(token)

# 7. No secrets/API keys in metrics or logs
def test_no_secrets_in_token_estimation():
    est = estimate_llm_request_tokens("Secret API key: 123", "My user prompt")
    assert isinstance(est, int)

# 8. ObservedLLM invokes limiter before provider generate_response
async def test_observed_llm_invokes_limiter_before(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    called_limiter = False

    async def mock_acquire(*args, **kwargs):
        nonlocal called_limiter
        called_limiter = True
        return ProviderRateLimitDecision(True, 0, "mock_llm", "mock_model", None, "Allowed")

    monkeypatch.setattr("backend.observability.provider_rate_limiter.acquire_provider_capacity", mock_acquire)

    provider = ObservedLLM(MockProvider())
    await provider.generate_response("system", "user")
    assert called_limiter is True

# 9. public chat does not expose limiter diagnostics to normal customers
def test_public_chat_does_not_expose_limiter_diagnostics(monkeypatch):
    item = {
        "id": uuid4(),
        "direction": "outbound",
        "message_text": "Hello client",
        "raw_payload": {
            "metrics": {
                "provider_throttle_wait_ms": 150,
            },
            "visitor_name": "normal_customer",
        }
    }
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("ENABLE_LOCAL_SIMULATION_DIAGNOSTICS", "false")
    record = _public_chat_message_record(item, latest_outbound_id=None, session={"visitor_name": "normal_customer"})
    assert "metrics" not in record["raw_payload"]
    assert "diagnostics" not in record

# 10. ObservedLangchainLLM wraps langchain and enforces limits, recording metrics and returning safe fallback on wait timeout
async def test_observed_langchain_llm_enforces_limits(monkeypatch):
    from backend.logic.engine.llm import ObservedLangchainLLM
    from langchain_core.messages import HumanMessage
    from backend.core.context import active_context_var, ExecutionContext, Tenant, User, ConversationSession
    from uuid import uuid4

    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_redis_client", mock_get_redis)

    # Force limit to 0 to trigger wait timeout immediately
    def mock_limits(p, m):
        return 0, 0
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_provider_limits", mock_limits)

    # Set up ExecutionContext
    session = ConversationSession(id=uuid4(), tenant_id=uuid4(), conversation_id=uuid4())
    from backend.core.context import TenantConfig
    context = ExecutionContext(
        tenant=Tenant(id=session.tenant_id, name="Test Tenant", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+15555555555"),
        session_data=session,
        config=TenantConfig(tenant_id=session.tenant_id, version=1, config_data={})
    )
    token = active_context_var.set(context)

    # Setup latency metrics
    metrics = {"provider_throttle_wait_ms": 0, "provider_latency_ms": 0}
    token_metrics = latency_metrics_var.set(metrics)

    class MockLangchainInner:
        def bind_tools(self, tools, **kwargs):
            return self
        async def ainvoke(self, messages, config=None, **kwargs):
            from langchain_core.messages import AIMessage
            return AIMessage(content="Inner response")

    try:
        model = ObservedLangchainLLM(MockLangchainInner(), "gemini", "gemini-3.1-flash-lite")
        resp = await model.ainvoke([HumanMessage(content="hello")])
        assert "I couldn't complete that request right now" in resp.content
        assert context.session_data.state.action_ledger.last_error is not None
        assert context.session_data.state.action_ledger.last_error.provider_error["error_type"] == "rate_limit_wait_timeout"
        assert metrics["provider_throttle_wait_ms"] >= 0
        assert metrics["provider_latency_ms"] == 0  # No provider call should have happened!
    finally:
        active_context_var.reset(token)
        latency_metrics_var.reset(token_metrics)
