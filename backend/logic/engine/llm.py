import json
import os
import re
from time import perf_counter
from backend.core.config import settings
from backend.ai.providers.base import normalize_llm_usage
from backend.core.context import ExecutionContext
from backend.ops.logger import logger
from backend.observability.llm_logging import (
    current_safe_trace_id,
    emit_legacy_llm_trace,
    emit_safe_llm_log,
    safe_identifier,
    safe_llm_error_code,
)

FAKE_PROVIDER_NAMES = {"deterministic_fake", "fake_tool", "fake", "mock", "deterministic"}
REAL_PROVIDER_NAMES = {"groq", "openai", "gemini"}


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_real_provider_required() -> bool:
    return _truthy(os.getenv("AWA_REAL_PROVIDER_REQUIRED")) or _truthy(
        getattr(settings, "AWA_REAL_PROVIDER_REQUIRED", False)
    )


def is_fake_provider(provider: str | None) -> bool:
    return (provider or "").strip().lower() in FAKE_PROVIDER_NAMES


def _safe_getattr(obj, name: str, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _tool_count(tools: list | None) -> int:
    count = 0
    for tool in tools or []:
        function_def = tool.get("function", {}) if isinstance(tool, dict) else {}
        if function_def.get("name"):
            count += 1
    return min(count, 64)


def _count_history_messages(memory_context: str | None) -> int:
    text = memory_context or ""
    return sum(
        1
        for line in text.splitlines()
        if line.startswith("Customer:") or line.startswith("Assistant:")
    )


def _count_rag_chunks(knowledge_context: str | None) -> int:
    text = knowledge_context or ""
    if not text.strip():
        return 0
    count = text.count("Knowledge reference")
    return count if count else 1


def _count_catalog_items(context: ExecutionContext | None) -> int:
    try:
        config = _safe_getattr(context, "config")
        config_data = _safe_getattr(config, "config_data", {}) or {}
        modules = config_data.get("modules", {})
        order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
        categories = order_config.get("categories", []) if isinstance(order_config, dict) else []
        total = 0
        for category in categories or []:
            if isinstance(category, dict):
                total += len(category.get("items") or [])
        return total
    except Exception:
        return 0


def _ledger_summary_chars(context: ExecutionContext | None) -> int:
    try:
        session_data = _safe_getattr(context, "session_data")
        state = _safe_getattr(session_data, "state")
        ledger = _safe_getattr(state, "action_ledger")
        if ledger is None:
            return 0
        summary = {
            "current_task": ledger.current_task,
            "task_status": ledger.task_status,
            "channel": ledger.channel,
            "pending_tool": ledger.pending_action.tool_key if ledger.pending_action else None,
            "last_result_tool": ledger.last_tool_result.tool_key if ledger.last_tool_result else None,
            "last_error_code": ledger.last_error.code if ledger.last_error else None,
        }
        return len(json.dumps({k: v for k, v in summary.items() if v}, ensure_ascii=True))
    except Exception:
        return 0


def build_llm_input_trace(
    *,
    context: ExecutionContext | None,
    provider: str | None,
    model: str | None = None,
    integration_mode: str | None = None,
    system_instructions: str | None = None,
    tools: list | None = None,
    memory_context: str | None = None,
    knowledge_context: str | None = None,
    latency_ms: int = 0,
    fallback_used: bool = False,
    provider_failure: dict | None = None,
    final_output_type: str = "text",
    route_intent: str | None = None,
    route_type: str | None = None,
    rag_skipped: bool = False,
    rag_skip_reason: str | None = None,
    direct_path_bypassed_llm: bool = False,
    scoped_tools_used: bool = False,
    quick_reply_count: int | None = None,
    trace_id: str | None = None,
) -> dict:
    incoming = (_safe_getattr(context, "incoming_message", {}) or {}) if context else {}
    scope_metrics = incoming.get("_llm_tool_scope_metrics") if isinstance(incoming, dict) else {}
    if not isinstance(scope_metrics, dict):
        scope_metrics = {}
    tool_count = _tool_count(tools)
    total_prompt_chars = len(system_instructions or "")
    tenant_config_chars = 0
    try:
        config = _safe_getattr(context, "config")
        config_data = _safe_getattr(config, "config_data", {}) or {}
        tenant_config_chars = len(json.dumps(config_data, ensure_ascii=True, default=str))
    except Exception:
        tenant_config_chars = 0
    history_chars = len(memory_context or "")
    rag_chars = len(knowledge_context or "")
    agentic_history_chars = 0
    try:
        session_data_for_history = _safe_getattr(context, "session_data")
        state_for_history = _safe_getattr(session_data_for_history, "state")
        raw_agentic_history = _safe_getattr(state_for_history, "agentic_history", []) or []
        agentic_history_chars = len(json.dumps(raw_agentic_history, ensure_ascii=True, default=str))
    except Exception:
        agentic_history_chars = 0
    tools_chars = sum(
        len(str((tool.get("function") or {}).get("name") or ""))
        for tool in tools or []
        if isinstance(tool, dict)
    )
    ledger_chars = _ledger_summary_chars(context)
    known_section_chars = tenant_config_chars + history_chars + rag_chars + tools_chars + ledger_chars
    prompt_sections = {
        "platform_rules_chars": max(0, total_prompt_chars - known_section_chars),
        "tenant_config_chars": tenant_config_chars,
        "history_chars": history_chars,
        "agentic_history_chars": agentic_history_chars,
        "rag_chars": rag_chars,
        "tools_chars": tools_chars,
        "ledger_chars": ledger_chars,
    }
    session_data = _safe_getattr(context, "session_data")
    state = _safe_getattr(session_data, "state")
    ledger = _safe_getattr(state, "action_ledger")
    agentic_history = _safe_getattr(state, "agentic_history", []) or []
    pending_action = _safe_getattr(ledger, "pending_action")
    provider_failure = provider_failure if isinstance(provider_failure, dict) else {}
    error_code = (
        safe_llm_error_code(
            error_type=provider_failure.get("error_type") or provider_failure.get("missing_reason"),
            status_code=provider_failure.get("status_code"),
        )
        if provider_failure else None
    )
    safe_rag_skip_reasons = {
        "greeting", "thanks_or_goodbye", "deterministic_direct",
        "transactional_field_collection", "protected_pending_action", "post_tool_success",
    }
    normalized_output_type = final_output_type if final_output_type in {
        "text", "tool_call", "safe_error", "empty"
    } else "text"
    unscoped_tool_count = min(64, max(0, int(scope_metrics.get("unscoped_tool_count") or tool_count)))
    scoped_tool_count = min(64, max(0, int(scope_metrics.get("scoped_tool_count") or tool_count)))
    return {
        "schema_version": "1",
        "event": "legacy_llm_input_trace",
        "trace_id": safe_identifier(trace_id) or current_safe_trace_id(),
        "purpose": "tool_selection" if tool_count else ("routing" if route_intent or route_type else "unknown"),
        "integration_mode": integration_mode or getattr(settings, "AWA_INTEGRATION_MODE", None),
        "provider": safe_identifier(provider, maximum=40),
        "model": safe_identifier(model),
        "is_fake_provider": is_fake_provider(provider),
        "prompt_sections": prompt_sections,
        "total_prompt_chars": total_prompt_chars,
        "approximate_input_tokens": max(0, int(total_prompt_chars / 4)),
        "tool_count": tool_count,
        "history_message_count": min(_count_history_messages(memory_context), 64),
        "agentic_history_count": min(len(agentic_history), 64) if isinstance(agentic_history, list) else 0,
        "rag_chunk_count": min(_count_rag_chunks(knowledge_context), 64),
        "catalog_item_count": min(_count_catalog_items(context), 10000),
        "has_pending_action": bool(pending_action),
        "quick_reply_count": min(20, max(0, int(quick_reply_count))) if quick_reply_count is not None else None,
        "direct_path_bypassed_llm": bool(direct_path_bypassed_llm),
        "scoped_tools_used": bool(scoped_tools_used or scope_metrics.get("scoped_tools_used")),
        "unscoped_tool_count": unscoped_tool_count,
        "scoped_tool_count": scoped_tool_count,
        "rag_skipped": bool(rag_skipped),
        "rag_skip_reason": (
            rag_skip_reason if rag_skip_reason in safe_rag_skip_reasons
            else ("other" if rag_skip_reason else None)
        ),
        "latency_ms": max(0, int(latency_ms or 0)),
        "fallback_used": bool(fallback_used),
        "error_code": error_code,
        "final_output_type": normalized_output_type,
    }


def log_llm_input_trace(trace: dict) -> None:
    emit_legacy_llm_trace(trace)


def final_output_type(response) -> str:
    if response is None:
        return "safe_error"
    if getattr(response, "tool_calls", None):
        return "tool_call"
    if getattr(response, "content", None):
        return "text"
    return "safe_error"


SAFE_PROVIDER_FAILURE_TEXT = "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."
SAFE_ACTION_FAILURE_TEXT = "I couldn't complete that action right now, and it has not been submitted. Please try again or contact the business."

def estimate_langchain_messages_tokens(messages: list, max_output_tokens: int = 1024) -> int:
    """
    Safely estimate prompt/request tokens from Langchain messages without logging or storing
    prompts, system instructions, user messages, tool arguments, or provider payloads.
    """
    try:
        total_len = 0
        for m in messages:
            if hasattr(m, "content"):
                content = m.content
                if isinstance(content, str):
                    total_len += len(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and "text" in item:
                            total_len += len(str(item["text"]))
                        elif isinstance(item, str):
                            total_len += len(item)
        estimated_input = int(total_len / 4)
        return estimated_input + max_output_tokens
    except Exception:
        return 2048

class ObservedLangchainLLM:
    """
    Wrapper for Langchain LLM objects to enforce provider rate limiting and record diagnostics.
    """
    def __init__(self, inner, provider: str, model_name: str):
        self.inner = inner
        self.provider = provider
        self.model_name = model_name

    def bind_tools(self, tools, **kwargs):
        bound = self.inner.bind_tools(tools, **kwargs)
        return ObservedLangchainLLM(bound, self.provider, self.model_name)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def ainvoke(self, input, config=None, **kwargs):
        from backend.observability.latency import latency_metrics_var, now_ms, elapsed_ms
        from backend.observability.provider_rate_limiter import (
            acquire_provider_capacity,
            get_redis_client,
        )
        from backend.ops.logger import _tenant_id_ctx
        from backend.core.config import settings

        tenant_id = _tenant_id_ctx.get()
        if tenant_id == "unknown":
            tenant_id = None

        estimated_tokens = estimate_langchain_messages_tokens(input)
        redis_client = await get_redis_client()

        throttle_start = now_ms()
        decision = await acquire_provider_capacity(
            redis_client=redis_client,
            provider=self.provider,
            model=self.model_name,
            tenant_id=tenant_id,
            estimated_tokens=estimated_tokens,
            max_wait_ms=settings.PROVIDER_RATE_LIMIT_MAX_WAIT_MS,
        )
        throttle_duration = elapsed_ms(throttle_start)

        metrics = latency_metrics_var.get()
        if metrics is not None:
            metrics["provider_llm_call_count"] = metrics.get("provider_llm_call_count", 0) + 1
            metrics["provider_total_estimated_tokens"] = metrics.get("provider_total_estimated_tokens", 0) + estimated_tokens
            metrics["provider_input_estimated_tokens"] = metrics.get("provider_input_estimated_tokens", 0) + max(0, estimated_tokens - 1024)
            if metrics.get("provider_throttle_wait_ms") is None:
                metrics["provider_throttle_wait_ms"] = 0
            metrics["provider_throttle_wait_ms"] += throttle_duration

        if not decision.allowed:
            from langchain_core.messages import AIMessage
            from backend.core.context import active_context_var, SafeError

            provider_err = {
                "provider": self.provider,
                "model": self.model_name,
                "error_type": "rate_limit_wait_timeout",
                "status_code": None,
                "customer_safe": True,
                "redacted": True
            }

            fallback_content = "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

            context = active_context_var.get()
            if context and context.session_data and context.session_data.state:
                context.session_data.state.action_ledger.last_error = SafeError(
                    code="provider_error",
                    safe_message=fallback_content,
                    provider_error=provider_err
                )

            log_llm_input_trace(build_llm_input_trace(
                context=context,
                provider=self.provider,
                model=self.model_name,
                integration_mode=getattr(settings, "AWA_INTEGRATION_MODE", None),
                provider_failure=provider_err,
                final_output_type="safe_error",
            ))
            return AIMessage(content=fallback_content)

        start = now_ms()
        actual_tokens = 0
        try:
            res = await self.inner.ainvoke(input, config, **kwargs)
            if res:
                usage_metadata = getattr(res, "usage_metadata", None)
                if isinstance(usage_metadata, dict):
                    actual_tokens = usage_metadata.get("total_tokens") or 0
            return res
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str.upper() or "rate_limit" in exc_str.lower() or "quota" in exc_str.lower():
                if metrics is not None:
                    metrics["provider_actual_429s"] = metrics.get("provider_actual_429s", 0) + 1
            raise
        finally:
            dur = elapsed_ms(start)
            if metrics is not None:
                if metrics.get("provider_latency_ms") is None:
                    metrics["provider_latency_ms"] = 0
                metrics["provider_latency_ms"] += dur

            refund_amount = estimated_tokens - actual_tokens
            if refund_amount > 0:
                from backend.observability.provider_rate_limiter import refund_provider_capacity
                try:
                    await refund_provider_capacity(
                        redis_client=redis_client,
                        provider=self.provider,
                        model=self.model_name,
                        tenant_id=tenant_id,
                        refund_amount=refund_amount,
                    )
                except Exception as e:
                    emit_safe_llm_log(
                        level="WARNING", event="legacy_provider_failure",
                        provider=safe_identifier(self.provider, maximum=40),
                        model=safe_identifier(self.model_name), outcome="error",
                        error_code=safe_llm_error_code(exception_type=type(e).__name__),
                    )

def get_llm():
    """Build the explicitly selected legacy-compatible chat model.

    Authoritative runtime callers use ``get_runtime_llm_gateway`` instead. This
    helper remains only for the platform kill switch, purpose-specific legacy
    modes, shadow comparisons, and opt-in legacy diagnostics.
    """
    provider = (settings.AGENT_LLM_PROVIDER or os.getenv("LLM_PROVIDER", "groq")).lower()
    if is_fake_provider(provider):
        if settings.AWA_INTEGRATION_MODE == "live":
            raise RuntimeError("Deterministic fake tool-calling LLM is not allowed in live integration mode")
        if is_real_provider_required():
            raise RuntimeError("Deterministic fake tool-calling LLM is not allowed when a real provider is required")
        emit_safe_llm_log(
            event="legacy_provider_mode", provider="deterministic_fake", outcome="mock"
        )
        return DeterministicFakeToolCallingLLM()
    if provider not in {"ollama", "gemini", "groq", "openai"}:
        raise RuntimeError(f"Unsupported tool-calling LLM provider: {provider}")
    if provider == "ollama":
        is_docker = os.getenv("ENV") == "docker"
        base_url = os.getenv("OLLAMA_BASE_URL")
        if not base_url:
            base_url = "http://host.docker.internal:11434/v1" if is_docker else "http://localhost:11434/v1"
        model_name = settings.AGENT_LLM_MODEL or "gemma4:e2b"
        from langchain_openai import ChatOpenAI
        emit_safe_llm_log(
            event="legacy_provider_mode", provider="ollama",
            model=safe_identifier(model_name), outcome="success",
        )
        inner = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            openai_api_key="ollama",
            api_key="ollama",
            max_tokens=1024,
        )
        return ObservedLangchainLLM(inner, "ollama", model_name)
    elif provider == "gemini":
        gemini_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        from backend.ai.providers.gemini_chat_adapter import GeminiChatOpenAI
        model_name = settings.AGENT_LLM_MODEL or "gemini-3.1-flash-lite"
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        emit_safe_llm_log(
            event="legacy_provider_mode", provider="gemini",
            model=safe_identifier(model_name), outcome="success",
        )
        inner = GeminiChatOpenAI(
            model=model_name,
            base_url=base_url,
            openai_api_key=gemini_key,
            api_key=gemini_key,
            max_tokens=1024,
        )
        return ObservedLangchainLLM(inner, "gemini", model_name)
    elif provider == "groq":
        if not settings.GROQ_API_KEY and not os.getenv("GROQ_API_KEY"):
            raise RuntimeError("GROQ_API_KEY is not configured")
        from langchain_groq import ChatGroq
        model_name = settings.AGENT_LLM_MODEL or "llama-3.3-70b-versatile"
        inner = ChatGroq(model=model_name, max_tokens=1024)
        return ObservedLangchainLLM(inner, "groq", model_name)
    else:
        if not settings.OPENAI_API_KEY and not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not configured")
        from langchain_openai import ChatOpenAI
        model_name = settings.AGENT_LLM_MODEL or "gpt-4o"
        inner = ChatOpenAI(model=model_name, max_tokens=1024)
        return ObservedLangchainLLM(inner, provider, model_name)
class DeterministicFakeToolCallingLLM:
    """
    Opt-in local/test LLM that can produce structured tool calls.
    This is intentionally small and rule-based. It is not a language model and
    must never be enabled in live mode.
    """
    def __init__(self):
        self.bound_tools = []
    def bind_tools(self, tools):
        clone = DeterministicFakeToolCallingLLM()
        clone.bound_tools = list(tools or [])
        return clone
    async def ainvoke(self, messages):
        from langchain_core.messages import AIMessage, ToolMessage
        from backend.ai.security import SECURITY_REFUSAL_MESSAGE, is_prompt_security_request
        last_message = messages[-1] if messages else None
        if isinstance(last_message, ToolMessage):
            return AIMessage(content=self._reply_from_tool_message(last_message.content))
        text = str(getattr(last_message, "content", "") or "")
        lowered = text.lower()
        # Security block
        if is_prompt_security_request(text) or any(k in lowered for k in ["system prompt", "tenant", "reveal", "bypass", "jailbreak", "disabled tool", "without confirmation"]):
            return AIMessage(content=SECURITY_REFUSAL_MESSAGE)
        # Check if the user is confirming a pending action
        if text.lower() in {"confirm", "yes", "y", "sure", "ok", "okay", "agree", "proceed", "go ahead"}:
            # Scan messages backwards to find the last tool call
            for msg in reversed(messages[:-1]):
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    last_tool = msg.tool_calls[0]
                    args = dict(last_tool.get("args") or {})
                    # Coerce types for the fake tool call from history
                    for int_field in ["quantity", "amount", "time_offset", "rating"]:
                        if int_field in args and args[int_field] is not None:
                            try:
                                args[int_field] = int(args[int_field])
                            except (ValueError, TypeError):
                                pass
                    # Return the exact same tool call to execute/commit it!
                    return AIMessage(content="", tool_calls=[{
                        "name": last_tool["name"],
                        "args": args,
                        "id": last_tool.get("id", "fake_call_confirm"),
                    }])
        tool_call = self._tool_call_for_text(text, messages)
        if tool_call:
            return AIMessage(content="", tool_calls=[tool_call])
        # FAQ / General responses
        if "hour" in lowered or "opening" in lowered or "open" in lowered:
            return AIMessage(content="We are open from 9 AM to 6 PM. Yes, we offer dental cleaning.")
        if "price" in lowered and "toothpaste" in lowered:
            return AIMessage(content="Toothpaste is available in our catalog for INR 120.")
        if "price" in lowered:
            return AIMessage(content="Dental cleaning is INR 1500, and toothpaste is INR 120.")
        return AIMessage(content="I can help with that. Could you share the details?")
    def _available_tool_names(self) -> set[str]:
        names = set()
        for tool in self.bound_tools:
            function_def = tool.get("function", {}) if isinstance(tool, dict) else {}
            if function_def.get("name"):
                names.add(function_def["name"])
        return names
    def _tool_call_for_text(self, text: str, messages: list):
        available = self._available_tool_names()
        lowered = text.lower()
        if "human" in lowered or "person" in lowered or "support" in lowered or "handoff" in lowered or "speak to" in lowered or "representative" in lowered:
            if "human_handoff_execute" in available:
                return {
                    "name": "human_handoff_execute",
                    "args": {"reason": text[:200] or "Customer requested a human"},
                    "id": "fake_call_human_handoff",
                }
        if any(k in lowered for k in ("interested", "interest", "contact me", "call me", "callback", "sign up", "signup", "learn more", "more info", "information")):
            if "lead_capture_execute" in available:
                return {
                    "name": "lead_capture_execute",
                    "args": {
                        "name": self._extract_name(messages),
                        "phone": self._extract_phone(messages),
                        "email": self._extract_email(messages),
                        "interest": self._extract_interest(messages),
                    },
                    "id": "fake_call_lead_capture",
                }
        if "book" in lowered or "appointment" in lowered or "slot" in lowered or "make it 11" in lowered:
            if "booking_execute" in available:
                return {
                    "name": "booking_execute",
                    "args": {
                        "service_name": self._extract_service(messages),
                        "preferred_date": self._extract_date(messages),
                        "preferred_time": self._extract_time(messages),
                        "customer_name": self._extract_name(messages),
                        "phone": self._extract_phone(messages),
                    },
                    "id": "fake_call_booking",
                }
        if "pay" in lowered or "payment" in lowered or "charge" in lowered or "paid" in lowered:
            if "payments_execute" in available:
                return {
                    "name": "payments_execute",
                    "args": {
                        "amount": self._extract_amount(messages),
                    },
                    "id": "fake_call_payments",
                }
        if "remind" in lowered or "reminder" in lowered:
            if "reminders_execute" in available:
                return {
                    "name": "reminders_execute",
                    "args": {
                        "reminder_type": "manual",
                        "time_offset": self._extract_duration_minutes(messages),
                        "message_text": self._extract_reminder_text(messages),
                    },
                    "id": "fake_call_reminders",
                }
        if ("order" in lowered or "buy" in lowered or "toothpaste" in lowered or "floss" in lowered or "biriyani" in lowered or "pickup" in lowered or "delivery" in lowered) and not ("price" in lowered or "cost" in lowered or "how much" in lowered):
            if "order_execute" in available:
                return {
                    "name": "order_execute",
                    "args": {
                        "item_name": self._extract_item(messages),
                        "quantity": self._extract_quantity(messages),
                        "fulfillment_type": "delivery" if any("deliver" in str(getattr(m, "content", "")).lower() for m in messages if getattr(m, "type", "") == "human") else "pickup",
                        "customer_name": self._extract_name(messages),
                        "phone": self._extract_phone(messages),
                    },
                    "id": "fake_call_order",
                }
        if "rate" in lowered or "rating" in lowered or "star" in lowered or "review" in lowered:
            if "reviews_execute" in available:
                return {
                    "name": "reviews_execute",
                    "args": {
                        "rating": self._extract_rating(messages),
                        "feedback": self._extract_feedback(messages),
                    },
                    "id": "fake_call_reviews",
                }
        return None
    @staticmethod
    def _reply_from_tool_message(content: str) -> str:
        import json
        try:
            payload = json.loads(content)
        except Exception:
            return "I have the tool result. What would you like to do next?"
        if payload.get("user_message"):
            return str(payload["user_message"])
        return "Done." if payload.get("success") else "I couldn't complete that action right now."
    @staticmethod
    def _extract_date(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "").lower()
            if "tomorrow" in content:
                return "tomorrow"
            match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", content)
            if match:
                return match.group(0)
        return "tomorrow"
    @staticmethod
    def _extract_time(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", content, flags=re.IGNORECASE)
            if match:
                return match.group(0)
        return "10 AM"
    @staticmethod
    def _extract_name(messages: list) -> str:
        # First, search all human messages (newest first) for explicit name markers.
        for msg in reversed(messages):
            if getattr(msg, "type", "") != "human":
                continue
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b(?:my name is|name is)\s+([A-Za-z][A-Za-z .'-]{1,40})", content, flags=re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                name = re.split(r"\b(?:and|my|is|phone|mobile|email|for|with|at|to|please)\b", name, flags=re.IGNORECASE)[0].strip()
                return name
        # Then allow "I am/I'm" only when it is not the common "I am interested..." intent phrase.
        for msg in reversed(messages):
            if getattr(msg, "type", "") != "human":
                continue
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b(?:i am|i'm)\s+(?!interested\b)([A-Za-z][A-Za-z .'-]{1,40})", content, flags=re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                name = re.split(r"\b(?:and|my|is|phone|mobile|email|for|with|at|to|please)\b", name, flags=re.IGNORECASE)[0].strip()
                return name
        # If not found, search all human messages for fallback markers
        for msg in reversed(messages):
            if getattr(msg, "type", "") != "human":
                continue
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b(?:for)\s+([A-Za-z][A-Za-z .'-]{1,40})", content, flags=re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                name = re.split(r"\b(?:and|my|is|phone|mobile|email|for|with|at|to|please)\b", name, flags=re.IGNORECASE)[0].strip()
                return name
        return "Test Customer"
    @staticmethod
    def _extract_phone(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\+?\d[\d\s().-]{6,}\d", content)
            if match:
                return re.sub(r"[^\d+]", "", match.group(0))
        return "+15550000000"
    @staticmethod
    def _extract_email(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", content)
            if match:
                return match.group(0)
        return ""
    @staticmethod
    def _extract_interest(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            lowered = content.lower()
            if "dental cleaning" in lowered:
                return "dental cleaning"
            if "consultation" in lowered:
                return "consultation"
            match = re.search(r"\binterested in\s+(.+?)(?:\.|,|$)", content, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()[:120]
        return "Customer inquiry"
    @staticmethod
    def _extract_quantity(messages: list) -> int:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b(\d{1,2})\b", content)
            if match:
                return int(match.group(1))
        return 1
    @staticmethod
    def _extract_service(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "").lower()
            if "clean" in content:
                return "Dental Cleaning"
        return "Dental Cleaning"
    @staticmethod
    def _extract_item(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "").lower()
            if "toothpaste" in content:
                return "Toothpaste"
            if "floss" in content:
                return "Dental Floss"
            if "biriyani" in content:
                return "Veg Biriyani"
        return "Toothpaste"
    @staticmethod
    def _extract_amount(messages: list) -> int:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b(\d+)\b", content)
            if match:
                return int(match.group(1))
        return 100
    @staticmethod
    def _extract_duration_minutes(messages: list) -> int:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b(\d+)\b\s*minute", content, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 1
    @staticmethod
    def _extract_reminder_text(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"to\s+(.+)$", content, flags=re.IGNORECASE)
            if match:
                return match.group(1).split(".")[0].strip()
        return "Reminder"
    @staticmethod
    def _extract_rating(messages: list) -> int:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            match = re.search(r"\b([1-5])\b\s*(?:star|rating)?", content, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
            lowered = content.lower()
            if "one" in lowered: return 1
            if "two" in lowered: return 2
            if "three" in lowered: return 3
            if "four" in lowered: return 4
            if "five" in lowered: return 5
        return 5
    @staticmethod
    def _extract_feedback(messages: list) -> str:
        for msg in reversed(messages):
            content = str(getattr(msg, "content", "") or "")
            if content:
                return content[:200]
        return "Feedback"
def record_langchain_usage(context: ExecutionContext, response) -> None:
    provider = (settings.AGENT_LLM_PROVIDER or os.getenv("LLM_PROVIDER", "")).lower() or None
    usage = normalize_llm_usage(
        provider=provider,
        usage_metadata=getattr(response, "usage_metadata", None),
        raw_response=response,
    )
    context.record_llm_usage(usage)
async def run_provider_fallback(
    context: ExecutionContext,
    system_instructions: str,
    user_message: str,
):
    """Run the historical provider chain for explicit legacy rollback only."""
    from backend.logic.engine.core import WorkflowResult
    from backend.ai.factory import LLMFactory
    from backend.ai.providers.base import (
        ProviderError,
        ProviderRetryableError,
        ProviderRateLimitError,
        ProviderTimeoutError,
        ProviderConfigurationError,
        ProviderAuthError,
    )

    # Prevent false success messages in text-only fallback when a protected action is pending or failed
    if (
        context
        and getattr(context, "session_data", None)
        and getattr(context.session_data, "state", None)
        and getattr(context.session_data.state, "action_ledger", None)
    ):
        ledger = context.session_data.state.action_ledger

        has_pending = getattr(ledger, "pending_action", None) is not None
        last_result = getattr(ledger, "last_tool_result", None)
        last_result_failed = (
            last_result is not None and getattr(last_result, "success", None) is False
        )

        if has_pending or last_result_failed:
            emit_safe_llm_log(
                level="WARNING", event="legacy_fallback_exhausted", purpose="unknown",
                outcome="unavailable", error_code="configuration",
            )
            return WorkflowResult(
                response_text=SAFE_ACTION_FAILURE_TEXT,
                next_state=context.session_data.state.workflow_state,
            )

    primary_provider = (settings.AGENT_LLM_PROVIDER or "groq").lower().strip()
    fallback_order = ["groq", "openai", "gemini"]
    candidates = [p for p in fallback_order if p != primary_provider]

    had_retryable = False
    last_exc = None
    fallback_trace_id = current_safe_trace_id()

    for attempt_number, candidate in enumerate(candidates, start=1):
        model_name = None
        try:
            emit_safe_llm_log(
                event="legacy_fallback_attempt", provider=safe_identifier(candidate, maximum=40),
                attempt_number=attempt_number, outcome="started", trace_id=fallback_trace_id,
            )
            provider = LLMFactory.create_provider(candidate)
            model_name = getattr(provider, "model_name", "") or getattr(provider, "model", "") or None

            # Check if candidate has API key configured (only in real production mode)
            is_mock_mode = (
                os.getenv("AWA_INTEGRATION_MODE", "mock") == "mock"
                or getattr(settings, "AWA_INTEGRATION_MODE", "mock") == "mock"
                or getattr(settings, "AWA_INTEGRATION_MODE", "mock") == "test"
            )
            if not is_mock_mode and not getattr(provider, "api_key", None):
                raise ProviderConfigurationError(f"Candidate {candidate} is missing API key")

            fallback_start = perf_counter()
            llm_response = await provider.generate_response(system_instructions, user_message)
            latency_ms = int((perf_counter() - fallback_start) * 1000)

            # Check for provider_error from ObservedLLM wrapper
            if getattr(llm_response, "provider_error", None):
                err_info = llm_response.provider_error
                err_type = err_info.get("error_type")
                if err_type == "rate_limit_wait_timeout" or err_type == "rate_limit":
                    raise ProviderRateLimitError(f"Rate limit / capacity denied for {candidate}")
                elif err_type == "auth_failed":
                    raise ProviderAuthError(f"Auth failed for {candidate}")
                elif err_type == "timeout":
                    raise ProviderTimeoutError(f"Timeout for {candidate}")
                else:
                    raise ProviderError(f"Provider error for {candidate}: {err_info}")

            context.record_llm_usage(getattr(llm_response, "usage_metadata", None))
            response_text = getattr(llm_response, "content", "")
            log_llm_input_trace(build_llm_input_trace(
                context=context,
                provider=candidate,
                model=model_name or getattr(llm_response, "model", None),
                integration_mode=getattr(settings, "AWA_INTEGRATION_MODE", None),
                system_instructions=system_instructions,
                latency_ms=latency_ms,
                fallback_used=True,
                final_output_type="text" if response_text else "safe_error",
                trace_id=fallback_trace_id,
            ))
            emit_safe_llm_log(
                event="legacy_fallback_success", provider=safe_identifier(candidate, maximum=40),
                model=safe_identifier(model_name or getattr(llm_response, "model", None)),
                attempt_number=attempt_number, latency_ms=latency_ms,
                outcome="success", trace_id=fallback_trace_id,
            )
            return WorkflowResult(
                response_text=response_text,
                next_state=context.session_data.state.workflow_state,
            )
        except (ProviderConfigurationError, ProviderAuthError) as e:
            log_llm_input_trace(build_llm_input_trace(
                context=context,
                provider=candidate,
                model=model_name,
                integration_mode=getattr(settings, "AWA_INTEGRATION_MODE", None),
                system_instructions=system_instructions,
                fallback_used=True,
                provider_failure={"provider": candidate, "error_type": type(e).__name__, "redacted": True},
                final_output_type="safe_error",
                trace_id=fallback_trace_id,
            ))
            emit_safe_llm_log(
                level="WARNING", event="legacy_fallback_failure",
                provider=safe_identifier(candidate, maximum=40), model=safe_identifier(model_name),
                attempt_number=attempt_number, outcome="error", trace_id=fallback_trace_id,
                error_code=safe_llm_error_code(exception_type=type(e).__name__),
            )
            last_exc = e
        except ProviderRetryableError as e:
            log_llm_input_trace(build_llm_input_trace(
                context=context,
                provider=candidate,
                model=model_name,
                integration_mode=getattr(settings, "AWA_INTEGRATION_MODE", None),
                system_instructions=system_instructions,
                fallback_used=True,
                provider_failure={"provider": candidate, "error_type": type(e).__name__, "redacted": True},
                final_output_type="safe_error",
                trace_id=fallback_trace_id,
            ))
            emit_safe_llm_log(
                level="WARNING", event="legacy_fallback_failure",
                provider=safe_identifier(candidate, maximum=40), model=safe_identifier(model_name),
                attempt_number=attempt_number, outcome="error", trace_id=fallback_trace_id,
                error_code=safe_llm_error_code(exception_type=type(e).__name__),
            )
            had_retryable = True
            last_exc = e
        except Exception as e:
            log_llm_input_trace(build_llm_input_trace(
                context=context,
                provider=candidate,
                model=model_name,
                integration_mode=getattr(settings, "AWA_INTEGRATION_MODE", None),
                system_instructions=system_instructions,
                fallback_used=True,
                provider_failure={"provider": candidate, "error_type": type(e).__name__, "redacted": True},
                final_output_type="safe_error",
                trace_id=fallback_trace_id,
            ))
            emit_safe_llm_log(
                level="WARNING", event="legacy_fallback_failure",
                provider=safe_identifier(candidate, maximum=40), model=safe_identifier(model_name),
                attempt_number=attempt_number, outcome="error", trace_id=fallback_trace_id,
                error_code=safe_llm_error_code(exception_type=type(e).__name__),
            )
            had_retryable = True
            last_exc = e

    from backend.core.context import SafeError

    provider_error = {
        "provider": primary_provider,
        "error_type": "all_fallback_providers_failed",
        "customer_safe": True,
        "redacted": True,
    }
    if last_exc is not None:
        provider_error["missing_reason"] = type(last_exc).__name__
    if (
        context
        and getattr(context, "session_data", None)
        and getattr(context.session_data, "state", None)
        and getattr(context.session_data.state, "action_ledger", None)
    ):
        context.session_data.state.action_ledger.last_error = SafeError(
            code="provider_error",
            safe_message=SAFE_PROVIDER_FAILURE_TEXT,
            provider_error=provider_error,
        )
    log_llm_input_trace(build_llm_input_trace(
        context=context,
        provider=primary_provider,
        integration_mode=getattr(settings, "AWA_INTEGRATION_MODE", None),
        system_instructions=system_instructions,
        fallback_used=True,
        provider_failure=provider_error,
        final_output_type="safe_error",
        trace_id=fallback_trace_id,
    ))
    emit_safe_llm_log(
        level="ERROR", event="legacy_fallback_exhausted",
        provider=safe_identifier(primary_provider, maximum=40), outcome="error",
        trace_id=fallback_trace_id, error_code="all_providers_failed",
    )
    return WorkflowResult(
        response_text=SAFE_PROVIDER_FAILURE_TEXT,
        next_state=context.session_data.state.workflow_state,
    )
