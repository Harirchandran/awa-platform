from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID


BUSINESS_EVENT_TYPES = {
    "lead.created",
    "booking.created",
    "booking.completed",
    "order.created",
    "payment.requested",
    "payment.completed",
    "reminder.scheduled",
    "reminder.sent",
    "review.requested",
    "review.submitted",
    "review.negative",
    "handoff.created",
    "conversation.turn.received",
}


@dataclass
class BusinessEventCreate:
    tenant_id: UUID
    event_type: str
    aggregate_type: str
    source_domain: str
    aggregate_id: Optional[UUID] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    conversation_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    available_at: Optional[datetime] = None
    max_attempts: int = 3


@dataclass
class EventHandlerResult:
    status: str
    reason_code: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def processed(cls, metadata: Optional[Dict[str, Any]] = None) -> "EventHandlerResult":
        return cls(status="processed", metadata=metadata or {})

    @classmethod
    def skipped(cls, reason_code: str, metadata: Optional[Dict[str, Any]] = None) -> "EventHandlerResult":
        return cls(status="skipped", reason_code=reason_code, metadata=metadata or {})

    @classmethod
    def failed(cls, reason_code: str, metadata: Optional[Dict[str, Any]] = None) -> "EventHandlerResult":
        return cls(status="failed", reason_code=reason_code, metadata=metadata or {})
