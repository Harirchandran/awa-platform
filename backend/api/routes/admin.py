"""
Super Admin API Routes.
Enforces `require_super_admin` dependency for all endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.api.pagination import decode_cursor
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4
from datetime import datetime, timezone
import json
import re
from backend.api.dependencies import require_super_admin
from backend.core.config import settings
from backend.core.context import Tenant
from backend.core.ai_modes import normalize_ai_mode
from backend.db.client import db_client
from backend.billing.plans import canonical_plan_name, canonicalize_plan_rows
from backend.billing.usage_guard import normalize_plan_code, usage_guard, validate_plan_limits
from backend.schemas.response import APIResponse
from backend.services.tool_controls import (
    apply_tool_control_update,
    build_tool_control_state,
    normalize_tool_definitions,
)
from backend.schemas.business_type import (
    BusinessTypeCreateRequest,
    BusinessTypeUpdateRequest,
    BusinessTypeResponse,
)
from backend.schemas.config import build_default_tenant_config
from backend.security.supabase_admin import create_supabase_auth_user, delete_supabase_auth_user
router = APIRouter(prefix="/admin", tags=["Super Admin"])
class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: Optional[str] = None
    status: str = "active"
    plan_code: str = "trial"
    business_type: str = Field(default="general", max_length=40, pattern=r"^[a-z0-9_-]+$")
    tenant_admin_email: Optional[EmailStr] = None
    tenant_admin_password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    tenant_admin_name: Optional[str] = Field(default=None, max_length=120)
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_phone_number: Optional[str] = None
    whatsapp_display_name: Optional[str] = None
    @field_validator("tenant_admin_password")
    @classmethod
    def validate_admin_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain at least one letter and one number")
        return value
class TenantToolControlRequest(BaseModel):
    enabled_modules: List[str]
    business_type: Optional[str] = None
    reason: str = "super_admin_tool_control_update"
def _slugify_tenant_name(value: str) -> str:
    slug = value.strip().lower().replace(" ", "_").replace("-", "_")
    slug = "".join(char for char in slug if char.isalnum() or char == "_").strip("_")
    return slug[:48] or "business"
async def _resolve_unique_tenant_slug(conn, requested_slug: Optional[str], business_name: str) -> str:
    base_slug = _slugify_tenant_name(requested_slug or business_name)
    slug = base_slug
    suffix = 1
    while await conn.fetchval("SELECT 1 FROM tenants WHERE slug = $1 AND deleted_at IS NULL", slug):
        suffix += 1
        slug = f"{base_slug[:40]}_{suffix}"
    return slug
async def _business_type_modules(conn, business_type: str) -> Optional[list[str]]:
    if business_type in {"general", "other"}:
        return None
    row = await conn.fetchrow("SELECT enabled_modules FROM business_types WHERE code = $1", business_type)
    if not row:
        return None
    enabled_modules = row["enabled_modules"]
    if isinstance(enabled_modules, str):
        try:
            parsed = json.loads(enabled_modules)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
    return enabled_modules if isinstance(enabled_modules, list) else None
@router.post("/tenants", response_model=APIResponse)
async def create_tenant(
    req: CreateTenantRequest,
    admin: dict = Depends(require_super_admin)
):
    """
    Super Admin only: Create a new tenant.
    """
    business_name = req.name.strip()
    tenant_admin_email = str(req.tenant_admin_email).strip().lower() if req.tenant_admin_email else None
    tenant_admin_password = req.tenant_admin_password.strip() if req.tenant_admin_password else None
    if tenant_admin_email and not tenant_admin_password:
        raise HTTPException(status_code=400, detail="Tenant admin password is required when tenant admin email is provided")
    if tenant_admin_password and not tenant_admin_email:
        raise HTTPException(status_code=400, detail="Tenant admin email is required when tenant admin password is provided")
    plan_code = normalize_plan_code(req.plan_code)
    tenant_id = uuid4()
    tenant_admin_user_id: Optional[UUID] = uuid4() if tenant_admin_email else None
    auth_user_created = False
    if tenant_admin_email and settings.effective_auth_mode == "supabase":
        tenant_admin_user_id = await create_supabase_auth_user(
            email=tenant_admin_email,
            password=tenant_admin_password or "",
            tenant_id=tenant_id,
            business_name=business_name,
        )
        auth_user_created = True
    password_hash = None
    if tenant_admin_email and tenant_admin_password and settings.effective_auth_mode == "postgres":
        from backend.security.hashing import hash_password
        password_hash = await hash_password(tenant_admin_password)
    try:
        async with db_client.acquire() as conn:
            async with conn.transaction():
                slug = await _resolve_unique_tenant_slug(conn, req.slug, business_name)
                if tenant_admin_email:
                    existing_user = await conn.fetchval(
                        "SELECT 1 FROM users WHERE lower(email) = lower($1) AND deleted_at IS NULL",
                        tenant_admin_email,
                    )
                    if existing_user:
                        raise HTTPException(status_code=409, detail="A user with that tenant admin email already exists")
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenants (id, name, slug, status, plan_code)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id, name, slug, status, plan_code, created_at;
                    """,
                    tenant_id,
                    business_name,
                    slug,
                    req.status,
                    plan_code,
                )
                if not row:
                    raise HTTPException(status_code=500, detail="Failed to create tenant")
                tenant_dict = dict(row)
                enabled_modules = await _business_type_modules(conn, req.business_type)
                default_config = build_default_tenant_config(
                    business_name=business_name,
                    business_type=req.business_type,
                    enabled_modules=enabled_modules,
                )
                await conn.execute(
                    """
                    INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active)
                    VALUES ($1, $2, 1, $3::jsonb, TRUE)
                    """,
                    uuid4(),
                    tenant_id,
                    json.dumps(default_config),
                )
                if tenant_admin_email and tenant_admin_user_id:
                    await conn.execute(
                        """
                        INSERT INTO users (id, phone_number, email, name)
                        VALUES ($1, $2, $3, $4)
                        """,
                        tenant_admin_user_id,
                        f"auth:{tenant_admin_user_id}",
                        tenant_admin_email,
                        req.tenant_admin_name or business_name,
                    )
                    await conn.execute(
                        """
                        INSERT INTO user_tenants (id, user_id, tenant_id, role)
                        VALUES ($1, $2, $3, 'tenant_admin')
                        """,
                        uuid4(),
                        tenant_admin_user_id,
                        tenant_id,
                    )
                    if password_hash:
                        await conn.execute(
                            """
                            INSERT INTO user_credentials (user_id, password_hash, password_algorithm)
                            VALUES ($1, $2, 'bcrypt')
                            """,
                            tenant_admin_user_id,
                            password_hash,
                        )
                # Auto-link WhatsApp account if provided via Super Admin Wizard
                if req.whatsapp_phone_number_id and req.whatsapp_phone_number:
                    await conn.execute(
                        """
                        INSERT INTO whatsapp_accounts (id, tenant_id, phone_number, phone_number_id, display_name, verification_status, created_at)
                        VALUES ($1, $2, $3, $4, $5, 'pending', NOW())
                        """,
                        uuid4(), tenant_dict["id"], req.whatsapp_phone_number, req.whatsapp_phone_number_id, req.whatsapp_display_name
                    )
                await conn.execute(
                    """
                    INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource_type, resource_id, details, created_at)
                    VALUES ($1, $2, $3, 'super_admin_tenant_created', 'tenant', $4, $5::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    admin["user_id"],
                    tenant_id,
                    json.dumps({
                        "plan_code": req.plan_code,
                        "business_type": req.business_type,
                        "tenant_admin_email": tenant_admin_email,
                        "tenant_admin_created": bool(tenant_admin_email),
                    }),
                )
        admin_access = {
            "status": "created" if tenant_admin_email else "not_configured",
            "email": tenant_admin_email,
            "user_id": str(tenant_admin_user_id) if tenant_admin_user_id else None,
            "role": "tenant_admin" if tenant_admin_email else None,
            "requires_login": settings.effective_auth_mode == "supabase",
            "message": (
                "Tenant admin account created. Ask the admin to sign in with the configured email and password."
                if tenant_admin_email
                else "No tenant admin account was configured for this business."
            ),
        }
        if tenant_admin_email and settings.effective_auth_mode == "mock" and settings.allow_mock_auth and tenant_admin_user_id:
            admin_access["dev_session_token"] = f"mock_token:{tenant_id}:{tenant_admin_user_id}:tenant_admin"
        return APIResponse.create_success(data={
            **tenant_dict,
            "business_type": req.business_type,
            "admin_access": admin_access,
            "next_steps": [
                "Tenant admin can sign in and complete configuration.",
                "Super admin can enable tools from Admin Tool Controls.",
            ],
        })
    except HTTPException:
        if auth_user_created and tenant_admin_user_id:
            await delete_supabase_auth_user(tenant_admin_user_id)
        raise
    except Exception:
        if auth_user_created and tenant_admin_user_id:
            await delete_supabase_auth_user(tenant_admin_user_id)
        raise
@router.get("/tenants", response_model=APIResponse)
async def list_all_tenants(admin: dict = Depends(require_super_admin)):
    """
    Super Admin only: List all tenants globally across the platform.
    """
    tenants = await db_client.get_all_tenants()
    for tenant in tenants:
        if isinstance(tenant, dict) and "ai_mode" in tenant:
            tenant["ai_mode"] = normalize_ai_mode(tenant.get("ai_mode"))
    return APIResponse.create_success(data=tenants)
async def _get_tenant_tool_policy(tenant_record: dict) -> tuple[dict, list[str]]:
    tenant = Tenant(
        id=tenant_record["id"],
        name=tenant_record["name"],
        plan_code=tenant_record.get("plan_code") or "trial",
        status=tenant_record.get("status") or "trial",
        ai_mode=tenant_record.get("ai_mode"),
        trial_ends_at=tenant_record.get("trial_ends_at"),
        current_period_end=tenant_record.get("current_period_end"),
    )
    policy = await usage_guard.get_policy_async(tenant)
    return {
        "plan_code": policy.plan_code,
        "allowed_ai_modes": list(policy.allowed_ai_modes),
        "allowed_modules": list(policy.allowed_modules),
    }, list(policy.allowed_modules)
async def _get_tool_catalog_or_none() -> Optional[list[dict[str, Any]]]:
    try:
        tools = await db_client.get_tool_catalog()
    except (AttributeError, TypeError):
        return None
    return tools if isinstance(tools, list) and tools else None
async def _validate_enabled_modules(enabled_modules: list[str]) -> None:
    tool_catalog = await _get_tool_catalog_or_none()
    known_tool_keys = {tool["key"] for tool in normalize_tool_definitions(tool_catalog)}
    invalid = sorted(set(enabled_modules) - known_tool_keys)
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid modules: {', '.join(invalid)}")
@router.get("/tool-controls", response_model=APIResponse)
async def list_tool_control_tenants(admin: dict = Depends(require_super_admin)):
    """Super Admin: summary of tenant tool enablement and available presets."""
    tenants = await db_client.get_all_tenants()
    # Fetch dynamic presets from DB
    db_presets = await db_client.get_all_business_types()
    tool_catalog = await _get_tool_catalog_or_none()
    presets_dict = {
        p["code"]: {
            "label": p["label"],
            "business_type": p["code"],
            "enabled_modules": p["enabled_modules"],
            "note": p["note"]
        }
        for p in db_presets
    }
    rows = []
    for tenant in tenants:
        config = await db_client.get_tenant_config(tenant["id"])
        config_json = config.get("config_json", {}) if config else {}
        if isinstance(config_json, str):
            config_json = json.loads(config_json)
        policy, allowed_modules = await _get_tenant_tool_policy(tenant)
        state = build_tool_control_state(
            config_json,
            allowed_modules,
            presets=presets_dict,
            tool_definitions=tool_catalog,
        )
        rows.append({
            **tenant,
            "config_version": config.get("version") if config else None,
            "business_type": state["business_type"],
            "active_modules": state["active_modules"],
            "plan_policy": policy,
        })
    return APIResponse.create_success(data={
        "tenants": rows,
        "presets": presets_dict,
    })
@router.get("/tenants/{tenant_id}/tool-controls", response_model=APIResponse)
async def get_tenant_tool_controls(
    tenant_id: UUID,
    admin: dict = Depends(require_super_admin),
):
    """Super Admin: read tool controls for one tenant."""
    tenant = await db_client.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(tenant_id)
    config_json = config.get("config_json", {}) if config else {}
    if isinstance(config_json, str):
        config_json = json.loads(config_json)
    policy, allowed_modules = await _get_tenant_tool_policy(tenant)
    # Fetch dynamic presets from DB
    db_presets = await db_client.get_all_business_types()
    tool_catalog = await _get_tool_catalog_or_none()
    presets_dict = {
        p["code"]: {
            "label": p["label"],
            "business_type": p["code"],
            "enabled_modules": p["enabled_modules"],
            "note": p["note"]
        }
        for p in db_presets
    }
    state = build_tool_control_state(
        config_json,
        allowed_modules,
        presets=presets_dict,
        tool_definitions=tool_catalog,
    )
    return APIResponse.create_success(data={
        "tenant": tenant,
        "config_version": config.get("version") if config else None,
        "plan_policy": policy,
        **state,
    })
@router.put("/tenants/{tenant_id}/tool-controls", response_model=APIResponse)
async def update_tenant_tool_controls(
    tenant_id: UUID,
    req: TenantToolControlRequest,
    admin: dict = Depends(require_super_admin),
):
    """Super Admin: update enabled workflow tools for one tenant."""
    tenant = await db_client.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(tenant_id)
    config_json = config.get("config_json", {}) if config else {
        "business_name": tenant["name"],
        "business_type": req.business_type or "business",
        "modules": {},
    }
    if isinstance(config_json, str):
        config_json = json.loads(config_json)
    policy, allowed_modules = await _get_tenant_tool_policy(tenant)
    tool_catalog = await _get_tool_catalog_or_none()
    try:
        updated_config = apply_tool_control_update(
            config_json,
            enabled_modules=req.enabled_modules,
            allowed_modules=allowed_modules,
            business_type=req.business_type,
            tool_definitions=tool_catalog,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    result = await db_client.create_config_version(tenant_id, updated_config)
    new_config_id = result.get("id")
    activated = await db_client.activate_config_version(tenant_id, new_config_id)
    if not activated:
        raise HTTPException(status_code=500, detail="Failed to activate tool configuration")
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=admin["user_id"],
        action="tenant_tool_controls_updated",
        resource_type="tenant_config",
        resource_id=new_config_id,
        details={
            "reason": req.reason,
            "enabled_modules": req.enabled_modules,
            "business_type": req.business_type,
            "previous_config_version": config.get("version") if config else None,
            "new_config_version": result.get("version"),
            "plan_policy": policy,
        },
    )
    # Fetch dynamic presets from DB
    db_presets = await db_client.get_all_business_types()
    tool_catalog = tool_catalog or await _get_tool_catalog_or_none()
    presets_dict = {
        p["code"]: {
            "label": p["label"],
            "business_type": p["code"],
            "enabled_modules": p["enabled_modules"],
            "note": p["note"]
        }
        for p in db_presets
    }
    state = build_tool_control_state(
        updated_config,
        allowed_modules,
        presets=presets_dict,
        tool_definitions=tool_catalog,
    )
    return APIResponse.create_success(data={
        "tenant": tenant,
        "config_version": result.get("version"),
        "config_id": str(new_config_id),
        "plan_policy": policy,
        **state,
    })
class PlanUpdateRequest(BaseModel):
    name: str
    plan_code: Optional[str] = None
    monthly_price_cents: int = Field(ge=0)
    currency: str
    razorpay_plan_id: str
    is_active: bool
    features_json: List[str] | str = []
    limits_json: Dict[str, Any] | str = {}
class TenantBillingOverrideRequest(BaseModel):
    plan_code: Optional[str] = None
    status: Optional[str] = None
    trial_ends_at: Optional[str] = None
    current_period_end: Optional[str] = None
    razorpay_customer_id: Optional[str] = None
    razorpay_subscription_id: Optional[str] = None
    reason: str = "manual_super_admin_update"
def _model_fields_set(model: BaseModel) -> set[str]:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(model, "__fields_set__", set())
    return set(fields_set)
def _optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
def _coerce_timestamptz(value: Optional[str], field_name: str) -> Optional[datetime]:
    if value is None:
        return None
    try:
        raw = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp for {field_name}")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
def _timestamp_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = _coerce_timestamptz(value, "timestamp")
    else:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(microsecond=0).isoformat()
def _json_text(value: Any, field_name: str) -> str:
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail=f"Invalid JSON for {field_name}")
        return value
    try:
        return json.dumps(value)
    except TypeError:
        raise HTTPException(status_code=400, detail=f"Invalid JSON for {field_name}")
def _required_plan_code(plan_code: Optional[str]) -> str:
    if not plan_code or not plan_code.strip():
        raise HTTPException(status_code=400, detail="Plan code is required")
    return normalize_plan_code(plan_code)
def _get_razorpay_client():
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay credentials are not configured")
    import razorpay
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
def _sync_plan_with_razorpay(req: PlanUpdateRequest) -> dict[str, Any]:
    """
    In test/live billing mode, validate paid Razorpay plan IDs against Razorpay
    and mirror authoritative amount/currency metadata back into AWA.
    """
    if settings.effective_billing_mode == "mock" or req.monthly_price_cents == 0:
        return {
            "monthly_price_cents": req.monthly_price_cents,
            "currency": req.currency,
            "razorpay_sync": {"mode": settings.effective_billing_mode, "synced": False},
        }
    if not req.razorpay_plan_id.startswith("plan_"):
        raise HTTPException(status_code=400, detail="Paid plans in test/live mode must use a real Razorpay plan_id")
    client = _get_razorpay_client()
    try:
        razorpay_plan = client.plan.fetch(req.razorpay_plan_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Razorpay plan sync failed: {e}")
    item = razorpay_plan.get("item") or {}
    amount = int(item.get("amount", req.monthly_price_cents))
    if amount < 0:
        raise HTTPException(status_code=400, detail="Plan price cannot be negative")
    currency = item.get("currency", req.currency)
    return {
        "monthly_price_cents": amount,
        "currency": str(currency).upper(),
        "razorpay_sync": {
            "mode": settings.effective_billing_mode,
            "synced": True,
            "razorpay_plan_status": razorpay_plan.get("status"),
            "razorpay_period": razorpay_plan.get("period"),
            "razorpay_interval": razorpay_plan.get("interval"),
        },
    }
@router.get("/plans", response_model=APIResponse)
async def list_plans(admin: dict = Depends(require_super_admin)):
    """Super Admin: View all billing plans."""
    async with db_client.acquire() as conn:
        rows = await conn.fetch("""
            SELECT *
            FROM subscription_plans
            WHERE plan_code IS NOT NULL
            ORDER BY monthly_price_cents ASC, name ASC
        """)
        plans = canonicalize_plan_rows(rows)
        return APIResponse.create_success(data=plans)
@router.post("/plans", response_model=APIResponse)
async def create_or_update_plan(req: PlanUpdateRequest, admin: dict = Depends(require_super_admin)):
    """Super Admin: Create or sync a Razorpay billing plan to the DB."""
    plan_code = _required_plan_code(req.plan_code)
    try:
        validated_limits = await validate_plan_limits(req.limits_json, plan_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    features_json = _json_text(req.features_json, "features_json")
    limits_json = json.dumps(validated_limits)
    plan_name = canonical_plan_name(plan_code, req.name.strip())
    synced = _sync_plan_with_razorpay(req)
    async with db_client.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO subscription_plans (name, plan_code, monthly_price_cents, currency, razorpay_plan_id, is_active, features_json, limits_json)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            ON CONFLICT (razorpay_plan_id) DO UPDATE
            SET name=EXCLUDED.name, plan_code=EXCLUDED.plan_code, monthly_price_cents=EXCLUDED.monthly_price_cents,
                currency=EXCLUDED.currency, is_active=EXCLUDED.is_active, features_json=EXCLUDED.features_json,
                limits_json=EXCLUDED.limits_json
            RETURNING *;
        """, plan_name, plan_code, synced["monthly_price_cents"], synced["currency"], req.razorpay_plan_id, req.is_active, features_json, limits_json)
        await db_client.write_audit_log(
            tenant_id=admin["tenant_id"],
            actor_id=admin["user_id"],
            action="billing_plan_upserted",
            resource_type="subscription_plan",
            resource_id=row["id"],
            details={
                "plan_code": row["plan_code"],
                "razorpay_plan_id": row["razorpay_plan_id"],
                "monthly_price_cents": row["monthly_price_cents"],
                "is_active": row["is_active"],
                "razorpay_sync": synced["razorpay_sync"],
            },
        )
        return APIResponse.create_success(data=dict(row))
