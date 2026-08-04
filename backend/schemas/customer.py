from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator
from uuid import UUID
from datetime import datetime
class LeadMetadata(BaseModel):
    """
    The structured data safely expected inside customers.metadata
    for Lead Capture modules.
    """
    city: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    lead_score: Optional[int] = None
    # Catch-all for extra raw fields
    extra_data: Optional[Dict[str, Any]] = None
class CustomerResponse(BaseModel):
    """
    Response model for GET /api/v1/customers
    Includes synthetic aggregation fields expected by the frontend.
    """
    id: UUID
    phone_number: str
    name: Optional[str] = None
    email: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    consent_status: str
    # Synthetics (required by frontend)
    first_seen: datetime
    last_interaction: Optional[datetime] = None
    conversation_count: int = 0
    booking_count: int = 0
    order_count: int = 0
    total_order_value: float = 0
    tags: list[Dict[str, Any]] = Field(default_factory=list)
class CustomerUpdateRequest(BaseModel):
    """
    Payload for PATCH /api/v1/customers/{id}
    """
    name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None
    consent_status: Optional[Literal["pending", "granted", "withdrawn"]] = None
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class CustomerTagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(default="#64748b", pattern=r"^#[0-9A-Fa-f]{6}$")
    description: Optional[str] = Field(default=None, max_length=240)
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())
class CustomerTagUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=40)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    description: Optional[str] = Field(default=None, max_length=240)
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return " ".join(value.strip().split())
class CustomerTagAttachRequest(BaseModel):
    tag_id: UUID
class CustomerNoteCreateRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
    visibility: Literal["internal"] = "internal"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        return value.strip()
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 3000:
            raise ValueError("metadata is too large")
        return value
class CustomerNoteUpdateRequest(BaseModel):
    note: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip()
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 3000:
            raise ValueError("metadata is too large")
        return value
