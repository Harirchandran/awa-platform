from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import json
from typing import Any, Dict, Iterable, Optional
from uuid import UUID
from backend.core.ai_modes import normalize_ai_mode, normalize_allowed_ai_modes, normalize_known_ai_mode
from backend.core.context import ExecutionContext, Tenant
from backend.ops.logger import logger
UNLIMITED = None
@dataclass(frozen=True)
class PlanPolicy:
    plan_code: str
    monthly_messages: Optional[int]
    daily_messages: Optional[int]
    monthly_ai_tokens: Optional[int]
    monthly_credits: Optional[float]
    allowed_ai_modes: tuple[str, ...]
    allowed_modules: tuple[str, ...]
    tool_credit_cost: float = 1.0
    message_credit_cost: float = 0.1
class UsageLimitExceeded(Exception):
    def __init__(self, reason: str, user_message: str, policy: Optional[PlanPolicy] = None):
        self.reason = reason
        self.user_message = user_message
        self.policy = policy
        super().__init__(reason)
DEFAULT_PLAN_POLICIES: Dict[str, PlanPolicy] = {
    "trial": PlanPolicy(
        plan_code="trial",
        monthly_messages=300,
        daily_messages=50,
        monthly_ai_tokens=100_000,
        monthly_credits=1_000,
        allowed_ai_modes=("standard",),
        allowed_modules=("booking", "order", "faq", "human_handoff"),
    ),
    "starter": PlanPolicy(
        plan_code="starter",
        monthly_messages=1_000,
        daily_messages=150,
        monthly_ai_tokens=300_000,
        monthly_credits=3_000,
        allowed_ai_modes=("standard",),
        allowed_modules=("booking", "order", "payments", "reminders", "reviews", "faq", "human_handoff"),
    ),
    "pro": PlanPolicy(
        plan_code="pro",
        monthly_messages=5_000,
        daily_messages=750,
        monthly_ai_tokens=2_000_000,
        monthly_credits=10_000,
        allowed_ai_modes=("standard", "advanced"),
        allowed_modules=("booking", "order", "payments", "reminders", "reviews", "lead_capture", "support", "faq", "human_handoff"),
    ),
    "enterprise": PlanPolicy(
        plan_code="enterprise",
        monthly_messages=UNLIMITED,
        daily_messages=UNLIMITED,
        monthly_ai_tokens=UNLIMITED,
        monthly_credits=UNLIMITED,
        allowed_ai_modes=("standard", "advanced"),
        allowed_modules=("booking", "lead_capture", "support", "faq", "human_handoff", "*"),
    ),
}
AI_CAPABILITY_PROFILES: Dict[str, Dict[str, Any]] = {
    "standard": {
        "code": "standard",
        "label": "Standard AI",
        "runtime_engine": "unified_agent",
        "summary": "Unified AWA agent with plan-limited automation tools, confirmations, and safety gates.",
        "is_autonomous": False,
        "ui_description": "Uses the same unified AWA engine with conservative plan-limited automation.",
    },
    "advanced": {
        "code": "advanced",
        "label": "Advanced AI",
        "runtime_engine": "unified_agent",
        "summary": "Unified AWA agent with enhanced conversation handling while obeying the same backend gates.",
        "is_autonomous": False,
        "ui_description": "Enhanced AI mode for more capable conversation handling. Advanced execution-tier features may depend on plan and platform configuration.",
    },
}
PLAN_ALIASES = {
    "free": "trial",
    "professional": "pro",
    "pro_plan": "pro",
    "pro_pending": "pro",
    "enterprise": "enterprise",
}
class UsageGuard:
    """
    Central plan, quota, and module access guard.
    The guard is intentionally backend-owned. Tenant config may disable modules,
    but it cannot grant capabilities beyond the tenant's billing plan.
    """
    def __init__(self, db_client_override=None):
        self._db = db_client_override
    async def check_message_allowed(self, context: ExecutionContext) -> PlanPolicy:
        policy = await self.get_policy_async(context.tenant)
        self._check_tenant_status(context.tenant, policy)
        usage = await self.get_usage_summary(context.tenant.id)
        self._check_usage_number(
            usage.get("month_message_count", 0),
            policy.monthly_messages,
            "monthly_messages",
            "This business has reached its monthly WhatsApp automation limit. Please contact the business directly.",
            policy,
        )
        self._check_usage_number(
            usage.get("today_message_count", 0),
            policy.daily_messages,
            "daily_messages",
            "This business has reached its daily WhatsApp automation limit. Please try again tomorrow.",
            policy,
        )
        self._check_usage_number(
            usage.get("month_ai_tokens_used", 0),
            policy.monthly_ai_tokens,
            "monthly_ai_tokens",
            "This business has reached its monthly AI usage limit. Please contact the business directly.",
            policy,
        )
        self._check_usage_number(
            usage.get("month_credits_used", 0),
            policy.monthly_credits,
            "monthly_credits",
            "This business has reached its monthly automation credits. Please contact the business directly.",
            policy,
        )
        return policy
    async def ensure_tool_allowed(self, context: ExecutionContext, tool_key: str) -> PlanPolicy:
        policy = await self.check_message_allowed(context)
        self._check_tool_policy_allowed(context, tool_key, policy)
        return policy
    async def get_tool_visibility_denial(self, context: ExecutionContext, tool_key: str) -> Optional[UsageLimitExceeded]:
        """
        Return why a tool should be hidden from LLM/native tool exposure.
        This uses the same plan/config/status rules as execution, without checking
        message quotas because message-level quota checks happen before tool binding.
        """
        policy = await self.get_policy_async(context.tenant)
        try:
            self._check_tenant_status(context.tenant, policy)
            self._check_tool_policy_allowed(context, tool_key, policy)
        except UsageLimitExceeded as exc:
            return exc
        return None
    def _check_tool_policy_allowed(
        self,
        context: ExecutionContext,
        tool_key: str,
        policy: PlanPolicy,
    ) -> None:
        module_name = module_from_tool_key(tool_key)
        if not self._module_allowed_by_plan(module_name, policy.allowed_modules):
            raise UsageLimitExceeded(
                reason=f"module_not_allowed:{module_name}",
                user_message="This feature is not available on the business plan yet.",
                policy=policy,
            )
        module_config = context.config.config_data.get("modules", {}).get(module_name, {})
        if module_config.get("enabled") is not True:
            raise UsageLimitExceeded(
                reason=f"module_disabled:{module_name}",
                user_message="This feature is currently turned off for this business.",
                policy=policy,
            )
    async def resolve_ai_mode(self, context: ExecutionContext, requested_mode: str) -> str:
        policy = await self.get_policy_async(context.tenant)
        requested = normalize_ai_mode(requested_mode)
        if requested in policy.allowed_ai_modes:
            return requested
        logger.info(
            "AI mode downgraded by plan: tenant=%s requested=%s plan=%s",
            context.tenant.id,
            requested,
            policy.plan_code,
        )
        return "standard"
    async def record_message_processed(
        self,
        tenant_id: UUID,
        ai_tokens_used: int = 0,
        ai_cost: float = 0.0,
        compute_cost: float = 0.0,
        credits_used: float = 0.0,
        llm_usage: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[UUID] = None,
        source_event_id: Optional[UUID] = None,
        tenant_timezone: str = "UTC",
    ) -> None:
        try:
            import zoneinfo
            try:
                tz = zoneinfo.ZoneInfo(tenant_timezone)
            except Exception:
                tz = timezone.utc
            cost_date = datetime.now(tz).date()
            db = self._get_db()
            await db.upsert_cost_daily(
                tenant_id=tenant_id,
                cost_date=cost_date,
                ai_token_cost=ai_cost,
                compute_cost=compute_cost,
                message_count=1,
                ai_tokens_used=ai_tokens_used,
                total_credits_used=credits_used,
            )
            await self._write_llm_usage_audit(
                db=db,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                source_event_id=source_event_id,
                llm_usage=llm_usage,
            )
        except Exception as e:
            logger.warning(f"Usage message persistence failed: {e}")
    async def record_tool_execution(
        self,
        tenant_id: UUID,
        policy: PlanPolicy,
        tool_key: str,
        success: bool,
    ) -> None:
        if not success:
            return
        try:
            db = self._get_db()
            await db.upsert_cost_daily(
                tenant_id=tenant_id,
                cost_date=date.today(),
                tool_action_count=1,
                total_credits_used=policy.tool_credit_cost,
            )
        except Exception as e:
            logger.warning(f"Usage tool persistence failed for {tool_key}: {e}")
    async def get_usage_summary(self, tenant_id: UUID) -> Dict[str, Any]:
        try:
            return await self._get_db().get_tenant_usage_summary(tenant_id)
        except Exception as e:
            logger.warning(f"Usage summary unavailable, allowing fallback: {e}")
            return {
                "today_message_count": 0,
                "month_message_count": 0,
                "month_ai_tokens_used": 0,
                "month_credits_used": 0,
                "month_tool_action_count": 0,
            }
    async def get_usage_snapshot(self, tenant: Tenant | Dict[str, Any]) -> Dict[str, Any]:
        tenant_obj = tenant if isinstance(tenant, Tenant) else Tenant(**tenant)
        policy = await self.get_policy_async(tenant_obj)
        usage = await self.get_usage_summary(tenant_obj.id)
        return {
            "plan_policy": {
                "plan_code": policy.plan_code,
                "monthly_messages": policy.monthly_messages,
                "daily_messages": policy.daily_messages,
                "monthly_ai_tokens": policy.monthly_ai_tokens,
                "monthly_credits": policy.monthly_credits,
                "allowed_ai_modes": list(policy.allowed_ai_modes),
                "allowed_modules": list(policy.allowed_modules),
                "ai_capability_profiles": build_ai_capability_profiles(policy),
            },
            "ai_capability_policy": get_ai_capability_policy(
                tenant_obj.ai_mode or "standard",
                policy,
            ),
            "usage": usage,
        }
    def get_policy(self, tenant: Tenant) -> PlanPolicy:
        plan_code = normalize_plan_code(tenant.plan_code)
        return DEFAULT_PLAN_POLICIES.get(plan_code, DEFAULT_PLAN_POLICIES["trial"])
    async def get_policy_async(self, tenant: Tenant) -> PlanPolicy:
        policy = self.get_policy(tenant)
        requested_plan_code = normalize_plan_code(tenant.plan_code)
        try:
            plan = await self._get_db().get_subscription_plan_by_code(requested_plan_code)
        except Exception as e:
            logger.debug(f"Plan policy lookup using defaults for tenant {tenant.id}: {e}")
            return policy
        if not plan:
            return policy
        limits = plan.get("limits_json") or {}
        if isinstance(limits, str):
            try:
                limits = json.loads(limits)
            except json.JSONDecodeError:
                return policy
        if not isinstance(limits, dict):
            return policy
        return _apply_limit_overrides(
            replace(policy, plan_code=str(plan.get("plan_code") or requested_plan_code)),
            limits,
        )
    def _check_tenant_status(self, tenant: Tenant, policy: PlanPolicy) -> None:
        status = (tenant.status or "").lower()
        if status in {"suspended", "expired", "past_due", "cancelled"}:
            raise UsageLimitExceeded(
                reason=f"tenant_status:{status}",
                user_message="This business account is currently inactive. Please contact the business directly.",
                policy=policy,
            )
        if status == "trial" and tenant.trial_ends_at:
            trial_ends_at = tenant.trial_ends_at
            if trial_ends_at.tzinfo is None:
                trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)
            if trial_ends_at < datetime.now(timezone.utc):
                raise UsageLimitExceeded(
                    reason="trial_expired",
                    user_message="This business trial has ended. Please contact the business directly.",
                    policy=policy,
                )
    def _check_usage_number(
        self,
        current: float,
        limit: Optional[float],
        reason: str,
        user_message: str,
        policy: PlanPolicy,
    ) -> None:
        if limit is not None and current >= limit:
            raise UsageLimitExceeded(reason=reason, user_message=user_message, policy=policy)
    def _module_allowed_by_plan(self, module_name: str, allowed_modules: Iterable[str]) -> bool:
        allowed = set(allowed_modules)
        return "*" in allowed or module_name in allowed
    def _get_db(self):
        if self._db is not None:
            return self._db
        from backend.db.client import db_client
        return db_client
    async def _write_llm_usage_audit(
        self,
        db,
        tenant_id: UUID,
        conversation_id: Optional[UUID],
        source_event_id: Optional[UUID],
        llm_usage: Optional[Dict[str, Any]],
    ) -> None:
        if not llm_usage or not llm_usage.get("call_count") or not hasattr(db, "write_audit_log"):
            return
        try:
            await db.write_audit_log(
                tenant_id=tenant_id,
                actor_id=None,
                action="llm_usage_recorded",
                resource_type="usage",
                resource_id=conversation_id,
                details={
                    "source_event_id": str(source_event_id) if source_event_id else None,
                    "usage_available": llm_usage.get("usage_available"),
                    "missing_usage_count": llm_usage.get("missing_usage_count"),
                    "call_count": llm_usage.get("call_count"),
                    "providers": llm_usage.get("providers") or [],
                    "models": llm_usage.get("models") or [],
                    "prompt_tokens": llm_usage.get("prompt_tokens"),
                    "completion_tokens": llm_usage.get("completion_tokens"),
                    "total_tokens": llm_usage.get("total_tokens"),
                    "cost_available": llm_usage.get("cost_available"),
                    "cost_usd": llm_usage.get("cost_usd"),
                    "records": llm_usage.get("records") or [],
                },
                ip_address="system",
            )
        except Exception as exc:
            logger.warning(f"LLM usage audit persistence failed: {exc}")
