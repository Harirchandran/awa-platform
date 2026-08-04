"""Self-service registration endpoint.

POST /api/v1/auth/register creates the Supabase Auth identity, tenant,
tenant admin user, and default config.
"""
import json
import re
from uuid import uuid4
import datetime
from asyncpg import UniqueViolationError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from backend.api.rate_limiter import limiter
from backend.core.config import settings
from backend.core.context import Tenant
from backend.core.redis_client import get_redis_client
from backend.billing.usage_guard import usage_guard
from backend.db.client import db_client
from backend.ops.logger import logger
from backend.schemas.config import build_default_tenant_config
from backend.schemas.response import APIResponse
from backend.security.supabase_admin import create_supabase_auth_user, delete_supabase_auth_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    business_type: str = Field(default="general", max_length=40, pattern=r"^[a-z0-9_-]+$")
    plan_code: str | None = Field(default=None, max_length=40, pattern=r"^[a-z0-9_-]+$")

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain at least one letter and one number")
        return value


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}"


async def _plan_compatible_default_modules(
    *,
    tenant_id,
    business_name: str,
    plan_code: str,
    inherited_modules: list[str] | None,
) -> list[str] | None:
    """Keep business-type defaults within the tenant's authoritative plan policy."""
    if inherited_modules is None:
        return None
    policy = await usage_guard.get_policy_async(Tenant(
        id=tenant_id,
        name=business_name,
        plan_code=plan_code,
        status="trial",
    ))
    allowed = set(policy.allowed_modules)
    if "*" in allowed:
        return inherited_modules
    return [module for module in inherited_modules if module in allowed]


def _assert_tenant_login_allowed(role: str, tenant_status: str | None) -> None:
    normalized_role = (role or "").strip().lower()
    normalized_status = (tenant_status or "").strip().lower()
    if normalized_role != "super_admin" and normalized_status in {"suspended", "expired", "past_due", "cancelled"}:
        raise HTTPException(status_code=403, detail="Tenant is suspended or inactive")


def _slugify_business_name(value: str) -> str:
    slug = value.strip().lower().replace(" ", "_").replace("-", "_")
    slug = "".join(c for c in slug if c.isalnum() or c == "_").strip("_")
    return slug[:48] or "business"


async def _resolve_unique_slug(conn, base_slug: str) -> str:
    slug = base_slug
    # Fast path check: if the base slug doesn't exist, use it immediately
    if not await conn.fetchval("SELECT 1 FROM tenants WHERE slug = $1", slug):
        return slug
    # Fetch all active suffixes matching the prefix
    pattern = f"{base_slug[:40]}_%"
    rows = await conn.fetch("SELECT slug FROM tenants WHERE slug LIKE $1", pattern)
    existing_suffixes = set()
    prefix_len = len(base_slug[:40]) + 1
    for r in rows:
        s = r["slug"]
        suffix_part = s[prefix_len:]
        if suffix_part.isdigit():
            existing_suffixes.add(int(suffix_part))

    # Find the first available integer suffix
    suffix = 1
    while suffix in existing_suffixes:
        suffix += 1
    return f"{base_slug[:40]}_{suffix}"


