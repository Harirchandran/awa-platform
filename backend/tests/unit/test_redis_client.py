import pytest

from backend.core import redis_client as redis_module

pytestmark = pytest.mark.anyio


class FakeRedisClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


async def test_redis_clients_are_reused_by_decode_mode_and_timeout(monkeypatch):
    created = []

    def fake_from_url(url, **kwargs):
        client = FakeRedisClient()
        created.append((client, kwargs))
        return client

    monkeypatch.setattr(redis_module.redis, "from_url", fake_from_url)
    monkeypatch.setattr(redis_module, "_original_from_url", fake_from_url)
    redis_module.reset_redis_clients()

    decoded_a = redis_module.get_redis_client(socket_timeout=2)
    decoded_b = redis_module.get_redis_client(socket_timeout=2)
    decoded_default = redis_module.get_redis_client()
    raw = redis_module.get_redis_client_raw(socket_timeout=2)

    assert decoded_a is decoded_b
    assert decoded_default is not decoded_a
    assert raw is not decoded_a
    assert len(created) == 3
    assert created[0][1] == {"decode_responses": True, "socket_timeout": 2}
    assert created[2][1] == {"decode_responses": False, "socket_timeout": 2}

    await redis_module.close_redis_clients()
    assert decoded_a.closed is True
    assert decoded_default.closed is True
    assert raw.closed is True
