from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator
from backend.core.states import ConversationState


def protected_action_binding(
    *,
    tenant_id: Any,
    conversation_id: Any,
    channel_session_id: Any,
    config_version: int,
    action_nonce: str,
    tool_key: str,
    args_hash: str,
) -> str:
    """Return the full canonical identity of one protected-action incarnation."""
    material = {
        "action_nonce": str(action_nonce),
        "args_hash": str(args_hash),
        "channel_session_id": str(channel_session_id),
        "config_version": int(config_version),
        "conversation_id": str(conversation_id),
        "tenant_id": str(tenant_id),
        "tool_key": str(tool_key),
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PendingAction(BaseModel):
    """Backend-owned description of an action awaiting confirmation or execution."""
    tool_key: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    args_hash: Optional[str] = None
    summary: Optional[str] = None
    idempotency_key: Optional[str] = None
    action_nonce: str = Field(default_factory=lambda: secrets.token_hex(16), min_length=32, max_length=64)
    requires_confirmation: bool = False
    requires_verified_customer: bool = False
    verification_action: Optional[str] = None
    required_assurance: str = "none"
    assurance_policy_source: Optional[str] = None
    verification_scope: Optional[str] = None
    channel_assurance_source: Optional[str] = None
    def model_post_init(self, __context: Any) -> None:
        if not self.args_hash:
            canonical = json.dumps(self.args or {}, sort_keys=True, default=str, separators=(",", ":"))
            self.args_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
class OtpVerificationEvidence(BaseModel):
    binding: str
    action_nonce: str
    verification_ref: str
    tenant_id: str
    conversation_id: str
    channel_session_id: str
    config_version: int
    tool_key: str
    args_hash: str
    verified_at: datetime
    expires_at: datetime
    consumed: bool = False
    consumption_ref: Optional[str] = None
    assurance_source: str = "awa_chat_otp_phone_possession"
    phone_identity_hash: Optional[str] = None
    verification_scope: str = "session_phone_possession"


class VerificationState(BaseModel):
    """Customer verification facts known to the backend."""
    customer_verified: bool = False
    phone_verified: bool = False
    otp_verified_actions: List[str] = Field(default_factory=list)
    otp_verifications: List[OtpVerificationEvidence] = Field(default_factory=list)
class LastToolResult(BaseModel):
    """Sanitized result from the last backend tool call."""
    tool_key: Optional[str] = None
    success: Optional[bool] = None
    denied_reason: Optional[str] = None
    user_message: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: Dict[str, str] = Field(default_factory=dict)
    created_resource_type: Optional[str] = None
    created_resource_id: Optional[str] = None
    action_nonce: Optional[str] = None
    args_hash: Optional[str] = None
class SafeError(BaseModel):
    """A customer-safe error summary for prompt/UI awareness."""
    code: Optional[str] = None
    safe_message: Optional[str] = None
    provider_error: Optional[Dict[str, Any]] = None

class PromptFocus(BaseModel):
    """Small backend-owned record of the fields requested on the last workflow prompt."""
    workflow: str
    requested_fields: List[str] = Field(default_factory=list, max_length=20)
    prompt_kind: str = "missing_fields"
    source: str = "backend_workflow"
    turn_sequence: Optional[int] = Field(default=None, ge=1)

    @field_validator("workflow")
    @classmethod
    def validate_workflow(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value):
            raise ValueError("invalid prompt-focus workflow")
        return value

    @field_validator("requested_fields")
    @classmethod
    def validate_requested_fields(cls, values: List[str]) -> List[str]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) for value in values):
            raise ValueError("invalid prompt-focus field")
        return list(dict.fromkeys(values))

    @field_validator("prompt_kind")
    @classmethod
    def validate_prompt_kind(cls, value: str) -> str:
        if value not in {"missing_fields", "invalid_field", "change_request"}:
            raise ValueError("invalid prompt-focus kind")
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value != "backend_workflow":
            raise ValueError("invalid prompt-focus source")
        return value

class ActionLedger(BaseModel):
    """
    Small durable state ledger for the LLM-first engine.
    This is intentionally much smaller than the old state machine. It stores
    backend facts the agent must be aware of without letting the agent decide
    whether a protected action may commit.
    """
    current_task: Optional[str] = None
    task_status: str = "idle"
    channel: Optional[str] = None
    pending_action: Optional[PendingAction] = None
    verification: VerificationState = Field(default_factory=VerificationState)
    last_tool_result: Optional[LastToolResult] = None
    last_error: Optional[SafeError] = None
    last_nba_signature: Optional[str] = None
    last_nba_suggestions: List[str] = Field(default_factory=list)
