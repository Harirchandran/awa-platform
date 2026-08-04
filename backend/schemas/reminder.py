from datetime import datetime, time
from typing import Any, Dict, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
ReminderTrigger = Literal[
    "manual",
    "before_booking",
    "after_booking",
    "before_payment_expiry",
    "after_payment_failed",
    "after_order_completed",
    "inactive_conversation",
    "custom",
]
ReminderChannel = Literal["whatsapp", "email", "manual"]
ScheduledMessageAction = Literal["cancel", "snooze", "mark_sent", "mark_failed", "skip", "mark_due", "retry"]
def _compact_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    compacted = " ".join(value.strip().split())
    return compacted or None
class ReminderRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger_type: ReminderTrigger = "manual"
    offset_minutes: int = Field(default=0, ge=-525600, le=525600)
    channel: ReminderChannel = "whatsapp"
    template_text: str = Field(min_length=1, max_length=1200)
    is_active: bool = True
    quiet_hours_start: Optional[time] = None
    quiet_hours_end: Optional[time] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _compact_text(value) or value
    @field_validator("template_text")
    @classmethod
    def normalize_template(cls, value: str) -> str:
        return value.strip()
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class ReminderRuleUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    trigger_type: Optional[ReminderTrigger] = None
    offset_minutes: Optional[int] = Field(default=None, ge=-525600, le=525600)
    channel: Optional[ReminderChannel] = None
    template_text: Optional[str] = Field(default=None, min_length=1, max_length=1200)
    is_active: Optional[bool] = None
    quiet_hours_start: Optional[time] = None
    quiet_hours_end: Optional[time] = None
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("template_text")
    @classmethod
    def normalize_template(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else value
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class ScheduledMessageCreateRequest(BaseModel):
    rule_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    booking_id: Optional[UUID] = None
    order_id: Optional[UUID] = None
    payment_request_id: Optional[UUID] = None
    review_request_id: Optional[UUID] = None
    trigger_type: ReminderTrigger = "manual"
    channel: ReminderChannel = "whatsapp"
    message_text: str = Field(min_length=1, max_length=1200)
    scheduled_for: datetime
    idempotency_key: Optional[str] = Field(default=None, max_length=160)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("message_text")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        return value.strip()
    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
    @model_validator(mode="after")
    def validate_target(self) -> "ScheduledMessageCreateRequest":
        if not any([
            self.customer_id,
            self.booking_id,
            self.order_id,
            self.payment_request_id,
            self.conversation_id,
            self.review_request_id,
        ]):
            raise ValueError("Provide a customer, conversation, booking, order, payment, or review target")
        return self
class ScheduledMessageActionRequest(BaseModel):
    action: ScheduledMessageAction
    scheduled_for: Optional[datetime] = None
    snooze_minutes: Optional[int] = Field(default=None, ge=1, le=43200)
    note: Optional[str] = Field(default=None, max_length=500)
    last_error: Optional[str] = Field(default=None, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("note", "last_error")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 3000:
            raise ValueError("metadata is too large")
        return value