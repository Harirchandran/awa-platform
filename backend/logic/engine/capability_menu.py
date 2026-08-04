from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Collection, Dict, List, Optional
from uuid import UUID

from backend.ai.security import sanitize_untrusted_text
from backend.ops.logger import logger


def _catalog_cache_json_default(value: Any) -> Any:
    """Preserve DB numeric values as JSON numbers at the Redis cache boundary."""
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def hydrate_public_catalog(tenant_id: UUID, config_data: Dict[str, Any]) -> None:
    """Overlay DB-backed order catalog into *config_data* in-memory.

    This is the shared public-chat catalog hydration helper.  It mirrors the
    logic in ``AgenticEngine._hydrate_order_catalog`` but operates on a plain
    ``config_data`` dict rather than an ``ExecutionContext``.

    Behaviour:
    - Tries Redis cache key ``awa:catalog:cache:<tenant_id>`` (60 s TTL).
    - On cache miss: queries ``catalog_service.list_categories`` / ``list_items``
      from the DB and populates the cache.
    - If DB returns active categories: overwrites
      ``config_data["modules"]["order"]["categories"]`` with DB data.
    - If DB returns empty: leaves the existing tenant JSON catalog intact (fallback).
    - If DB query or cache raises: logs warning and leaves config_data unchanged.
    - Never crashes the caller; all exceptions are swallowed after logging.
    - Does NOT mutate persistent tenant config; only the in-memory dict is changed.
    - Does NOT expose internal UUIDs or private refs in the overlaid categories —
      item ``id`` fields are DB UUIDs kept only for internal category→item mapping;
      they are NOT forwarded to LLM prompts (capability_menu helpers never emit ``id``).
    """
    try:
        from backend.core.redis_client import get_redis_client

        cache_key = f"awa:catalog:cache:{tenant_id}"

        # 1. Try Redis cache
        cached_data = None
        try:
            redis_client = get_redis_client()
            cached_data = await redis_client.get(cache_key)
        except Exception as redis_exc:
            logger.warning(f"[public_chat_catalog] Redis cache read failed for tenant {tenant_id}: {redis_exc}")

        categories_list = None
        if cached_data:
            try:
                categories_list = json.loads(cached_data)
                logger.debug(f"[public_chat_catalog] Loaded catalog from cache for tenant {tenant_id}")
            except Exception as parse_exc:
                logger.warning(f"[public_chat_catalog] Failed to parse cached catalog for tenant {tenant_id}: {parse_exc}")

        # 2. Cache miss — query DB
        if not categories_list:
            from backend.domains.catalog.service import catalog_service

            db_categories = await catalog_service.list_categories(tenant_id)
            db_items = await catalog_service.list_items(tenant_id, active_only=True, available_only=True)

            category_map: Dict[Any, Dict[str, Any]] = {}
            for cat in db_categories:
                if cat.get("is_active") is not False:
                    category_map[cat["id"]] = {
                        "id": str(cat["id"]),
                        "name": cat["name"],
                        "enabled": cat.get("is_active", True),
                        "items": [],
                    }

            for item in db_items:
                cat_id = item.get("category_id")
                if cat_id in category_map:
                    category_map[cat_id]["items"].append({
                        "id": str(item["id"]),
                        "name": item["name"],
                        "price": float(item["price"]) if item.get("price") is not None else 0.0,
                        "enabled": item.get("is_active", True),
                        "stock_quantity": item.get("stock_quantity"),
                        "stock_tracking_enabled": item.get("stock_tracking_enabled", False),
                    })

            categories_list = list(category_map.values())

            # Populate cache
            try:
                redis_client = get_redis_client()
                await redis_client.set(
                    cache_key,
                    json.dumps(categories_list, default=_catalog_cache_json_default),
                    ex=60,
                )
                logger.debug(f"[public_chat_catalog] Cached catalog for tenant {tenant_id}")
            except Exception as redis_exc:
                logger.warning(f"[public_chat_catalog] Redis cache write failed for tenant {tenant_id}: {redis_exc}")

        # 3. Overlay into config_data only when DB has data
        if categories_list and isinstance(config_data, dict):
            modules = config_data.setdefault("modules", {})
            if isinstance(modules, dict):
                order_config = modules.setdefault("order", {})
                if isinstance(order_config, dict):
                    order_config["categories"] = categories_list
                    logger.debug(
                        f"[public_chat_catalog] Hydrated {len(categories_list)} categories into public chat config for tenant {tenant_id}"
                    )
        elif isinstance(categories_list, list) and not categories_list:
            logger.debug(f"[public_chat_catalog] DB catalog empty for tenant {tenant_id}; using tenant JSON fallback")

    except Exception as exc:
        logger.warning(
            f"[public_chat_catalog] Catalog hydration failed for tenant {tenant_id}: {exc}",
            exc_info=False,
        )


