from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
PaymentType = Literal[
    "booking_deposit",
    "booking_full_payment",
    "order_payment",
    "custom_payment",
    "balance_due",
]
PaymentProvider = Literal["manual", "external", "razorpay", "upi", "cash", "bank_transfer"]
PaymentAction = Literal["send", "cancel", "mark_paid", "mark_failed", "mark_refunded", "expire"]
def _compact_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    compacted = " ".join(value.strip().split())
    return compacted or None
class PaymentRequestCreateRequest(BaseModel):
    customer_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    booking_id: Optional[UUID] = None
    order_id: Optional[UUID] = None
    amount: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="INR", min_length=1, max_length=8)
    type: PaymentType = "custom_payment"
    provider: PaymentProvider = "manual"
    payment_url: Optional[str] = Field(default=None, max_length=1000)
    expires_at: Optional[datetime] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=160)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()
    @field_validator("payment_url", "idempotency_key")
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
    def validate_amount_source(self) -> "PaymentRequestCreateRequest":
        if self.amount is None and self.order_id is None:
            raise ValueError("amount is required unless order_id is provided")
        return self
class PaymentRequestUpdateRequest(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    currency: Optional[str] = Field(default=None, min_length=1, max_length=8)
    type: Optional[PaymentType] = None
    provider: Optional[PaymentProvider] = None
    payment_url: Optional[str] = Field(default=None, max_length=1000)
    expires_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value is not None else value
    @field_validator("payment_url")
    @classmethod
    def normalize_url(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class PaymentActionRequest(BaseModel):
    action: PaymentAction
    note: Optional[str] = Field(default=None, max_length=500)
    provider_payment_id: Optional[str] = Field(default=None, max_length=160)
    payment_url: Optional[str] = Field(default=None, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("note", "provider_payment_id", "payment_url")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 3000:
            raise ValueError("metadata is too large")
        return value