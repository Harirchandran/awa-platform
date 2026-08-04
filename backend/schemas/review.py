from typing import Any, Dict, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
ReviewStatus = Literal[
    "draft",
    "scheduled",
    "sent",
    "responded",
    "public_review_requested",
    "negative_feedback_escalated",
    "skipped",
    "expired",
]
ReviewSource = Literal["manual", "booking", "order", "reminder", "automation"]
ReviewAction = Literal[
    "send",
    "submit_rating",
    "request_public_review",
    "escalate_negative",
    "skip",
    "expire",
    "reopen",
]
def _compact_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    compacted = " ".join(value.strip().split())
    return compacted or None
class ReviewSettingsUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    public_review_url: Optional[str] = Field(default=None, max_length=1000)
    ask_after_booking_completed: Optional[bool] = None
    ask_after_order_completed: Optional[bool] = None
    ask_delay_minutes: Optional[int] = Field(default=None, ge=0, le=525600)
    positive_rating_threshold: Optional[int] = Field(default=None, ge=1, le=5)
    negative_feedback_escalation_enabled: Optional[bool] = None
    request_template: Optional[str] = Field(default=None, min_length=1, max_length=1200)
    positive_template: Optional[str] = Field(default=None, min_length=1, max_length=1200)
    negative_template: Optional[str] = Field(default=None, min_length=1, max_length=1200)
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("public_review_url")
    @classmethod
    def validate_public_review_url(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            val = value.strip()
            if val and not (val.startswith("http://") or val.startswith("https://")):
                raise ValueError("public_review_url must start with http:// or https://")
            return val
        return value

    @field_validator("request_template", "positive_template", "negative_template")
    @classmethod
    def normalize_text(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else value
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class ReviewRequestCreateRequest(BaseModel):
    customer_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    booking_id: Optional[UUID] = None
    order_id: Optional[UUID] = None
    status: ReviewStatus = "draft"
    source: ReviewSource = "manual"
    public_review_url: Optional[str] = Field(default=None, max_length=1000)
    idempotency_key: Optional[str] = Field(default=None, max_length=160)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("public_review_url")
    @classmethod
    def validate_public_review_url(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            val = _compact_text(value)
            if val and not (val.startswith("http://") or val.startswith("https://")):
                raise ValueError("public_review_url must start with http:// or https://")
            return val
        return value

    @field_validator("idempotency_key")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
    @model_validator(mode="after")
    def validate_target(self) -> "ReviewRequestCreateRequest":
        if not any([self.customer_id, self.conversation_id, self.booking_id, self.order_id]):
            raise ValueError("Provide a customer, conversation, booking, or order target")
        return self
class ReviewRequestUpdateRequest(BaseModel):
    status: Optional[ReviewStatus] = None
    public_review_url: Optional[str] = Field(default=None, max_length=1000)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    feedback_text: Optional[str] = Field(default=None, max_length=3000)
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("public_review_url")
    @classmethod
    def validate_public_review_url(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            val = _compact_text(value)
            if val and not (val.startswith("http://") or val.startswith("https://")):
                raise ValueError("public_review_url must start with http:// or https://")
            return val
        return value

    @field_validator("feedback_text")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class ReviewActionRequest(BaseModel):
    action: ReviewAction
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    feedback_text: Optional[str] = Field(default=None, max_length=3000)
    public_review_url: Optional[str] = Field(default=None, max_length=1000)
    note: Optional[str] = Field(default=None, max_length=500)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("feedback_text", "public_review_url", "note")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 3000:
            raise ValueError("metadata is too large")
        return value
    @model_validator(mode="after")
    def validate_action_payload(self) -> "ReviewActionRequest":
        if self.action == "submit_rating" and self.rating is None:
            raise ValueError("rating is required for submit_rating")
        return self