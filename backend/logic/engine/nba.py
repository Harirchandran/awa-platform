from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Optional

from backend.core.context import ExecutionContext
from backend.core.states import ConversationState
from backend.domains.reminders.tool_definitions import get_concierge_reminder_channels
from backend.logic.engine.capability_menu import has_order_catalog, rank_suggestions_by_behavior_profile
from backend.pipeline.interactive_parser import sanitize_quick_replies

MAX_NEXT_BEST_ACTIONS = 3
PAYMENT_ALREADY_REQUESTED_STATUSES = {"sent", "paid", "completed", "success", "successful", "settled"}
BOOKING_REMINDER_OFFER_TEXT = "Would you like me to set a reminder for this booking?"
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
REMINDER_QUESTION_PATTERN = re.compile(
    r"(?:^|[.!]\s+)[^.!?]*\bremind(?:er|ers)?\b[^.!?]*\?",
    re.IGNORECASE,
)


async def append_next_best_actions(
    context: ExecutionContext,
    workflow_result: Any,
    tool_registry: Any = None,
) -> Any:
    """Append safe NBA quick replies to an existing WorkflowResult-like object."""
    suggestions = await generate_next_best_actions(
        context,
        workflow_result=workflow_result,
        tool_registry=tool_registry,
    )
    if not suggestions:
        return workflow_result

    existing = sanitize_quick_replies(list(getattr(workflow_result, "quick_replies", []) or []))
    merged = _merge_quick_replies(existing, suggestions)
    workflow_result.quick_replies = merged
    return ensure_booking_reminder_offer(context, workflow_result)


async def generate_next_best_actions(
    context: ExecutionContext,
    workflow_result: Any = None,
    tool_registry: Any = None,
) -> list[str]:
    """
    Return deterministic, customer-facing next-step suggestions for the latest
    backend-confirmed milestone. Suggestions are labels only; they never execute actions.
    """
    ledger = getattr(getattr(context.session_data, "state", None), "action_ledger", None)
    if not ledger:
        return []
    last_result = getattr(ledger, "last_tool_result", None)
    if not last_result:
        return []

    existing = sanitize_quick_replies(list(getattr(workflow_result, "quick_replies", []) or []))
    visibility = _CapabilityVisibility(context, tool_registry)
    milestone = _milestone_key(context, last_result)
    if not milestone:
        return []

    suggestions: list[str]
    if _is_escalated(context, ledger, last_result):
        suggestions = await _handoff_suggestions(visibility)
    elif getattr(last_result, "success", None) is not True:
        suggestions = []
    elif milestone == "booking":
        suggestions = await _booking_suggestions(context, visibility)
    elif milestone == "order":
        suggestions = await _order_suggestions(context, last_result, visibility)
    elif milestone == "payment":
        suggestions = await _payment_suggestions(visibility)
    elif milestone == "reminder":
        suggestions = await _reminder_suggestions(visibility)
    elif milestone == "review":
        suggestions = await _review_suggestions(visibility)
    elif milestone == "lead_capture":
        suggestions = await _lead_capture_suggestions(context, visibility)
    else:
        suggestions = []

    try:
        suggestions = rank_suggestions_by_behavior_profile(
            suggestions,
            getattr(context, "runtime_behavior_profile", None),
        )
    except Exception:
        pass
    safe = _sanitize_nba_suggestions(suggestions)
    safe = [option for option in safe if option.lower() not in {item.lower() for item in existing}]
    safe = _filter_repeated_suggestions(ledger, last_result, milestone, safe)
    return safe[:MAX_NEXT_BEST_ACTIONS]


async def _booking_suggestions(context: ExecutionContext, visibility: "_CapabilityVisibility") -> list[str]:
    suggestions: list[str] = []
    if await visibility.tool_visible("booking.update"):
        suggestions.extend(["Reschedule", "Cancel booking"])
    if await visibility.module_visible("reminders") and _has_deliverable_reminder_channel(context):
        suggestions.append("Set reminder")
    if await visibility.module_visible("reviews"):
        suggestions.append("Leave feedback")
    if await visibility.support_visible():
        suggestions.append("Contact support")
    if _has_business_location(context.config.config_data):
        suggestions.append("Get directions")
    return suggestions


async def _order_suggestions(
    context: ExecutionContext,
    last_result: Any,
    visibility: "_CapabilityVisibility",
) -> list[str]:
    suggestions: list[str] = []
    payment_status = str((getattr(last_result, "data", None) or {}).get("payment_status") or "").strip().lower()
    if payment_status not in PAYMENT_ALREADY_REQUESTED_STATUSES and await visibility.module_visible("payments"):
        suggestions.append("Make payment")
    if await visibility.support_visible():
        suggestions.append("Contact support")
    if await visibility.module_visible("order") and has_order_catalog(context.config.config_data):
        suggestions.append("View menu")
    if await visibility.module_visible("reminders") and _has_deliverable_reminder_channel(context):
        suggestions.append("Set reminder")
    if await visibility.module_visible("reviews"):
        suggestions.append("Leave feedback")
    return suggestions


