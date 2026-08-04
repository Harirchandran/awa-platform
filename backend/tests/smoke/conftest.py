import pytest
import os
import json
from uuid import uuid4, UUID
from datetime import datetime, timezone
import redis.asyncio as redis
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.db.client import db_client
from backend.api.app import app

# Ensure we are using mock integration mode for tests
os.environ["AWA_INTEGRATION_MODE"] = "mock"
os.environ["LLM_PROVIDER"] = "deterministic_fake"

class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def from_url(self, *args, **kwargs):
        return self

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, px=None, nx=False, **kwargs):
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        if px is not None:
            self.ttls[key] = int(px)
        elif ex is not None:
            self.ttls[key] = int(ex) * 1000
        return True

    async def setnx(self, key, value):
        if key in self.data:
            return 0
        self.data[key] = str(value)
        return 1

    async def expire(self, key, ttl):
        pass

    async def delete(self, key):
        self.data.pop(key, None)
        self.ttls.pop(key, None)

    async def eval(self, script, key_count, *parts):
        keys = parts[:key_count]
        args = parts[key_count:]
        if key_count == 1:
            key = keys[0]
            owner_token = args[0]
            if self.data.get(key) != owner_token:
                return 0
            if "del" in script.lower():
                await self.delete(key)
            elif len(args) > 1:
                self.ttls[key] = int(args[1])
            return 1
        challenge_key, claim_key = keys
        expected_code, claim_value, ttl_ms = args
        if "local challenge" in script:
            challenge = self.data.get(challenge_key)
            if challenge is None or claim_key in self.data:
                return [0, 0]
            if challenge != expected_code:
                return [-1, 0]
            remaining_ms = self.ttls.get(challenge_key, 60_000)
            self.data[claim_key] = str(claim_value)
            self.ttls[claim_key] = int(ttl_ms)
            self.data.pop(challenge_key, None)
            self.ttls.pop(challenge_key, None)
            return [1, remaining_ms]
        if self.data.get(claim_key) != claim_value or challenge_key in self.data:
            return 0
        self.data.pop(claim_key, None)
        self.ttls.pop(claim_key, None)
        self.data[challenge_key] = str(expected_code)
        self.ttls[challenge_key] = int(ttl_ms)
        return 1

    async def aclose(self):
        pass

    async def ping(self):
        return True

    async def llen(self, key):
        val = self.data.get(key, [])
        if isinstance(val, (list, set, dict)):
            return len(val)
        return 0

    async def publish(self, channel, message):
        return 0

    async def incr(self, key):
        val = int(self.data.get(key, 0)) + 1
        self.data[key] = str(val)
        return val

    # List operations
    async def lpush(self, key, value):
        self.data.setdefault(key, []).insert(0, value)
        return len(self.data[key])

    async def lrange(self, key, start, stop):
        lst = self.data.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start:stop+1]

    async def lrem(self, key, count, value):
        if key not in self.data:
            return 0
        lst = self.data[key]
        before = len(lst)
        if count == 0:
            self.data[key] = [x for x in lst if x != value]
        else:
            # Simple count-based removal
            occurrences = 0
            new_lst = []
            for x in lst:
                if x == value and occurrences < abs(count):
                    occurrences += 1
                else:
                    new_lst.append(x)
            self.data[key] = new_lst
        return before - len(self.data[key])

    # Zset operations (simulated using lists of tuples (member, score))
    async def zadd(self, key, mapping):
        # mapping is a dict: {member: score}
        lst = self.data.setdefault(key, [])
        for member, score in mapping.items():
            # remove existing
            self.data[key] = [x for x in lst if x[0] != member]
            self.data[key].append((member, float(score)))
        return len(mapping)

    async def zrem(self, key, *values):
        if key not in self.data:
            return 0
        lst = self.data[key]
        before = len(lst)
        self.data[key] = [x for x in lst if x[0] not in values]
        return before - len(self.data[key])

    async def zrangebyscore(self, key, min, max, start=0, num=None):
        lst = self.data.get(key, [])
        # filter by score
        import math
        f_min = -math.inf if min == "-inf" else float(min)
        f_max = math.inf if max == "+inf" else float(max)
        filtered = [x[0] for x in lst if f_min <= x[1] <= f_max]
        if num is not None:
            return filtered[start:start+num]
        return filtered[start:]

    async def zrange(self, key, start, stop):
        lst = self.data.get(key, [])
        # sort by score
        sorted_lst = [x[0] for x in sorted(lst, key=lambda x: x[1])]
        if stop == -1:
            return sorted_lst[start:]
        return sorted_lst[start:stop+1]

    async def brpoplpush(self, src, dst, timeout=0):
        # Non-blocking pop for mock
        if src in self.data and self.data[src]:
            val = self.data[src].pop()
            self.data.setdefault(dst, []).insert(0, val)
            return val
        return None

    async def rpush(self, key, value):
        self.data.setdefault(key, []).append(value)
        return len(self.data[key])

    async def sadd(self, key, member):
        s = self.data.setdefault(key, set())
        if not isinstance(s, set):
            if isinstance(s, list):
                s = set(s)
            else:
                s = {s}
            self.data[key] = s
        s.add(str(member))
        return 1

    async def smembers(self, key):
        s = self.data.get(key, set())
        if isinstance(s, list):
            return set(s)
        if isinstance(s, str):
            return {s}
        return s

    async def srem(self, key, member):
        s = self.data.get(key, set())
        if isinstance(s, list):
            s = set(s)
            self.data[key] = s
        if str(member) in s:
            s.remove(str(member))
            return 1
        return 0

    async def exists(self, key):
        return 1 if key in self.data else 0

    def pipeline(self):
        return FakeRedisPipeline(self)

