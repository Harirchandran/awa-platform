"""
AWA API Application Entry Point — Production
FastAPI app with versioning, middleware, rate limiting, and standard response format.
"""
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from uuid import uuid4
import re
import time
from backend.api.webhook import webhook_router
from backend.api.privacy import router as privacy_router
from backend.api.rate_limiter import limiter, rate_limit_exceeded_handler
from backend.core.config import settings
from backend.ops.logger import logger
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from asyncpg.exceptions import (
    UniqueViolationError,
    ForeignKeyViolationError,
    CheckViolationError,
    NotNullViolationError,
    DataError,
    InvalidTextRepresentationError,
    InsufficientPrivilegeError,
)
# Import all API route modules
from backend.api.routes.tenant import router as tenant_router
from backend.api.routes.customers import router as customers_router
from backend.api.routes.conversations import router as conversations_router
from backend.api.routes.bookings import router as bookings_router
from backend.api.routes.orders import router as orders_router
from backend.api.routes.catalog import router as catalog_router
from backend.api.routes.media import router as media_router
from backend.api.routes.payments import router as payments_router
from backend.api.routes.reminders import router as reminders_router
from backend.api.routes.reviews import router as reviews_router
from backend.api.routes.whatsapp_accounts import router as wa_accounts_router
from backend.api.routes.config import router as config_router
from backend.api.routes.feature_flags import router as feature_flags_router
from backend.api.routes.costs import router as costs_router
from backend.api.routes.audit_logs import router as audit_logs_router
from backend.api.routes.awa_chat import router as awa_chat_router
from backend.api.routes.public_chat import router as public_chat_router
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.team import router as team_router
from backend.api.routes.leads import router as leads_router
# ==================== APP FACTORY ====================
app = FastAPI(
    title="AWA API",
    description="WhatsApp Automation Platform API",
    version="1.0.0",
    docs_url=None if settings.ENV == "production" else "/docs",
    redoc_url=None if settings.ENV == "production" else "/redoc"
)
# ==================== RATE LIMITING ====================
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@app.on_event("shutdown")
async def shutdown_shared_clients():
    from backend.core.redis_client import close_redis_clients

    await close_redis_clients()

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from backend.schemas.response import APIResponse
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=APIResponse.create_error(
            error_code=f"HTTP_{exc.status_code}",
            message=str(exc.detail)
        )
    )
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        safe_error = dict(error)
        if isinstance(safe_error.get("ctx"), dict):
            safe_error["ctx"] = {key: str(value) for key, value in safe_error["ctx"].items()}
        errors.append(safe_error)
    details = None if settings.ENV == "production" else {"errors": errors}
    message = "Request validation failed"
    path = request.url.path
    if path.startswith("/api/v1/public-chat/sessions/") and path.endswith("/messages"):
        for error in exc.errors():
            loc = error.get("loc", ())
            if error.get("type") == "string_too_long" and loc and loc[-1] == "message_text":
                message = "Keep messages under 800 characters."
                break
    return JSONResponse(
        status_code=422,
        content=APIResponse.create_error(
            error_code="VALIDATION_ERROR",
            message=message,
            details=details
        )
    )
@app.exception_handler(UniqueViolationError)
async def unique_violation_handler(request: Request, exc: UniqueViolationError):
    logger.error(f"Database Unique Violation on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=409,
        content=APIResponse.create_error(
            error_code="DUPLICATE_RESOURCE",
            message="A resource with the specified unique attributes already exists."
        )
    )

@app.exception_handler(ForeignKeyViolationError)
async def foreign_key_violation_handler(request: Request, exc: ForeignKeyViolationError):
    logger.error(f"Database Foreign Key Violation on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=400,
        content=APIResponse.create_error(
            error_code="INVALID_REFERENCE",
            message="The request references a linked resource that does not exist or is in use."
        )
    )

@app.exception_handler(CheckViolationError)
async def check_violation_handler(request: Request, exc: CheckViolationError):
    logger.error(f"Database Check Violation on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=400,
        content=APIResponse.create_error(
            error_code="CONSTRAINT_VIOLATION",
            message="The provided parameters violate business validation rules."
        )
    )