@router.patch("/plans/{plan_id}", response_model=APIResponse)
async def update_plan(
    plan_id: UUID,
    req: PlanUpdateRequest,
    admin: dict = Depends(require_super_admin),
):
    """Super Admin: Update a billing plan by database ID."""
    plan_code = _required_plan_code(req.plan_code)
    try:
        validated_limits = await validate_plan_limits(req.limits_json, plan_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    features_json = _json_text(req.features_json, "features_json")
    limits_json = json.dumps(validated_limits)
    plan_name = canonical_plan_name(plan_code, req.name.strip())
    synced = _sync_plan_with_razorpay(req)
    async with db_client.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE subscription_plans
            SET name = $1,
                plan_code = $2,
                monthly_price_cents = $3,
                currency = $4,
                razorpay_plan_id = $5,
                is_active = $6,
                features_json = $7::jsonb,
                limits_json = $8::jsonb,
                updated_at = NOW()
            WHERE id = $9
            RETURNING *;
            """,
            plan_name,
            plan_code,
            synced["monthly_price_cents"],
            synced["currency"],
            req.razorpay_plan_id,
            req.is_active,
            features_json,
            limits_json,
            plan_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    await db_client.write_audit_log(
        tenant_id=admin["tenant_id"],
        actor_id=admin["user_id"],
        action="billing_plan_updated",
        resource_type="subscription_plan",
        resource_id=plan_id,
        details={
            "plan_code": row["plan_code"],
            "razorpay_plan_id": row["razorpay_plan_id"],
            "monthly_price_cents": row["monthly_price_cents"],
            "is_active": row["is_active"],
            "razorpay_sync": synced["razorpay_sync"],
        },
    )
    return APIResponse.create_success(data=dict(row))
@router.get("/billing/tenants", response_model=APIResponse)
async def list_tenant_billing(admin: dict = Depends(require_super_admin)):
    """Super Admin: Global tenant billing, subscription, and usage overview."""
    async with db_client.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH usage_month AS (
                SELECT tenant_id,
                       COALESCE(SUM(message_count), 0) AS month_message_count,
                       COALESCE(SUM(ai_tokens_used), 0) AS month_ai_tokens_used,
                       COALESCE(SUM(total_credits_used), 0) AS month_credits_used,
                       COALESCE(SUM(tool_action_count), 0) AS month_tool_action_count,
                       COALESCE(SUM(ai_token_cost + compute_cost), 0) AS month_cost
                FROM tenant_cost_daily
                WHERE cost_date >= date_trunc('month', CURRENT_DATE)::date
                GROUP BY tenant_id
            )
            SELECT t.id, t.name, t.slug, t.status, t.plan_code,
                   t.trial_ends_at, t.current_period_end,
                   t.razorpay_customer_id, t.razorpay_subscription_id,
                   t.created_at,
                   sp.name AS plan_name,
                   sp.monthly_price_cents,
                   sp.currency,
                   sp.razorpay_plan_id,
                   sp.limits_json,
                   COALESCE(u.month_message_count, 0) AS month_message_count,
                   COALESCE(u.month_ai_tokens_used, 0) AS month_ai_tokens_used,
                   COALESCE(u.month_credits_used, 0) AS month_credits_used,
                   COALESCE(u.month_tool_action_count, 0) AS month_tool_action_count,
                   COALESCE(u.month_cost, 0) AS month_cost
            FROM tenants t
            LEFT JOIN subscription_plans sp ON lower(sp.plan_code) = lower(t.plan_code)
            LEFT JOIN usage_month u ON u.tenant_id = t.id
            WHERE t.deleted_at IS NULL
            ORDER BY t.created_at DESC;
            """
        )
    tenant_rows = []
    for row in rows:
        tenant = dict(row)
        tenant["plan_code"] = normalize_plan_code(tenant.get("plan_code"))
        tenant["plan_name"] = canonical_plan_name(tenant["plan_code"], tenant.get("plan_name"))
        tenant_rows.append(tenant)
    return APIResponse.create_success(data=tenant_rows)
@router.patch("/billing/tenants/{tenant_id}", response_model=APIResponse)
async def update_tenant_billing(
    tenant_id: UUID,
    req: TenantBillingOverrideRequest,
    admin: dict = Depends(require_super_admin),
):
    """Super Admin: Manually update a tenant's billing state with audit trail."""
    allowed_statuses = {"trial", "active", "suspended", "expired"}
    if req.status and req.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Invalid tenant billing status")
    fields_set = _model_fields_set(req)
    if "plan_code" in fields_set and not _optional_text(req.plan_code):
        raise HTTPException(status_code=400, detail="Plan code is required")
    if "status" in fields_set and not req.status:
        raise HTTPException(status_code=400, detail="Status is required")
    async with db_client.acquire() as conn:
        before = await conn.fetchrow(
            """
            SELECT id, name, slug, status, plan_code, trial_ends_at, current_period_end,
                   razorpay_customer_id, razorpay_subscription_id, updated_at
            FROM tenants
            WHERE id = $1 AND deleted_at IS NULL
            """,
            tenant_id,
        )
        if not before:
            raise HTTPException(status_code=404, detail="Tenant not found")
        before_dict = dict(before)
        changes: dict[str, Any] = {}
        if "plan_code" in fields_set:
            next_plan_code = normalize_plan_code(req.plan_code)
            if next_plan_code != normalize_plan_code(before_dict.get("plan_code")):
                changes["plan_code"] = next_plan_code
        if "status" in fields_set and req.status != before_dict.get("status"):
            changes["status"] = req.status
        for field_name in ("trial_ends_at", "current_period_end"):
            if field_name in fields_set:
                requested = _coerce_timestamptz(getattr(req, field_name), field_name)
                if _timestamp_key(requested) != _timestamp_key(before_dict.get(field_name)):
                    changes[field_name] = requested
        for field_name in ("razorpay_customer_id", "razorpay_subscription_id"):
            if field_name in fields_set:
                requested = _optional_text(getattr(req, field_name))
                if requested != _optional_text(before_dict.get(field_name)):
                    changes[field_name] = requested
        if not changes:
            return APIResponse.create_success(data={
                **before_dict,
                "noop": True,
                "changed_fields": [],
            })
        cast_by_field = {
            "status": "::tenant_status",
            "trial_ends_at": "::timestamptz",
            "current_period_end": "::timestamptz",
        }
        assignments = []
        values = []
        for index, (field_name, value) in enumerate(changes.items(), start=1):
            assignments.append(f"{field_name} = ${index}{cast_by_field.get(field_name, '')}")
            values.append(value)
        tenant_arg_index = len(values) + 1
        row = await conn.fetchrow(
            f"""
            UPDATE tenants
            SET {', '.join(assignments)},
                updated_at = NOW()
            WHERE id = ${tenant_arg_index}
            RETURNING id, name, slug, status, plan_code, trial_ends_at, current_period_end,
                      razorpay_customer_id, razorpay_subscription_id, updated_at;
            """,
            *values,
            tenant_id,
        )
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=admin["user_id"],
        action="billing_tenant_override",
        resource_type="tenant",
        resource_id=tenant_id,
        details={
            "reason": req.reason,
            "changed_fields": sorted(changes.keys()),
            "before": before_dict,
            "after": dict(row),
        },
    )
    return APIResponse.create_success(data={
        **dict(row),
        "noop": False,
        "changed_fields": sorted(changes.keys()),
    })
@router.get("/billing/events", response_model=APIResponse)
async def list_billing_events(admin: dict = Depends(require_super_admin)):
    """Super Admin: Recent billing-related audit events across tenants."""
    async with db_client.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id, a.tenant_id, t.name AS tenant_name, a.actor_id, a.action,
                   a.resource_type, a.resource_id, a.details, a.created_at
            FROM audit_logs a
            LEFT JOIN tenants t ON t.id = a.tenant_id
            WHERE a.action LIKE 'billing_%'
               OR a.action LIKE 'razorpay_%'
               OR a.resource_type IN ('subscription_plan', 'billing', 'payment')
            ORDER BY a.created_at DESC
            LIMIT 100;
            """
        )
    return APIResponse.create_success(data=[dict(r) for r in rows])
@router.get("/business-types", response_model=APIResponse)
async def list_business_types(admin: dict = Depends(require_super_admin)):
    """Super Admin: List all business types."""
    types = await db_client.get_all_business_types()
    validated = [BusinessTypeResponse(**t).model_dump(mode="json") for t in types]
    return APIResponse.create_success(data=validated)
@router.get("/business-types/{code}", response_model=APIResponse)
async def get_business_type(code: str, admin: dict = Depends(require_super_admin)):
    """Super Admin: Get a single business type by code."""
    res = await db_client.get_business_type_by_code(code)
    if not res:
        raise HTTPException(status_code=404, detail=f"Business type with code '{code}' not found")
    validated = BusinessTypeResponse(**res).model_dump(mode="json")
    return APIResponse.create_success(data=validated)
@router.post("/business-types", response_model=APIResponse)
async def create_business_type(req: BusinessTypeCreateRequest, admin: dict = Depends(require_super_admin)):
    """Super Admin: Create a new business type."""
    await _validate_enabled_modules(req.enabled_modules)
    existing = await db_client.get_business_type_by_code(req.code)
    if existing:
        raise HTTPException(status_code=400, detail=f"Business type with code '{req.code}' already exists")
    res = await db_client.create_business_type(
        code=req.code,
        label=req.label,
        enabled_modules=req.enabled_modules,
        note=req.note
    )
    validated = BusinessTypeResponse(**res).model_dump(mode="json")
    return APIResponse.create_success(data=validated)
@router.patch("/business-types/{code}", response_model=APIResponse)
async def update_business_type(code: str, req: BusinessTypeUpdateRequest, admin: dict = Depends(require_super_admin)):
    """Super Admin: Update an existing business type."""
    if req.enabled_modules is not None:
        await _validate_enabled_modules(req.enabled_modules)
    existing = await db_client.get_business_type_by_code(code)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Business type with code '{code}' not found")
    res = await db_client.update_business_type(
        code=code,
        label=req.label,
        enabled_modules=req.enabled_modules,
        note=req.note
    )
    validated = BusinessTypeResponse(**res).model_dump(mode="json")
    return APIResponse.create_success(data=validated)
@router.delete("/business-types/{code}", response_model=APIResponse)
async def delete_business_type(code: str, admin: dict = Depends(require_super_admin)):
    """Super Admin: Delete a business type by code."""
    existing = await db_client.get_business_type_by_code(code)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Business type with code '{code}' not found")
    deleted = await db_client.delete_business_type(code)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete business type")
    return APIResponse.create_success(data={"message": f"Business type '{code}' deleted successfully"})

def redact_sensitive_values(data: Any) -> Any:
    """Recursively redact sensitive keys in dictionaries/lists."""
    if isinstance(data, dict):
        redacted = {}
        for k, v in data.items():
            k_lower = k.lower()
            if any(term in k_lower for term in [
                "secret", "token", "password", "key", "signature",
                "otp", "auth", "credential", "private", "payload"
            ]):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive_values(v)
        return redacted
    elif isinstance(data, list):
        return [redact_sensitive_values(item) for item in data]
    return data

@router.post("/impersonate/{tenant_id}", response_model=APIResponse)
async def impersonate_tenant(
    tenant_id: UUID,
    admin: dict = Depends(require_super_admin),
):
    """
    Super Admin: Generate a short-lived impersonation token for a tenant's primary admin user.

    The returned JWT is identical in structure to a normal access token and is
    accepted by all standard API dependencies. It additionally carries
    ``app_metadata.impersonator_id`` so every action performed during the
    session is attributable to the originating super admin in audit logs.

    No refresh token is issued — the impersonation session expires when the
    access token does (ACCESS_TOKEN_EXPIRE_MINUTES).
    """
    from backend.security.auth import sign_impersonation_token

    # 1. Verify target tenant exists
    async with db_client.acquire() as conn:
        tenant_row = await conn.fetchrow(
            "SELECT id, name FROM tenants WHERE id = $1 AND deleted_at IS NULL",
            tenant_id,
        )
    if not tenant_row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # 2. Find the primary tenant_admin for this tenant
    async with db_client.acquire() as conn:
        user_row = await conn.fetchrow(
            """
            SELECT u.id, u.email
            FROM users u
            JOIN user_tenants ut ON u.id = ut.user_id
            WHERE ut.tenant_id = $1
              AND ut.role = 'tenant_admin'
              AND u.deleted_at IS NULL
            ORDER BY ut.created_at ASC
            LIMIT 1
            """,
            tenant_id,
        )
    if not user_row:
        raise HTTPException(
            status_code=404,
            detail="No tenant_admin user found for this tenant. Create one before impersonating.",
        )

    target_user_id: UUID = user_row["id"]
    target_email: str = user_row["email"]
    impersonator_id: UUID = UUID(admin["user_id"])

    # 3. Mint the impersonation token
    token = sign_impersonation_token(
        user_id=target_user_id,
        email=target_email,
        tenant_id=tenant_id,
        role="tenant_admin",
        impersonator_id=impersonator_id,
    )

    # 4. Write an immutable audit log entry (best-effort — must not block the response)
    try:
        await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=impersonator_id,
            action="impersonation_started",
            resource_type="tenant",
            resource_id=tenant_id,
            details={
                "target_user_id": str(target_user_id),
                "target_email": target_email,
                "tenant_name": tenant_row["name"],
            },
        )
    except Exception as audit_exc:
        from backend.ops.logger import logger
        logger.warning(f"IMPERSONATE: Failed to write audit log for tenant={tenant_id}: {audit_exc}")

    return APIResponse.create_success(data={
        "access_token": token,
        "token_type": "bearer",
        "tenant_id": str(tenant_id),
        "tenant_name": tenant_row["name"],
        "target_user_id": str(target_user_id),
    })


@router.get("/audit-logs", response_model=APIResponse)

async def list_global_audit_logs(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(50, ge=1, le=200),
    action_type: Optional[str] = Query(None, description="Filter by exact action string"),
    date_from: Optional[str] = Query(None, description="Filter from date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Filter to date YYYY-MM-DD"),
    tenant_id: Optional[UUID] = Query(None, description="Filter by tenant ID"),
    admin: dict = Depends(require_super_admin)
):
    """Super Admin: View global platform audit logs with optional filtering and redaction."""
    decoded = decode_cursor(cursor) if cursor else None
    result = await db_client.list_platform_audit_logs(
        cursor=decoded, limit=limit,
        action_type=action_type, date_from=date_from, date_to=date_to,
        tenant_id_filter=tenant_id
    )
    for item in result["items"]:
        if item.get("details"):
            item["details"] = redact_sensitive_values(item["details"])
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})


# ---------------------------------------------------------------------------
# Phase 4 – Global Platform Metrics (C-Suite Telemetry)
# ---------------------------------------------------------------------------

@router.get("/metrics", response_model=APIResponse)
async def get_platform_metrics(admin: dict = Depends(require_super_admin)):
    """
    Super Admin: Aggregated platform telemetry for the current month.
    Returns:
      - active_tenants: count of non-deleted tenants with status='active'
      - mtd_messages: total messages processed across all tenants this calendar month
      - mtd_cost_usd: total AI + compute cost (USD) across all tenants this calendar month
    """
    async with db_client.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                (
                    SELECT COUNT(*)
                    FROM tenants
                    WHERE deleted_at IS NULL
                      AND status = 'active'
                ) AS active_tenants,
                COALESCE(SUM(tcd.message_count), 0)                   AS mtd_messages,
                COALESCE(SUM(tcd.ai_token_cost + tcd.compute_cost), 0) AS mtd_cost_usd
            FROM tenant_cost_daily tcd
            WHERE tcd.cost_date >= date_trunc('month', CURRENT_DATE)::date
            """
        )
    return APIResponse.create_success(data={
        "active_tenants": int(row["active_tenants"]),
        "mtd_messages":   int(row["mtd_messages"]),
        "mtd_cost_usd":   float(row["mtd_cost_usd"]),
    })
