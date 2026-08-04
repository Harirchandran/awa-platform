"""
Response Envelope & Error Models — Part V-A §2, §10
Standard API response format for all endpoints.
"""
from typing import Any, Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from uuid import uuid4
class APIMeta(BaseModel):
    """Metadata for paginated responses."""
    page: int = 1
    per_page: int = 20
    total: Optional[int] = None
    total_pages: Optional[int] = None
class APIError(BaseModel):
    """Structured error response."""
    code: str  # e.g. "AUTH_INVALID_TOKEN", "VALIDATION_ERROR"
    message: str
    details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
class APIResponse(BaseModel):
    """
    Standard response envelope for all API responses.
    Part V-A §2 compliance.
    """
    success: bool = True
    data: Any = None
    meta: Optional[Dict[str, Any]] = None
    error: Optional[APIError] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    @classmethod
    def create_success(cls, data: Any = None, meta: Optional[Dict[str, Any]] = None) -> dict:
        """Build a success response envelope."""
        resp = cls(success=True, data=data, meta=meta)
        return resp.model_dump(exclude_none=True)
    @classmethod
    def create_fail(cls, code: str, message: str, details: Optional[Dict] = None) -> dict:
        """Build an error response envelope."""
        resp = cls(
            success=False,
            error=APIError(code=code, message=message, details=details)
        )
        return resp.model_dump(exclude_none=True)
    @classmethod
    def create_error(cls, error_code: str, message: str, details: Optional[Dict] = None) -> dict:
        """Backward-compatible alias used by FastAPI exception handlers."""
        return cls.create_fail(error_code, message, details)
def success_response(data: Any, meta: Optional[APIMeta] = None, request_id: Optional[str] = None) -> dict:
    """Helper to build a success response envelope."""
    resp = APIResponse(
        success=True,
        data=data,
        meta=meta,
        request_id=request_id or str(uuid4())
    )
    return resp.model_dump(exclude_none=True)
def error_response(code: str, message: str, details: Optional[Dict] = None,
                   status_code: int = 500, request_id: Optional[str] = None) -> dict:
    """Helper to build an error response envelope."""
    resp = APIResponse(
        success=False,
        error=APIError(
            code=code,
            message=message,
            details=details,
            request_id=request_id or str(uuid4())
        ),
        request_id=request_id or str(uuid4())
    )
    return resp.model_dump(exclude_none=True)