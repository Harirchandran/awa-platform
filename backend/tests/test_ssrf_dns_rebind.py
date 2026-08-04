import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import socket
import ipaddress
import httpx
from backend.support.notification_routing import record_support_notification_route, PinnedAsyncHTTPTransport

pytestmark = pytest.mark.anyio

async def test_support_notification_route_ssrf_protection(monkeypatch):
    class FakeConn:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def fetchval(self, *args, **kwargs):
            return None

    db_client = AsyncMock()
    db_client.acquire = lambda: FakeConn()
    tenant_id = uuid4()
    conversation_id = uuid4()
    actor_id = uuid4()

    # 1. Test localhost rejection
    config = {
        "support_notifications": {
            "webhook_urls": ["http://localhost/webhook"]
        }
    }

    # Mock socket.getaddrinfo to return loopback IP for localhost
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "localhost":
            return [(None, None, None, None, ("127.0.0.1",))]
        raise socket.gaierror()

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    route = await record_support_notification_route(
        db_client=db_client,
        tenant_id=tenant_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        config=config,
        source="customer",
        reason="Test SSRF",
    )

    assert route["external_notification"]["status"] == "failed"

    # Verify that a failed audit log was written
    failed_logs = [call for call in db_client.write_audit_log.call_args_list if call.kwargs.get("action") == "support_notification_failed"]
    assert len(failed_logs) > 0
    assert failed_logs[0].kwargs["details"]["reason"] == "unsafe_url"

async def test_support_notification_route_pins_transport(monkeypatch):
    class FakeConn:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def fetchval(self, *args, **kwargs):
            return None

    db_client = AsyncMock()
    db_client.acquire = lambda: FakeConn()
    tenant_id = uuid4()
    conversation_id = uuid4()
    actor_id = uuid4()

    # 2. Test safe URL pins transport
    config = {
        "support_notifications": {
            "webhook_urls": ["https://example.com/webhook"]
        }
    }

    # Mock socket.getaddrinfo to return safe IP
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(None, None, None, None, ("93.184.216.34",))]
        raise socket.gaierror()

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    # Capture transport passed to httpx.AsyncClient
    captured_transport = None
    original_client_init = httpx.AsyncClient.__init__

    def mock_client_init(self, *args, **kwargs):
        nonlocal captured_transport
        captured_transport = kwargs.get("transport")
        original_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", mock_client_init)

    # Mock httpx.AsyncClient.post to avoid actual network requests
    mock_response = httpx.Response(status_code=200, json={"status": "ok"})
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response))

    route = await record_support_notification_route(
        db_client=db_client,
        tenant_id=tenant_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        config=config,
        source="customer",
        reason="Test SSRF",
    )

    assert route["external_notification"]["status"] == "delivered"
    assert isinstance(captured_transport, PinnedAsyncHTTPTransport)
    assert captured_transport.resolved_ip == "93.184.216.34"
    assert captured_transport.original_host == "example.com"