async def _payment_suggestions(visibility: "_CapabilityVisibility") -> list[str]:
    suggestions: list[str] = []
    if await visibility.support_visible():
        suggestions.append("Contact support")
    return suggestions


async def _reminder_suggestions(visibility: "_CapabilityVisibility") -> list[str]:
    suggestions = ["Continue"]
    if await visibility.support_visible():
        suggestions.append("Contact support")
    return suggestions


async def _review_suggestions(visibility: "_CapabilityVisibility") -> list[str]:
    suggestions = ["Thank you"]
    if await visibility.support_visible():
        suggestions.append("Contact support")
    if await visibility.module_visible("booking"):
        suggestions.append("Book again")
    return suggestions


async def _handoff_suggestions(visibility: "_CapabilityVisibility") -> list[str]:
    if not await visibility.support_visible(allow_paused=True):
        return ["Wait for agent", "Add more details"]
    return ["Wait for agent", "Add more details"]


async def _lead_capture_suggestions(context: ExecutionContext, visibility: "_CapabilityVisibility") -> list[str]:
    suggestions = ["Ask a question"]
    if await visibility.module_visible("order") and has_order_catalog(context.config.config_data):
        suggestions.append("View menu")
    elif await visibility.module_visible("booking") or context.config.config_data.get("services"):
        suggestions.append("View services")
    if await visibility.support_visible():
        suggestions.append("Contact support")
    return suggestions


def _milestone_key(context: ExecutionContext, last_result: Any) -> Optional[str]:
    tool_key = str(getattr(last_result, "tool_key", "") or "")
    module = tool_key.split(".", 1)[0]
    resource_type = str(getattr(last_result, "created_resource_type", "") or "")
    if _is_escalated(context, getattr(context.session_data.state, "action_ledger", None), last_result):
        return "handoff"
    if resource_type == "booking" or tool_key == "booking.execute":
        return "booking"
    if resource_type == "order" or tool_key == "order.execute":
        return "order"
    if resource_type == "payment_request" or module == "payments":
        return "payment"
    if resource_type == "scheduled_message" or module == "reminders":
        return "reminder"
    if resource_type == "review_request" or module == "reviews":
        return "review"
    if resource_type == "lead" or module == "lead_capture":
        return "lead_capture"
    if resource_type == "conversation_escalation" or module in {"support", "human_handoff"}:
        return "handoff"
    return None


def _should_offer_booking_reminder(context: ExecutionContext, quick_replies: Iterable[str]) -> bool:
    """Add prose only for a persisted booking with a visible reminder action."""
    ledger = getattr(getattr(context.session_data, "state", None), "action_ledger", None)
    last_result = getattr(ledger, "last_tool_result", None)
    if not last_result or getattr(last_result, "success", None) is not True:
        return False
    if str(getattr(last_result, "created_resource_type", "") or "") != "booking":
        return False
    if not str(getattr(last_result, "created_resource_id", "") or "").strip():
        return False
    return any(str(reply).strip().casefold() == "set reminder" for reply in quick_replies)


def ensure_booking_reminder_offer(context: ExecutionContext, workflow_result: Any) -> Any:
    """Restore the booking reminder question if a wording layer omits it."""
    quick_replies = list(getattr(workflow_result, "quick_replies", []) or [])
    if not _should_offer_booking_reminder(context, quick_replies):
        return workflow_result
    response_text = str(getattr(workflow_result, "response_text", "") or "").strip()
    if REMINDER_QUESTION_PATTERN.search(response_text):
        return workflow_result
    workflow_result.response_text = (
        f"{response_text}\n\n{BOOKING_REMINDER_OFFER_TEXT}"
        if response_text
        else BOOKING_REMINDER_OFFER_TEXT
    )
    return workflow_result


def _is_escalated(context: ExecutionContext, ledger: Any, last_result: Any) -> bool:
    if context.session_data.state.workflow_state == ConversationState.ESCALATED:
        return True
    if str(getattr(ledger, "task_status", "") or "").lower() == "escalated":
        return True
    resource_type = str(getattr(last_result, "created_resource_type", "") or "")
    tool_key = str(getattr(last_result, "tool_key", "") or "")
    return resource_type == "conversation_escalation" or tool_key in {"support.execute", "human_handoff.execute"}


def _has_business_location(config: dict[str, Any]) -> bool:
    for key in ("business_location", "location", "address", "business_address"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict) and any(str(item or "").strip() for item in value.values()):
            return True
    return False


def _has_deliverable_reminder_channel(context: ExecutionContext) -> bool:
    try:
        return bool(get_concierge_reminder_channels(context.config.config_data))
    except Exception:
        return False


