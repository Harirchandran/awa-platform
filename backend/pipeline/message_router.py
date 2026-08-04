"""
Message Router - Module Dispatch Decision Layer.

Determines which workflow module should handle an incoming message,
keeping this logic separate from the workflow engine orchestration.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
import re


@dataclass(frozen=True)
class RoutingDecision:
    intent: str
    confidence: float
    route_type: str
    is_tangent: bool
    should_clear_active_task: bool
    reason: str


class MessageRouter:
    """
    Decides which module executes based on message content and tenant config.
    Keeps workflow_engine focused on state orchestration rather than routing.
    """

    BOOKING_KEYWORDS = {"book", "appointment", "schedule", "reserve", "reschedule"}
    ORDER_KEYWORDS = {
        "order", "buy", "purchase", "cart", "menu", "delivery", "deliver",
        "pickup", "takeaway", "take away", "checkout",
    }
    ORDER_PHRASES = {
        "place order", "make an order", "want to order", "need to order",
        "can i order", "i want order", "i need order",
    }
    LEAD_KEYWORDS = {"interested", "info", "information", "learn", "sign up", "signup", "contact", "newsletter"}
    PAYMENT_KEYWORDS = {"payment", "pay", "charge", "paid", "collect payment", "payment link"}
    REMINDER_KEYWORDS = {"remind", "reminder", "reminders"}
    REVIEW_KEYWORDS = {"review", "rate", "rating", "star", "stars", "feedback"}
    SUPPORT_KEYWORDS = {
        "help", "support", "problem", "issue", "complaint", "broken", "fix",
        "human", "person", "handoff", "speak to", "agent", "representative",
    }
    FAQ_KEYWORDS = {
        "price", "cost", "location", "located", "where", "hours", "timing",
        "when", "open", "close", "insur", "accept", "what", "how much",
    }
    GREETING_HELP_PHRASES = {
        "what can you help",
        "how can you help",
        "what can i do",
        "what can you do",
        "what do you do",
        "how does this work",
    }
    THANKS_BYE_TEXTS = {
        "thanks", "thank you", "thankyou", "thx", "bye", "goodbye", "see you",
    }
    CANCEL_TEXTS = {
        "cancel", "stop", "reset", "start over", "nevermind", "never mind",
        "forget it", "discard", "clear",
    }
    EXACT_CATALOG_REQUESTS = {
        "show menu", "menu", "show catalog", "catalog", "items", "show items",
    }
    EXACT_HOURS_REQUESTS = {
        "opening hours", "hours", "business hours", "timings", "timing",
    }
    SHORT_ACTIVE_REPLIES = {
        "yes", "y", "no", "n", "ok", "okay", "sure", "confirm", "change", "edit",
        "correct", "agree", "proceed", "go ahead", "pickup", "delivery", "cod",
        "cash", "upi", "card",
    }

    def route_message(
        self,
        message: str,
        config: Dict[str, Any],
        *,
        current_task: Optional[str] = None,
        current_module: Optional[str] = None,
        has_pending_disambiguation: bool = False,
    ) -> RoutingDecision:
        """
        Return a structured routing decision for the current user message.

        This is deterministic Phase 3D routing only. LLM triage is intentionally
        not invoked here because the existing AIClient.classify_intent path does
        not provide the required timeout and provider/fake safety boundary.

        When ``has_pending_disambiguation`` is True the customer is choosing
        among previously-offered options (e.g. "1", "2", or a label). Any
        message must route back to the active task so the disambiguation handler
        can resolve the selection without treating "1" as an item name to search.
        """
        normalized = self._normalize(message)
        active_task = current_task or current_module
        has_active_task = bool(active_task)

        # Fast-path: disambiguation is pending — all messages continue the active task
        if has_pending_disambiguation and active_task:
            return RoutingDecision(
                active_task,
                0.95,
                "active_task",
                False,
                False,
                "active_task_disambiguation_selection",
            )
        if not normalized:
            return RoutingDecision("greeting", 1.0, "direct_answer", False, False, "empty_message_greeting")

        if normalized in self.CANCEL_TEXTS:
            return RoutingDecision(
                "cancel",
                1.0,
                "direct_answer",
                False,
                has_active_task,
                "explicit_cancellation",
            )

        if normalized in self.THANKS_BYE_TEXTS:
            return RoutingDecision(
                "thanks",
                1.0,
                "direct_answer",
                has_active_task,
                False,
                "thanks_or_goodbye",
            )

        if self._is_greeting(normalized):
            if has_active_task:
                return RoutingDecision(
                    active_task,
                    0.9,
                    "active_task",
                    False,
                    False,
                    "active_task_short_continuation",
                )
            return RoutingDecision("greeting", 1.0, "direct_answer", False, False, "greeting")

        if normalized in self.EXACT_CATALOG_REQUESTS:
            return RoutingDecision(
                "order",
                1.0,
                "direct_action",
                bool(active_task and active_task != "order"),
                False,
                "exact_catalog_request",
            )

        if self._is_natural_catalog_request(normalized):
            return RoutingDecision(
                "order",
                0.95,
                "direct_action",
                bool(active_task and active_task != "order"),
                False,
                "natural_catalog_request",
            )

        if normalized in self.EXACT_HOURS_REQUESTS:
            return RoutingDecision(
                "faq",
                1.0,
                "direct_action",
                has_active_task,
                False,
                "exact_opening_hours_request",
            )

        deterministic_intent = self._classify_deterministic_intent(normalized, config)
        if deterministic_intent == "ambiguous":
            return RoutingDecision(
                "ambiguous",
                0.95,
                "clarification",
                has_active_task,
                False,
                "mixed_workflow_intent",
            )

        if has_active_task:
            if deterministic_intent == "faq":
                return RoutingDecision(
                    "faq",
                    0.85,
                    "direct_answer",
                    True,
                    False,
                    "active_task_faq_tangent",
                )
            if deterministic_intent in {"unknown", "greeting"} and self._is_likely_active_field_value(normalized):
                return RoutingDecision(
                    active_task,
                    0.8,
                    "active_task",
                    False,
                    False,
                    "active_task_likely_field_value",
                )
            if deterministic_intent not in {"unknown", active_task}:
                return RoutingDecision(
                    deterministic_intent,
                    0.9,
                    "workflow",
                    False,
                    True,
                    "explicit_workflow_switch",
                )
            return RoutingDecision(
                active_task,
                0.75,
                "active_task",
                False,
                False,
                "active_task_continuation",
            )

        route_type = "workflow" if self.get_module_for_intent(deterministic_intent, config) else "direct_answer"
        if deterministic_intent == "unknown":
            route_type = "unknown"
        reason = "deterministic_keyword_match" if deterministic_intent != "unknown" else "no_deterministic_match"
        if (
            deterministic_intent == "booking"
            and self._message_mentions_booking_service(normalized, config)
            and self._has_booking_turn_hint(normalized)
        ):
            reason = "configured_service_booking_candidate"
        return RoutingDecision(
            deterministic_intent,
            0.75 if deterministic_intent != "unknown" else 0.4,
            route_type,
            False,
            False,
            reason,
        )

    def classify_intent(self, message: str, config: Dict[str, Any]) -> str:
        """
        Compatibility wrapper for older callers that only need the intent.

        Structured Phase 3D metadata is available through route_message().
        """
        return self.route_message(message, config).intent

    def _classify_deterministic_intent(self, normalized: str, config: Dict[str, Any]) -> str:
        if not normalized:
            return "greeting"
        if self._is_greeting(normalized):
            return "greeting"

        modules = config.get("modules", {}) if isinstance(config, dict) else {}
        booking_hit = any(self._contains_keyword(normalized, kw) for kw in self.BOOKING_KEYWORDS)
        configured_service_booking_hit = (
            self._message_mentions_booking_service(normalized, config)
            and self._has_booking_turn_hint(normalized)
        )
        order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
        order_hit = False
        explicit_order_hit = False
        if isinstance(order_config, dict) and order_config.get("enabled", False):
            explicit_order_hit = (
                any(self._contains_keyword(normalized, kw) for kw in self.ORDER_KEYWORDS)
                or any(phrase in normalized for phrase in self.ORDER_PHRASES)
            )
            order_hit = (
                explicit_order_hit
                or self._message_mentions_order_item(normalized, order_config)
            )
        # A tenant may expose the same label in both its bookable services and
        # order catalog. Temporal appointment language is stronger evidence for
        # booking than a passive catalog-name match; explicit ordering language
        # still wins or remains ambiguous through the normal rules below.
        if configured_service_booking_hit and not explicit_order_hit:
            return "booking"
        if booking_hit and order_hit:
            return "ambiguous"
        if order_hit:
            return "order"
        if booking_hit:
            return "booking"
        if configured_service_booking_hit:
            return "booking"
        if isinstance(modules, dict) and modules.get("payments", {}).get("enabled", False):
            if any(self._contains_keyword(normalized, kw) for kw in self.PAYMENT_KEYWORDS):
                return "payments"
        if isinstance(modules, dict) and modules.get("lead_capture", {}).get("enabled", False):
            if any(kw in normalized for kw in self.LEAD_KEYWORDS):
                return "lead_capture"
        support_enabled = (
            isinstance(modules, dict)
            and (
                modules.get("support", {}).get("enabled", False)
                or modules.get("human_handoff", {}).get("enabled", False)
            )
        )
        if support_enabled:
            if any(self._contains_keyword(normalized, kw) for kw in self.SUPPORT_KEYWORDS):
                return "support"
        if isinstance(modules, dict) and modules.get("reminders", {}).get("enabled", False):
            if any(self._contains_keyword(normalized, kw) for kw in self.REMINDER_KEYWORDS):
                return "reminders"
        if isinstance(modules, dict) and modules.get("reviews", {}).get("enabled", False):
            if any(self._contains_keyword(normalized, kw) for kw in self.REVIEW_KEYWORDS):
                return "reviews"
        if any(kw in normalized for kw in self.FAQ_KEYWORDS):
            return "faq"
        return "unknown"

    def _message_mentions_booking_service(self, normalized_message: str, config: Dict[str, Any]) -> bool:
        modules = config.get("modules", {}) if isinstance(config, dict) else {}
        booking_config = modules.get("booking", {}) if isinstance(modules, dict) else {}
        if not isinstance(booking_config, dict) or booking_config.get("enabled") is not True:
            return False
        for service in config.get("services", []) or []:
            if not isinstance(service, dict) or service.get("enabled", True) is False:
                continue
            name = self._normalize(service.get("name", ""))
            if len(name) >= 3 and name in normalized_message:
                return True
        return False

    def _has_booking_turn_hint(self, normalized_message: str) -> bool:
        if any(self._contains_keyword(normalized_message, keyword) for keyword in self.BOOKING_KEYWORDS):
            return True
        if any(
            term in normalized_message
            for term in (
                "today", "tomorrow", "morning", "afternoon", "evening",
                "this week", "next week", "later this week", "weekend",
                "after ", "before ", "sometime", "available", "availability",
            )
        ):
            return True
        if any(day in normalized_message for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
            return True
        if re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", normalized_message):
            return True
        return re.search(
            r"\b(?:at|after|before|around)\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
            normalized_message,
        ) is not None

    def _message_mentions_order_item(self, normalized_message: str, order_config: Dict[str, Any]) -> bool:
        for category in order_config.get("categories", []) or []:
            if not isinstance(category, dict) or category.get("enabled", True) is False:
                continue
            for item in category.get("items", []) or []:
                if not isinstance(item, dict) or item.get("enabled", True) is False:
                    continue
                name = str(item.get("name", "")).strip().lower()
                if len(name) >= 3 and name in normalized_message:
                    return True
        return False

    @staticmethod
    def _contains_keyword(normalized_message: str, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        if not keyword:
            return False
        if " " in keyword:
            return keyword in normalized_message
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", normalized_message) is not None

    @staticmethod
    def _normalize(message: str) -> str:
        text = str(message or "").strip().lower()
        text = re.sub(r"[^a-z0-9+]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _is_greeting(self, normalized: str) -> bool:
        if normalized in ("hi", "hello", "hey", "start", "good morning", "good afternoon", "good evening"):
            return True
        if any(phrase in normalized for phrase in self.GREETING_HELP_PHRASES):
            return True
        return normalized.startswith(("hi ", "hello ", "hey ")) and "help" in normalized

    def _is_natural_catalog_request(self, normalized: str) -> bool:
        if not any(term in normalized for term in ("menu", "menue", "catalog")):
            return False
        if any(self._contains_keyword(normalized, term) for term in ("order", "buy", "purchase", "checkout")):
            return False
        return (
            normalized in {"menu please", "menue please", "catalog please"}
            or normalized.startswith(("show ", "see ", "view ", "send ", "share "))
            or any(phrase in normalized for phrase in ("can i see", "could i see", "what is on", "whats on"))
        )

    def _is_likely_active_field_value(self, normalized: str) -> bool:
        if not normalized:
            return False
        if normalized in self.SHORT_ACTIVE_REPLIES:
            return True
        if re.fullmatch(r"\d{1,6}", normalized):
            return True
        if re.fullmatch(r"\d{1,2}(:\d{2})?\s?(am|pm)?", normalized):
            return True
        if normalized in {"today", "tomorrow"}:
            return True
        if any(day in normalized for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
            return True
        words = normalized.split()
        workflow_keywords = (
            *self.BOOKING_KEYWORDS,
            *self.ORDER_KEYWORDS,
            *self.LEAD_KEYWORDS,
            *self.PAYMENT_KEYWORDS,
            *self.REMINDER_KEYWORDS,
            *self.REVIEW_KEYWORDS,
            *self.SUPPORT_KEYWORDS,
            *self.FAQ_KEYWORDS,
        )
        return 1 <= len(words) <= 4 and not any(
            self._contains_keyword(normalized, keyword)
            for keyword in workflow_keywords
        )

    def get_module_for_intent(self, intent: str, config: Dict[str, Any]) -> Optional[str]:
        """
        Maps an intent to a module name if the module is enabled in tenant config.
        Returns None if no module should handle this intent.
        """
        modules = config.get("modules", {}) if isinstance(config, dict) else {}
        if intent == "booking" and modules.get("booking", {}).get("enabled", False):
            return "booking"
        if intent == "order" and modules.get("order", {}).get("enabled", False):
            return "order"
        if intent == "lead_capture" and modules.get("lead_capture", {}).get("enabled", False):
            return "lead_capture"
        if intent == "payments" and modules.get("payments", {}).get("enabled", False):
            return "payments"
        if intent == "support" and (
            modules.get("support", {}).get("enabled", False)
            or modules.get("human_handoff", {}).get("enabled", False)
        ):
            return "support"
        if intent == "reminders" and modules.get("reminders", {}).get("enabled", False):
            return "reminders"
        if intent == "reviews" and modules.get("reviews", {}).get("enabled", False):
            return "reviews"
        return None

    def should_use_module(self, intent: str, config: Dict[str, Any]) -> bool:
        """Check if a module-based workflow should be started for this intent."""
        return self.get_module_for_intent(intent, config) is not None


message_router = MessageRouter()