class FakeRedisPipeline:
    def __init__(self, redis_instance):
        self.redis = redis_instance
        self.commands = []

    def lrem(self, key, count, value):
        self.commands.append((self.redis.lrem, (key, count, value)))
        return self

    def zrem(self, key, value):
        self.commands.append((self.redis.zrem, (key, value)))
        return self

    def lpush(self, key, value):
        self.commands.append((self.redis.lpush, (key, value)))
        return self

    def zadd(self, key, mapping):
        self.commands.append((self.redis.zadd, (key, mapping)))
        return self

    def rpush(self, key, value):
        self.commands.append((self.redis.rpush, (key, value)))
        return self

    def sadd(self, key, member):
        self.commands.append((self.redis.sadd, (key, member)))
        return self

    def srem(self, key, member):
        self.commands.append((self.redis.srem, (key, member)))
        return self

    def lrange(self, key, start, stop):
        self.commands.append((self.redis.lrange, (key, start, stop)))
        return self

    def delete(self, key):
        self.commands.append((self.redis.delete, (key,)))
        return self

    def set(self, key, value, ex=None):
        self.commands.append((self.redis.set, (key, value, ex)))
        return self

    async def execute(self):
        results = []
        for func, args in self.commands:
            res = await func(*args)
            results.append(res)
        return results

from backend.ai.providers.base import BaseLLM, LLMResponse

