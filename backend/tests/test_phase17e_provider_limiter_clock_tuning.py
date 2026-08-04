import pytest
import asyncio
import time
from uuid import uuid4
from backend.core.config import settings
from backend.observability.provider_rate_limiter import (
    acquire_provider_capacity,
    get_provider_limits,
    estimate_llm_request_tokens,
    ProviderRateLimitDecision,
    get_effective_rpm,
    get_effective_burst_cap,
)
from backend.ai.factory import ObservedLLM
from backend.ai.providers.base import BaseLLM, LLMResponse
from backend.observability.latency import latency_metrics_var
from backend.api.routes.public_chat import _public_chat_message_record

pytestmark = pytest.mark.anyio

class FakeRedis:
    def __init__(self):
        self.store = {}
        self.current_time = 1718611200.0  # 2026-06-17 08:00:00 UTC

    async def ping(self):
        return True

    async def aclose(self):
        pass

    async def time(self):
        secs = int(self.current_time)
        microsecs = int((self.current_time - secs) * 1_000_000)
        return secs, microsecs

    async def hmget(self, key, fields):
        data = self.store.get(key, {})
        return [data.get(field) for field in fields]

    async def hmset(self, key, mapping):
        if key not in self.store:
            self.store[key] = {}
        self.store[key].update({k: str(v) for k, v in mapping.items()})
        return True

    async def eval(self, script, numkeys, *keys_and_args):
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        from backend.observability.provider_rate_limiter import _check_all_buckets_fallback
        allowed, wait_ms = await _check_all_buckets_fallback(self, keys, args)
        return [1 if allowed else 0, wait_ms]


# 1. Two workers share one provider/model bucket and cannot exceed effective RPM
async def test_two_workers_share_limits(monkeypatch):
    redis_client = FakeRedis()
    provider = "gemini"
    model = "gemini-3.1-flash-lite"

    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "PROVIDER_RATE_LIMIT_SAFETY_MARGIN_PERCENT", 20)

    # Configure 10 RPM -> effective RPM = 8
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_RPM_LIMIT", 10)

    # Effective RPM should be 8, burst cap = 4 (50% of 8)
    effective_rpm = get_effective_rpm(provider, 10)
    burst_cap = get_effective_burst_cap(provider, effective_rpm)
    assert effective_rpm == 8
    assert burst_cap == 4

    # Worker 1 takes 3 calls
    for _ in range(3):
        dec = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
        assert dec.allowed is True

    # Worker 2 takes 1 call (total 4, hitting burst cap)
    dec = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
    assert dec.allowed is True

    # Next call (5th) should be denied immediately due to burst cap
    dec_denied = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
    assert dec_denied.allowed is False
    assert dec_denied.reason == "Rate limit wait timeout"


# 2. Burst cap prevents initial overshoot (cannot do more than burst_cap calls immediately)
async def test_burst_cap_limits_initial_burst(monkeypatch):
    redis_client = FakeRedis()
    provider = "gemini"
    model = "gemini-3.1-flash-lite"

    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    # 15 RPM configured, effective = 12, burst cap = 3 (explicit override via config/test)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_RPM_LIMIT", 15)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_BURST_CAP", 3)

    # First 3 allowed
    for _ in range(3):
        dec = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
        assert dec.allowed is True

    # 4th denied (burst cap of 3 exceeded)
    dec = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
    assert dec.allowed is False


# 3. Denied decision consumes no tokens
async def test_denied_decision_consumes_no_tokens(monkeypatch):
    redis_client = FakeRedis()
    provider = "gemini"
    model = "gemini-3.1-flash-lite"

    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_RPM_LIMIT", 15)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_BURST_CAP", 1) # Only 1 allowed

    # 1st allowed
    dec1 = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
    assert dec1.allowed is True

    # Capture token state of the keys
    keys = [
        f"ratelimit:provider:{provider}:rpm",
        f"ratelimit:provider:{provider}:tpm",
    ]
    tokens_before = [await redis_client.hmget(k, ["tokens"]) for k in keys]

    # 2nd denied
    dec2 = await acquire_provider_capacity(redis_client, provider, model, None, 10, 0)
    assert dec2.allowed is False

    # Verify tokens have not changed after denial
    tokens_after = [await redis_client.hmget(k, ["tokens"]) for k in keys]
    assert tokens_before == tokens_after


