import asyncio
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from backend.ops.logger import logger
from backend.core.config import settings

@dataclass
class ProviderRateLimitDecision:
    allowed: bool
    wait_ms: int
    provider: str
    model: str
    tenant_id: Optional[str]
    reason: str

# Atomic token bucket decision Lua script
LUA_LIMITER_SCRIPT = """
local now = tonumber(ARGV[#KEYS * 3 + 1])
local allowed = true
local max_wait = 0

-- First pass: Dry-run check all buckets
for i = 1, #KEYS do
    local key = KEYS[i]
    local capacity = tonumber(ARGV[(i - 1) * 3 + 1])
    local refill_rate = tonumber(ARGV[(i - 1) * 3 + 2])
    local requested = tonumber(ARGV[(i - 1) * 3 + 3])

    local state = redis.call('HMGET', key, 'tokens', 'last_updated')
    local tokens = tonumber(state[1])
    local last_updated = tonumber(state[2])

    if not tokens then
        tokens = capacity
        last_updated = now
    else
        local elapsed = now - last_updated
        if elapsed > 0 then
            tokens = math.min(capacity, tokens + elapsed * refill_rate)
        end
    end

    if tokens < requested then
        allowed = false
        local missing = requested - tokens
        local wait_time = refill_rate > 0 and (missing / refill_rate) or 999999
        local wait_ms = math.ceil(wait_time * 1000)
        if wait_ms > max_wait then
            max_wait = wait_ms
        end
    end
end

-- Second pass: Commit/consume if all buckets allow
if allowed then
    for i = 1, #KEYS do
        local key = KEYS[i]
        local capacity = tonumber(ARGV[(i - 1) * 3 + 1])
        local refill_rate = tonumber(ARGV[(i - 1) * 3 + 2])
        local requested = tonumber(ARGV[(i - 1) * 3 + 3])

        local state = redis.call('HMGET', key, 'tokens', 'last_updated')
        local tokens = tonumber(state[1])
        local last_updated = tonumber(state[2])

        if not tokens then
            tokens = capacity
            last_updated = now
        else
            local elapsed = now - last_updated
            if elapsed > 0 then
                tokens = math.min(capacity, tokens + elapsed * refill_rate)
            end
        end

        tokens = tokens - requested
        redis.call('HMSET', key, 'tokens', tokens, 'last_updated', now)
    end
    return {1, 0}
else
    return {0, max_wait}
end
"""

async def _check_all_buckets_fallback(redis_client, keys: List[str], args: List[Any]) -> Tuple[bool, int]:
    """
    Python-based fallback rate limiter logic.
    WARNING: Not safe for concurrent multi-worker production environments.
    Only for local testing/mocks when Lua EVAL is not supported.
    """
    num_keys = len(keys)
    now = float(args[-1])

    allowed = True
    max_wait = 0
    bucket_states = []

    for i in range(num_keys):
        key = keys[i]
        capacity = float(args[i * 3])
        refill_rate = float(args[i * 3 + 1])
        requested = float(args[i * 3 + 2])

        state = await redis_client.hmget(key, ["tokens", "last_updated"])
        tokens_val, last_updated_val = state[0], state[1]

        tokens = float(tokens_val) if tokens_val is not None else capacity
        last_updated = float(last_updated_val) if last_updated_val is not None else now

        elapsed = now - last_updated
        if elapsed > 0:
            tokens = min(capacity, tokens + elapsed * refill_rate)

        bucket_states.append((tokens, last_updated))

        if tokens < requested:
            allowed = False
            missing = requested - tokens
            wait_time = missing / refill_rate if refill_rate > 0 else 999999
            wait_ms = int(wait_time * 1000)
            if wait_ms > max_wait:
                max_wait = wait_ms

    if allowed:
        for i in range(num_keys):
            key = keys[i]
            requested = float(args[i * 3 + 2])
            tokens, _ = bucket_states[i]
            tokens = tokens - requested
            await redis_client.hmset(key, {"tokens": tokens, "last_updated": now})
        return True, 0
    else:
        return False, max_wait