class FakeLLM(BaseLLM):
    def __init__(self):
        self.intent_override = None
        self.reply_override = None

    def get_provider_name(self) -> str:
        return "fake_llm"

    async def generate_response(self, system_prompt: str, user_message: str, temperature: float = 0.7) -> LLMResponse:
        system_prompt_lower = system_prompt.lower()
        if "intent classifier" in system_prompt_lower or "classify their intent" in system_prompt_lower:
            if self.intent_override:
                return LLMResponse(content=self.intent_override, raw_response=None, provider="fake_llm", model="mock")

            # Default mock classification based on user message keywords
            normalized = user_message.lower()
            if any(kw in normalized for kw in ["book", "appointment", "schedule", "reserve"]):
                intent = "booking"
            elif any(kw in normalized for kw in ["order", "toothpaste", "buy"]):
                intent = "order"
            elif any(kw in normalized for kw in ["lead", "callback", "contact", "interest"]):
                intent = "lead_capture"
            elif any(kw in normalized for kw in ["help", "support", "ticket", "issue"]):
                intent = "support"
            elif any(kw in normalized for kw in ["human", "agent", "person"]):
                intent = "support"
            elif any(kw in normalized for kw in ["pay", "payment", "charge", "inr", "rs", "link", "collect"]):
                intent = "payments"
            elif any(kw in normalized for kw in ["hi", "hello", "hey"]):
                intent = "greeting"
            else:
                intent = "unknown"

            content = f'{{"intent": "{intent}", "confidence": 0.95}}'
            return LLMResponse(content=content, raw_response=None, provider="fake_llm", model="mock")

        # Default reply content
        if self.reply_override:
            return LLMResponse(content=self.reply_override, raw_response=None, provider="fake_llm", model="mock")

        return LLMResponse(content=f"Mock response for {user_message}", raw_response=None, provider="fake_llm", model="mock")

@pytest.fixture(autouse=True)
def setup_smoke_environment(monkeypatch):
    monkeypatch.setattr(settings, "AWA_INTEGRATION_MODE", "mock")
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
    monkeypatch.setattr(settings, "AGENT_LLM_PROVIDER", "deterministic_fake")
    monkeypatch.setattr(settings, "ENABLE_MOCK_BILLING", True)
    monkeypatch.setattr(settings, "BILLING_MODE", "mock")
    monkeypatch.setattr(settings, "ENABLE_WEBHOOK_SIGNATURE_BYPASS", True)
    monkeypatch.setattr(settings, "CUSTOMER_OTP_MODE", "mock")
    yield

@pytest.fixture(autouse=True)
def mock_redis_globally(monkeypatch):
    fake_redis_instance = FakeRedis()

    # Mock redis from_url in all places
    monkeypatch.setattr("redis.asyncio.from_url", lambda *args, **kwargs: fake_redis_instance)
    monkeypatch.setattr("backend.api.routes.public_chat.redis", fake_redis_instance)
    monkeypatch.setattr("backend.pipeline.worker.redis", fake_redis_instance)
    monkeypatch.setattr("backend.pipeline.event_dispatcher.redis", fake_redis_instance)
    monkeypatch.setattr("backend.api.webhook.redis_client", fake_redis_instance)

    # Patch session_manager singleton
    from backend.pipeline.session_manager import session_manager
    monkeypatch.setattr(session_manager, "redis", fake_redis_instance)

    # Patch burst_buffer singleton
    from backend.pipeline.burst_buffer import burst_buffer
    monkeypatch.setattr(burst_buffer, "redis", fake_redis_instance)

    # Also override get_redis in worker.py
    async def fake_get_redis():
        return fake_redis_instance
    monkeypatch.setattr("backend.pipeline.worker.get_redis", fake_get_redis)

    yield fake_redis_instance

@pytest.fixture(autouse=True)
def mock_llm_globally(monkeypatch):
    fake_llm_instance = FakeLLM()

    # Mock LLMFactory
    monkeypatch.setattr("backend.ai.factory.LLMFactory.create_provider", lambda *args, **kwargs: fake_llm_instance)


    yield fake_llm_instance

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def api_client():
    import httpx
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    except AttributeError:
        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            yield ac

@pytest.fixture
async def test_db():
    await db_client.connect()
    yield db_client
    await db_client.close()