@dataclass(frozen=True)
class CapabilityMenu:
    text: str
    suggestions: List[str]
    capabilities: List[str]


@dataclass(frozen=True)
class MenuResponse:
    text: str
    quick_replies: List[str]


GREETING_TEXTS = {
    "hi",
    "hello",
    "hey",
    "start",
    "good morning",
    "good afternoon",
    "good evening",
}

BROAD_HELP_PHRASES = (
    "what can you help",
    "how can you help",
    "what can i do",
    "what can you do",
    "what do you do",
    "how does this work",
)

SELF_SERVE_FALLBACKS = ["Services & prices", "Opening hours", "Ask a question"]
UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
PROFILE_SUGGESTION_ORDER = {
    "booking": ("Book appointment", "Reschedule", "Cancel booking", "Book again", "Get directions"),
    "order": ("Show menu", "Place simple order", "View menu"),
    "payments": ("Make payment",),
    "reminders": ("Set reminder", "Continue"),
    "reviews": ("Thank you",),
    "faq": ("Services & prices", "Opening hours", "Ask a question"),
    "lead_capture": ("Request callback",),
    "support": ("Get support", "Contact support"),
    "human_handoff": ("Contact support", "Get support"),
}


def _safe_label_text(value: Any, max_chars: int) -> str:
    return UUID_PATTERN.sub("[redacted]", sanitize_untrusted_text(value, max_chars=max_chars))


def _active_behavior_profile(behavior_profile_resolution: Any) -> Any:
    try:
        if behavior_profile_resolution is None:
            return None
        if getattr(behavior_profile_resolution, "source", None) == "legacy_fallback":
            return None
        return getattr(behavior_profile_resolution, "effective_profile", None)
    except Exception:
        return None


def rank_suggestions_by_behavior_profile(
    suggestions: List[str],
    behavior_profile_resolution: Any,
    *,
    available_modules: Optional[Collection[str]] = None,
) -> List[str]:
    try:
        profile = _active_behavior_profile(behavior_profile_resolution)
        if profile is None:
            return suggestions
        available = {str(module) for module in (available_modules or [])}
        priority = [str(module) for module in (getattr(profile, "journey_priority", ()) or ())]
        ranked: List[str] = []
        remaining = list(suggestions)
        for module in priority:
            if available and module not in available:
                continue
            for label in PROFILE_SUGGESTION_ORDER.get(module, ()):
                for suggestion in list(remaining):
                    if suggestion.lower() == label.lower():
                        ranked.append(suggestion)
                        remaining.remove(suggestion)
                        break
        return _dedupe([*ranked, *remaining])
    except Exception:
        return suggestions


def _rank_suggestions_by_behavior_profile(
    suggestions: List[str],
    behavior_profile_resolution: Any,
    *,
    available_modules: Optional[Collection[str]] = None,
) -> List[str]:
    return rank_suggestions_by_behavior_profile(
        suggestions,
        behavior_profile_resolution,
        available_modules=available_modules,
    )


def enabled_modules(
    config: Dict[str, Any],
    available_modules: Optional[Collection[str]] = None,
) -> set[str]:
    modules = config.get("modules", {}) if isinstance(config, dict) else {}
    if not isinstance(modules, dict):
        return set()
    enabled = {
        str(key)
        for key, module_config in modules.items()
        if isinstance(module_config, dict) and module_config.get("enabled") is True
    }
    return enabled if available_modules is None else enabled.intersection(available_modules)