def normalize_plan_code(plan_code: Optional[str]) -> str:
    normalized = (plan_code or "trial").strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    return PLAN_ALIASES.get(normalized, normalized)
def module_from_tool_key(tool_key: str) -> str:
    return tool_key.split(".", 1)[0]
def build_ai_capability_profiles(policy: PlanPolicy) -> list[Dict[str, Any]]:
    allowed_modes = set(policy.allowed_ai_modes)
    return [
        {
            **profile,
            "allowed": code in allowed_modes,
            "locked_reason": None if code in allowed_modes else f"Requires a plan that includes {profile['label']}.",
        }
        for code, profile in AI_CAPABILITY_PROFILES.items()
    ]
def get_ai_capability_policy(configured_mode: Optional[str], policy: PlanPolicy) -> Dict[str, Any]:
    requested = normalize_ai_mode(configured_mode)
    effective = requested if requested in policy.allowed_ai_modes else "standard"
    profile = dict(AI_CAPABILITY_PROFILES[effective])
    requested_profile = dict(AI_CAPABILITY_PROFILES[requested])
    return {
        "requested_mode": requested,
        "effective_mode": effective,
        "runtime_engine": profile["runtime_engine"],
        "label": profile["label"],
        "summary": profile["summary"],
        "ui_description": profile["ui_description"],
        "is_autonomous": profile["is_autonomous"],
        "downgraded": effective != requested,
        "locked_reason": None if effective == requested else (
            f"{requested_profile['label']} is not available on the current {policy.plan_code} plan."
        ),
    }