class SessionState(BaseModel):
    """
    Represents the runtime state of a conversation.
    Maps to `conversation_sessions.state_json` in DB.
    """
    workflow_state: ConversationState = Field(default=ConversationState.NEW)
    current_workflow_id: Optional[str] = None
    current_step_id: Optional[str] = None
    step_data: Dict[str, Any] = Field(default_factory=dict)
    scoped_step_data: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    agentic_history: list = Field(default_factory=list)
    action_ledger: ActionLedger = Field(default_factory=ActionLedger)
    prompt_focus: Optional[PromptFocus] = None
    orientation_config_pending: bool = False
    # Validation Logic
    validation_retry_count: int = 0
    last_prompt_key: Optional[str] = None
    repeated_prompt_count: int = 0
    confusion_signal_count: int = 0
    last_escalation_reason: Optional[str] = None
    # Metadata for debugging
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    @staticmethod
    def action_args_hash(args: Dict[str, Any]) -> str:
        canonical = json.dumps(args or {}, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    def get_task_step_data(self, task_key: Optional[str]) -> Dict[str, Any]:
        """Return task-scoped collected data, falling back to legacy step_data."""
        if not task_key:
            return dict(self.step_data or {})
        scoped = self.scoped_step_data.get(task_key)
        if isinstance(scoped, dict):
            return dict(scoped)
        if not self.action_ledger.current_task or self.action_ledger.current_task == task_key:
            return dict(self.step_data or {})
        return {}

    def update_task_step_data(self, task_key: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Merge values into a task-scoped data bag and mirror it for legacy modules."""
        existing = self.scoped_step_data.get(task_key)
        scoped = dict(existing) if isinstance(existing, dict) else {}
        if isinstance(values, dict):
            scoped.update(values)
        else:
            logger.warning(f"update_task_step_data received non-dict values: {type(values)}")
        self.scoped_step_data[task_key] = scoped
        self.step_data = dict(scoped)
        self.action_ledger.current_task = task_key
        return scoped

    def activate_task_step_data(self, task_key: str) -> Dict[str, Any]:
        """Mirror task-scoped data into legacy step_data before module execution."""
        scoped = self.get_task_step_data(task_key)
        self.scoped_step_data[task_key] = dict(scoped)
        self.step_data = dict(scoped)
        self.action_ledger.current_task = task_key
        return scoped

    def replace_task_step_data(
        self,
        task_key: str,
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Exact-replace a task scope; previous unrelated keys are dropped.

        This is a replacement, not a merge. Only ``scoped_step_data[task_key]`` is
        updated and the current task is set; global ``step_data`` is left untouched so
        the tool registry remains the single owner of global activation.
        """
        scoped = dict(values) if isinstance(values, dict) else {}
        self.scoped_step_data[task_key] = scoped
        self.action_ledger.current_task = task_key
        return dict(scoped)

    def restore_task_step_data_snapshot(
        self,
        task_key: str,
        *,
        scope_existed: bool,
        scoped_values: Dict[str, Any],
        global_values: Dict[str, Any],
        current_task: Optional[str],
    ) -> Dict[str, Any]:
        if scope_existed:
            restored_scoped = dict(scoped_values)
            self.scoped_step_data[task_key] = restored_scoped
        else:
            self.scoped_step_data.pop(task_key, None)
            restored_scoped = {}

        self.step_data = dict(global_values)
        self.action_ledger.current_task = current_task
        return dict(restored_scoped)
    def prepare_pending_action(
        self,
        *,
        tool_key: str,
        args: Dict[str, Any],
        summary: Optional[str] = None,
        requires_confirmation: bool = True,
        requires_verified_customer: bool = False,
        verification_action: Optional[str] = None,
        required_assurance: str = "none",
        assurance_policy_source: Optional[str] = None,
        verification_scope: Optional[str] = None,
        channel_assurance_source: Optional[str] = None,
        channel_type: Optional[str] = None,
    ) -> PendingAction:
        """Store a protected action that must be confirmed before commit."""
        if requires_verified_customer and required_assurance == "none":
            # Compatibility callers still receive a platform-owned snapshot;
            # tenant config is never consulted here.
            from backend.core.customer_assurance import policy_source, required_assurance as _required_assurance
            requirement = _required_assurance(tool_key, requires_verified_customer=True)
            required_assurance = requirement.value
            assurance_policy_source = assurance_policy_source or policy_source(tool_key)
            verification_scope = verification_scope or "session_phone_possession"
        args_hash = self.action_args_hash(args or {})
        existing = self.action_ledger.pending_action
        action_nonce = (
            existing.action_nonce
            if existing
            and existing.tool_key == tool_key
            and existing.args_hash == args_hash
            and existing.requires_confirmation == requires_confirmation
            and existing.requires_verified_customer == requires_verified_customer
            and existing.verification_action == verification_action
            and existing.required_assurance == required_assurance
            else secrets.token_hex(16)
        )
        pending = PendingAction(
            tool_key=tool_key,
            args=args or {},
            args_hash=args_hash,
            action_nonce=action_nonce,
            summary=summary,
            requires_confirmation=requires_confirmation,
            requires_verified_customer=requires_verified_customer,
            verification_action=verification_action,
            required_assurance=required_assurance,
            assurance_policy_source=assurance_policy_source,
            verification_scope=verification_scope,
            channel_assurance_source=channel_assurance_source,
        )
        self.action_ledger.pending_action = pending
        self.action_ledger.current_task = tool_key.split(".", 1)[0]
        self.action_ledger.task_status = "awaiting_confirmation" if requires_confirmation else "prepared"
        self.prompt_focus = None
        if channel_type:
            self.action_ledger.channel = str(channel_type)
        return pending

    def set_prompt_focus(
        self,
        *,
        workflow: str,
        requested_fields: List[str],
        prompt_kind: str = "missing_fields",
        turn_sequence: Optional[int] = None,
    ) -> None:
        """Persist bounded canonical identifiers, never rendered wording or field values."""
        safe_workflow = str(workflow or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", safe_workflow):
            return
        safe_fields: List[str] = []
        for field_name in requested_fields or []:
            candidate = str(field_name or "").strip()
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate) and candidate not in safe_fields:
                safe_fields.append(candidate)
            if len(safe_fields) >= 20:
                break
        if not safe_fields:
            self.prompt_focus = None
            return
        kind = prompt_kind if prompt_kind in {"missing_fields", "invalid_field", "change_request"} else "missing_fields"
        self.prompt_focus = PromptFocus(
            workflow=safe_workflow,
            requested_fields=safe_fields,
            prompt_kind=kind,
            turn_sequence=turn_sequence if isinstance(turn_sequence, int) and turn_sequence > 0 else None,
        )

    def clear_prompt_focus(self) -> None:
        self.prompt_focus = None
    def pending_action_matches(self, tool_key: str, args: Dict[str, Any]) -> bool:
        pending = self.action_ledger.pending_action
        if not pending or pending.tool_key != tool_key:
            return False
        return pending.args_hash == self.action_args_hash(args or {})
    def confirm_pending_action(self, tool_key: str, args: Dict[str, Any]) -> bool:
        """Mark the pending action confirmed only when the current args match."""
        if not self.pending_action_matches(tool_key, args):
            return False
        self.action_ledger.task_status = "confirmed"
        return True
    def record_tool_result(
        self,
        result: Any,
        channel_type: Optional[str] = None,
        *,
        action_nonce: Optional[str] = None,
        args_hash: Optional[str] = None,
    ) -> None:
        """Record a sanitized backend tool result for the next agent turn."""
        tool_key = str(getattr(result, "tool_key", "") or "")
        module_name = tool_key.split(".", 1)[0] if tool_key else None
        success = bool(getattr(result, "success", False))
        denied_reason = getattr(result, "denied_reason", None)
        if self.workflow_state == ConversationState.ESCALATED:
            task_status = "escalated"
        elif success:
            task_status = "completed"
            self.prompt_focus = None
        elif str(denied_reason or "").startswith("confirmation_required") and self.action_ledger.pending_action:
            task_status = "awaiting_confirmation"
        elif denied_reason:
            task_status = "blocked"
        else:
            task_status = "failed"
        self.action_ledger.current_task = module_name
        self.action_ledger.task_status = task_status
        if channel_type:
            self.action_ledger.channel = str(channel_type)
        self.action_ledger.last_tool_result = LastToolResult(
            tool_key=tool_key or None,
            success=success,
            denied_reason=denied_reason,
            user_message=getattr(result, "user_message", None),
            data=getattr(result, "data", None) or {},
            validation_errors=getattr(result, "validation_errors", None) or {},
            created_resource_type=getattr(result, "created_resource_type", None),
            created_resource_id=getattr(result, "created_resource_id", None),
            action_nonce=action_nonce,
            args_hash=args_hash,
        )
        self.action_ledger.last_error = None if success else SafeError(
            code=denied_reason or "tool_failed",
            safe_message=getattr(result, "user_message", None) or "I couldn't complete that action right now.",
        )
    def record_safe_error(self, code: str, safe_message: str, channel_type: Optional[str] = None) -> None:
        """Record a customer-safe error summary without exposing internals."""
        if channel_type:
            self.action_ledger.channel = str(channel_type)
        self.action_ledger.task_status = "failed"
        self.action_ledger.last_error = SafeError(code=code, safe_message=safe_message)
class ConversationSession(BaseModel):
    """
    Core Session Object.
    Maps to `conversation_sessions` table rows.
    """
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    # Pinned Config Version (Invariant)
    config_version: int = Field(default=0)
    # State data
    current_module: Optional[str] = None
    current_intent: Optional[str] = None
    state: SessionState = Field(default_factory=SessionState)
    expires_at: Optional[datetime] = None

    def activate_workflow_task(self, task_key: str) -> bool:
        """Atomically establish a collecting workflow without executing anything."""
        normalized_task = str(task_key or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", normalized_task):
            return False

        ledger = self.state.action_ledger
        active_task = ledger.current_task or self.current_module
        pending = ledger.pending_action
        pending_task = (
            str(getattr(pending, "tool_key", "") or "").split(".", 1)[0]
            if pending is not None
            else None
        )
        if pending is not None and pending_task != normalized_task:
            return False
        if active_task and active_task != normalized_task:
            return False

        self.state.activate_task_step_data(normalized_task)
        self.current_module = normalized_task
        if self.state.workflow_state == ConversationState.NEW:
            self.state.workflow_state = ConversationState.COLLECTING_DATA
        ledger.task_status = "collecting"
        return True
class TenantConfig(BaseModel):
    """
    Represents the active configuration for a tenant.
    """
    tenant_id: UUID
    version: int
    config_data: Dict[str, Any]
class Tenant(BaseModel):
    id: UUID
    name: str
    plan_code: str
    status: str
    ai_mode: Optional[str] = None
    trial_ends_at: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
class User(BaseModel):
    id: UUID
    phone_number: str
    name: Optional[str] = None
class ExecutionContext(BaseModel):
    """
    Single source of truth passed to workers.
    Aggregates all context required for deterministic execution.
    """
    tenant: Tenant
    user: User
    session_data: ConversationSession
    config: TenantConfig
    incoming_message: Optional[Dict[str, Any]] = None
    llm_usage_records: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_behavior_profile: Optional[Any] = None
    # Ephemeral shadow-only evidence. Excluded from persistence and public serialization.
    shadow_turn_evidence: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    turn_interpretation: Optional[Any] = Field(default=None, exclude=True)
    turn_interpretation_comparison: Optional[Any] = Field(default=None, exclude=True)
    turn_plan: Optional[Any] = Field(default=None, exclude=True)
    def record_llm_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        if not isinstance(usage, dict):
            return
        self.llm_usage_records.append({
            "provider": usage.get("provider"),
            "model": usage.get("model"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost_usd": usage.get("cost_usd"),
            "usage_available": bool(usage.get("usage_available")),
            "cost_available": bool(usage.get("cost_available")),
            "source": usage.get("source"),
            "missing_reason": usage.get("missing_reason"),
        })
    def aggregate_llm_usage(self) -> Dict[str, Any]:
        records = list(self.llm_usage_records or [])
        usage_records = [record for record in records if record.get("usage_available")]
        cost_records = [
            float(record["cost_usd"])
            for record in records
            if record.get("cost_available") and record.get("cost_usd") is not None
        ]
        def sum_int(key: str) -> Optional[int]:
            values = [int(record[key]) for record in usage_records if record.get(key) is not None]
            return sum(values) if values else None
        return {
            "call_count": len(records),
            "usage_available": bool(usage_records),
            "missing_usage_count": len(records) - len(usage_records),
            "providers": sorted({str(record.get("provider")) for record in records if record.get("provider")}),
            "models": sorted({str(record.get("model")) for record in records if record.get("model")}),
            "prompt_tokens": sum_int("prompt_tokens"),
            "completion_tokens": sum_int("completion_tokens"),
            "total_tokens": sum_int("total_tokens"),
            "cost_available": bool(cost_records),
            "cost_usd": sum(cost_records) if cost_records else None,
            "records": records,
        }

import contextvars
active_context_var: contextvars.ContextVar[Optional["ExecutionContext"]] = contextvars.ContextVar("active_context_var", default=None)