@router.post("/register", response_model=APIResponse)
@limiter.limit("5/minute")
async def register_business(request: Request, req: RegisterRequest):
    """
    Create a tenant admin account and the tenant bootstrap records.
    Mock auth mode returns a dev-only token for local UI flows. Supabase mode
    creates a real Auth user and requires the user to sign in after registration.
    """
    email = str(req.email).strip().lower()
    business_name = req.business_name.strip()
    if not business_name or not email or not req.password.strip():
        raise HTTPException(status_code=400, detail="All fields are required")
    requested_plan_code = (req.plan_code or "").strip().lower()
    if requested_plan_code and not settings.allow_mock_auth:
        raise HTTPException(status_code=400, detail="plan_code is only supported for local mock registration")
    plan_code = requested_plan_code or "trial"
    slug_base = _slugify_business_name(business_name)
    tenant_id = uuid4()
    user_id = uuid4()
    auth_user_created = False
    try:
        async with db_client.acquire() as conn:
            existing_user = await conn.fetchval(
                "SELECT 1 FROM users WHERE lower(email) = lower($1) AND deleted_at IS NULL",
                email,
            )
            if existing_user:
                # H-1: Return generic success to prevent email enumeration.
                # The caller cannot distinguish between "created" and "already exists."
                logger.warning(f"REGISTER: Duplicate email attempt for {_mask_email(email)}")
                return APIResponse.create_success(data={
                    "message": "Registration successful. Please check your email to continue.",
                    "requires_login": True,
                })
        password_hash = None
        if settings.effective_auth_mode == "postgres":
            from backend.security.hashing import hash_password
            password_hash = await hash_password(req.password)
        if settings.effective_auth_mode == "supabase":
            user_id = await create_supabase_auth_user(
                email=email,
                password=req.password,
                tenant_id=tenant_id,
                business_name=business_name,
            )
            auth_user_created = True
        async with db_client.acquire() as conn:
            async with conn.transaction():
                slug = await _resolve_unique_slug(conn, slug_base)
                await conn.execute(
                    """
                    INSERT INTO tenants (id, name, slug, plan_code, status, trial_ends_at)
                    VALUES ($1, $2, $3, $4, 'trial', NOW() + INTERVAL '7 days')
                    """,
                    tenant_id,
                    business_name,
                    slug,
                    plan_code,
                )
                await conn.execute(
                    """
                    INSERT INTO users (id, phone_number, email, name)
                    VALUES ($1, $2, $3, $4)
                    """,
                    user_id,
                    f"auth:{user_id}",
                    email,
                    business_name,
                )
                await conn.execute(
                    """
                    INSERT INTO user_tenants (id, user_id, tenant_id, role)
                    VALUES ($1, $2, $3, 'tenant_admin')
                    """,
                    uuid4(),
                    user_id,
                    tenant_id,
                )
                if password_hash:
                    await conn.execute(
                        """
                        INSERT INTO user_credentials (user_id, password_hash, password_algorithm)
                        VALUES ($1, $2, 'bcrypt')
                        """,
                        user_id,
                        password_hash,
                    )
                enabled_modules = None
                if req.business_type and req.business_type not in ("other", "general"):
                    bt_row = await conn.fetchrow(
                        "SELECT enabled_modules FROM business_types WHERE code = $1",
                        req.business_type
                    )
                    if bt_row:
                        enabled_modules_raw = bt_row["enabled_modules"]
                        if isinstance(enabled_modules_raw, str):
                            try:
                                enabled_modules = json.loads(enabled_modules_raw)
                            except Exception:
                                enabled_modules = None
                        elif isinstance(enabled_modules_raw, list):
                            enabled_modules = enabled_modules_raw
                enabled_modules = await _plan_compatible_default_modules(
                    tenant_id=tenant_id,
                    business_name=business_name,
                    plan_code=plan_code,
                    inherited_modules=enabled_modules,
                )
                default_config = build_default_tenant_config(
                    business_name=business_name,
                    business_type=req.business_type,
                    enabled_modules=enabled_modules,
                )
                await conn.execute(
                    """
                    INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active)
                    VALUES ($1, $2, 1, $3::jsonb, true)
                    """,
                    uuid4(),
                    tenant_id,
                    json.dumps(default_config),
                )
                await conn.execute(
                    """
                    INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource_type, details, created_at)
                    VALUES ($1, $2, $3, 'self_service_registration', 'tenant', $4::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    user_id,
                    json.dumps({"email_domain": email.split("@")[-1], "business_type": req.business_type}),
                )
        logger.info(f"REGISTER: New business '{business_name}' | tenant={tenant_id} user={user_id}")
        access_token = None
        refresh_token = None
        if settings.effective_auth_mode == "postgres":
            from backend.security.auth import sign_access_token, generate_refresh_token, hash_refresh_token
            access_token = sign_access_token(user_id, email, tenant_id, "tenant_admin")
            refresh_token = generate_refresh_token()
            rt_hash = hash_refresh_token(refresh_token)
            expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
            async with db_client.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO user_sessions (user_id, refresh_token_hash, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    user_id,
                    rt_hash,
                    expires_at,
                )
        data = {
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "email": email,
            "business_name": business_name,
            "slug": slug,
            "message": "Registration successful",
            "requires_login": settings.effective_auth_mode == "supabase",
        }
        if settings.effective_auth_mode == "postgres":
            data.update({
                "access_token": access_token,
                "refresh_token": refresh_token,
                "requires_login": False,
            })
        if settings.allow_mock_auth:
            data["dev_session_token"] = f"mock_token:{tenant_id}:{user_id}:tenant_admin"
        return APIResponse.create_success(data=data)
    except HTTPException:
        if auth_user_created:
            await delete_supabase_auth_user(user_id)
        raise
    except UniqueViolationError:
        if auth_user_created:
            await delete_supabase_auth_user(user_id)
        raise HTTPException(status_code=409, detail="A business with that name or email already exists")
    except Exception as exc:
        if auth_user_created:
            await delete_supabase_auth_user(user_id)
        logger.error(f"REGISTER: Failed for {_mask_email(email)}: {exc}")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login", response_model=APIResponse)
@limiter.limit("5/minute")
async def login(request: Request, req: LoginRequest):
    """
    Log in using backend-owned email/password and active PostgreSQL storage.
    """
    if settings.effective_auth_mode != "postgres":
        raise HTTPException(status_code=400, detail="Custom credentials login is not enabled in this mode")
    email = str(req.email).strip().lower()
    # Rate limit by email using Redis
    import hashlib
    email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()
    redis_key = f"rate_limit:login:email:{email_hash}"
    redis_client = get_redis_client(socket_timeout=2)
    try:
        attempts = await redis_client.incr(redis_key)
        if attempts == 1:
            await redis_client.expire(redis_key, 60)
        if attempts > 5:
            logger.warning(f"LOGIN: Rate limit exceeded for email {_mask_email(email)}")
            raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"LOGIN: Redis rate limiting error: {exc}")
    from backend.security.hashing import verify_password
    dummy_hash = "$2b$12$LpyP0o1V6Bbe9H/qW1d3f.x1Vl5/cPyzYd86e9e4f5g6h7i8j9k0l"
    try:
        # Fetch user
        async with db_client.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT id, name FROM users WHERE lower(email) = lower($1) AND deleted_at IS NULL",
                email,
            )
        if not user_row:
            # Timing-safe dummy validation
            await verify_password(req.password, dummy_hash)
            logger.warning(f"LOGIN: Failed login attempt (user not found) for {_mask_email(email)}")
            raise HTTPException(status_code=401, detail="Invalid email or password")
        user_id = user_row["id"]
        # Fetch credentials
        async with db_client.acquire() as conn:
            cred_row = await conn.fetchrow(
                "SELECT password_hash FROM user_credentials WHERE user_id = $1",
                user_id,
            )
        if not cred_row or not await verify_password(req.password, cred_row["password_hash"]):
            logger.warning(f"LOGIN: Incorrect password attempt for {_mask_email(email)}")
            raise HTTPException(status_code=401, detail="Invalid email or password")
        # Fetch tenant membership and tenant lifecycle status
        async with db_client.acquire() as conn:
            tenant_row = await conn.fetchrow(
                """
                SELECT ut.tenant_id, ut.role, t.status AS tenant_status
                FROM user_tenants ut
                JOIN tenants t ON t.id = ut.tenant_id
                WHERE ut.user_id = $1 AND t.deleted_at IS NULL
                LIMIT 1
                """,
                user_id,
            )
        if not tenant_row:
            raise HTTPException(status_code=403, detail="User is not associated with any tenant")
        tenant_id = tenant_row["tenant_id"]
        role = tenant_row["role"]
        _assert_tenant_login_allowed(role, tenant_row.get("tenant_status"))
        # Update last_login_at
        async with db_client.acquire() as conn:
            await conn.execute(
                "UPDATE user_credentials SET last_login_at = NOW(), updated_at = NOW() WHERE user_id = $1",
                user_id,
            )
        # Sign access token and generate refresh token
        from backend.security.auth import sign_access_token, generate_refresh_token, hash_refresh_token
        access_token = sign_access_token(user_id, email, tenant_id, role)
        refresh_token = generate_refresh_token()
        rt_hash = hash_refresh_token(refresh_token)
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        async with db_client.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_sessions (user_id, refresh_token_hash, expires_at)
                VALUES ($1, $2, $3)
                """,
                user_id,
                rt_hash,
                expires_at,
            )
        # Clear rate limit on successful login
        try:
            await redis_client.delete(redis_key)
        except Exception as exc:
            logger.error(f"LOGIN: Redis delete rate limit error: {exc}")
        # Audit super_admin logins — best-effort, must not block the response
        if role == "super_admin":
            try:
                client_ip = request.client.host if request.client else "unknown"
                await db_client.write_audit_log(
                    tenant_id=tenant_id,
                    actor_id=user_id,
                    action="super_admin_login",
                    resource_type="system",
                    resource_id=None,
                    details={"ip_address": client_ip, "email": _mask_email(email)},
                )
            except Exception as audit_exc:
                logger.warning(f"LOGIN: Failed to write super_admin_login audit log: {audit_exc}")
        logger.info(f"LOGIN: Successful login for {_mask_email(email)} | user={user_id} tenant={tenant_id}")
        return APIResponse.create_success(data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": role,
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"LOGIN: Unexpected failure for {_mask_email(email)}: {exc}")
        raise HTTPException(status_code=500, detail="Authentication failed. Please try again.")


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=APIResponse)
async def refresh_session(req: RefreshRequest):
    """
    Refresh access token using a valid session refresh token.
    """
    if settings.effective_auth_mode != "postgres":
        raise HTTPException(status_code=400, detail="Custom credentials refresh is not enabled in this mode")
    from backend.security.auth import hash_refresh_token, sign_access_token, generate_refresh_token
    rt_hash = hash_refresh_token(req.refresh_token)
    try:
        async with db_client.acquire() as conn:
            async with conn.transaction():
                # Atomically revoke old token and fetch session details
                session_row = await conn.fetchrow(
                    """
                    UPDATE user_sessions
                    SET revoked_at = NOW(), updated_at = NOW()
                    WHERE refresh_token_hash = $1 AND revoked_at IS NULL
                    RETURNING id, user_id, expires_at
                    """,
                    rt_hash,
                )
                if not session_row:
                    raise HTTPException(status_code=401, detail="Invalid refresh token or session has been revoked")

                if session_row["expires_at"] < datetime.datetime.now(datetime.timezone.utc):
                    raise HTTPException(status_code=401, detail="Session has expired")

                user_id = session_row["user_id"]
                # Fetch user details
                user_row = await conn.fetchrow(
                    "SELECT email FROM users WHERE id = $1 AND deleted_at IS NULL",
                    user_id,
                )
                if not user_row:
                    raise HTTPException(status_code=401, detail="User no longer exists")

                # Fetch tenant membership and tenant lifecycle status
                tenant_row = await conn.fetchrow(
                    """
                    SELECT ut.tenant_id, ut.role, t.status AS tenant_status
                    FROM user_tenants ut
                    JOIN tenants t ON t.id = ut.tenant_id
                    WHERE ut.user_id = $1 AND t.deleted_at IS NULL
                    LIMIT 1
                    """,
                    user_id,
                )
                if not tenant_row:
                    raise HTTPException(status_code=401, detail="User is not associated with a tenant")

                tenant_id = tenant_row["tenant_id"]
                role = tenant_row["role"]
                email = user_row["email"]
                _assert_tenant_login_allowed(role, tenant_row.get("tenant_status"))

                # Rotate tokens
                new_refresh_token = generate_refresh_token()
                new_rt_hash = hash_refresh_token(new_refresh_token)
                new_expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

                # Save new token
                await conn.execute(
                    """
                    INSERT INTO user_sessions (user_id, refresh_token_hash, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    user_id,
                    new_rt_hash,
                    new_expires_at,
                )
        access_token = sign_access_token(user_id, email, tenant_id, role)
        return APIResponse.create_success(data={
            "access_token": access_token,
            "refresh_token": new_refresh_token,
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"REFRESH: Failed to rotate refresh token: {exc}")
        raise HTTPException(status_code=500, detail="Token refresh failed")


class LogoutRequest(BaseModel):
    refresh_token: str


@router.post("/logout", response_model=APIResponse)
async def logout_session(request: Request, req: LogoutRequest):
    """
    Logout session by revoking the refresh token and blocklisting the access token.
    """
    if settings.effective_auth_mode != "postgres":
        raise HTTPException(status_code=400, detail="Custom credentials logout is not enabled in this mode")
    from backend.security.auth import hash_refresh_token, verify_jwt

    # Revoke access token if present in headers
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        access_token = auth_header.split(" ")[1]
        try:
            payload = verify_jwt(access_token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
                time_remaining = int(exp - now_ts)
                if time_remaining > 0:
                    redis_client = get_redis_client(socket_timeout=2)
                    await redis_client.setex(f"jwt:revoke:{jti}", time_remaining, "revoked")
                    logger.info(f"LOGOUT: Access token {jti} blocklisted for {time_remaining}s")
        except HTTPException as e:
            logger.info(f"LOGOUT: Access token invalid or expired during logout check: {e.detail}")
        except Exception as e:
            logger.error(f"LOGOUT: Error blocklisting access token: {e}")

    rt_hash = hash_refresh_token(req.refresh_token)
    try:
        async with db_client.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_sessions
                SET revoked_at = NOW(), updated_at = NOW()
                WHERE refresh_token_hash = $1 AND revoked_at IS NULL
                """,
                rt_hash,
            )
        return APIResponse.create_success(data={"message": "Logged out successfully"})
    except Exception as exc:
        logger.error(f"LOGOUT: Failed to revoke token: {exc}")
        raise HTTPException(status_code=500, detail="Logout failed")