def has_order_catalog(config: Dict[str, Any]) -> bool:
    modules = config.get("modules", {}) if isinstance(config, dict) else {}
    order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
    if not isinstance(order_config, dict):
        return False
    for category in order_config.get("categories", []) or []:
        if not isinstance(category, dict) or category.get("enabled", True) is False:
            continue
        for item in category.get("items", []) or []:
            if not isinstance(item, dict) or item.get("enabled", True) is False:
                continue
            if str(item.get("name") or "").strip():
                return True
    return False


def build_self_serve_suggestions(
    config: Dict[str, Any],
    limit: int = 5,
    available_modules: Optional[Collection[str]] = None,
    behavior_profile_resolution: Any = None,
) -> List[str]:
    modules = enabled_modules(config, available_modules)
    suggestions: List[str] = []
    if "order" in modules and has_order_catalog(config):
        suggestions.extend(["Show menu", "Place simple order"])
    if "booking" in modules:
        suggestions.append("Book appointment")
    if "faq" in modules or config.get("services"):
        suggestions.append("Services & prices")
    if "faq" in modules or config.get("working_hours"):
        suggestions.append("Opening hours")
    if not suggestions:
        if "lead_capture" in modules:
            suggestions.append("Request callback")
        if "support" in modules:
            suggestions.append("Get support")

    if not suggestions:
        suggestions = list(SELF_SERVE_FALLBACKS)
    suggestions = _rank_suggestions_by_behavior_profile(
        _dedupe(suggestions),
        behavior_profile_resolution,
        available_modules=modules,
    )
    return suggestions[:limit]


def build_capability_menu(
    config: Dict[str, Any],
    business_name: Optional[str] = None,
    available_modules: Optional[Collection[str]] = None,
    behavior_profile_resolution: Any = None,
    menu_requested: bool = False,
) -> CapabilityMenu:
    modules = enabled_modules(config, available_modules)
    profile = _active_behavior_profile(behavior_profile_resolution)
    suggestions = build_self_serve_suggestions(
        config,
        available_modules=available_modules,
        behavior_profile_resolution=behavior_profile_resolution,
    )
    capabilities: List[str] = []
    if "booking" in modules:
        capabilities.append("appointments")
    if "order" in modules and has_order_catalog(config):
        capabilities.append("simple orders")
    if "faq" in modules or config.get("services"):
        capabilities.append("services and prices")
    if "faq" in modules or config.get("working_hours"):
        capabilities.append("opening hours")
    if not capabilities:
        if "lead_capture" in modules:
            capabilities.append("callbacks")
        if "support" in modules:
            capabilities.append("support requests")
    if not capabilities:
        capabilities = ["business questions"]

    business = sanitize_untrusted_text(
        business_name or config.get("business_name") or "this business",
        max_chars=80,
    ) or "this business"
    capability_text = _human_join(capabilities)
    greeting_mode = str(getattr(profile, "greeting_mode", "") or "hybrid") if profile else "hybrid"
    menu_policy = str(getattr(profile, "menu_policy", "") or "show_available_workflows") if profile else "show_available_workflows"
    if menu_policy == "hide_menu_unless_asked" and not menu_requested:
        suggestions = []
        text = f"Hi, I can help with {business}.\n\nWhat can I help you with today?"
    elif menu_policy == "hide_menu_unless_asked":
        text = (
            f"Here are the options currently available for {business}.\n\n"
            "Choose an option or ask your question."
        )
    elif greeting_mode == "ask_need_first":
        suggestions = []
        text = f"Hi, I can help with {business}.\n\nWhat can I help you with today?"
    elif greeting_mode == "menu_first":
        text = (
            f"Hi, I can help with {capability_text} for {business}.\n\n"
            "Choose an option or ask your question."
        )
    else:
        text = (
            f"Hi, I can help with {capability_text} for {business}.\n\n"
            f"What would you like to do?"
        )
    return CapabilityMenu(text=text, suggestions=suggestions, capabilities=capabilities)


def is_discovery_message(message: str, intent: Optional[str] = None) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return True
    normalized = re.sub(r"\s+", " ", text)
    if _is_escalation_or_urgent_request(normalized):
        return False
    if normalized in GREETING_TEXTS:
        return True
    if any(phrase in normalized for phrase in BROAD_HELP_PHRASES):
        return True
    if normalized.startswith(("hi ", "hi,", "hello ", "hello,", "hey ", "hey,")) and "help" in normalized:
        return True
    return intent in {"greeting", "unknown"}


