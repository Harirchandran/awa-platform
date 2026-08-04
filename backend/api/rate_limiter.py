"""
Rate Limiter — Production Security
Configurable rate limiting using slowapi to prevent webhook abuse.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse
from backend.core.config import settings
# Rate limit from config (default: 10 req/sec)
RATE_LIMIT = getattr(settings, "RATE_LIMIT_PER_SECOND", 10)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{RATE_LIMIT}/second"],
    storage_uri=getattr(settings, "REDIS_URL", "memory://"),
)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Custom handler for rate limit exceeded errors."""
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Too many requests. Please try again later.",
                "retry_after": exc.detail
            }
        }
    )