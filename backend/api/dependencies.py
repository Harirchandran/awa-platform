"""
JWT Authentication Dependency for FastAPI routes.
Extracts tenant_id and user_id from JWT claims.
"""
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from uuid import UUID
from asyncpg import PostgresError
from backend.db.client import db_client
from backend.security.auth import verify_jwt
from backend.ops.logger import set_trace_context
security_scheme = HTTPBearer(auto_error=False)
TENANT_READ_ROLES = {"member", "operator", "tenant_admin", "admin", "owner", "super_admin"}
TENANT_OPERATION_ROLES = {"operator", "tenant_admin", "admin", "owner", "super_admin"}
TENANT_WRITE_ROLES = {"tenant_admin", "admin", "owner", "super_admin"}
def _parse_uuid(value: object, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=f"Token has invalid {field_name}") from exc
    raise HTTPException(status_code=401, detail=f"Token missing {field_name}")
async def _check_token_revoked(jti: str) -> None:
    from backend.core.redis_client import redis_client, reset_redis_clients
    key = f"jwt:revoke:{jti}"
    try:
        revoked = await redis_client.exists(key)
    except RuntimeError as exc:
        if "Event loop is closed" not in str(exc):
            raise
        reset_redis_clients()
        revoked = await redis_client.exists(key)
    if revoked:
        raise HTTPException(status_code=401, detail="Token has been revoked")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme)
) -> dict:
    """
    Extract and verify JWT, return user claims.
    Returns dict with 'user_id', 'tenant_id', and 'role'.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    payload = verify_jwt(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    jti = payload.get("jti")
    if jti:
        await _check_token_revoked(jti)

    user_id = payload.get("sub") or payload.get("user_id")
    app_meta = payload.get("app_metadata", {})
    tenant_id = app_meta.get("tenant_id")
    impersonator_id = app_meta.get("impersonator_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="User is not associated with a tenant")
    parsed_user_id = _parse_uuid(user_id, "user_id")
    parsed_tenant_id = _parse_uuid(tenant_id, "tenant_id")
    parsed_impersonator_id = _parse_uuid(impersonator_id, "impersonator_id") if impersonator_id else None
    try:
        db_user = await db_client.get_user(parsed_user_id)
        db_role = await db_client.get_user_tenant_role(parsed_user_id, parsed_tenant_id)
        tenant = await db_client.get_tenant(parsed_tenant_id)
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="Authentication store unavailable") from exc
    if not db_user or not db_role:
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if not tenant:
        raise HTTPException(status_code=403, detail="Tenant is suspended or inactive")
    role = db_role.strip().lower()
    if role not in TENANT_READ_ROLES:
        raise HTTPException(status_code=403, detail="User role is not allowed")
    tenant_status = str((tenant or {}).get("status") or "").lower()
    if role != "super_admin" and tenant_status in {"suspended", "expired", "past_due", "cancelled"}:
        raise HTTPException(status_code=403, detail="Tenant is suspended or inactive")
    # Inject Context into Request State for downstream routing & logging
    request.state.user_id = str(parsed_user_id)
    request.state.tenant_id = str(parsed_tenant_id)
    if parsed_impersonator_id:
        request.state.impersonator_id = str(parsed_impersonator_id)
    # Update trace context logger
    trace_id = getattr(request.state, "trace_id", "unknown")
    set_trace_context(trace_id, tenant_id)
    return {
        "user_id": parsed_impersonator_id if parsed_impersonator_id else parsed_user_id,
        "tenant_id": parsed_tenant_id,
        "role": role,
    }
async def get_tenant_id(user: dict = Depends(get_current_user)) -> UUID:
    """Extract tenant_id only — convenience dependency."""
    return user["tenant_id"]
async def require_super_admin(user: dict = Depends(get_current_user)) -> dict:
    """Strictly enforces a database-verified super_admin role for admin routes."""
    if user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return user
async def require_tenant_admin(user: dict = Depends(get_current_user)) -> dict:
    """Require a database-verified tenant admin/owner role for tenant mutations."""
    if user.get("role") not in TENANT_WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Tenant admin access required")
    return user
async def require_tenant_operator(user: dict = Depends(get_current_user)) -> dict:
    """Require an operator/admin role for customer-conversation operations."""
    if user.get("role") not in TENANT_OPERATION_ROLES:
        raise HTTPException(status_code=403, detail="Tenant operator access required")
    return user
async def get_authenticated_user_only(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme)
) -> dict:
    """
    Verify JWT and return authenticated user ID and email.
    Bypasses tenant membership check.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    payload = verify_jwt(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    jti = payload.get("jti")
    if jti:
        await _check_token_revoked(jti)

    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user_id")
    parsed_user_id = _parse_uuid(user_id, "user_id")
    try:
        db_user = await db_client.get_user(parsed_user_id)
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="Authentication store unavailable") from exc
    if not db_user:
        raise HTTPException(status_code=401, detail="User account not found")
    email = db_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User email not configured")
    return {
        "user_id": parsed_user_id,
        "email": email.strip().lower(),
    }
