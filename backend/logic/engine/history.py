import json
import re
from typing import List, Any
from langchain_core.messages import SystemMessage, messages_to_dict
from backend.ops.logger import logger
from backend.db.client import db_client
from backend.core.context import ExecutionContext
from backend.ai.security import sanitize_untrusted_text
from backend.ai.providers.gemini_chat_adapter import sanitize_gemini_additional_kwargs
HISTORY_LIMIT = 15
MAX_HISTORY_CHARS = 2000
MEDIUM_HISTORY_LIMIT = 8
MEDIUM_HISTORY_CHARS = 1400
SMALL_HISTORY_LIMIT = 4
SMALL_HISTORY_CHARS = 800
AGENTIC_HISTORY_LIMIT = 10
AGENTIC_HISTORY_MESSAGE_MAX_CHARS = 900
AGENTIC_HISTORY_TOTAL_MAX_CHARS = 4000
AGENTIC_ALLOWED_HISTORY_TYPES = {"human", "ai", "tool"}
FULL_HISTORY_STATUSES = {
    "awaiting_confirmation",
    "confirmed",
    "executing",
    "awaiting_verification",
    "pending_verification",
    "awaiting_payment",
    "pending_payment",
}
def sanitize_tool_calls(tool_calls: List[Any]) -> List[dict]:
    clean_calls: List[dict] = []
    for call in tool_calls[:3]:
        if not isinstance(call, dict):
            continue
        args = call.get("args")
        if not isinstance(args, dict):
            args = {}
        clean_args = {
            str(key)[:80]: sanitize_untrusted_text(value, max_chars=200)
            for key, value in args.items()
        }
        clean_calls.append({
            "name": str(call.get("name") or "")[:80],
            "args": clean_args,
            "id": str(call.get("id") or "")[:120],
            "type": str(call.get("type") or "tool_call")[:40],
        })
    return clean_calls
def sanitize_agentic_history_dicts(history: List[Any]) -> List[dict]:
    """Drop unsafe/oversized history entries before LangChain reload or DB persistence."""
    if not isinstance(history, list):
        return []
    sanitized: List[dict] = []
    total_chars = 0
    unmatched_tool_ids = set()
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        msg_type = item.get("type")
        if msg_type not in AGENTIC_ALLOWED_HISTORY_TYPES:
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        content = data.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        elif content is None:
            content = ""
        else:
            content = str(content)
        content = sanitize_untrusted_text(content, max_chars=AGENTIC_HISTORY_MESSAGE_MAX_CHARS)
        entry = {
            "type": msg_type,
            "data": {
                "content": content,
            },
        }
        if msg_type == "tool":
            tool_call_id = data.get("tool_call_id")
            entry["data"]["tool_call_id"] = str(tool_call_id or "tool_call")
        if msg_type == "ai":
            tool_calls = data.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                entry["data"]["tool_calls"] = sanitize_tool_calls(tool_calls)
                provider_kwargs = sanitize_gemini_additional_kwargs(
                    data.get("additional_kwargs"),
                    allowed_call_ids=[call.get("id", "") for call in entry["data"]["tool_calls"]],
                )
                if provider_kwargs:
                    entry["data"]["additional_kwargs"] = provider_kwargs

        entry_size = len(json.dumps(entry, ensure_ascii=True, default=str))
        if not unmatched_tool_ids:
            if sanitized and total_chars + entry_size > AGENTIC_HISTORY_TOTAL_MAX_CHARS:
                break
            if len(sanitized) >= AGENTIC_HISTORY_LIMIT:
                break

        if msg_type == "tool":
            unmatched_tool_ids.add(entry["data"]["tool_call_id"])
        elif msg_type == "ai":
            tcs = entry["data"].get("tool_calls", [])
            for tc in tcs:
                tc_id = tc.get("id")
                if tc_id in unmatched_tool_ids:
                    unmatched_tool_ids.remove(tc_id)

        sanitized.append(entry)
        total_chars += entry_size
    sanitized.reverse()
    return sanitized
def serialize_agentic_history(messages: List[Any]) -> List[dict]:
    """Persist only bounded non-system history in session state."""
    history_messages = [
        message for message in messages
        if not isinstance(message, SystemMessage)
    ]
    return sanitize_agentic_history_dicts(messages_to_dict(history_messages))