def _merge_quick_replies(existing: list[str], suggestions: Iterable[str]) -> list[str]:
    merged = list(existing)
    seen = {option.lower() for option in merged}
    for suggestion in suggestions:
        if len(merged) >= MAX_NEXT_BEST_ACTIONS:
            break
        key = suggestion.lower()
        if key in seen:
            continue
        merged.append(suggestion)
        seen.add(key)
    return sanitize_quick_replies(merged, max_options=MAX_NEXT_BEST_ACTIONS)


def _sanitize_nba_suggestions(suggestions: Iterable[str]) -> list[str]:
    candidates = []
    for suggestion in suggestions:
        label = str(suggestion or "").strip()
        if not label or UUID_PATTERN.search(label):
            continue
        if any(term in label.lower() for term in ("uuid", "tool", "schema", "id:")):
            continue
        candidates.append(label)
    return sanitize_quick_replies(candidates, max_options=MAX_NEXT_BEST_ACTIONS)


def _filter_repeated_suggestions(ledger: Any, last_result: Any, milestone: str, suggestions: list[str]) -> list[str]:
    if not suggestions:
        return []
    signature = _signature_for(last_result, milestone, suggestions)
    if getattr(ledger, "last_nba_signature", None) == signature:
        return []
    ledger.last_nba_signature = signature
    ledger.last_nba_suggestions = list(suggestions)
    return suggestions


def _signature_for(last_result: Any, milestone: str, suggestions: list[str]) -> str:
    data = getattr(last_result, "data", None) or {}
    safe_data = {}
    if isinstance(data, dict):
        for key in ("status", "payment_status", "fulfillment_type", "rating"):
            if key in data:
                safe_data[key] = data.get(key)
    payload = {
        "milestone": milestone,
        "tool_key": getattr(last_result, "tool_key", None),
        "success": getattr(last_result, "success", None),
        "resource_type": getattr(last_result, "created_resource_type", None),
        "data": safe_data,
        "suggestions": suggestions,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class _CapabilityVisibility:
    def __init__(self, context: ExecutionContext, tool_registry: Any = None):
        self.context = context
        self.tool_registry = tool_registry
        self._cache: dict[str, bool] = {}

    async def support_visible(self, *, allow_paused: bool = False) -> bool:
        return (
            await self.module_visible("human_handoff", allow_paused=allow_paused)
            or await self.module_visible("support", allow_paused=allow_paused)
        )

    async def module_visible(self, module_name: str, *, allow_paused: bool = False) -> bool:
        tool_key = self._tool_key_for_module(module_name)
        if tool_key:
            return await self.tool_visible(tool_key, allow_paused=allow_paused)
        return self._module_config_enabled(module_name)

    async def tool_visible(self, tool_key: str, *, allow_paused: bool = False) -> bool:
        cache_key = f"{tool_key}:{allow_paused}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        visible = await self._tool_visible_uncached(tool_key, allow_paused=allow_paused)
        self._cache[cache_key] = visible
        return visible

    async def _tool_visible_uncached(self, tool_key: str, *, allow_paused: bool) -> bool:
        if not self.tool_registry:
            module_name = self._module_from_tool_key(tool_key)
            return self._module_config_enabled(module_name)
        try:
            tool_def = self.tool_registry.get_tool(tool_key)
        except Exception:
            return False
        if not self.tool_registry._is_config_visible_tool(self.context, tool_def):
            return False
        try:
            denial = await self.tool_registry.guard.get_tool_visibility_denial(self.context, tool_key)
        except Exception:
            return False
        if denial:
            return False
        metadata = self.tool_registry._get_metadata(tool_def, self.context)
        channel_type = self.tool_registry._context_channel_type(self.context)
        supported_channels = {str(channel).lower() for channel in metadata.supported_channels}
        if supported_channels and channel_type not in supported_channels:
            return False
        if (
            self.context.session_data.state.workflow_state == ConversationState.ESCALATED
            and not (allow_paused or metadata.allowed_when_paused)
        ):
            return False
        return True

    def _tool_key_for_module(self, module_name: str) -> Optional[str]:
        if self.tool_registry:
            try:
                tool_key = self.tool_registry.get_tool_key_for_module(module_name)
                if tool_key:
                    return tool_key
            except Exception:
                pass
        if module_name == "booking_update":
            return "booking.update"
        return f"{module_name}.execute"

    @staticmethod
    def _module_from_tool_key(tool_key: str) -> str:
        if tool_key == "booking.update":
            return "booking"
        return str(tool_key or "").split(".", 1)[0]

    def _module_config_enabled(self, module_name: str) -> bool:
        incoming = self.context.incoming_message or {}
        runtime_modules = incoming.get("_runtime_available_modules")
        if isinstance(runtime_modules, (list, tuple, set, frozenset)):
            return str(module_name) in {str(module) for module in runtime_modules}
        config = self.context.config.config_data if self.context and self.context.config else {}
        modules = config.get("modules", {}) if isinstance(config, dict) else {}
        module_config = modules.get(module_name, {}) if isinstance(modules, dict) else {}
        return isinstance(module_config, dict) and module_config.get("enabled") is True
