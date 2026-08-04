from uuid import UUID
from typing import Optional
from backend.schemas.canonical_message import (
    CanonicalMessage, SourceType, ChannelType, WorkerMetadata, MessagePayload,
    MessageDirection, MessageType, SecurityDisposition, ProcessingMetadata, TraceMetadata
)
from backend.db.client import PublicChatTurnIntakeOutcome

def build_canonical_public_chat_turn(
    *,
    intake: PublicChatTurnIntakeOutcome,
    tenant_id: UUID,
    conversation_id: UUID,
    customer_id: UUID,
    public_chat_session_id: UUID,
    channel_account_id: UUID,
    message_text: Optional[str],
    worker_version: str,
    max_retries: int,
) -> CanonicalMessage:
    if not tenant_id:
        raise ValueError("tenant_id must be provided")
    if not conversation_id:
        raise ValueError("conversation_id must be provided")
    if not public_chat_session_id:
        raise ValueError("public_chat_session_id must be provided")
    if not channel_account_id:
        raise ValueError("channel_account_id must be provided")
    if not customer_id:
        raise ValueError("customer_id must be provided")

    return CanonicalMessage(
        schema_version=2,
        event_type="incoming_message",
        event_id=intake.event_id,
        source_type=SourceType.AWA_CHAT,
        channel_type=ChannelType.AWA_CHAT,
        channel_account_id=channel_account_id,
        channel_session_id=str(public_chat_session_id),
        tenant_id=tenant_id,
        whatsapp_account_id=None,
        conversation_id=conversation_id,
        conversation_sequence_number=intake.conversation_sequence_number,
        customer_id=customer_id,
        workflow_instance_id=None,
        config_version=intake.config_version,
        worker_metadata=WorkerMetadata(worker_version=worker_version),
        message=MessagePayload(
            message_id=intake.message_id,
            provider_message_id=intake.provider_message_id,
            direction=MessageDirection.INBOUND,
            timestamp=intake.message_created_at,
            message_type=MessageType.TEXT,
            text=message_text,
            media_url=None,
            security_disposition=SecurityDisposition(intake.security_disposition)
        ),
        processing=ProcessingMetadata(
            retry_count=0,
            max_retries=max_retries,
            first_received_at=intake.message_created_at,
            last_retry_at=None,
            failure_code=None
        ),
        trace=TraceMetadata(
            trace_id=intake.trace_id,
            parent_trace_id=None
        )
    )