_redis_client = None

async def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try:
            import redis.asyncio as aioredis
            _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.error(f"Failed to initialize Redis client for rate limiter: {e}")
            _redis_client = None
    return _redis_client

def get_provider_limits(provider: str, model: str) -> Tuple[int, int]:
    """
    Return (rpm_limit, tpm_limit) for a provider from settings.
    Note: Real limits vary by provider account, model, tier, and project.
    Production/staging environments should explicitly override these limits.
    """
    p = (provider or "").lower().strip()
    m = (model or "").lower().strip()

    if p == "ollama":
        base_url = settings.OLLAMA_BASE_URL or ""
        is_local_ollama = not base_url or any(k in base_url for k in ("localhost", "127.0.0.1", "host.docker.internal"))
        if is_local_ollama:
            return 999999, 999999

    if p == "gemini":
        return settings.PROVIDER_GEMINI_RPM_LIMIT, settings.PROVIDER_GEMINI_TPM_LIMIT
    elif p == "openai":
        return settings.PROVIDER_OPENAI_RPM_LIMIT, settings.PROVIDER_OPENAI_TPM_LIMIT
    elif p == "groq":
        return settings.PROVIDER_GROQ_RPM_LIMIT, settings.PROVIDER_GROQ_TPM_LIMIT
    elif p == "ollama":
        return settings.PROVIDER_OLLAMA_RPM_LIMIT, settings.PROVIDER_OLLAMA_TPM_LIMIT

    return 60, 90000

def estimate_llm_request_tokens(system_prompt: str, user_message: str, max_output_tokens: int = 1024) -> int:
    """
    Estimate prompt/request tokens cleanly without logging or storing any prompts,
    system instructions, or user message content.
    Returns only a numeric count.
    """
    try:
        sys_len = len(system_prompt or "")
        user_len = len(user_message or "")
        estimated_input = int((sys_len + user_len) / 4)
        return estimated_input + max_output_tokens
    except Exception:
        return 2048

def get_effective_rpm(provider: str, configured_rpm: int) -> int:
    import math
    p = (provider or "").lower().strip()
    if p == "gemini" and settings.PROVIDER_GEMINI_EFFECTIVE_RPM_LIMIT is not None:
        return settings.PROVIDER_GEMINI_EFFECTIVE_RPM_LIMIT

    margin = settings.PROVIDER_RATE_LIMIT_SAFETY_MARGIN_PERCENT
    min_rpm = settings.PROVIDER_RATE_LIMIT_MIN_EFFECTIVE_RPM
    effective = int(math.floor(configured_rpm * (1 - margin / 100.0)))
    return max(min_rpm, effective)

def get_effective_burst_cap(provider: str, effective_rpm: int) -> int:
    import math
    p = (provider or "").lower().strip()
    if p == "gemini" and settings.PROVIDER_GEMINI_BURST_CAP is not None:
        return settings.PROVIDER_GEMINI_BURST_CAP

    cap_percent = settings.PROVIDER_RATE_LIMIT_BURST_CAP_PERCENT
    cap = int(math.floor(effective_rpm * (cap_percent / 100.0)))
    return max(1, cap)

