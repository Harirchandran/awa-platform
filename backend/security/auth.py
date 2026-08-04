from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
import jwt
from uuid import UUID
import datetime
import secrets
import hashlib
from typing import Tuple
from backend.core.config import settings

security_scheme = HTTPBearer()


def verify_jwt(token: str) -> dict:
    """
    Validates JWT (Supabase or AWA backend-owned) and returns the decoded payload.
    """
    try:
        # Explicit local UI testing bypass. This is intentionally controlled by
        # a named flag instead of ENV alone.
        if settings.allow_mock_auth and token.startswith("mock_token"):
            user_id = "00000000-0000-0000-0000-000000000100"
            tenant_id = "00000000-0000-0000-0000-000000000001"
            role = "tenant_admin"
            if token != "mock_token":
                parts = token.split(":")
                if len(parts) != 4 or parts[0] != "mock_token":
                    raise HTTPException(status_code=401, detail="Invalid mock token")
                tenant_id, user_id, role = parts[1], parts[2], parts[3]
                try:
                    UUID(tenant_id)
                    UUID(user_id)
                except ValueError:
                    raise HTTPException(status_code=401, detail="Invalid mock token")
                allowed_mock_roles = {"member", "operator", "tenant_admin", "admin", "owner", "super_admin"}
                if role not in allowed_mock_roles:
                    raise HTTPException(status_code=401, detail="Invalid mock token")
                if role == "super_admin" and not settings.ENABLE_MOCK_SUPER_ADMIN:
                    raise HTTPException(status_code=403, detail="Mock super admin is disabled")
            return {
                "sub": user_id,
                "app_metadata": {
                    "tenant_id": tenant_id,
                    "role": role
                },
                "awa_mock": True,
            }
        secret = settings.effective_jwt_secret
        if not secret:
            raise HTTPException(status_code=500, detail="Authentication is not configured")
        if settings.effective_auth_mode == "postgres":
            decode_kwargs = {
                "algorithms": [settings.JWT_ALGORITHM],
                "options": {
                    "verify_exp": True,
                    "verify_aud": False,  # Local JWT does not strictly require audience check
                },
            }
        else:
            decode_kwargs = {
                "algorithms": ["HS256"],
                "options": {
                    "verify_exp": True,  # M-1: Explicit to prevent accidental disabling
                    "verify_aud": bool(settings.SUPABASE_JWT_AUDIENCE),
                },
            }
            if settings.SUPABASE_JWT_AUDIENCE:
                decode_kwargs["audience"] = settings.SUPABASE_JWT_AUDIENCE
            if settings.SUPABASE_JWT_ISSUER:
                decode_kwargs["issuer"] = settings.SUPABASE_JWT_ISSUER
        # Decode and Verify Signature
        payload = jwt.decode(
            token,
            secret,
            **decode_kwargs,
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def sign_access_token(user_id: UUID, email: str, tenant_id: UUID, role: str) -> str:
    """
    Signs a backend-owned JWT Access Token with claims compatible with existing dependencies.
    """
    secret = settings.effective_jwt_secret
    if not secret:
        raise ValueError("JWT Secret is not configured")
    now = datetime.datetime.now(datetime.timezone.utc)
    expiry = now + datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "email": email,
        "app_metadata": {
            "tenant_id": str(tenant_id),
            "role": role,
        },
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, secret, algorithm=settings.JWT_ALGORITHM)


def sign_impersonation_token(
    user_id: UUID,
    email: str,
    tenant_id: UUID,
    role: str,
    impersonator_id: UUID,
) -> str:
    """
    Signs a short-lived impersonation JWT on behalf of a super admin.
    Identical to sign_access_token but embeds 'impersonator_id' inside
    app_metadata so every downstream API call is auditable.
    The token expires in ACCESS_TOKEN_EXPIRE_MINUTES (same as a normal token).
    No refresh token is issued -- impersonation sessions are ephemeral.
    """
    secret = settings.effective_jwt_secret
    if not secret:
        raise ValueError("JWT Secret is not configured")
    now = datetime.datetime.now(datetime.timezone.utc)
    expiry = now + datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "email": email,
        "app_metadata": {
            "tenant_id": str(tenant_id),
            "role": role,
            "impersonator_id": str(impersonator_id),
        },
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, secret, algorithm=settings.JWT_ALGORITHM)


def generate_refresh_token() -> str:
    """
    Generates a secure random refresh token.
    """
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """
    Hashes a refresh token using SHA-256 for secure DB storage.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