def is_explicit_capability_request(message: str) -> bool:
    """Distinguish a requested menu from an initial greeting without changing availability."""
    normalized = re.sub(r"\s+", " ", str(message or "").strip().lower())
    if not normalized:
        return False
    if any(phrase in normalized for phrase in BROAD_HELP_PHRASES):
        return True
    return normalized in {"menu", "show menu", "options", "show options", "capabilities"}


def should_send_discovery_menu(context: Any, intent: Optional[str], message: str) -> bool:
    state = getattr(getattr(context, "session_data", None), "state", None)
    if state is None:
        return False
    ledger = getattr(state, "action_ledger", None)
    if ledger:
        if getattr(ledger, "pending_action", None) or getattr(ledger, "last_error", None):
            return False
        if getattr(ledger, "current_task", None):
            return False
    if getattr(context.session_data, "current_module", None):
        return False
    if getattr(state, "step_data", None):
        return False
    if getattr(state, "scoped_step_data", None):
        return False
    return is_discovery_message(message, intent)


def build_direct_action_response(
    context: Any,
    message: str,
    available_modules: Optional[Collection[str]] = None,
) -> Optional[MenuResponse]:
    normalized = _normalize_action(message)
    config = context.config.config_data
    if normalized in {"book appointment", "book an appointment", "appointment booking"}:
        if available_modules is not None and "booking" not in available_modules:
            return None
        if not _can_handle_direct_action(context, expected_task="booking"):
            return None
        _activate_task(context, "booking")
        return build_service_choices_response(config, booking_mode=True)
    if normalized in {"services prices", "services and prices", "service prices", "prices", "service price"}:
        if not _can_handle_direct_action(context):
            return None
        return build_service_choices_response(config, booking_mode=False)
    if normalized in {"show menu", "menu", "show catalog", "catalog"}:
        if available_modules is not None and "order" not in available_modules:
            return None
        if not _can_handle_direct_action(context, expected_task="order", allow_empty_task=True):
            return None
        return build_catalog_choices_response(config, ordering_mode=False)
    if normalized in {"place simple order", "place order", "order now", "simple order"}:
        if available_modules is not None and "order" not in available_modules:
            return None
        if not _can_handle_direct_action(context, expected_task="order"):
            return None
        _activate_task(context, "order")
        return build_catalog_choices_response(config, ordering_mode=True)
    if normalized in {"opening hours", "hours", "business hours", "timings", "timing"}:
        if not _can_handle_direct_action(context):
            return None
        return build_hours_response(config)
    return None


def build_service_choices_response(config: Dict[str, Any], booking_mode: bool = False, limit: int = 8) -> MenuResponse:
    services = [
        service
        for service in (config.get("services") or [])
        if isinstance(service, dict) and service.get("enabled", True) is not False
    ]
    if not services:
        if booking_mode:
            return MenuResponse("Please tell me the service you would like to book.", [])
        return MenuResponse(
            "I do not see any services configured yet. You can ask a specific question or check opening hours.",
            ["Opening hours"],
        )
    visible = services[:limit]
    lines = [_format_service_line(service) for service in visible]
    labels = [_option_label(service.get("name") or "Service") for service in visible]
    labels = [label for label in labels if label]
    heading = "Which service would you like to book?" if booking_mode else "Here are the available services:"
    tail = "Choose a service to continue." if booking_mode else "Choose a service if you want to book."
    return MenuResponse(f"{heading}\n\n" + "\n".join(lines) + f"\n\n{tail}", labels[:10])


def build_catalog_choices_response(config: Dict[str, Any], ordering_mode: bool = False, limit: int = 8) -> MenuResponse:
    items = _catalog_items(config)
    if not items:
        return MenuResponse(
            "I do not see any active menu items right now. You can still ask about services or opening hours.",
            ["Services & prices", "Opening hours"],
        )
    visible = items[:limit]
    lines = [_format_catalog_line(item) for item in visible]
    labels = [_option_label(item.get("name") or "Item") for item in visible]
    labels = [label for label in labels if label]
    if len(labels) < 2 and labels:
        labels.append("Place simple order")
    heading = "What would you like to order?" if ordering_mode else "Here are the available menu items:"
    tail = "Choose an item to continue."
    return MenuResponse(f"{heading}\n\n" + "\n".join(lines) + f"\n\n{tail}", labels[:10])