async def acquire_provider_capacity(
    redis_client,
    provider: str,
    model: str,
    tenant_id: Optional[str],
    estimated_tokens: int,
    max_wait_ms: int,
) -> ProviderRateLimitDecision:
    """
    Redis-backed, provider-aware pre-call rate limiter.
    Throttles calling provider by checking provider level, model level, and optional tenant level limits.
    Utilizes a single atomic Redis Lua script to either allow and consume all tokens, or deny and consume nothing.
    """
    if not settings.ENABLE_PROVIDER_RATE_LIMITER:
        return ProviderRateLimitDecision(True, 0, provider, model, tenant_id, "Disabled")

    if redis_client is None:
        if settings.PROVIDER_RATE_LIMIT_FAIL_OPEN:
            logger.warning("Limiter warning: Redis unavailable, failing open")
            return ProviderRateLimitDecision(True, 0, provider, model, tenant_id, "Redis unavailable, failed open")
        else:
            logger.warning("Limiter warning: Redis unavailable, failing closed")
            return ProviderRateLimitDecision(False, -1, provider, model, tenant_id, "Redis unavailable, failed closed")

    p = (provider or "").lower().strip()
    m = (model or "").lower().strip()

    rpm_limit, tpm_limit = get_provider_limits(p, m)
    effective_rpm = get_effective_rpm(p, rpm_limit)
    burst_cap = get_effective_burst_cap(p, effective_rpm)

    from backend.observability.latency import latency_metrics_var
    metrics = latency_metrics_var.get()
    if metrics is not None:
        metrics["rate_limiter_configured_rpm"] = rpm_limit
        metrics["rate_limiter_effective_rpm"] = effective_rpm
        metrics["rate_limiter_safety_margin_percent"] = settings.PROVIDER_RATE_LIMIT_SAFETY_MARGIN_PERCENT
        metrics["rate_limiter_burst_cap"] = burst_cap

    keys = [
        f"ratelimit:provider:{p}:rpm",
        f"ratelimit:provider:{p}:tpm",
        f"ratelimit:model:{p}:{m}:rpm",
        f"ratelimit:model:{p}:{m}:tpm",
    ]

    args = [
        burst_cap, effective_rpm / 60.0, 1,
        tpm_limit, tpm_limit / 60.0, estimated_tokens,
        burst_cap, effective_rpm / 60.0, 1,
        tpm_limit, tpm_limit / 60.0, estimated_tokens,
    ]

    if tenant_id:
        tenant_rpm = effective_rpm * 2
        tenant_tpm = tpm_limit * 2
        tenant_burst = burst_cap * 2
        keys.extend([
            f"ratelimit:tenant:{tenant_id}:{p}:rpm",
            f"ratelimit:tenant:{tenant_id}:{p}:tpm"
        ])
        args.extend([
            tenant_burst, tenant_rpm / 60.0, 1,
            tenant_tpm, tenant_tpm / 60.0, estimated_tokens,
        ])

    start_time = time.monotonic()
    total_wait_ms = 0.0

    while True:
        redis_time_ok = False
        now = None

        if hasattr(redis_client, "time"):
            try:
                seconds, microseconds = await redis_client.time()
                now = seconds + (microseconds / 1_000_000.0)
                redis_time_ok = True
            except Exception as e:
                logger.error(f"Limiter Redis TIME call failed inside loop: {e}")

        if not redis_time_ok:
            is_test_env = (
                settings.ENV != "production" and (
                    "FakeRedis" in type(redis_client).__name__ or
                    not hasattr(redis_client, "eval")
                )
            )
            if is_test_env:
                now = time.time()
            else:
                if settings.PROVIDER_RATE_LIMIT_FAIL_OPEN:
                    logger.warning("Limiter warning: Redis TIME unavailable in loop, failing open")
                    return ProviderRateLimitDecision(True, int(total_wait_ms), provider, model, tenant_id, "Redis TIME unavailable, failed open")
                else:
                    logger.error("Limiter error: Redis TIME unavailable in loop, failing closed")
                    if metrics is not None:
                        metrics["provider_calls_denied"] = metrics.get("provider_calls_denied", 0) + 1
                    return ProviderRateLimitDecision(False, int(total_wait_ms), provider, model, tenant_id, "Redis TIME unavailable, failed closed")

        run_args = list(args)
        run_args.append(now)

        try:
            if hasattr(redis_client, "eval"):
                res = await redis_client.eval(LUA_LIMITER_SCRIPT, len(keys), *keys, *run_args)
                allowed = bool(res[0])
                wait_ms = int(res[1])
            else:
                allowed, wait_ms = await _check_all_buckets_fallback(redis_client, keys, run_args)
        except Exception as e:
            logger.error(f"Limiter Redis call failed: {e}")
            if settings.PROVIDER_RATE_LIMIT_FAIL_OPEN:
                logger.warning("Limiter warning: Redis call failed, failing open")
                return ProviderRateLimitDecision(True, int(total_wait_ms), provider, model, tenant_id, "Redis error, failed open")
            else:
                logger.warning("Limiter warning: Redis call failed, failing closed")
                if metrics is not None:
                    metrics["provider_calls_denied"] = metrics.get("provider_calls_denied", 0) + 1
                return ProviderRateLimitDecision(False, int(total_wait_ms), provider, model, tenant_id, "Redis error, failed closed")

        if allowed:
            if metrics is not None:
                metrics["provider_calls_allowed"] = metrics.get("provider_calls_allowed", 0) + 1
            return ProviderRateLimitDecision(True, int(total_wait_ms), provider, model, tenant_id, "Allowed")

        remaining_wait_ms = max_wait_ms - total_wait_ms
        if remaining_wait_ms <= 0:
            if metrics is not None:
                metrics["provider_calls_denied"] = metrics.get("provider_calls_denied", 0) + 1
                metrics["provider_wait_timeouts"] = metrics.get("provider_wait_timeouts", 0) + 1
            return ProviderRateLimitDecision(False, int(total_wait_ms), provider, model, tenant_id, "Rate limit wait timeout")

        import random
        # Wait strategy: sleep for recommended wait + jitter
        jitter_ms = random.uniform(10.0, 50.0)
        sleep_ms = min(wait_ms + jitter_ms, remaining_wait_ms)
        if sleep_ms <= 0:
            sleep_ms = 10

        await asyncio.sleep(sleep_ms / 1000.0)
        total_wait_ms = (time.monotonic() - start_time) * 1000.0


