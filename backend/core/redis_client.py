from typing import Any

import redis.asyncio as redis
from backend.core.config import settings

# Save the original from_url reference to detect monkeypatching in tests
_original_from_url = redis.from_url

_client_cache: dict[tuple[bool, float | int | None], Any] = {}

def reset_redis_clients():
    """Reset cached clients without awaiting close; useful for tests after monkeypatching."""
    _client_cache.clear()

async def close_redis_clients() -> None:
    """Close all cached Redis clients during application shutdown."""
    clients = list(_client_cache.values())
    _client_cache.clear()
    for client in clients:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result

def _build_client(*, decode_responses: bool, socket_timeout: float | int | None = None):
    kwargs: dict[str, Any] = {"decode_responses": decode_responses}
    if socket_timeout is not None:
        kwargs["socket_timeout"] = socket_timeout
    return redis.from_url(settings.REDIS_URL, **kwargs)

def get_redis_client(socket_timeout: float | int | None = None):
    # If redis.from_url was monkeypatched by a test (Mock, lambda, etc.), always run the patch dynamically
    if redis.from_url is not _original_from_url:
        return _build_client(decode_responses=True, socket_timeout=socket_timeout)
    key = (True, socket_timeout)
    if key not in _client_cache:
        _client_cache[key] = _build_client(decode_responses=True, socket_timeout=socket_timeout)
    return _client_cache[key]

def get_redis_client_raw(socket_timeout: float | int | None = None):
    # If redis.from_url was monkeypatched by a test (Mock, lambda, etc.), always run the patch dynamically
    if redis.from_url is not _original_from_url:
        return _build_client(decode_responses=False, socket_timeout=socket_timeout)
    key = (False, socket_timeout)
    if key not in _client_cache:
        _client_cache[key] = _build_client(decode_responses=False, socket_timeout=socket_timeout)
    return _client_cache[key]

class RedisClientProxy:
    def __getattr__(self, name):
        return getattr(get_redis_client(), name)

class RedisRawClientProxy:
    def __getattr__(self, name):
        return getattr(get_redis_client_raw(), name)

redis_client = RedisClientProxy()
redis_client_raw = RedisRawClientProxy()


_CLAIM_OTP_CHALLENGE_LUA = """
local challenge = redis.call('get', KEYS[1])
if not challenge then
  return {0, 0}
end
if challenge ~= ARGV[1] then
  return {-1, 0}
end
if redis.call('exists', KEYS[2]) == 1 then
  return {0, 0}
end
local ttl = redis.call('pttl', KEYS[1])
if ttl <= 0 then
  return {0, 0}
end
redis.call('set', KEYS[2], ARGV[2], 'PX', ARGV[3], 'NX')
redis.call('del', KEYS[1])
return {1, ttl}
"""


_RESTORE_OTP_CHALLENGE_LUA = """
if redis.call('get', KEYS[2]) ~= ARGV[2] then
  return 0
end
if redis.call('exists', KEYS[1]) == 1 then
  return 0
end
redis.call('del', KEYS[2])
redis.call('set', KEYS[1], ARGV[1], 'PX', ARGV[3], 'NX')
return 1
"""


async def claim_otp_challenge(
    client: Any,
    *,
    challenge_key: str,
    expected_code: str,
    claim_key: str,
    claim_value: str,
    claim_ttl_seconds: int,
) -> tuple[str, int]:
    """Atomically validate/consume one OTP and create its continuation claim."""
    result = await client.eval(
        _CLAIM_OTP_CHALLENGE_LUA,
        2,
        challenge_key,
        claim_key,
        expected_code,
        claim_value,
        max(1, int(claim_ttl_seconds * 1000)),
    )
    code, remaining_ms = int(result[0]), int(result[1])
    return ({1: "claimed", 0: "consumed", -1: "invalid"}.get(code, "invalid"), remaining_ms)


async def restore_otp_challenge_after_enqueue_failure(
    client: Any,
    *,
    challenge_key: str,
    expected_code: str,
    claim_key: str,
    claim_value: str,
    remaining_ttl_ms: int,
) -> bool:
    """Rollback a pre-enqueue claim only while the exact claim is still owned."""
    result = await client.eval(
        _RESTORE_OTP_CHALLENGE_LUA,
        2,
        challenge_key,
        claim_key,
        expected_code,
        claim_value,
        max(1, int(remaining_ttl_ms)),
    )
    return bool(result)


async def claim_protected_action_execution(
    client: Any,
    *,
    claim_key: str,
    claim_value: str,
    ttl_seconds: int,
) -> bool:
    """Acquire one cross-worker protected-action dispatch claim."""
    return bool(await client.set(claim_key, claim_value, ex=max(1, int(ttl_seconds)), nx=True))