@pytest.fixture
async def tenant_factory(test_db):
    created_tenants = []

    async def _create(
        name="Smoke Test Tenant",
        plan_code="trial",
        status="active",
        ai_mode="standard",
        modules_config=None,
    ):
        tenant_id = uuid4()
        user_id = uuid4()
        email = f"owner-{uuid4()}@example.com"

        if modules_config is None:
            modules_config = {
                "booking": {
                    "enabled": True,
                    "fields": [
                        {"key": "service_id", "label": "Service", "type": "select", "options_from": "services", "required": True},
                        {"key": "preferred_date", "label": "Preferred Date", "type": "date", "required": True},
                        {"key": "preferred_time", "label": "Preferred Time", "type": "time", "required": True},
                        {"key": "customer_name", "label": "Name", "type": "string", "required": True},
                        {"key": "phone", "label": "Phone", "type": "string", "required": False},
                    ],
                },
                "order": {
                    "enabled": True,
                    "currency": "INR",
                    "fulfillment_types": ["pickup", "delivery"],
                    "payment_methods": ["cod", "upi_on_delivery", "pay_at_store"],
                    "requires_confirmation": True,
                    "categories": [
                        {
                            "id": "products",
                            "name": "Products",
                            "enabled": True,
                            "items": [{"id": "toothpaste", "name": "Toothpaste", "price": 120, "enabled": True}],
                        }
                    ],
                },
                "lead_capture": {
                    "enabled": True,
                    "fields": [
                        {"key": "name", "label": "Name", "type": "string", "required": True},
                        {"key": "interest", "label": "Interested In", "type": "string", "required": True},
                    ]
                },
                "human_handoff": {"enabled": True},
                "support": {"enabled": True},
            }

        config_data = {
            "business_name": name,
            "business_type": "clinic",
            "timezone": "Asia/Kolkata",
            "default_language": "en",
            "working_hours": {
                "monday": "09:00-18:00", "tuesday": "09:00-18:00",
                "wednesday": "09:00-18:00", "thursday": "09:00-18:00",
                "friday": "09:00-18:00", "saturday": "09:00-18:00",
                "sunday": "09:00-18:00"
            },
            "services": [
                {"id": "dental-cleaning", "name": "dental cleaning", "duration_mins": 45, "price": 1000, "enabled": True}
            ],
            "modules": modules_config,
            "security": {
                "public_chat_captcha_required": False,
                "customer_otp_required_for_commitments": True,
            }
        }

        async with db_client.acquire() as conn:
            # Create tenant
            await conn.execute(
                """
                INSERT INTO tenants (id, name, slug, plan_code, status, ai_mode)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                tenant_id, name, f"slug-{uuid4().hex[:6]}", plan_code, status, ai_mode
            )
            # Create user
            await conn.execute(
                """
                INSERT INTO users (id, phone_number, email, name)
                VALUES ($1, $2, $3, $4)
                """,
                user_id, f"+1555{uuid4().hex[:7]}", email, name
            )
            # Link user to tenant
            await conn.execute(
                """
                INSERT INTO user_tenants (id, user_id, tenant_id, role)
                VALUES ($1, $2, $3, $4)
                """,
                uuid4(), user_id, tenant_id, 'admin'
            )
            # Create config
            await conn.execute(
                """
                INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active)
                VALUES ($1, $2, 1, $3, true)
                """,
                uuid4(), tenant_id, json.dumps(config_data)
            )

        created_tenants.append((tenant_id, user_id, email))
        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "email": email,
            "config_data": config_data
        }

    yield _create

    # Cleanup tenants
    async with db_client.acquire() as conn:
        for tenant_id, user_id, email in created_tenants:
            await conn.execute("DELETE FROM bookings WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE tenant_id = $1)", tenant_id)
            await conn.execute("DELETE FROM orders WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM review_events WHERE review_request_id IN (SELECT id FROM review_requests WHERE tenant_id = $1)", tenant_id)
            await conn.execute("DELETE FROM review_requests WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM scheduled_message_events WHERE scheduled_message_id IN (SELECT id FROM scheduled_messages WHERE tenant_id = $1)", tenant_id)
            await conn.execute("DELETE FROM scheduled_messages WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM messages WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM public_chat_sessions WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM public_chat_links WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM conversation_sessions WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM conversations WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM customers WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenant_configs WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM user_tenants WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM audit_logs WHERE tenant_id = $1 OR resource_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
