from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from uuid import UUID
from backend.core.context import ExecutionContext

class CapabilityRequest(BaseModel):
    tenant_id: Optional[UUID] = None
    tool_name: str
    channel: str
    module_name: str
    customer_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    user_context: Dict[str, Any] = Field(default_factory=dict)
    session_context: Dict[str, Any] = Field(default_factory=dict)
    input_payload: Optional[Dict[str, Any]] = None
    tenant_config: Dict[str, Any] = Field(default_factory=dict)
    plan_context: Dict[str, Any] = Field(default_factory=dict)
    execution_flags: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_context(cls, tool_key: str, context: ExecutionContext, args: Optional[Dict[str, Any]] = None) -> CapabilityRequest:
        tenant_id = getattr(getattr(context, "tenant", None), "id", None)
        user_id = getattr(getattr(context, "user", None), "id", None)
        session = getattr(context, "session_data", None)
        session_id = getattr(session, "id", None) if session else None
        conversation_id = getattr(session, "conversation_id", None) if session else None

        module_name = tool_key.split(".", 1)[0]
        channel = str((context.incoming_message or {}).get("channel_type") or "whatsapp").lower()

        tenant_config = getattr(getattr(context, "config", None), "config_data", {}) or {}

        payload = args if args is not None else (context.session_data.state.get_task_step_data(module_name) if (session and hasattr(session, "state")) else None)

        user_ctx = {
            "id": user_id,
            "role": getattr(getattr(context, "user", None), "role", None) if getattr(context, "user", None) else None
        }

        session_ctx = {}
        if session and hasattr(session, "state"):
            session_ctx = {
                "id": session_id,
                "workflow_state": getattr(session.state, "workflow_state", None),
                "action_ledger": session.state.action_ledger.model_dump() if hasattr(session.state, "action_ledger") else None
            }

        return cls(
            tenant_id=tenant_id,
            tool_name=tool_key,
            channel=channel,
            module_name=module_name,
            customer_id=user_id,
            conversation_id=conversation_id,
            user_context=user_ctx,
            session_context=session_ctx,
            input_payload=payload,
            tenant_config=tenant_config,
            plan_context={},
            execution_flags={
                "incoming_message": context.incoming_message
            }
        )

class CapabilityDecision(BaseModel):
    status: str # allowed / denied / unavailable / needs_confirmation / needs_verification / escalated
    reason_code: str
    user_message: Optional[str] = None
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)
    required_action: Optional[str] = None
    safe_to_execute: bool = False
    policy: Optional[Any] = None
