from __future__ import annotations

import re
from typing import Any, Dict, Optional
from backend.billing.usage_guard import UsageLimitExceeded, usage_guard
from backend.core.context import ExecutionContext
from backend.core.customer_assurance import has_required_assurance, policy_source, required_assurance
from backend.core.states import ConversationState
from backend.ops.logger import logger
from backend.capabilities.models import CapabilityRequest, CapabilityDecision

class CapabilityGateway:
    def __init__(self, guard=usage_guard):
        self.guard = guard

    async def evaluate(
        self,
        request: CapabilityRequest,
        context: ExecutionContext,
        tool_def: Any, # ToolDefinition from registry
        metadata: Any, # ToolMetadata
    ) -> CapabilityDecision:
        # 1. Unknown tool
        if not tool_def:
            decision = CapabilityDecision(
                status="unavailable",
                reason_code="unknown_tool",
                user_message="That action is not available.",
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 2. Enabled check
        module_config = request.tenant_config.get("modules", {}).get(request.module_name, {})
        if not module_config.get("enabled"):
            decision = CapabilityDecision(
                status="denied",
                reason_code=f"module_disabled:{tool_def.module_name}",
                user_message="This feature is currently turned off for this business.",
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 3. Context validation
        context_errors = self._context_check(request, context, tool_def)
        if context_errors:
            decision = CapabilityDecision(
                status="denied",
                reason_code=f"context_validation_failed:{tool_def.module_name}",
                user_message="I can't complete that action because this chat context is not valid for it.",
                audit_metadata={"validation_errors": context_errors},
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 4. Schema/Payload/Argument validation
        schema_errors = self._schema_check(request, tool_def)
        if schema_errors:
            decision = CapabilityDecision(
                status="denied",
                reason_code=f"argument_validation_failed:{tool_def.module_name}",
                user_message="I need valid details before I can complete that action.",
                audit_metadata={"validation_errors": schema_errors},
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 5. Plan / Subscription limit checks (UsageLimitExceeded)
        try:
            policy = await self.guard.ensure_tool_allowed(context, tool_def.key)
        except UsageLimitExceeded as e:
            decision = CapabilityDecision(
                status="denied",
                reason_code=e.reason,
                user_message=e.user_message or f"Action {tool_def.key} is not allowed on your plan.",
                audit_metadata={"denied_reason": e.reason},
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 6. Module custom validate_payload (if defined)
        if hasattr(tool_def.module, "validate_payload"):
            error_message = tool_def.module.validate_payload(context)
            if error_message:
                decision = CapabilityDecision(
                    status="denied",
                    reason_code="validation_failed",
                    user_message=error_message,
                    audit_metadata={"validation_message": error_message},
                    safe_to_execute=False
                )
                self._log_decision(request, decision)
                return decision

        # 7. Channel check
        supported_channels = {str(channel).lower() for channel in metadata.supported_channels}
        if supported_channels and request.channel not in supported_channels:
            decision = CapabilityDecision(
                status="denied",
                reason_code=f"channel_not_supported:{tool_def.module_name}:{request.channel}",
                user_message="This action is not available in this chat channel.",
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 8. Conversation Escalated check
        workflow_state = request.session_context.get("workflow_state")
        if workflow_state == ConversationState.ESCALATED and not metadata.allowed_when_paused:
            decision = CapabilityDecision(
                status="escalated",
                reason_code=f"chat_paused:{tool_def.module_name}",
                user_message="This chat is paused for human support. A person can continue from here.",
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 9. Confirmation check
        requires_confirmation = metadata.requires_confirmation
        if requires_confirmation and not self._has_confirmed_pending_action(tool_def, request, context):
            current_args = request.input_payload or {}
            pending = context.session_data.state.prepare_pending_action(
                tool_key=tool_def.key,
                args=current_args,
                summary=self._summarize_pending_action(tool_def, current_args),
                requires_confirmation=True,
                requires_verified_customer=metadata.requires_verified_customer,
                verification_action=f"{tool_def.module_name}_commitment",
                required_assurance=(required_assurance(tool_def.key, requires_verified_customer=True).value if metadata.requires_verified_customer else "none"),
                assurance_policy_source=policy_source(tool_def.key),
                verification_scope="session_phone_possession",
                channel_type=request.channel,
            )
            decision = CapabilityDecision(
                status="needs_confirmation",
                reason_code=f"confirmation_required:{tool_def.module_name}",
                user_message=(
                    f"{pending.summary or 'Please review the details.'}\n\n"
                    "Please confirm before I complete this action. [Confirm] [Change] [Cancel]"
                ),
                audit_metadata={
                    "pending_action": pending.model_dump(mode="json", exclude_none=True),
                    "required_next_step": "confirm",
                },
                required_action="confirm",
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # 10. Customer verification check
        if metadata.requires_verified_customer and not self._has_verified_customer(tool_def, request, context, True):
            # FIX: Ensure pending_action exists so the frontend OTP UI triggers, even in Concierge mode
            if not context.session_data.state.action_ledger.pending_action:
                current_args = request.input_payload or {}
                context.session_data.state.prepare_pending_action(
                    tool_key=tool_def.key,
                    args=current_args,
                    summary=self._summarize_pending_action(tool_def, current_args),
                    requires_confirmation=metadata.requires_confirmation,
                    requires_verified_customer=True,
                    verification_action=f"{tool_def.module_name}_commitment",
                    required_assurance=required_assurance(tool_def.key, requires_verified_customer=True).value,
                    assurance_policy_source=policy_source(tool_def.key),
                    verification_scope="session_phone_possession",
                    channel_type=request.channel,
                )

            verification_message = (
                "Please verify your phone number in this browser chat before I complete this action."
                if request.channel == "awa_chat"
                else (
                    "This action needs customer verification before it can be completed. "
                    "Please continue with the business team or use a verified browser chat link."
                )
            )
            decision = CapabilityDecision(
                status="needs_verification",
                reason_code=f"customer_verification_required:{tool_def.module_name}",
                user_message=verification_message,
                audit_metadata={
                    "required_next_step": "customer_verification",
                    "verification_action": f"{tool_def.module_name}_commitment",
                    "channel": request.channel,
                    "verification_available_in_channel": request.channel == "awa_chat",
                },
                required_action="verify",
                safe_to_execute=False
            )
            self._log_decision(request, decision)
            return decision

        # Allowed!
        decision = CapabilityDecision(
            status="allowed",
            reason_code="allowed",
            safe_to_execute=True,
            policy=policy if 'policy' in locals() else None,
            audit_metadata={"policy": getattr(policy, "model_dump", lambda: {})()} if ('policy' in locals() and policy) else {}
        )
        self._log_decision(request, decision)
        return decision

    def _log_decision(self, request: CapabilityRequest, decision: CapabilityDecision):
        log_payload = {
            "tool": request.tool_name,
            "decision": decision.status,
            "reason_code": decision.reason_code,
            "tenant_id": str(request.tenant_id) if request.tenant_id else None,
            "channel": request.channel,
            "audit_metadata": decision.audit_metadata
        }
        logger.info("CapabilityGateway decision: %s", log_payload)

    def _context_check(self, request: CapabilityRequest, context: ExecutionContext, tool_def: Any) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        if not request.tenant_id:
            errors["tenant_id"] = "Trusted tenant context is missing."
        if not request.customer_id:
            errors["customer_id"] = "Trusted customer context is missing."
        session = getattr(context, "session_data", None)
        if not session or not getattr(session, "id", None):
            errors["session_id"] = "Trusted session context is missing."
        if not request.conversation_id:
            errors["conversation_id"] = "Trusted conversation context is missing."
        config = getattr(context, "config", None)
        if not config or not getattr(config, "tenant_id", None):
            errors["config_tenant_id"] = "Trusted tenant config context is missing."

        tenant_id = request.tenant_id
        if tenant_id and session and getattr(session, "tenant_id", None) != tenant_id:
            errors["session_tenant_id"] = "Conversation session does not belong to the trusted tenant."
        if tenant_id and config and getattr(config, "tenant_id", None) != tenant_id:
            errors["config_tenant_id"] = "Tenant config does not belong to the trusted tenant."

        incoming = context.incoming_message or {}
        for incoming_key, trusted_value in (
            ("tenant_id", tenant_id),
            ("customer_id", request.customer_id),
            ("conversation_id", request.conversation_id),
            ("session_id", getattr(session, "id", None) if session else None),
        ):
            if incoming.get(incoming_key) and trusted_value and str(incoming[incoming_key]) != str(trusted_value):
                errors[incoming_key] = f"Incoming message {incoming_key} conflicts with trusted server context."
        return errors

    def _schema_check(self, request: CapabilityRequest, tool_def: Any) -> Dict[str, str]:
        if not tool_def.module:
            return {"module": "That action is not available."}
        payload = request.input_payload
        if not isinstance(payload, dict):
            return {"arguments": "Tool arguments must be an object."}

        from backend.tools.registry import ToolRegistry
        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(request.tenant_config))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = {str(field) for field in schema.get("required") or []}
        allowed = set(properties.keys())
        errors: Dict[str, str] = {}
        # ``service_id`` is resolver-owned booking state.  It is intentionally
        # absent from the public tool schema but remains available to the
        # booking handler as a stable configured-service identity.
        internal_keys = {"service_id"} if tool_def.module_name == "booking" else set()
        unknown = sorted(str(key) for key in payload.keys() if str(key) not in allowed and str(key) not in internal_keys)
        if allowed and unknown:
            errors["extra_arguments"] = f"Unsupported tool arguments: {', '.join(unknown[:10])}."
        for field in required:
            if payload.get(field) in (None, ""):
                errors[field] = "This required field is missing."
        for key, value in payload.items():
            key = str(key)
            field_schema = properties.get(key)
            if not isinstance(field_schema, dict) or value in (None, ""):
                continue
            expected_type = field_schema.get("type")
            if not ToolRegistry._matches_schema_type(value, expected_type):
                errors[key] = f"Expected {expected_type}."
                continue
            enum_values = field_schema.get("enum")
            if isinstance(enum_values, list) and enum_values and str(value) not in {str(item) for item in enum_values}:
                errors[key] = "Value is not one of the supported options."
        field_defs = {str(field.get("key")): field for field in tool_def.module.get_required_fields(request.tenant_config) if field.get("key")}
        for key, field_def in field_defs.items():
            if key not in payload:
                continue
            validation = tool_def.module.validate_input(field_def, payload.get(key), tenant_config=request.tenant_config)
            if not validation.is_valid:
                errors[key] = validation.error_message or "Invalid value."
        return errors

    def _has_confirmed_pending_action(self, tool_def: Any, request: CapabilityRequest, context: ExecutionContext) -> bool:
        session = getattr(context, "session_data", None)
        if not session or not hasattr(session, "state"):
            return False
        ledger = session.state.action_ledger
        pending = ledger.pending_action
        if not pending or pending.tool_key != tool_def.key:
            return False
        from backend.tools.registry import ToolRegistry
        if not session.state.pending_action_matches(tool_def.key, ToolRegistry._tool_action_args(tool_def, context)):
            return False
        if pending.requires_confirmation is False:
            return True
        return ledger.task_status in {"confirmed", "executing"}

    def _is_customer_confirmation(self, request: CapabilityRequest) -> bool:
        return False

    def _confirm_pending_action(self, tool_def: Any, request: CapabilityRequest, context: ExecutionContext) -> bool:
        from backend.tools.registry import ToolRegistry
        current_args = ToolRegistry._tool_action_args(tool_def, context)
        return context.session_data.state.confirm_pending_action(tool_def.key, current_args)

    def _summarize_pending_action(self, tool_def: Any, args: Dict[str, Any]) -> str:
        module_title = tool_def.module_name.replace("_", " ").title()
        internal_key_pattern = re.compile(r"(^id$|_id$|uuid|token|secret|trace|raw|payload)", re.IGNORECASE)
        visible_items = [
            (str(key), value)
            for key, value in (args or {}).items()
            if value not in (None, "") and not internal_key_pattern.search(str(key))
        ][:8]
        if not visible_items:
            return f"Please review the {module_title} request."
        lines = [f"Please review this {module_title} request:"]
        lines.extend(f"- {key}: {value}" for key, value in visible_items)
        return "\n".join(lines)

    def _has_verified_customer(self, tool_def: Any, request: CapabilityRequest, context: ExecutionContext, requires_verified_customer: bool = False) -> bool:
        return has_required_assurance(
            context, tool_def.key, requires_verified_customer=requires_verified_customer
        )