def build_hours_response(config: Dict[str, Any]) -> MenuResponse:
    hours = config.get("working_hours") if isinstance(config.get("working_hours"), dict) else {}
    if not hours:
        return MenuResponse("Opening hours are not configured yet. You can ask about services instead.", ["Services & prices"])
    context = _matched_hours_context(hours, "hours")
    return MenuResponse(
        context or "Opening hours are not configured yet. You can ask about services instead.",
        [] if context else ["Services & prices"],
    )


def build_matched_business_context(
    config: Dict[str, Any],
    message: str,
    limit: int = 5,
    *,
    intent: Optional[str] = None,
    route_type: Optional[str] = None,
    is_tangent: bool = False,
    current_task: Optional[str] = None,
    available_modules: Optional[Collection[str]] = None,
    additional_intents: Optional[Collection[str]] = None,
) -> str:
    text = str(message or "").strip()
    normalized = text.lower()
    sections: List[str] = []
    normalized_intent = str(intent or "").strip().lower()
    normalized_route = str(route_type or "").strip().lower()
    normalized_task = str(current_task or "").strip().lower()
    additional_scopes = {
        str(value).strip().lower()
        for value in (additional_intents or ())
        if str(value).strip()
    }
    effective_scope = normalized_intent
    if normalized_route == "active_task" and normalized_task:
        effective_scope = normalized_task

    if not is_tangent and normalized_intent in {"greeting", "thanks", "cancel"}:
        return ""

    include_all_business_context = (
        not normalized_intent
        or is_tangent
        or normalized_intent in {"faq", "support", "unknown", "ambiguous"}
    )
    include_services = (
        include_all_business_context
        or effective_scope == "booking"
        or "booking" in additional_scopes
    )
    include_catalog = (
        include_all_business_context
        or effective_scope == "order"
        or "order" in additional_scopes
    )
    include_hours_and_faqs = (
        include_all_business_context
        or normalized_intent == "faq"
        or "faq" in additional_scopes
    )

    runtime_modules = enabled_modules(config, available_modules)
    services = (
        _matched_services(config.get("services", []) or [], normalized, limit=limit)
        if include_services and "booking" in runtime_modules
        else []
    )
    if services:
        lines = [
            _format_service_line(service)
            for service in services
        ]
        sections.append("Relevant services:\n" + "\n".join(line for line in lines if line))

    items = _matched_catalog_items(config, normalized, limit=limit) if include_catalog and "order" in runtime_modules else []
    if items:
        lines = [
            _format_catalog_line(item)
            for item in items
        ]
        sections.append("Relevant catalog items:\n" + "\n".join(line for line in lines if line))

    hours_context = _matched_hours_context(config.get("working_hours") or {}, normalized) if include_hours_and_faqs else ""
    if hours_context:
        sections.append(hours_context)

    faqs = (
        _matched_faqs(config.get("faqs", []) or [], normalized, limit=3)
        if include_hours_and_faqs and "faq" in runtime_modules
        else []
    )
    if faqs:
        lines = []
        for faq in faqs:
            question = sanitize_untrusted_text(str(faq.get("question") or ""), max_chars=180)
            answer = sanitize_untrusted_text(str(faq.get("answer") or ""), max_chars=500)
            if question and answer:
                lines.append(f"- Q: {question}\n  A: {answer}")
        if lines:
            sections.append("Relevant configured FAQs:\n" + "\n".join(lines))

    return "\n\n".join(sections)


def compact_capability_summary(
    config: Dict[str, Any],
    available_modules: Optional[Collection[str]] = None,
) -> str:
    modules = enabled_modules(config, available_modules)
    services = config.get("services", []) if isinstance(config.get("services"), list) else []
    working_hours = config.get("working_hours", {}) if isinstance(config.get("working_hours"), dict) else {}
    faqs = config.get("faqs", []) if isinstance(config.get("faqs"), list) else []
    category_count, item_count = _catalog_counts(config) if "order" in modules else (0, 0)
    menu = build_capability_menu(config, available_modules=available_modules)
    return "\n".join([
        "Business capability summary:",
        f"- Enabled modules: {', '.join(sorted(modules)) if modules else 'none'}",
        f"- Self-serve menu options: {', '.join(menu.suggestions)}",
        f"- Bookable services configured: {len(services) if 'booking' in modules else 0}",
        f"- Order catalog: {category_count} categories, {item_count} active items",
        f"- Working-hours days configured: {len(working_hours)}",
        f"- Configured FAQs: {len(faqs) if 'faq' in modules else 0}",
        "- Human handoff is fallback only; do not show it in the initial self-serve menu.",
    ])


