from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.core.context import ConversationSession, ExecutionContext, OtpVerificationEvidence, SessionState, Tenant, TenantConfig, User
from backend.core.customer_assurance import (
    AssuranceLevel,
    has_required_assurance,
    phone_identity_hash,
    required_assurance,
)
from backend.schemas.config import normalize_tenant_config


def _context(*, incoming: dict | None = None) -> ExecutionContext:
    tenant_id, conversation_id, session_id = uuid4(), uuid4(), uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000"),
        session_data=ConversationSession(
            id=session_id, tenant_id=tenant_id, conversation_id=conversation_id, config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={}),
        incoming_message=incoming or {"channel_type": "awa_chat", "channel_session_id": str(session_id)},
    )


def test_booking_assurance_is_platform_owned_and_legacy_config_is_ignored():
    assert required_assurance("booking.execute") == AssuranceLevel.PHONE_POSSESSION
    for value in (True, False, None):
        normalized = normalize_tenant_config({
            "business_name": "Test", "security": {"customer_otp_required_for_commitments": value},
        })
        assert "customer_otp_required_for_commitments" not in normalized.get("security", {})


def test_trusted_whatsapp_provenance_satisfies_phone_possession():
    context = _context()
    account_id = uuid4()
    context.incoming_message = {
        "channel_type": "whatsapp", "source_type": "webhook",
        "tenant_id": str(context.tenant.id), "conversation_id": str(context.session_data.conversation_id),
        "channel_account_id": str(account_id), "whatsapp_account_id": str(account_id),
    }
    assert has_required_assurance(context, "booking.execute", requires_verified_customer=True)


def test_raw_whatsapp_channel_string_is_not_assurance():
    context = _context(incoming={"channel_type": "whatsapp"})
    assert not has_required_assurance(context, "booking.execute", requires_verified_customer=True)


def test_awa_chat_evidence_is_session_bound_and_expires():
    context = _context()
    now = datetime.now(timezone.utc)
    context.session_data.state.action_ledger.verification.otp_verifications = [OtpVerificationEvidence(
        binding="a" * 64, action_nonce="b" * 32, verification_ref="c" * 32,
        tenant_id=str(context.tenant.id), conversation_id=str(context.session_data.conversation_id),
        channel_session_id=str(context.session_data.id), config_version=1, tool_key="booking.execute",
        args_hash="d" * 24, verified_at=now, expires_at=now + timedelta(minutes=5),
        phone_identity_hash=phone_identity_hash("+15550000000"),
    )]
    assert has_required_assurance(context, "booking.execute", requires_verified_customer=True)
    context.session_data.state.action_ledger.verification.otp_verifications[0].expires_at = now - timedelta(seconds=1)
    assert not has_required_assurance(context, "booking.execute", requires_verified_customer=True)
