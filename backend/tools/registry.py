from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union
from backend.billing.usage_guard import UsageLimitExceeded, usage_guard
from backend.core.context import ExecutionContext
from backend.core.customer_assurance import has_required_assurance, policy_source, required_assurance
from backend.core.states import ConversationState
from backend.modules.base_module import BaseModule, ToolMetadata
from backend.ops.logger import logger
@dataclass(frozen=True)
class ToolExecutionResult:
    tool_key: str
    success: bool
    denied_reason: Optional[str] = None
    user_message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    validation_errors: Optional[Dict[str, str]] = None
    created_resource_type: Optional[str] = None
    created_resource_id: Optional[str] = None
    def to_tool_message(self) -> str:
        payload: Dict[str, Any] = {
            "success": self.success,
            "tool_key": self.tool_key,
        }
        if self.user_message:
            payload["user_message"] = self.user_message
        if self.denied_reason:
            payload["denied_reason"] = self.denied_reason
        if self.validation_errors:
            payload["validation_errors"] = self.validation_errors
        if self.created_resource_type:
            payload["created_resource_type"] = self.created_resource_type
        if self.created_resource_id:
            payload["created_resource_id"] = self.created_resource_id
        if self.data:
            payload["data"] = self.data
        return json.dumps(payload, ensure_ascii=True, default=str)
ToolModuleResult = Union[bool, ToolExecutionResult]
ToolExecutor = Callable[[ExecutionContext], Awaitable[ToolModuleResult]]
@dataclass(frozen=True)
class ToolDefinition:
    key: str
    llm_name: str
    module_name: str
    description: str
    execute: ToolExecutor
    audit_action: str
    module: Optional[BaseModule] = None
@dataclass(frozen=True)
class ToolValidationDecision:
    allowed: bool
    tool: ToolDefinition
    metadata: ToolMetadata
    policy: Any = None
    result: Optional[ToolExecutionResult] = None
    reason: Optional[str] = None
