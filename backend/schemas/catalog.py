from typing import Any, Dict, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
def _compact_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    compacted = " ".join(value.strip().split())
    return compacted or None
class CatalogCategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    sort_order: int = 0
    is_active: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _compact_text(value) or value
    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class CatalogCategoryUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class CatalogItemCreateRequest(BaseModel):
    category_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=1000)
    price: float = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=1, max_length=8)
    sku: Optional[str] = Field(default=None, max_length=80)
    image_url: Optional[str] = Field(default=None, max_length=1000)
    is_active: bool = True
    is_available: bool = True
    stock_tracking_enabled: bool = False
    stock_quantity: float = 0
    low_stock_threshold: Optional[float] = Field(default=None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _compact_text(value) or value
    @field_validator("description", "sku", "image_url")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class CatalogItemUpdateRequest(BaseModel):
    category_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=1000)
    price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=1, max_length=8)
    sku: Optional[str] = Field(default=None, max_length=80)
    image_url: Optional[str] = Field(default=None, max_length=1000)
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None
    stock_tracking_enabled: Optional[bool] = None
    stock_quantity: Optional[float] = None
    low_stock_threshold: Optional[float] = Field(default=None, ge=0)
    metadata: Optional[Dict[str, Any]] = None
    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("description", "sku", "image_url")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value is not None else value
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is not None and len(str(value)) > 5000:
            raise ValueError("metadata is too large")
        return value
class InventoryAdjustRequest(BaseModel):
    quantity_delta: float
    reason: Optional[str] = Field(default=None, max_length=500)
    event_type: Literal["manual_adjustment", "restock", "correction"] = "manual_adjustment"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    @field_validator("quantity_delta")
    @classmethod
    def validate_delta(cls, value: float) -> float:
        if value == 0:
            raise ValueError("quantity_delta cannot be zero")
        return value
    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: Optional[str]) -> Optional[str]:
        return _compact_text(value)
    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(str(value)) > 3000:
            raise ValueError("metadata is too large")
        return value