class MockLoginRequest(BaseModel):
    email: str


@router.post("/mock-login", response_model=APIResponse)
async def mock_login(req: MockLoginRequest):
    """
    Generate a dev-only session token matching the user's registered tenant context.
    """
    if settings.ENV == "production":
        raise HTTPException(status_code=403, detail="Mock authentication is not available in production")
    if not settings.allow_mock_auth:
        raise HTTPException(status_code=400, detail="Mock authentication is not enabled")

    email = str(req.email).strip().lower()

    # Handle mock super admin
    if email == "admin@docker.local":
        if not settings.ENABLE_MOCK_SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="Mock super admin is disabled")
        token = "mock_token:00000000-0000-0000-0000-000000000001:00000000-0000-0000-0000-000000000099:super_admin"
        return APIResponse.create_success(data={"access_token": token, "role": "super_admin"})

    # Look up user in database
    try:
        async with db_client.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT u.id as user_id, ut.tenant_id, ut.role
                FROM users u
                JOIN user_tenants ut ON u.id = ut.user_id
                WHERE lower(u.email) = $1 AND u.deleted_at IS NULL
                LIMIT 1
                """,
                email,
            )
    except Exception as exc:
        logger.warning(f"Mock login database check failed ({exc}). Falling back to seed tenant token.")
        row = None

    if not row:
        # Resilient fallback to the seeded tenant to avoid breaking local tests
        token = "mock_token:00000000-0000-0000-0000-000000000001:00000000-0000-0000-0000-000000000100:tenant_admin"
        return APIResponse.create_success(data={"access_token": token, "role": "tenant_admin"})

    token = f"mock_token:{row['tenant_id']}:{row['user_id']}:{row['role']}"
    return APIResponse.create_success(data={"access_token": token, "role": row["role"]})