def _apply_limit_overrides(policy: PlanPolicy, limits: Dict[str, Any]) -> PlanPolicy:
    return PlanPolicy(
        plan_code=policy.plan_code,
        monthly_messages=_optional_int(limits, "monthly_messages", policy.monthly_messages),
        daily_messages=_optional_int(limits, "daily_messages", policy.daily_messages),
        monthly_ai_tokens=_optional_int(limits, "monthly_ai_tokens", policy.monthly_ai_tokens),
        monthly_credits=_optional_float(limits, "monthly_credits", policy.monthly_credits),
        allowed_ai_modes=_ai_mode_tuple(limits.get("allowed_ai_modes"), policy.allowed_ai_modes),
        allowed_modules=_string_tuple(limits.get("allowed_modules"), policy.allowed_modules),
        tool_credit_cost=_optional_float(limits, "tool_credit_cost", policy.tool_credit_cost) or policy.tool_credit_cost,
        message_credit_cost=_optional_float(limits, "message_credit_cost", policy.message_credit_cost) or policy.message_credit_cost,
    )
def _optional_int(source: Dict[str, Any], key: str, default: Optional[int]) -> Optional[int]:
    if key not in source:
        return default
    value = source.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
def _optional_float(source: Dict[str, Any], key: str, default: Optional[float]) -> Optional[float]:
    if key not in source:
        return default
    value = source.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
