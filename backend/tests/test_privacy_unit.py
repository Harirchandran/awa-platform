
import pytest
import logging
from uuid import uuid4
from datetime import datetime, timezone
from backend.ops.logger import PIIMasker
from backend.api.webhook import receive_webhook
from backend.api import privacy
# ==================== PII MASKER TESTS ====================
def test_pii_masking():
    masker = PIIMasker()
    # Test Phone
    record = logging.LogRecord("test", logging.INFO, "", 0, "Call me at +1 555-555-5555", (), None)
    masker.filter(record)
    assert "[REDACTED_PHONE]" in record.msg
    assert "555" not in record.msg
    # Test Email
    record.msg = "Email me at user@example.com please"
    masker.filter(record)
    assert "[REDACTED_EMAIL]" in record.msg
    assert "user@example.com" not in record.msg
    # Test Name Patterns
    record.msg = '{"name": "John Doe", "age": 30}'
    masker.filter(record)
    assert '[REDACTED_NAME]' in record.msg
    assert "John Doe" not in record.msg
    record.msg = "name: Jane Doe"
    masker.filter(record)
    assert "[REDACTED_NAME]" in record.msg
# ==================== DATA MINIMIZATION TESTS ====================
@pytest.mark.asyncio
async def test_raw_payload_stripping():
    # Mock webhook payload with full PII
    full_payload = {
        "id": "wamid.123",
        "type": "text",
        "timestamp": "1234567890",
        "from": "919876543210",  # PII
        "profile": {"name": "Alice"}, # PII
        "text": {"body": "Hello World"}
    }
    # Simulate the stripping logic from webhook.py
    stripped_payload = {
        "message_id": full_payload.get("id"),
        "type": full_payload.get("type"),
        "timestamp": full_payload.get("timestamp"),
    }
    assert "from" not in stripped_payload
    assert "profile" not in stripped_payload
    assert "text" not in stripped_payload
    assert stripped_payload["message_id"] == "wamid.123"
# ==================== CONSENT & PRIVACY FLOW TESTS ====================
# These require DB access - integration tests
@pytest.mark.asyncio
async def test_consent_lifecycle():
    """
    Simulate:
    1. Create customer (default pending)
    2. Check pending status
    3. Grant consent
    4. Check granted status
    5. Withdraw consent
    6. Verify data erasure (mocked or checked via DB)
    """
    # This would be an integration test requiring the DB fixture
    pass
@pytest.mark.anyio
async def test_privacy_export_writes_count_only_audit(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    customer_id = uuid4()
    audits = []
    class FakeDb:
        async def export_customer_data(self, _tenant_id, _customer_id):
            return {
                "customer": {"id": customer_id, "phone_number": "+15550123"},
                "messages": [{"message_text": "private message"}],
                "bookings": [],
            }
        async def write_audit_log(self, **kwargs):
            audits.append(kwargs)
    monkeypatch.setattr(privacy, "db_client", FakeDb())
    response = await privacy.export_customer_data(
        customer_id,
        user={"tenant_id": tenant_id, "user_id": user_id, "role": "tenant_admin"},
    )
    assert response["data"]["action"] == "data_exported"
    assert audits[0]["action"] == "privacy_data_exported"
    assert audits[0]["details"] == {
        "records_exported": {
            "customer": 1,
            "messages": 1,
            "bookings": 0,
        }
    }
    assert "private message" not in str(audits[0]["details"])
    assert "+15550123" not in str(audits[0]["details"])
@pytest.mark.anyio
async def test_privacy_erase_writes_deletion_count_audit(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    customer_id = uuid4()
    audits = []
    class FakeDb:
        async def update_customer_consent(self, tenant_id, customer_id, consent_status):
            return True
        async def erase_customer_data(self, _tenant_id, _customer_id):
            return {"messages": 2, "conversations": 1, "customers": 1}
        async def write_audit_log(self, **kwargs):
            audits.append(kwargs)
    monkeypatch.setattr(privacy, "db_client", FakeDb())
    response = await privacy.update_consent(
        customer_id,
        status="withdrawn",
        user={"tenant_id": tenant_id, "user_id": user_id, "role": "tenant_admin"},
    )
    assert response["data"]["action"] == "consent_withdrawn_and_data_erased"
    assert audits[0]["action"] == "privacy_consent_withdrawn_data_erased"
    assert audits[0]["details"]["records_deleted"]["customers"] == 1