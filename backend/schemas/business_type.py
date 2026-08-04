from __future__ import annotations
import re
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
CODE_PATTERN = re.compile(r"^[a-z0-9_-]+$")
MODULE_KEY_PATTERN = re.compile(r"^[a-z0-9_]+$")
class BusinessTypeCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    enabled_modules: List[str] = Field(default_factory=list)
    note: Optional[str] = Field(None, max_length=500)
    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not CODE_PATTERN.match(cleaned):
            raise ValueError("Business type code must be lowercase alphanumeric and may contain only hyphens or underscores")
        return cleaned
    @field_validator("enabled_modules")
    @classmethod
    def validate_enabled_modules(cls, value: List[str]) -> List[str]:
        invalid = [m for m in value if not MODULE_KEY_PATTERN.match(m)]
        if invalid:
            raise ValueError(f"Invalid module key format: {', '.join(invalid)}")
        return value
class BusinessTypeUpdateRequest(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    enabled_modules: Optional[List[str]] = None
    note: Optional[str] = Field(None, max_length=500)
    @field_validator("enabled_modules")
    @classmethod
    def validate_enabled_modules(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        invalid = [m for m in value if not MODULE_KEY_PATTERN.match(m)]
        if invalid:
            raise ValueError(f"Invalid module key format: {', '.join(invalid)}")
        return value
class BusinessTypeResponse(BaseModel):
    id: UUID
    code: str
    label: str
    enabled_modules: List[str]
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime