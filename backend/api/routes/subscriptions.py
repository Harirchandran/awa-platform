"""
Subscriptions & Billing API Routes
Handles Razorpay integration for SaaS tenant billing.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.db.client import db_client
from backend.schemas.response import APIResponse
from backend.core.config import settings
from backend.core.ai_modes import normalize_ai_mode
from backend.ops.logger import logger
from backend.core.context import Tenant
from backend.billing.usage_guard import usage_guard, normalize_plan_code
from backend.billing.plans import canonicalize_plan_rows
import uuid
router = APIRouter(prefix="/api/v1/billing", tags=["Billing"])
class SubscribeRequest(BaseModel):
    plan_id: str = Field(min_length=3, max_length=120)  # maps to subscription_plans.razorpay_plan_id
def compute_billing_lifecycle_status(tenant: dict) -> dict:
    """Return safe SaaS billing lifecycle state for tenant dashboard/API."""
    status = str(tenant.get("status") or "trial").lower()
    subscription_id = tenant.get("razorpay_subscription_id")
    trial_ends_at = tenant.get("trial_ends_at")
    current_period_end = tenant.get("current_period_end")
    lifecycle = status
    attention_required = False
    reason = None
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if status == "trial":
        if trial_ends_at:
            trial_dt = trial_ends_at if trial_ends_at.tzinfo else trial_ends_at.replace(tzinfo=timezone.utc)
            if trial_dt < now:
                lifecycle = "trial_expired"
                attention_required = True
                reason = "trial_expired"
            else:
                lifecycle = "trialing"
        else:
            lifecycle = "trialing"
    elif status == "active":
        lifecycle = "active"
        if current_period_end:
            period_dt = current_period_end if current_period_end.tzinfo else current_period_end.replace(tzinfo=timezone.utc)
            if period_dt < now and subscription_id:
                lifecycle = "renewal_overdue"
                attention_required = True
                reason = "current_period_end_elapsed"
    elif status == "suspended":
        lifecycle = "past_due_or_suspended" if subscription_id else "suspended"
        attention_required = True
        reason = "payment_or_subscription_attention_required"
    elif status == "expired":
        lifecycle = "expired"
        attention_required = True
        reason = "subscription_completed_or_trial_expired"
    return {
        "lifecycle_status": lifecycle,
        "attention_required": attention_required,
        "reason": reason,
        "saas_billing_only": True,
    }
@router.get("/status")
async def get_billing_status(user: dict = Depends(get_current_user)):
    """Fetch current subscription status, limits, and plan details for the tenant."""
    tenant_id = user["tenant_id"]
    async with db_client.acquire() as conn:
        tenant = await conn.fetchrow("""
            SELECT status, plan_code, trial_ends_at, current_period_end,
                   razorpay_customer_id, razorpay_subscription_id, id, name, ai_mode
            FROM tenants WHERE id = $1
        """, tenant_id)
        # Pull canonical active plans to surface in the UI. Rows without a
        # plan_code are legacy payment rows and are not tenant-selectable.
        plans = await conn.fetch("""
            SELECT *
            FROM subscription_plans
            WHERE is_active = true
              AND plan_code IS NOT NULL
            ORDER BY monthly_price_cents ASC, name ASC
        """)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant_dict = dict(tenant)
    tenant_model = Tenant(
        id=tenant_dict["id"],
        name=tenant_dict.get("name") or "Tenant",
        plan_code=tenant_dict.get("plan_code") or "trial",
        status=tenant_dict.get("status") or "trial",
        ai_mode=tenant_dict.get("ai_mode"),
        trial_ends_at=tenant_dict.get("trial_ends_at"),
        current_period_end=tenant_dict.get("current_period_end"),
    )
    usage_snapshot = await usage_guard.get_usage_snapshot(tenant_model)
    current_plan_code = normalize_plan_code(tenant_model.plan_code)
    plan_dicts = canonicalize_plan_rows(plans)
    current_plan = next(
        (plan for plan in plan_dicts if normalize_plan_code(plan.get("plan_code") or plan.get("razorpay_plan_id")) == current_plan_code),
        None,
    )
    lifecycle = compute_billing_lifecycle_status(tenant_dict)
    tenant_dict["ai_mode"] = normalize_ai_mode(tenant_dict.get("ai_mode"))
    return APIResponse.create_success(data={
        "tenant_billing": tenant_dict,
        "billing_lifecycle": lifecycle,
        "available_plans": plan_dicts,
        "current_plan": current_plan,
        "billing_mode": settings.effective_billing_mode,
        "mock_billing": settings.allow_mock_billing,
        **usage_snapshot,
    })
@router.post("/subscribe")
async def create_subscription(
    body: SubscribeRequest,
    user: dict = Depends(require_tenant_admin)
):
    """
    Creates a Razorpay Subscription for the tenant.
    Returns the subscription_id for the frontend Razorpay Checkout drop-in.
    """
    tenant_id = user["tenant_id"]
    # Check if dummy environment
    is_dummy = (
        settings.allow_mock_billing
        and (settings.RAZORPAY_KEY_ID == "dummy_razorpay_key" or not settings.RAZORPAY_KEY_ID)
    )
    try:
        async with db_client.acquire() as conn:
            plan = await conn.fetchrow(
                "SELECT plan_code, razorpay_plan_id FROM subscription_plans WHERE razorpay_plan_id = $1 AND is_active = true",
                body.plan_id,
            )
            tenant = await conn.fetchrow(
                """
                SELECT t.id, t.name, t.razorpay_customer_id, u.email, u.phone_number
                FROM tenants t
                LEFT JOIN user_tenants ut ON ut.tenant_id = t.id
                LEFT JOIN users u ON u.id = ut.user_id
                WHERE t.id = $1
                ORDER BY ut.created_at ASC
                LIMIT 1
                """,
                tenant_id,
            )
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        # Mock behavior for local testing if no keys
        if is_dummy:
            logger.info(f"MOCK: Simulating Razorpay subscription setup for tenant {tenant_id}")
            sub_id = f"sub_dummy_{str(uuid.uuid4())[:8]}"
            # Save the simulated subscription locally as active for local UI testing.
            async with db_client.acquire() as conn:
                await conn.execute("""
                    UPDATE tenants
                    SET razorpay_subscription_id = $1, plan_code = $2, status = 'active'
                    WHERE id = $3
                """, sub_id, plan["plan_code"], tenant_id)
            return APIResponse.create_success(data={
                "subscription_id": sub_id,
                "activation_status": "active",
                "mock": True,
            })
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            raise HTTPException(status_code=500, detail="Razorpay credentials are not configured")
        # Real Razorpay Execution
        import razorpay
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        # Create subscription
        sub_data = {
            "plan_id": body.plan_id,
            "total_count": 12, # E.g., 12 billing cycles (1 year)
            "quantity": 1,
            "customer_notify": 1,
            "notes": {
                "tenant_id": str(tenant_id),
                "tenant_name": tenant.get("name") or "Tenant",
                "plan_code": plan["plan_code"],
            },
        }
        subscription = client.subscription.create(data=sub_data)
        customer_id = subscription.get("customer_id") or tenant.get("razorpay_customer_id")
        async with db_client.acquire() as conn:
            await conn.execute("""
                UPDATE tenants
                SET razorpay_subscription_id = $1,
                    razorpay_customer_id = COALESCE($2, razorpay_customer_id)
                WHERE id = $3
            """, subscription['id'], customer_id, tenant_id)
        await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=user["user_id"],
            action="billing_subscription_created",
            resource_type="billing",
            resource_id=tenant_id,
            details={
                "subscription_id": subscription["id"],
                "customer_id": customer_id,
                "plan_code": plan["plan_code"],
                "razorpay_plan_id": body.plan_id,
                "activation_status": "pending_webhook",
            },
        )
        return APIResponse.create_success(data={
            "subscription_id": subscription['id'],
            "customer_id": customer_id,
            "activation_status": "pending_webhook"
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create Razorpay subscription: {e}")
        raise HTTPException(status_code=500, detail="Failed to create subscription")
