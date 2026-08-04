from datetime import datetime
from enum import Enum
from typing import Optional, Literal
from uuid import UUID
from pydantic import BaseModel, Field, constr
# Enums based on DB contract
class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    INTERACTIVE = "interactive"
    CONTACTS = "contacts"
    UNKNOWN = "unknown"
class ProcessingStatus(str, Enum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DLQ = "dlq"
class SourceType(str, Enum):
    WEBHOOK = "webhook"
    AWA_CHAT = "awa_chat"
    SCHEDULER = "scheduler"
    MANUAL = "manual"
    INTERNAL_EVENT = "internal_event"
class ChannelType(str, Enum):
    WHATSAPP = "whatsapp"
    AWA_CHAT = "awa_chat"
class SecurityDisposition(str, Enum):
    NONE = "none"
    PROMPT_SECURITY_FLAGGED = "prompt_security_flagged"
# Sub-models for structured data
class WorkerMetadata(BaseModel):
    worker_version: str
class MessagePayload(BaseModel):
    message_id: UUID
    provider_message_id: str
    direction: MessageDirection
    timestamp: datetime
    message_type: MessageType
    text: Optional[str] = None
    media_url: Optional[str] = None # For non-text types
    security_disposition: SecurityDisposition = SecurityDisposition.NONE
class ProcessingMetadata(BaseModel):
    retry_count: int = 0
    max_retries: int = 5
    first_received_at: datetime
    last_retry_at: Optional[datetime] = None
    failure_code: Optional[str] = None
class TraceMetadata(BaseModel):
    trace_id: UUID
    parent_trace_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
class DLQMetadata(BaseModel):
    dlq_reason: str
    dlq_timestamp: datetime
    last_failure_code: Optional[str] = None
class CostContext(BaseModel):
    """Per-message cost tracking — written to tenant_cost_daily after execution."""
    ai_tokens_used: int = 0
    ai_cost: float = 0.0
    compute_cost: float = 0.0
# Main Canonical Message Schema
class CanonicalMessage(BaseModel):
    schema_version: int = 2
    event_type: Literal["incoming_message"] = "incoming_message"
    event_id: UUID
    source_type: SourceType
    channel_type: ChannelType = ChannelType.WHATSAPP
    channel_account_id: Optional[UUID] = None
    channel_session_id: Optional[str] = None
    tenant_id: UUID
    whatsapp_account_id: Optional[UUID] = None
    conversation_id: UUID
    conversation_sequence_number: int
    customer_id: UUID
    workflow_instance_id: Optional[UUID] = None
    config_version: int
    worker_metadata: WorkerMetadata
    message: MessagePayload
    processing: ProcessingMetadata
    trace: TraceMetadata
    dlq_metadata: Optional[DLQMetadata] = None
    cost_context: CostContext = Field(default_factory=CostContext)
    class Config:
        json_schema_extra = {
            "example": {
                "schema_version": 2,
                "event_type": "incoming_message",
                "event_id": "123e4567-e89b-12d3-a456-426614174000",
                "source_type": "webhook",
                "tenant_id": "123e4567-e89b-12d3-a456-426614174001",
                "whatsapp_account_id": "123e4567-e89b-12d3-a456-426614174002",
                "conversation_id": "123e4567-e89b-12d3-a456-426614174003",
                "conversation_sequence_number": 182,
                "customer_id": "123e4567-e89b-12d3-a456-426614174004",
                "workflow_instance_id": None,
                "config_version": 12,
                "worker_metadata": {
                    "worker_version": "worker-1.3.7"
                },
                "message": {
                    "message_id": "123e4567-e89b-12d3-a456-426614174005",
                    "provider_message_id": "wamid.HBgM...",
                    "direction": "inbound",
                    "timestamp": "2026-02-08T12:00:00Z",
                    "message_type": "text",
                    "text": "Hello world"
                },
                "processing": {
                    "retry_count": 0,
                    "max_retries": 5,
                    "first_received_at": "2026-02-08T12:00:00Z",
                    "last_retry_at": None,
                    "failure_code": None
                },
                "trace": {
                    "trace_id": "123e4567-e89b-12d3-a456-426614174006",
                    "parent_trace_id": None
                }
            }
        }