def _matched_services(services: List[Any], normalized: str, limit: int) -> List[Dict[str, Any]]:
    if not services:
        return []
    if _asks_for_services(normalized):
        return [service for service in services if isinstance(service, dict)][:limit]
    query_tokens = _tokens(normalized)
    matches: List[Dict[str, Any]] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        haystack = " ".join(str(service.get(key) or "") for key in ("id", "name", "description")).lower()
        if _overlaps(query_tokens, haystack):
            matches.append(service)
    return matches[:limit]


def _matched_catalog_items(config: Dict[str, Any], normalized: str, limit: int) -> List[Dict[str, Any]]:
    items = _catalog_items(config)
    if not items:
        return []
    if _asks_for_catalog(normalized):
        return items[:limit]
    query_tokens = _tokens(normalized)
    matches: List[Dict[str, Any]] = []
    for item in items:
        haystack = " ".join(str(item.get(key) or "") for key in ("id", "name", "description", "category_name")).lower()
        if _overlaps(query_tokens, haystack):
            matches.append(item)
    return matches[:limit]


def _matched_hours_context(hours: Dict[str, Any], normalized: str) -> str:
    if not hours or not _asks_for_hours(normalized):
        return ""
    target_days = _target_days(normalized)
    if not target_days:
        target_days = list(hours.keys())[:7]
    lines = []
    for day in target_days:
        schedule = hours.get(day)
        if schedule is None:
            continue
        safe_day = sanitize_untrusted_text(day.capitalize(), max_chars=20)
        if isinstance(schedule, dict):
            if not schedule.get("is_open", False):
                lines.append(f"- {safe_day}: closed")
            else:
                open_time = sanitize_untrusted_text(schedule.get("open_time") or "09:00", max_chars=20)
                close_time = sanitize_untrusted_text(schedule.get("close_time") or "17:00", max_chars=20)
                lines.append(f"- {safe_day}: {open_time}-{close_time}")
        else:
            lines.append(f"- {safe_day}: {sanitize_untrusted_text(schedule, max_chars=80)}")
    return "Relevant opening hours:\n" + "\n".join(lines) if lines else ""


def _matched_faqs(faqs: List[Any], normalized: str, limit: int) -> List[Dict[str, Any]]:
    if not faqs:
        return []
    query_tokens = _tokens(normalized)
    matches: List[tuple[int, Dict[str, Any]]] = []
    for faq in faqs:
        if not isinstance(faq, dict):
            continue
        haystack = f"{faq.get('question') or ''} {faq.get('answer') or ''}".lower()
        score = sum(1 for token in query_tokens if token in haystack)
        if score:
            matches.append((score, faq))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [faq for _, faq in matches[:limit]]


def _asks_for_services(normalized: str) -> bool:
    return any(term in normalized for term in ("service", "services", "price", "prices", "cost", "costs", "how much"))