# 4. Allowed decision consumes all required buckets atomically
async def test_allowed_decision_consumes_buckets_atomically(monkeypatch):
    redis_client = FakeRedis()
    provider = "gemini"
    model = "gemini-3.1-flash-lite"

    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_RPM_LIMIT", 15)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_TPM_LIMIT", 1000000)
    monkeypatch.setattr(settings, "PROVIDER_GEMINI_BURST_CAP", 6)

    # Check that tokens are deducted across provider, model and tenant buckets
    dec = await acquire_provider_capacity(redis_client, provider, model, "tenant123", 50, 0)
    assert dec.allowed is True

    prov_rpm = await redis_client.hmget(f"ratelimit:provider:{provider}:rpm", ["tokens"])
    prov_tpm = await redis_client.hmget(f"ratelimit:provider:{provider}:tpm", ["tokens"])
    tenant_rpm = await redis_client.hmget(f"ratelimit:tenant:tenant123:{provider}:rpm", ["tokens"])
    tenant_tpm = await redis_client.hmget(f"ratelimit:tenant:tenant123:{provider}:tpm", ["tokens"])

    assert float(prov_rpm[0]) < 6
    assert float(prov_tpm[0]) < 1000000
    assert float(tenant_rpm[0]) < 12 # tenant capacity is 2x provider capacity (6 * 2)
    assert float(tenant_tpm[0]) < 2000000


# 5. Wait timeout skips provider call
class MockProvider(BaseLLM):
    def __init__(self):
        self.call_count = 0
    def get_provider_name(self) -> str:
        return "mock_llm"
    @property
    def model_name(self) -> str:
        return "mock_model"
    async def generate_response(self, system_prompt: str, user_message: str, temperature: float = 0.7) -> LLMResponse:
        self.call_count += 1
        return LLMResponse("Real response", provider="mock_llm", model="mock_model")

async def test_wait_timeout_skips_provider_call(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    # Set limit to 0
    def mock_limits(p, m):
        return 0, 0
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_provider_limits", mock_limits)

    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_redis_client", mock_get_redis)

    inner = MockProvider()
    provider = ObservedLLM(inner)

    metrics = {"provider_throttle_wait_ms": 0, "provider_latency_ms": 0}
    token = latency_metrics_var.set(metrics)

    try:
        res = await provider.generate_response("system", "user")
        assert "I couldn't complete that request right now" in res.content
        assert res.provider_error is not None
        assert res.provider_error["error_type"] == "rate_limit_wait_timeout"
        # Confirm no actual provider call occurred
        assert inner.call_count == 0
        assert metrics["provider_latency_ms"] == 0
    finally:
        latency_metrics_var.reset(token)


# 6. Fake Redis fallback is marked non-production (fails closed in production environment)
class BrokenRedisNoTime:
    async def hmget(self, *args, **kwargs):
        raise ConnectionError("Disconnected")
    async def hmset(self, *args, **kwargs):
        raise ConnectionError("Disconnected")

async def test_fallback_fails_closed_in_production(monkeypatch):
    # Set to production
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "PROVIDER_RATE_LIMIT_FAIL_OPEN", False)

    broken = BrokenRedisNoTime()
    dec = await acquire_provider_capacity(broken, "gemini", "flash", None, 10, 100)
    assert dec.allowed is False
    assert "Redis TIME unavailable, failed closed" in dec.reason or "Redis error, failed closed" in dec.reason


# 7. Actual 429 increments internal metric only
async def test_actual_429_increments_metric(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)

    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.observability.provider_rate_limiter.get_redis_client", mock_get_redis)

    class RateLimitMockProvider(BaseLLM):
        def get_provider_name(self) -> str:
            return "mock_llm"
        @property
        def model_name(self) -> str:
            return "mock_model"
        async def generate_response(self, system_prompt: str, user_message: str, temperature: float = 0.7) -> LLMResponse:
            raise Exception("429 rate limit exceeded")

    provider = ObservedLLM(RateLimitMockProvider())
    metrics = {"provider_actual_429s": 0}
    token = latency_metrics_var.set(metrics)

    try:
        with pytest.raises(Exception, match="429"):
            await provider.generate_response("system", "user")
        assert metrics["provider_actual_429s"] == 1
    finally:
        latency_metrics_var.reset(token)


# 8. Public chat does not expose limiter diagnostics
def test_public_chat_hides_diagnostics(monkeypatch):
    item = {
        "id": uuid4(),
        "direction": "outbound",
        "message_text": "Hello, how can I help you?",
        "raw_payload": {
            "metrics": {
                "provider_throttle_wait_ms": 120,
                "provider_actual_429s": 1,
                "rate_limiter_effective_rpm": 12,
            },
            "visitor_name": "client_visitor",
        }
    }
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("ENABLE_LOCAL_SIMULATION_DIAGNOSTICS", "false")

    record = _public_chat_message_record(item, latest_outbound_id=None, session={"visitor_name": "client_visitor"})

    # The diagnostics metrics should be completely hidden/redacted
    assert "metrics" not in record["raw_payload"]
    assert "diagnostics" not in record