@app.exception_handler(NotNullViolationError)
async def not_null_violation_handler(request: Request, exc: NotNullViolationError):
    logger.error(f"Database Not Null Violation on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=400,
        content=APIResponse.create_error(
            error_code="MISSING_FIELD",
            message="A required field was not provided."
        )
    )

@app.exception_handler(DataError)
async def data_error_handler(request: Request, exc: DataError):
    logger.error(f"Database Data Error on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=422,
        content=APIResponse.create_error(
            error_code="TYPE_MISMATCH",
            message="Invalid data format or type mismatch detected."
        )
    )

@app.exception_handler(InvalidTextRepresentationError)
async def invalid_text_representation_handler(request: Request, exc: InvalidTextRepresentationError):
    logger.error(f"Database Invalid Text Representation Error on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=422,
        content=APIResponse.create_error(
            error_code="TYPE_MISMATCH",
            message="Invalid data format or type mismatch detected."
        )
    )

@app.exception_handler(InsufficientPrivilegeError)
async def insufficient_privilege_handler(request: Request, exc: InsufficientPrivilegeError):
    logger.error(f"Database Insufficient Privilege (RLS violation) on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=403,
        content=APIResponse.create_error(
            error_code="FORBIDDEN",
            message="Access to the requested resource is denied."
        )
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled Exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=APIResponse.create_error(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred."
        )
    )
# ==================== CORS ====================
# Configurable via CORS_ORIGINS env var (comma-separated)
cors_origins = getattr(settings, "CORS_ORIGINS", "http://localhost:3000")
allowed_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Trace-ID"],
)
# ==================== OBSERVABILITY MIDDLEWARE ====================
_HEADER_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
def _safe_header(value: str | None) -> str:
    """L-3: Validate client-supplied trace/request IDs to prevent log injection."""
    if value and _HEADER_RE.match(value):
        return value
    return str(uuid4())
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """
    Injects X-Trace-ID and X-Request-ID headers (Part V-A §16).
    """
    request_id = _safe_header(request.headers.get("X-Request-ID"))
    trace_id = _safe_header(request.headers.get("X-Trace-ID"))
    start_time = time.time()
    # Inject into request state for downstream dependencies
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    response: Response = await call_next(request)
    # Inject headers into response
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id
    response.headers["X-Response-Time"] = f"{(time.time() - start_time)*1000:.2f}ms"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.ENV == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # M-8: Content Security Policy for production
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' https://connect.facebook.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://*.supabase.co https://graph.facebook.com; "
            "frame-ancestors 'none'",
        )
    return response
# ==================== MOUNT VERSIONED ROUTES ====================
# Mount the webhook app under /api/v1/
app.include_router(webhook_router, prefix="/api/v1")
# Mount privacy/compliance endpoints
app.include_router(privacy_router, prefix="/api/v1")
# Mount authenticated CRUD API routes (Phase 1 & 2)
app.include_router(tenant_router)
app.include_router(customers_router)
app.include_router(conversations_router)
app.include_router(bookings_router)
app.include_router(leads_router)
app.include_router(orders_router)
app.include_router(catalog_router)
app.include_router(media_router)
app.include_router(payments_router)
app.include_router(reminders_router)
app.include_router(reviews_router)
app.include_router(wa_accounts_router)
app.include_router(config_router)
app.include_router(feature_flags_router)
app.include_router(costs_router)
app.include_router(audit_logs_router)
app.include_router(awa_chat_router)
app.include_router(public_chat_router)
app.include_router(knowledge_router)
app.include_router(team_router)
from backend.api.routes.admin import router as admin_router
from backend.api.routes.business_behavior_presets import router as business_behavior_presets_router
from backend.api.routes.prompts import router as prompts_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.subscriptions import router as subscriptions_router
app.include_router(admin_router, prefix="/api/v1")
app.include_router(business_behavior_presets_router, prefix="/api/v1")
app.include_router(prompts_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(subscriptions_router)
from backend.schemas.response import APIResponse
from backend.api.health import router as health_router
app.include_router(health_router, prefix="/api/v1")
# Root redirect
@app.get("/")
async def root():
    data = {
        "service": "AWA API",
        "version": "1.0.0",
        "health": "/api/v1/health"
    }
    if settings.ENV != "production":
        data["docs"] = "/docs"
    return APIResponse.create_success(data={
        **data
    })