async def refund_provider_capacity(
    redis_client,
    provider: str,
    model: str,
    tenant_id: Optional[str],
    refund_amount: int,
) -> None:
    """Refund unused capacity tokens back to rate limiter buckets in Redis."""
    if not settings.ENABLE_PROVIDER_RATE_LIMITER or refund_amount <= 0 or redis_client is None:
        return

    p = (provider or "").lower().strip()
    m = (model or "").lower().strip()

    _, tpm_limit = get_provider_limits(p, m)

    keys = [
        f"ratelimit:provider:{p}:tpm",
        f"ratelimit:model:{p}:{m}:tpm",
    ]
    limits = [tpm_limit, tpm_limit]

    if tenant_id:
        keys.append(f"ratelimit:tenant:{tenant_id}:{p}:tpm")
        limits.append(tpm_limit * 2)

    try:
        if hasattr(redis_client, "eval"):
            lua_script = """
            local refund_amount = tonumber(ARGV[1])
            for i = 1, #KEYS do
                local key = KEYS[i]
                local limit = tonumber(ARGV[i + 1])
                local tokens = tonumber(redis.call('HGET', key, 'tokens'))
                if tokens then
                    local new_tokens = math.min(limit, tokens + refund_amount)
                    redis.call('HSET', key, 'tokens', new_tokens)
                end
            end
            """
            run_args = [refund_amount] + limits
            await redis_client.eval(lua_script, len(keys), *keys, *run_args)
        else:
            for i, key in enumerate(keys):
                limit = limits[i]
                val = await redis_client.hget(key, "tokens")
                if val is not None:
                    tokens = float(val)
                    new_tokens = min(limit, tokens + refund_amount)
                    await redis_client.hset(key, "tokens", new_tokens)
    except Exception as e:
        logger.error(f"Limiter refund failed: {e}")