def format_step_data_text(step_data: dict) -> str:
    if not isinstance(step_data, dict) or not step_data:
        return "None"

    # Allowlist of only user-facing keys
    allowed_keys = {
        "item_name", "quantity", "fulfillment_type", "customer_name", "phone", "delivery_address",
        "item_sku", "items", "notes", "payment_method",
        "service_name", "preferred_date", "preferred_time",
        "action", "new_date", "new_time", "amount", "reminder_type", "time_offset", "message_text", "rating", "feedback"
    }

    lines = []
    # Sort keys for deterministic output in tests
    for key in sorted(step_data.keys()):
        # Backend-only internal reference — never expose to LLM or customer
        if key == "_backend_refs":
            continue

        val = step_data[key]
        if val in (None, "", {}):
            continue

        # Special handling for pending_disambiguation:
        # render only the public options list (index + name + optional price),
        # never _backend_refs which contain internal item dicts and UUIDs.
        if key == "pending_disambiguation":
            if not isinstance(val, dict):
                continue
            options = val.get("options") or []
            if not options:
                continue
            option_lines = ["Pending item clarification:"]
            for opt in options:
                name = str(opt.get("name") or "")
                idx = opt.get("index", "?")
                price = opt.get("price")
                price_str = f" ({price})" if price is not None else ""
                option_lines.append(f"  {idx}. {name}{price_str}")
            lines.append("\n".join(option_lines))
            continue

        # Recursive formatting for nested module dicts
        if isinstance(val, dict):
            formatted_val = format_step_data_text(val)
            if formatted_val and formatted_val != "None":
                indented = "\n".join(f"  {line}" for line in formatted_val.splitlines())
                lines.append(f"- {key}:\n{indented}")
            continue

        if key in allowed_keys:
            # Nested items in order modules
            if key == "items" and isinstance(val, list):
                item_lines = []
                for item in val:
                    if isinstance(item, dict):
                        parts = []
                        for ik, iv in sorted(item.items()):
                            if ik in allowed_keys:
                                parts.append(f"{ik}={iv}")
                        if parts:
                            item_lines.append("{" + ", ".join(parts) + "}")
                if item_lines:
                    lines.append(f"- items: {', '.join(item_lines)}")
                continue

            val_str = str(val)
            # Remove any UUID patterns if they somehow end up in user-facing fields
            if re.search(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", val_str):
                continue
            lines.append(f"- {key}: {val_str}")

    return "\n".join(lines) if lines else "None"



def format_action_ledger_context(context: ExecutionContext) -> str:
    """Format the durable action ledger for prompt awareness using a compact text-only layout."""
    ledger = getattr(context.session_data.state, "action_ledger", None)
    if not ledger:
        return "No active task."
    try:
        lines = []
        if ledger.current_task:
            lines.append(f"Current Task: {ledger.current_task}")
        if ledger.task_status:
            lines.append(f"Task Status: {ledger.task_status}")
        if ledger.channel:
            lines.append(f"Channel: {ledger.channel}")

        prompt_focus = getattr(context.session_data.state, "prompt_focus", None)
        if prompt_focus:
            lines.append(f"Requested Workflow: {prompt_focus.workflow}")
            lines.append(f"Requested Fields: {', '.join(prompt_focus.requested_fields)}")
            lines.append(f"Prompt Kind: {prompt_focus.prompt_kind}")

        pending = getattr(ledger, "pending_action", None)
        if pending:
            lines.append("Pending Action:")
            lines.append(f"  Action key: {pending.tool_key}")
            # Ensure the summary itself has no raw UUIDs
            summary_clean = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "[redacted]", pending.summary or "")
            lines.append(f"  Summary: {summary_clean}")
            lines.append(f"  Requires Confirmation: {pending.requires_confirmation}")
            lines.append(f"  Requires Verified Customer: {pending.requires_verified_customer}")
            if pending.verification_action:
                lines.append(f"  Verification Action: {pending.verification_action}")

        last_result = getattr(ledger, "last_tool_result", None)
        if last_result:
            lines.append("Last Tool Result:")
            lines.append(f"  Last action: {last_result.tool_key}")
            lines.append(f"  Success: {last_result.success}")
            if last_result.denied_reason:
                lines.append(f"  Denied Reason: {last_result.denied_reason}")
            if last_result.user_message:
                lines.append(f"  User Message: {last_result.user_message}")
            if last_result.created_resource_type:
                lines.append(f"  Created Resource Type: {last_result.created_resource_type}")
            if last_result.created_resource_id:
                res_id = last_result.created_resource_id
                # Only explicitly public, non-UUID references may appear. If it matches a UUID pattern, do not expose.
                if re.search(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", res_id):
                    pass
                else:
                    lines.append(f"  Created Resource ID: {res_id}")
            if last_result.validation_errors:
                err_text = ", ".join(f"{k}: {v}" for k, v in last_result.validation_errors.items())
                lines.append(f"  Validation Errors: {err_text}")
            rns = (last_result.data or {}).get("required_next_step") if isinstance(last_result.data, dict) else None
            if rns:
                lines.append(f"  Required Next Step: {rns}")
            vact = (last_result.data or {}).get("verification_action") if isinstance(last_result.data, dict) else None
            if vact:
                lines.append(f"  Verification Action: {vact}")

        last_error = getattr(ledger, "last_error", None)
        if last_error:
            lines.append("Last Error:")
            lines.append(f"  Code: {last_error.code}")
            lines.append(f"  Safe Message: {last_error.safe_message}")

        ledger_text = "\n".join(lines)
    except Exception as exc:
        logger.warning(f"Error formatting action ledger text: {exc}")
        return "No active task."

    if len(ledger_text) > 1800:
        ledger_text = ledger_text[:1800] + "...[truncated]"
    return sanitize_untrusted_text(ledger_text, max_chars=2000)


def _route_value(routing_decision: Any, field_name: str) -> str:
    return str(getattr(routing_decision, field_name, "") or "").strip().lower()


def history_limits_for_context(
    context: ExecutionContext,
    routing_decision: Any = None,
) -> tuple[int, int, str]:
    """Pick conservative memory limits from safe routing and ledger state."""
    state = getattr(context.session_data, "state", None)
    ledger = getattr(state, "action_ledger", None) if state else None
    current_task = (
        getattr(ledger, "current_task", None)
        or getattr(context.session_data, "current_module", None)
        or ""
    )
    task_status = str(getattr(ledger, "task_status", "") or "").strip().lower() if ledger else ""
    if (
        current_task
        or getattr(context.session_data, "current_module", None)
        or getattr(ledger, "pending_action", None)
        or getattr(ledger, "last_tool_result", None)
        or getattr(ledger, "last_error", None)
        or task_status in FULL_HISTORY_STATUSES
        or getattr(state, "step_data", None)
        or getattr(state, "scoped_step_data", None)
    ):
        return HISTORY_LIMIT, MAX_HISTORY_CHARS, "active_workflow_full"

    intent = _route_value(routing_decision, "intent")
    route_type = _route_value(routing_decision, "route_type")
    is_tangent = bool(getattr(routing_decision, "is_tangent", False)) if routing_decision else False
    if is_tangent or intent in {"faq", "support", "unknown", "ambiguous"} or route_type in {"unknown", "clarification"}:
        return MEDIUM_HISTORY_LIMIT, MEDIUM_HISTORY_CHARS, "medium_route"
    if intent in {"greeting", "thanks", "cancel"} or route_type in {"direct_answer", "direct_action"}:
        return SMALL_HISTORY_LIMIT, SMALL_HISTORY_CHARS, "simple_route_small"
    return MEDIUM_HISTORY_LIMIT, MEDIUM_HISTORY_CHARS, "default_medium"


async def build_memory_context(context: ExecutionContext, routing_decision: Any = None) -> str:
    """Build compact recent-history context for the concierge prompt."""
    history_limit, max_history_chars, _history_policy = history_limits_for_context(context, routing_decision)
    try:
        result = await db_client.list_messages(
            context.tenant.id,
            context.session_data.conversation_id,
            limit=history_limit,
        )
        messages = list(reversed(result.get("items", [])))
    except Exception as exc:
        logger.warning(f"Failed to load concierge conversation memory: {exc}")
        messages = []
    lines = []
    incoming_id = str((context.incoming_message or {}).get("message_id") or "")
    for item in messages:
        raw_payload = item.get("raw_payload")
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                raw_payload = {}
        raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
        is_orientation = (
            raw_payload.get("include_in_semantic_history") is False
            or raw_payload.get("semantic_role") == "orientation"
            or raw_payload.get("source") == "greeting"
        )
        if is_orientation:
            logger.debug("Excluded orientation from semantic history", extra={"extra_data": {"reason_code": "orientation_excluded_from_history"}})
            continue
        if incoming_id and str(item.get("id") or "") == incoming_id:
            continue
        if str(item.get("provider_message_id") or "").startswith(
            "awa-internal:otp_verified_pending_action:"
        ):
            continue
        text = str(item.get("message_text") or "").strip()
        if not text:
            continue
        text = sanitize_untrusted_text(text, max_chars=500)
        role = "Customer" if item.get("direction") == "inbound" else "Assistant"
        lines.append(f"{role}: {text}")
    history = "\n".join(lines)
    if len(history) > max_history_chars:
        history = history[-max_history_chars:].lstrip()
    collected = context.session_data.state.step_data or {}
    scoped_collected = context.session_data.state.scoped_step_data or {}
    collected_text = format_step_data_text(collected)
    scoped_text = format_step_data_text(scoped_collected)
    action_ledger_text = format_action_ledger_context(context)
    return (
        "Session variables:\n"
        f"{collected_text}\n\n"
        "Active task variables:\n"
        f"{scoped_text}\n\n"
        "Action status:\n"
        f"{action_ledger_text}\n\n"
        "Recent conversation, oldest to newest:\n"
        f"{history if history else '(no prior messages)'}"
    )