def _string_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list):
        values = tuple(str(item) for item in value if str(item).strip())
        return values or default
    return default
def _ai_mode_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list):
        return normalize_allowed_ai_modes(value, default)
    return default
usage_guard = UsageGuard()

async def validate_plan_limits(limits_json: Any, plan_code: str | None = None) -> dict[str, Any]:
    """
    Semantically validate super-admin plan limits before saving to subscription_plans.limits_json.
    Raises ValueError with descriptive reasons on failure.
    """
    if isinstance(limits_json, str):
        if not limits_json.strip():
            limits = {}
        else:
            try:
                limits = json.loads(limits_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON format for limits_json: {e}")
    elif isinstance(limits_json, dict):
        limits = limits_json
    elif limits_json is None:
        limits = {}
    else:
        raise ValueError("limits_json must be a JSON object or string")

    # 1. Unknown keys
    allowed_keys = {
        "monthly_messages",
        "daily_messages",
        "monthly_ai_tokens",
        "monthly_credits",
        "allowed_ai_modes",
        "allowed_modules",
        "tool_credit_cost",
        "message_credit_cost",
    }
    unknown_keys = set(limits.keys()) - allowed_keys
    if unknown_keys:
        raise ValueError(f"Unknown keys in limits_json: {', '.join(sorted(unknown_keys))}")

    # 2. Integer numeric fields (must be null or non-negative integer, no string/boolean coercion)
    int_keys = {"monthly_messages", "daily_messages", "monthly_ai_tokens"}
    for key in int_keys:
        if key in limits:
            val = limits[key]
            if val is not None:
                if isinstance(val, bool) or not isinstance(val, int):
                    raise ValueError(f"'{key}' must be an integer, not string or boolean")
                if val < 0:
                    raise ValueError(f"'{key}' must be a non-negative integer")

    # 3. Float/number fields (must be null or non-negative number, no string/boolean coercion)
    float_keys = {"monthly_credits", "tool_credit_cost", "message_credit_cost"}
    for key in float_keys:
        if key in limits:
            val = limits[key]
            if val is not None:
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"'{key}' must be a number, not string or boolean")
                if val < 0:
                    raise ValueError(f"'{key}' must be a non-negative number")

    # 4. Validate allowed_ai_modes
    if "allowed_ai_modes" in limits:
        ai_modes = limits["allowed_ai_modes"]
        if ai_modes is None:
            raise ValueError("'allowed_ai_modes' cannot be null")
        if not isinstance(ai_modes, list):
            raise ValueError("'allowed_ai_modes' must be a list")
        if len(ai_modes) == 0:
            raise ValueError("'allowed_ai_modes' cannot be empty")
        canonical_modes: list[str] = []
        for item in ai_modes:
            if not isinstance(item, str):
                raise ValueError("All elements in 'allowed_ai_modes' must be strings")
            canonical = normalize_known_ai_mode(item)
            if canonical is None:
                raise ValueError(f"Unknown AI mode: '{item}'")
            if canonical not in canonical_modes:
                canonical_modes.append(canonical)
        limits = dict(limits)
        limits["allowed_ai_modes"] = canonical_modes

    # 5. Validate allowed_modules
    if "allowed_modules" in limits:
        modules = limits["allowed_modules"]
        if modules is None:
            raise ValueError("'allowed_modules' cannot be null")
        if not isinstance(modules, list):
            raise ValueError("'allowed_modules' must be a list")

        if "*" in modules:
            if len(modules) > 1:
                raise ValueError("Wildcard '*' must be the sole entry in 'allowed_modules'")
        else:
            # Resolve valid modules from defaults and database
            valid_modules = {
                "booking",
                "order",
                "payments",
                "reminders",
                "reviews",
                "lead_capture",
                "support",
                "faq",
                "human_handoff",
            }
            try:
                from backend.db.client import db_client
                from backend.services.tool_controls import normalize_tool_definitions
                db_tools = await db_client.get_tool_catalog()
                if db_tools:
                    for t in normalize_tool_definitions(db_tools):
                        if t.get("key"):
                            valid_modules.add(t["key"])
            except Exception:
                pass

            for item in modules:
                if not isinstance(item, str):
                    raise ValueError("All elements in 'allowed_modules' must be strings")
                if item not in valid_modules:
                    raise ValueError(f"Unknown module: '{item}'")

    return limits