def _normalize_action(message: str) -> str:
    text = re.sub(r"&", " and ", str(message or "").strip().lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _can_handle_direct_action(
    context: Any,
    expected_task: Optional[str] = None,
    allow_empty_task: bool = True,
) -> bool:
    state = getattr(getattr(context, "session_data", None), "state", None)
    if state is None:
        return False
    ledger = getattr(state, "action_ledger", None)
    if not ledger:
        return True
    if getattr(ledger, "pending_action", None) or getattr(ledger, "last_error", None):
        return False
    current_task = getattr(ledger, "current_task", None) or getattr(context.session_data, "current_module", None)
    if not current_task:
        return allow_empty_task
    return bool(expected_task and current_task == expected_task)


def _activate_task(context: Any, task_key: str) -> None:
    state = getattr(getattr(context, "session_data", None), "state", None)
    if not state:
        return
    state.action_ledger.current_task = task_key
    state.action_ledger.task_status = "collecting"
    context.session_data.current_module = task_key
    state.activate_task_step_data(task_key)


def _is_escalation_or_urgent_request(normalized: str) -> bool:
    return any(
        term in normalized
        for term in (
            "human",
            "person",
            "agent",
            "representative",
            "talk to someone",
            "speak to someone",
            "emergency",
            "urgent",
            "complaint",
            "angry",
            "upset",
        )
    )


def _asks_for_catalog(normalized: str) -> bool:
    return any(term in normalized for term in ("menu", "catalog", "order", "item", "items", "buy", "purchase", "show menu"))


def _asks_for_hours(normalized: str) -> bool:
    return any(term in normalized for term in ("hour", "hours", "timing", "open", "close", "closed", "today", "tomorrow"))


def _target_days(normalized: str) -> List[str]:
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    hits = [day for day in day_names if day in normalized]
    if "today" in normalized:
        hits.append(date.today().strftime("%A").lower())
    if "tomorrow" in normalized:
        hits.append((date.today() + timedelta(days=1)).strftime("%A").lower())
    return _dedupe(hits)


def _catalog_items(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    modules = config.get("modules", {}) if isinstance(config, dict) else {}
    order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
    if not isinstance(order_config, dict) or order_config.get("enabled") is not True:
        return []
    items: List[Dict[str, Any]] = []
    for category in order_config.get("categories", []) or []:
        if not isinstance(category, dict) or category.get("enabled", True) is False:
            continue
        category_name = category.get("name")
        for item in category.get("items", []) or []:
            if not isinstance(item, dict) or item.get("enabled", True) is False:
                continue
            item_copy = dict(item)
            item_copy["category_name"] = category_name
            items.append(item_copy)
    return items


def _catalog_counts(config: Dict[str, Any]) -> tuple[int, int]:
    modules = config.get("modules", {}) if isinstance(config, dict) else {}
    order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
    if not isinstance(order_config, dict) or order_config.get("enabled") is not True:
        return 0, 0
    categories = 0
    items = 0
    for category in order_config.get("categories", []) or []:
        if not isinstance(category, dict) or category.get("enabled", True) is False:
            continue
        categories += 1
        for item in category.get("items", []) or []:
            if isinstance(item, dict) and item.get("enabled", True) is not False:
                items += 1
    return categories, items


def _format_service_line(service: Dict[str, Any]) -> str:
    name = _safe_label_text(service.get("name") or "Service", max_chars=120)
    price = service.get("price")
    duration = service.get("duration_mins") or service.get("duration_minutes")
    parts = [f"- {name}"]
    if price not in (None, ""):
        parts.append(f"INR {price}")
    if duration:
        parts.append(f"{duration} mins")
    return " - ".join(parts)


def _format_catalog_line(item: Dict[str, Any]) -> str:
    name = _safe_label_text(item.get("name") or "Item", max_chars=120)
    sku = _safe_label_text(item.get("sku") or "", max_chars=40)
    sku_text = f" (sku: {sku})" if sku else ""
    price = item.get("price")
    category = _safe_label_text(item.get("category_name") or "", max_chars=80)
    parts = [f"- {name}{sku_text}"]
    if price not in (None, ""):
        parts.append(f"INR {price}")
    if category:
        parts.append(category)
    return " - ".join(parts)


def _option_label(value: Any) -> str:
    label = sanitize_untrusted_text(str(value or "").strip(), max_chars=24)
    label = re.sub(r"\s+", " ", label).strip()
    return label[:24].strip()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in {"what", "when", "where", "with", "from", "that", "this", "please"}
    }


def _overlaps(query_tokens: set[str], haystack: str) -> bool:
    if not query_tokens:
        return False
    haystack_tokens = _tokens(haystack)
    if query_tokens & haystack_tokens:
        return True
    return any(token in haystack for token in query_tokens)


def _human_join(items: List[str]) -> str:
    clean = [item for item in items if item]
    if not clean:
        return "business questions"
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return ", ".join(clean[:-1]) + f", and {clean[-1]}"


def _dedupe(items: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        key = str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(str(item).strip())
    return result