class ToolRegistry:
    """
    Internal tool registry for tenant-safe automation actions.
    AI/agentic flows can request tools later, but execution always passes through
    this backend registry so plan, tenant-config, and audit rules stay central.
    """
    def __init__(self, guard=usage_guard):
        self.guard = guard
        self._tools: Dict[str, ToolDefinition] = {}
        self._module_to_tool: Dict[str, str] = {}
        self._llm_name_to_tool: Dict[str, str] = {}
        self.modules: Dict[str, BaseModule] = {}
    @staticmethod
    def _llm_name_for_tool(module_name: str) -> str:
        return f"{module_name}_execute"
    def register_module(self, module: BaseModule, description: str = "") -> None:
        module_name = module.get_module_name()
        if module_name == "catalog_search":
            self.register(
                ToolDefinition(
                    key="order.catalog_search",
                    llm_name="catalog_search",
                    module_name="order",
                    description=description or "Search active available catalog items for this tenant",
                    execute=module.execute,
                    audit_action="tool_catalog_search_executed",
                    module=module,
                )
            )
            return
        if module_name == "booking.availability":
            self.register(
                ToolDefinition(
                    key="booking.availability",
                    llm_name="booking_check_availability",
                    module_name="booking",
                    description=(
                        description
                        or "Read configured working hours and current bookings to check an exact booking start time "
                        "or search valid start windows for one service and tenant-local date. Use this before any "
                        "availability or unavailability claim. This does not create or confirm a booking."
                    ),
                    execute=module.execute,
                    audit_action="tool_booking_availability_checked",
                    module=module,
                )
            )
            return
        if module_name == "booking.update":
            tool_key = "booking.update"
            llm_name = "booking_update_execute"
            self.register(
                ToolDefinition(
                    key=tool_key,
                    llm_name=llm_name,
                    module_name="booking",
                    description=description or "Update or cancel booking",
                    execute=module.execute,
                    audit_action="tool_booking_update_executed",
                    module=module,
                )
            )
            return
        tool_key = f"{module_name}.execute"
        self.register(
            ToolDefinition(
                key=tool_key,
                llm_name=self._llm_name_for_tool(module_name),
                module_name=module_name,
                description=description or f"Execute {module_name} workflow",
                execute=module.execute,
                audit_action=f"tool_{module_name}_executed",
                module=module,
            )
        )
    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.key] = tool
        if tool.key == f"{tool.module_name}.execute" or tool.module_name not in self._module_to_tool:
            self._module_to_tool[tool.module_name] = tool.key
        self._llm_name_to_tool[tool.llm_name] = tool.key
        if tool.module:
            self.modules[tool.module_name] = tool.module
    def get_tool_key_for_module(self, module_name: str) -> Optional[str]:
        return self._module_to_tool.get(module_name)
    def get_tool_key_for_llm_name(self, llm_name: str) -> Optional[str]:
        return self._llm_name_to_tool.get(llm_name)
    def get_tool(self, tool_key: str) -> ToolDefinition:
        if tool_key not in self._tools:
            raise KeyError(f"Unknown tool: {tool_key}")
        return self._tools[tool_key]

    async def get_pre_route_eligible_tool(
        self,
        context: ExecutionContext,
        tool_key: str,
    ) -> Optional[ToolDefinition]:
        """Return a tenant-visible tool without route scoping or side effects."""
        tool = self._tools.get(tool_key)
        if tool is None or not self._is_config_visible_tool(context, tool):
            return None
        denial = await self.guard.get_tool_visibility_denial(context, tool_key)
        if denial:
            return None
        return tool

    def get_llm_tools(self, context: ExecutionContext) -> List[Dict[str, Any]]:
        """
        Returns config-enabled tool schemas without async plan checks.
        Prefer `get_llm_tools_for_context` for customer-facing LLM binding.
        """
        return self._build_llm_tools(context, allowed_tool_keys=None)
    async def get_llm_tools_for_context(
        self,
        context: ExecutionContext,
        *,
        intent: Optional[str] = None,
        route_type: Optional[str] = None,
        current_task: Optional[str] = None,
        is_tangent: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Returns only tools that are executable for this tenant/customer context:
        implemented, enabled in config, allowed by plan, and safe for LLM use.
        """
        allowed_tool_keys = set()
        for tool_key, tool_def in self._tools.items():
            if not self._is_config_visible_tool(context, tool_def):
                continue
            denial = await self.guard.get_tool_visibility_denial(context, tool_key)
            if denial:
                logger.info("Hiding LLM tool %s: %s", tool_key, denial.reason)
                continue
            allowed_tool_keys.add(tool_key)
        scoped_tool_keys = self._scope_tool_keys_for_route(
            allowed_tool_keys,
            context,
            intent=intent,
            route_type=route_type,
            current_task=current_task,
            is_tangent=is_tangent,
        )
        try:
            if isinstance(context.incoming_message, dict):
                context.incoming_message["_llm_tool_scope_metrics"] = {
                    "unscoped_tool_count": len(allowed_tool_keys),
                    "scoped_tool_count": len(scoped_tool_keys),
                    "scoped_tools_used": scoped_tool_keys != allowed_tool_keys,
                }
        except Exception:
            pass
        if scoped_tool_keys != allowed_tool_keys:
            logger.info(
                "Route-scoped LLM tools",
                extra={"extra_data": {
                    "intent": intent,
                    "route_type": route_type,
                    "is_tangent": bool(is_tangent),
                    "before_count": len(allowed_tool_keys),
                    "after_count": len(scoped_tool_keys),
                }},
            )
        return self._build_llm_tools(context, allowed_tool_keys=scoped_tool_keys)

    def _scope_tool_keys_for_route(
        self,
        allowed_tool_keys: set[str],
        context: ExecutionContext,
        *,
        intent: Optional[str],
        route_type: Optional[str],
        current_task: Optional[str],
        is_tangent: bool,
    ) -> set[str]:
        if intent is None and route_type is None and current_task is None and not is_tangent:
            return allowed_tool_keys
        if context.session_data.state.workflow_state == ConversationState.ESCALATED:
            return set()
        normalized_intent = self._normalize_route_key(intent)
        normalized_route_type = self._normalize_route_key(route_type)
        normalized_task = self._normalize_route_key(current_task)
        if normalized_task == "human_handoff" or normalized_intent == "human_handoff":
            return self._handoff_tool_keys(allowed_tool_keys)
        if is_tangent:
            return self._handoff_tool_keys(allowed_tool_keys) | self._catalog_search_tool_keys(allowed_tool_keys)
        if normalized_route_type in {"direct_answer", "direct_action", "clarification", "unknown"}:
            if normalized_intent in {"faq", "unknown", "ambiguous", "menu", "catalog"}:
                return self._handoff_tool_keys(allowed_tool_keys) | self._catalog_search_tool_keys(allowed_tool_keys)
            return self._handoff_tool_keys(allowed_tool_keys)
        if normalized_intent in {
            "greeting", "thanks", "cancel",
            "opening_hours", "hours",
        }:
            return self._handoff_tool_keys(allowed_tool_keys)
        if normalized_intent in {
            "faq", "unknown", "ambiguous",
            "menu", "catalog", "opening_hours", "hours",
        }:
            return self._handoff_tool_keys(allowed_tool_keys) | self._catalog_search_tool_keys(allowed_tool_keys)

        effective_route = normalized_task if normalized_route_type == "active_task" and normalized_task else normalized_intent
        route_modules = self._route_modules(effective_route)
        if not route_modules:
            return self._handoff_tool_keys(allowed_tool_keys)
        matched = {
            tool_key
            for tool_key in allowed_tool_keys
            if self._tools[tool_key].module_name in route_modules
        }
        return matched | self._handoff_tool_keys(allowed_tool_keys)

    @staticmethod
    def _normalize_route_key(value: Optional[str]) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _route_modules(route_key: str) -> set[str]:
        route_map = {
            "booking": {"booking"},
            "order": {"order"},
            "payments": {"payments"},
            "payment": {"payments"},
            "reminders": {"reminders"},
            "reminder": {"reminders"},
            "reviews": {"reviews"},
            "review": {"reviews"},
            "lead_capture": {"lead_capture"},
            "lead": {"lead_capture"},
            "support": {"support"},
        }
        return route_map.get(route_key, set())

    def _handoff_tool_keys(self, allowed_tool_keys: set[str]) -> set[str]:
        return {
            tool_key
            for tool_key in allowed_tool_keys
            if self._tools[tool_key].module_name == "human_handoff"
        }

    def _catalog_search_tool_keys(self, allowed_tool_keys: set[str]) -> set[str]:
        return {
            tool_key
            for tool_key in allowed_tool_keys
            if tool_key == "order.catalog_search"
        }
    def _build_llm_tools(
        self,
        context: ExecutionContext,
        allowed_tool_keys: Optional[set[str]],
    ) -> List[Dict[str, Any]]:
        config_data = context.config.config_data
        llm_tools = []
        for tool_key, tool_def in self._tools.items():
            if allowed_tool_keys is not None and tool_key not in allowed_tool_keys:
                continue
            if not self._is_config_visible_tool(context, tool_def):
                continue
            parameters = self._strict_tool_schema(tool_def.module.get_tool_schema(config_data))
            llm_tools.append({
                "type": "function",
                "function": {
                    "name": tool_def.llm_name,
                    "description": tool_def.description,
                    "parameters": parameters,
                }
            })
        return llm_tools
    @staticmethod
    def _is_config_visible_tool(context: ExecutionContext, tool_def: ToolDefinition) -> bool:
        if not tool_def.module:
            return False
        if not tool_def.module.is_llm_tool():
            return False
        module_config = context.config.config_data.get("modules", {}).get(tool_def.module_name, {})
        return module_config.get("enabled") is True
    async def ensure_allowed(self, tool_key: str, context: ExecutionContext) -> None:
        await self.guard.ensure_tool_allowed(context, tool_key)
    async def validate_tool_call(
        self,
        tool_key: str,
        context: ExecutionContext,
        args: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationDecision:
        from backend.capabilities.gateway import CapabilityGateway
        from backend.capabilities.models import CapabilityRequest

        tool = self.get_tool(tool_key)
        metadata = self._get_metadata(tool, context)

        request_args = args
        validation_payload = getattr(tool.module, "get_tool_validation_payload", None)
        if request_args is None and callable(validation_payload):
            request_args = validation_payload(context)
        request = CapabilityRequest.from_context(tool_key, context, request_args)
        gateway = CapabilityGateway(guard=self.guard)
        decision = await gateway.evaluate(request, context, tool, metadata)

        allowed = (decision.status == "allowed")
        policy = decision.policy

        if not allowed:
            denied_reason = decision.reason_code
            validation_errors = decision.audit_metadata.get("validation_errors")
            user_message = decision.user_message or "I can't complete that action right now."
            data = decision.audit_metadata if decision.audit_metadata else None

            result = ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=denied_reason,
                user_message=user_message,
                validation_errors=validation_errors,
                data=data
            )

            await self._write_audit(
                context,
                "tool_blocked",
                tool,
                {
                    "permission_decision": "blocked",
                    "reason": denied_reason,
                    "validation_errors": validation_errors,
                    **self._metadata_audit_details(metadata),
                },
            )
            return ToolValidationDecision(False, tool, metadata, policy=policy, result=result, reason=denied_reason)

        await self._write_audit(
            context,
            "tool_validation",
            tool,
            {
                "permission_decision": "allowed",
                **self._metadata_audit_details(metadata),
            },
        )
        return ToolValidationDecision(True, tool, metadata, policy=policy)
    async def execute_tool(self, tool_key: str, context: ExecutionContext) -> ToolExecutionResult:
        from backend.observability.latency import latency_metrics_var, now_ms, elapsed_ms
        start = now_ms()
        tool = None
        try:
            decision = await self.validate_tool_call(tool_key, context)
            tool = decision.tool
            metadata = decision.metadata
            policy = decision.policy
            if not decision.allowed:
                return decision.result or ToolExecutionResult(
                    tool_key=tool.key,
                    success=False,
                    denied_reason=decision.reason or "tool_not_allowed",
                    user_message="I can't complete that action right now.",
                )
            context.session_data.state.activate_task_step_data(tool.module_name)
            raw_result = await tool.execute(context)
            result = self._normalize_result(tool.key, raw_result)
            if result.success and (metadata.requires_confirmation or metadata.requires_verified_customer):
                context.session_data.state.action_ledger.pending_action = None
                context.session_data.state.action_ledger.task_status = "completed"
            elif not result.success:
                context.session_data.state.action_ledger.pending_action = None
            await self.guard.record_tool_execution(context.tenant.id, policy, tool.key, result.success)
            await self._write_audit(
                context,
                tool.audit_action,
                tool,
                {
                    "success": result.success,
                    "permission_decision": "allowed",
                    "created_resource_type": result.created_resource_type,
                    "created_resource_id": result.created_resource_id,
                    "denied_reason": result.denied_reason,
                    **self._metadata_audit_details(metadata),
                },
            )
            return result
        finally:
            cleanup = getattr(getattr(tool, "module", None), "cleanup_after_attempt", None)
            if callable(cleanup):
                try:
                    cleanup(context)
                except Exception as exc:
                    logger.warning(
                        "Tool attempt cleanup failed",
                        extra={"extra_data": {"tool_key": tool_key, "error_type": type(exc).__name__}},
                    )
            dur = elapsed_ms(start)
            metrics = latency_metrics_var.get()
            if metrics is not None:
                if metrics.get("tool_execution_ms") is None:
                    metrics["tool_execution_ms"] = 0
                metrics["tool_execution_ms"] += dur
    @staticmethod
    def _strict_tool_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        strict = dict(schema or {})
        strict.setdefault("type", "object")
        strict.setdefault("properties", {})
        strict.setdefault("required", [])
        strict["additionalProperties"] = False
        return strict
    def _context_denial(self, tool: ToolDefinition, context: ExecutionContext) -> Optional[ToolExecutionResult]:
        errors: Dict[str, str] = {}
        if not getattr(context, "tenant", None) or not getattr(context.tenant, "id", None):
            errors["tenant_id"] = "Trusted tenant context is missing."
        if not getattr(context, "user", None) or not getattr(context.user, "id", None):
            errors["customer_id"] = "Trusted customer context is missing."
        session = getattr(context, "session_data", None)
        if not session or not getattr(session, "id", None):
            errors["session_id"] = "Trusted session context is missing."
        if not session or not getattr(session, "conversation_id", None):
            errors["conversation_id"] = "Trusted conversation context is missing."
        config = getattr(context, "config", None)
        if not config or not getattr(config, "tenant_id", None):
            errors["config_tenant_id"] = "Trusted tenant config context is missing."
        tenant_id = getattr(getattr(context, "tenant", None), "id", None)
        if tenant_id and session and getattr(session, "tenant_id", None) != tenant_id:
            errors["session_tenant_id"] = "Conversation session does not belong to the trusted tenant."
        if tenant_id and config and getattr(config, "tenant_id", None) != tenant_id:
            errors["config_tenant_id"] = "Tenant config does not belong to the trusted tenant."
        incoming = context.incoming_message or {}
        for incoming_key, trusted_value in (
            ("tenant_id", tenant_id),
            ("customer_id", getattr(getattr(context, "user", None), "id", None)),
            ("conversation_id", getattr(session, "conversation_id", None) if session else None),
            ("session_id", getattr(session, "id", None) if session else None),
        ):
            if incoming.get(incoming_key) and trusted_value and str(incoming[incoming_key]) != str(trusted_value):
                errors[incoming_key] = f"Incoming message {incoming_key} conflicts with trusted server context."
        if errors:
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"context_validation_failed:{tool.module_name}",
                user_message="I can't complete that action because this chat context is not valid for it.",
                validation_errors=errors,
            )
        return None
    def _schema_denial(
        self,
        tool: ToolDefinition,
        context: ExecutionContext,
        args: Optional[Dict[str, Any]],
    ) -> Optional[ToolExecutionResult]:
        if not tool.module:
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"tool_not_executable:{tool.module_name}",
                user_message="That action is not available.",
            )
        payload = args if args is not None else context.session_data.state.get_task_step_data(tool.module_name)
        if not isinstance(payload, dict):
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"argument_validation_failed:{tool.module_name}",
                user_message="I need valid details before I can complete that action.",
                validation_errors={"arguments": "Tool arguments must be an object."},
            )
        schema = self._strict_tool_schema(tool.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = {str(field) for field in schema.get("required") or []}
        allowed = set(properties.keys())
        errors: Dict[str, str] = {}
        # Booking retains the resolver-provided service identity in durable
        # state, while its public schema deliberately exposes service_name.
        # The identity is consumed by the booking handler, not supplied by a
        # customer or provider tool call.
        internal_keys = {"service_id"} if tool.module_name == "booking" else set()
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
            if not self._matches_schema_type(value, expected_type):
                errors[key] = f"Expected {expected_type}."
                continue
            enum_values = field_schema.get("enum")
            if isinstance(enum_values, list) and enum_values and str(value) not in {str(item) for item in enum_values}:
                errors[key] = "Value is not one of the supported options."
        field_defs = {str(field.get("key")): field for field in tool.module.get_required_fields(context.config.config_data) if field.get("key")}
        for key, field_def in field_defs.items():
            if key not in payload:
                continue
            validation = tool.module.validate_input(field_def, payload.get(key), tenant_config=context.config.config_data)
            if not validation.is_valid:
                errors[key] = validation.error_message or "Invalid value."
        if errors:
            logger.warning(f"Argument validation failed for tool {tool.key}. Errors: {errors}. Payload: {payload}")
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"argument_validation_failed:{tool.module_name}",
                user_message="I need valid details before I can complete that action.",
                validation_errors=errors,
            )
        return None
    @staticmethod
    def _matches_schema_type(value: Any, expected_type: Any) -> bool:
        if isinstance(expected_type, list):
            return any(ToolRegistry._matches_schema_type(value, item) for item in expected_type)
        if expected_type == "integer":
            if isinstance(value, bool):
                return False
            try:
                return float(value).is_integer()
            except (TypeError, ValueError):
                return False
        if expected_type == "number":
            if isinstance(value, bool):
                return False
            try:
                float(value)
                return True
            except (TypeError, ValueError):
                return False
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "string":
            return isinstance(value, (str, int, float)) and not isinstance(value, bool)
        return True
    def _get_metadata(self, tool: ToolDefinition, context: ExecutionContext) -> ToolMetadata:
        if tool.module:
            return tool.module.get_tool_metadata(context.config.config_data)
        return ToolMetadata()
    @staticmethod
    def _metadata_audit_details(metadata: ToolMetadata) -> Dict[str, Any]:
        return {
            "risk_level": metadata.risk_level,
            "requires_confirmation": metadata.requires_confirmation,
            "requires_verified_customer": metadata.requires_verified_customer,
            "supported_channels": list(metadata.supported_channels),
            "idempotency_scope": metadata.idempotency_scope,
            "allowed_when_paused": metadata.allowed_when_paused,
        }
    def _metadata_denial(
        self,
        tool: ToolDefinition,
        metadata: ToolMetadata,
        context: ExecutionContext,
    ) -> Optional[ToolExecutionResult]:
        module_name = tool.module_name
        channel_type = self._context_channel_type(context)
        supported_channels = {str(channel).lower() for channel in metadata.supported_channels}
        if supported_channels and channel_type not in supported_channels:
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"channel_not_supported:{module_name}:{channel_type}",
                user_message="This action is not available in this chat channel.",
            )
        if (
            context.session_data.state.workflow_state == ConversationState.ESCALATED
            and not metadata.allowed_when_paused
        ):
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"chat_paused:{module_name}",
                user_message="This chat is paused for human support. A person can continue from here.",
            )
        if metadata.requires_confirmation and not self._has_confirmed_pending_action(tool, context):
            current_args = self._tool_action_args(tool, context)
            pending = context.session_data.state.prepare_pending_action(
                tool_key=tool.key,
                args=current_args,
                summary=self._summarize_pending_action(tool, current_args),
                requires_confirmation=True,
                requires_verified_customer=metadata.requires_verified_customer,
                verification_action=f"{module_name}_commitment",
                required_assurance=(required_assurance(tool.key, requires_verified_customer=True).value if metadata.requires_verified_customer else "none"),
                assurance_policy_source=policy_source(tool.key),
                verification_scope="session_phone_possession",
                channel_type=channel_type,
            )
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"confirmation_required:{module_name}",
                user_message=(
                    f"{pending.summary or 'Please review the details.'}\n\n"
                    "Please confirm before I complete this action. [Confirm] [Change] [Cancel]"
                ),
                data={
                    "pending_action": pending.model_dump(mode="json", exclude_none=True),
                    "required_next_step": "confirm",
                },
            )
        if metadata.requires_verified_customer and not self._has_verified_customer(tool, context, True):
            verification_message = (
                "Please verify your phone number in this browser chat before I complete this action."
                if channel_type == "awa_chat"
                else (
                    "This action needs customer verification before it can be completed. "
                    "Please continue with the business team or use a verified browser chat link."
                )
            )
            return ToolExecutionResult(
                tool_key=tool.key,
                success=False,
                denied_reason=f"customer_verification_required:{module_name}",
                user_message=verification_message,
                data={
                    "required_next_step": "customer_verification",
                    "verification_action": f"{module_name}_commitment",
                    "channel": channel_type,
                    "verification_available_in_channel": channel_type == "awa_chat",
                },
            )
        return None
    @staticmethod
    def _context_channel_type(context: ExecutionContext) -> str:
        channel = (context.incoming_message or {}).get("channel_type")
        if not channel and context.session_data and context.session_data.state and context.session_data.state.action_ledger:
            channel = context.session_data.state.action_ledger.channel
        return str(channel or "whatsapp").lower()
    @staticmethod
    def _current_action_args(context: ExecutionContext) -> Dict[str, Any]:
        task_key = context.session_data.state.action_ledger.current_task
        return context.session_data.state.get_task_step_data(task_key)
    @staticmethod
    def _tool_action_args(tool: ToolDefinition, context: ExecutionContext) -> Dict[str, Any]:
        return context.session_data.state.get_task_step_data(tool.module_name)
    @staticmethod
    def _is_customer_confirmation(context: ExecutionContext) -> bool:
        return False
    @staticmethod
    def _summarize_pending_action(tool: ToolDefinition, args: Dict[str, Any]) -> str:
        module_title = tool.module_name.replace("_", " ").title()
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
    @staticmethod
    def _has_confirmed_pending_action(tool: ToolDefinition, context: ExecutionContext) -> bool:
        ledger = context.session_data.state.action_ledger
        pending = ledger.pending_action
        if not pending or pending.tool_key != tool.key:
            return False
        if not context.session_data.state.pending_action_matches(tool.key, ToolRegistry._tool_action_args(tool, context)):
            return False
        if pending.requires_confirmation is False:
            return True
        return ledger.task_status in {"confirmed", "executing"}
    @staticmethod
    def _has_verified_customer(tool: ToolDefinition, context: ExecutionContext, requires_verified_customer: bool = False) -> bool:
        return has_required_assurance(
            context, tool.key, requires_verified_customer=requires_verified_customer
        )
    @staticmethod
    def _normalize_result(tool_key: str, result: ToolModuleResult) -> ToolExecutionResult:
        if isinstance(result, ToolExecutionResult):
            if result.tool_key == tool_key:
                return result
            return ToolExecutionResult(
                tool_key=tool_key,
                success=result.success,
                denied_reason=result.denied_reason,
                user_message=result.user_message,
                data=result.data,
                validation_errors=result.validation_errors,
                created_resource_type=result.created_resource_type,
                created_resource_id=result.created_resource_id,
            )
        return ToolExecutionResult(tool_key=tool_key, success=bool(result))
    async def _write_audit(self, context: ExecutionContext, action: str, tool: ToolDefinition, details: dict) -> None:
        try:
            from backend.db.client import db_client
            await db_client.write_audit_log(
                tenant_id=context.tenant.id,
                actor_id=context.user.id,
                action=action,
                resource_type="tool",
                details={"tool_key": tool.key, "module_name": tool.module_name, **details},
            )
        except Exception as e:
            logger.warning(f"Tool audit write failed for {tool.key}: {e}")
