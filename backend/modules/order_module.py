from __future__ import annotations
import re
import hashlib
import json
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Dict, List, Mapping, Optional
from backend.core.context import ExecutionContext

from backend.domains.catalog.service import catalog_service
from backend.domains.orders.tool_definitions import (
    DEFAULT_ORDER_FIELDS,
    get_order_required_fields,
    get_order_tool_metadata,
    get_order_tool_schema,
)
from backend.domains.orders import tool_handlers as order_tool_handlers
from backend.domains.orders.service import order_service
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult


@dataclass(frozen=True)
class OrderConfiguredCandidateValidationResult:
    """Dry-run deterministic validation of externally supplied configured-field candidates.

    This result is OBSERVATIONAL ONLY. It records which exact configured keys
    would be accepted or rejected by the existing deterministic order validation
    authority without mutating any authoritative order state. It contains no
    customer-visible text, no tool data, and no persistence claims.
    """

    accepted_patch: Mapping[str, Any]
    accepted_field_keys: tuple[str, ...]
    rejected_field_keys: tuple[str, ...]
    reason_code: str | None


# Item/identity/financial/internal keys the order shadow must never accept from
# a semantic candidate or validate. Exact match only.
_ORDER_SHADOW_PROTECTED_KEYS: frozenset[str] = frozenset({
    "item_name",
    "item_sku",
    "item_id",
    "items",
    "_backend_refs",
    "pending_disambiguation",
    "order_id",
    "tenant_id",
    "idempotency_key",
    "unit_price",
    "price",
    "subtotal",
    "total",
    "currency",
    "status",
    "payment_status",
    "tool_name",
    "tool_args",
    "id",
    "uuid",
    "token",
    "secret",
    "trace",
    "raw",
    "payload",
    "backend",
    "otp",
    "password",
    "customer_id",
})


@dataclass(frozen=True)
class ExternalOrderCandidateApplicationResult:
    """Typed outcome of an authoritative external-candidate application.

    Replaces a Boolean signal so the engine can distinguish four outcomes:

      * applied new values (``handled`` + ``applied`` + ``update_performed``);
      * already satisfied with no write (``handled`` only,
        ``reason_code == "already_satisfied"``);
      * not handled because candidates were invalid/unavailable
        (``handled`` is False, existing focused collector may run);
      * uncertain write failure (propagated as an exception, never a normal
        handled result).

    It is fact-only: no customer values, no state references, no tool data, and
    no persistence success claim beyond ``update_performed``. Only exact sorted
    configured keys are exposed so the engine can continue from authoritative
    order state.
    """

    handled: bool
    applied: bool
    update_performed: bool
    accepted_field_keys: tuple[str, ...] = ()
    rejected_field_keys: tuple[str, ...] = ()
    reason_code: str | None = None
class OrderModule(BaseModule):
    DEFAULT_FIELDS = DEFAULT_ORDER_FIELDS
    def get_module_name(self) -> str:
        return "order"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_order_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_order_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_order_tool_schema(config)
    def get_required_fields_for_context(self, context: ExecutionContext) -> List[Dict[str, Any]]:
        fields = self.get_required_fields(context.config.config_data)
        step_data = context.session_data.state.get_task_step_data(self.get_module_name())
        filtered: List[Dict[str, Any]] = []
        for field in fields:
            applies_when = str(field.get("applies_when") or "").strip().lower()
            if applies_when == "fulfillment_type == delivery":
                if str(step_data.get("fulfillment_type", "")).lower() != "delivery":
                    continue
            filtered.append(field)
        return filtered
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        field_type = str(field_def.get("type", "string")).lower()
        field_key = str(field_def.get("key", "")).lower()
        text = str(value or "").strip()
        if not text:
            return ValidationResult(False, "Value is empty")
        if field_key == "quantity" or field_type == "number":
            try:
                qty = int(float(text))
            except (TypeError, ValueError):
                return ValidationResult(False, "Please enter a valid quantity")
            if qty < 1 or qty > 99:
                return ValidationResult(False, "Quantity must be between 1 and 99")
            return ValidationResult(True)
        if field_type == "phone" or field_key in {"phone", "customer_phone"}:
            cleaned = re.sub(r"[\s\-()+]", "", text)
            if not re.match(r"^\d{7,15}$", cleaned):
                return ValidationResult(False, "Please provide a valid phone number")
            return ValidationResult(True)
        if field_key == "fulfillment_type":
            resolved = self.resolve_fulfillment_type(text, tenant_config or {})
            if resolved is None:
                configured = self._configured_fulfillment_options(tenant_config or {})
                options_display = ", ".join(configured) if configured else "pickup or delivery"
                return ValidationResult(
                    False,
                    f"Please choose a fulfillment option: {options_display}",
                )
            return ValidationResult(True)
        return ValidationResult(True)
    @staticmethod
    def _order_shadow_condition_met(applies_when: str, preview: Dict[str, Any]) -> bool:
        """Evaluate a single supported order conditional expression in-memory.

        Only the existing supported order condition format is evaluated. No
        arbitrary expression execution is performed.
        """
        applies_when = applies_when.strip().lower()
        match = re.fullmatch(r"([a-z_]+)\s*==\s*([\"']?)([a-z0-9 _\-]+)\2", applies_when)
        if not match:
            return False
        left, _, right = match.group(1), match.group(2), match.group(3)
        actual = str(preview.get(left, "")).strip().lower()
        return actual == right.strip().lower()
    def _order_shadow_select_options(self, key: str, context: ExecutionContext) -> list:
        """Resolve configured select options for a safe order field."""
        order_config = {}
        modules = (context.config.config_data or {}).get("modules", {}) if isinstance(context.config.config_data, dict) else {}
        order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
        if key == "fulfillment_type":
            return list(order_config.get("fulfillment_types") or [])
        if key == "payment_method":
            return list(order_config.get("payment_methods") or [])
        return []
    def validate_external_configured_candidates(
        self,
        context: ExecutionContext,
        candidates: Mapping[str, Any],
    ) -> "OrderConfiguredCandidateValidationResult":
        """Dry-run deterministic validation of externally supplied configured candidates.

        This is OBSERVATIONAL ONLY. It reuses the existing deterministic order
        validation authority (``validate_input``) so no field-type validation is
        duplicated. It never mutates order state, never modifies prompt focus,
        never touches the action ledger, never executes tools, never calls an
        LLM, and never logs candidate values.

        Protected item/identity/financial/internal keys are rejected; unknown
        keys are rejected; already-collected non-empty fields are rejected;
        currently-missing required or optional safe configured fields are
        accepted. Conditional ``applies_when`` fields are evaluated against an
        in-memory preview of current order state merged with the accepted batch.
        """
        if not isinstance(candidates, Mapping) or not candidates:
            return OrderConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(candidates.keys())) if isinstance(candidates, Mapping) else (),
                reason_code="no_eligible_candidates",
            )

        fields = self.get_required_fields(context.config.config_data)
        fields_by_key: Dict[str, Dict[str, Any]] = {}
        for field in fields:
            key = str(field.get("key") or "").strip()
            if key and key not in fields_by_key:
                fields_by_key[key] = field

        current = context.session_data.state.get_task_step_data("order") or {}
        configured_keys = set(fields_by_key.keys())

        raw_candidates: Dict[str, Any] = {}
        rejected: set[str] = set()
        for raw_key, value in candidates.items():
            if not isinstance(raw_key, str) or not raw_key:
                rejected.add(str(raw_key))
                continue
            if raw_key in _ORDER_SHADOW_PROTECTED_KEYS:
                rejected.add(raw_key)
                continue
            if raw_key not in configured_keys:
                rejected.add(raw_key)
                continue
            if raw_key not in fields_by_key:
                rejected.add(raw_key)
                continue
            if current.get(raw_key) not in (None, "", [], {}):
                rejected.add(raw_key)
                continue
            if isinstance(value, (dict, list)) and not isinstance(value, str):
                rejected.add(raw_key)
                continue
            if value is None or value == "":
                rejected.add(raw_key)
                continue
            raw_candidates[raw_key] = value

        if not raw_candidates:
            return OrderConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(rejected)),
                reason_code="no_eligible_candidates",
            )

        accepted_patch: Dict[str, Any] = {}
        accepted_keys: list[str] = []
        for key, value in raw_candidates.items():
            field_def = fields_by_key[key]
            field_type = str(field_def.get("type", "string")).lower()
            # Conditional applies_when evaluation against an in-memory preview
            # of current order state merged with the complete candidate batch.
            applies_when = str(field_def.get("applies_when") or "").strip().lower()
            if applies_when:
                preview = dict(current)
                preview.update(raw_candidates)
                if not self._order_shadow_condition_met(applies_when, preview):
                    rejected.add(key)
                    continue
            # Select options enforcement.
            if field_type == "select":
                options = field_def.get("options") or self._order_shadow_select_options(key, context)
                option_values = [str(option).strip().lower() for option in options] if isinstance(options, list) else []
                if option_values and str(value).strip().lower() not in option_values:
                    rejected.add(key)
                    continue
            validation = self.validate_input(field_def, value, tenant_config=context.config.config_data)
            if not validation.is_valid:
                rejected.add(key)
                continue
            accepted_patch[key] = value
            accepted_keys.append(key)

        if not accepted_patch:
            return OrderConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(rejected)),
                reason_code="all_candidates_rejected",
            )
        return OrderConfiguredCandidateValidationResult(
            accepted_patch=dict(accepted_patch),
            accepted_field_keys=tuple(sorted(accepted_keys)),
            rejected_field_keys=tuple(sorted(rejected)),
            reason_code="validated",
        )
    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await order_tool_handlers.execute_order_tool(
            context,
            module=self,
            order_service=order_service,
            catalog_service=catalog_service,
            logger=logger,
        )
    def prefill_from_message(self, message: str, context: ExecutionContext) -> None:
        """Best-effort parse of common first messages such as '2 biriyani delivery'."""
        step_data = context.session_data.state.step_data
        if context.user:
            user_phone = getattr(context.user, "phone_number", None)
            if user_phone and user_phone != "unknown" and not step_data.get("phone"):
                step_data["phone"] = user_phone

            user_name = getattr(context.user, "name", None)
            if user_name and user_name != "unknown" and not step_data.get("customer_name"):
                step_data["customer_name"] = user_name

        if not message:
            return

        # --- Disambiguation selection: if pending, try to resolve from this message ---
        pending_disambig = step_data.get("pending_disambiguation")
        if isinstance(pending_disambig, dict) and pending_disambig.get("options"):
            from backend.domains.orders.tool_handlers import resolve_disambiguation_selection
            resolved_item = resolve_disambiguation_selection(message, pending_disambig)
            if resolved_item is not None:
                # Resolved: populate item_name, clear pending_disambiguation
                step_data["item_name"] = resolved_item.get("name")
                step_data.pop("pending_disambiguation", None)
                # Also clear from scoped_step_data
                scoped = context.session_data.state.scoped_step_data.get(self.get_module_name())
                if isinstance(scoped, dict):
                    scoped["item_name"] = resolved_item.get("name")
                    scoped.pop("pending_disambiguation", None)
                return
            # If message looks only like a number/label for disambiguation, don't proceed
            # to item resolution (avoids searching DB for item named "1")
            stripped = str(message or "").strip()
            if re.fullmatch(r"\d{1,2}", stripped):
                # Numeric reply that didn't match – out of range, ignore silently
                return

        # --- Normal item resolve (only if no pending disambiguation) ---
        item = self.resolve_item(message, context.config.config_data) if not step_data.get("item_name") else None
        if item:
            step_data["item_name"] = item.get("name")
        if "quantity" not in step_data:
            qty_match = re.search(r"\b(\d{1,2})\b", message)
            if qty_match:
                step_data["quantity"] = int(qty_match.group(1))

        # --- Fulfillment type: use tenant-config driven normalisation ---
        if "fulfillment_type" not in step_data:
            resolved_ft = self.resolve_fulfillment_type(message, context.config.config_data)
            if resolved_ft:
                step_data["fulfillment_type"] = resolved_ft

        if "phone" not in step_data:
            phone_match = re.search(r"(?:\+?\d[\d\s().-]{6,}\d)", message)
            if phone_match:
                step_data["phone"] = re.sub(r"[^\d+]", "", phone_match.group(0))
        if "customer_name" not in step_data:
            name_match = re.search(
                r"\b(?:my name is|name is|i am|i'm)\s+([A-Za-z][A-Za-z .'-]{1,60})",
                message,
                flags=re.IGNORECASE,
            )
            if name_match:
                name = re.split(
                    r"\s*(?:,|\bphone\b|\bmobile\b)",
                    name_match.group(1).strip(),
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                if name:
                    step_data["customer_name"] = name
    def resolve_item(self, value: Any, config: Dict[str, Any]) -> Dict[str, Any] | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        items = self.get_catalog_items(config, enabled_only=True)
        if not items:
            return None
        for item in items:
            if text == str(item.get("id", "")).lower() or text == str(item.get("name", "")).lower():
                return item
        for item in items:
            name = str(item.get("name", "")).lower()
            if text in name or name in text:
                return item
        text_tokens = self._normalized_tokens(text)
        best_item = None
        best_score = 0
        for item in items:
            name_tokens = self._normalized_tokens(str(item.get("name", "")))
            if not name_tokens:
                continue
            score = len(text_tokens & name_tokens)
            if score > best_score:
                best_item = item
                best_score = score
        if best_item and best_score >= min(2, len(self._normalized_tokens(str(best_item.get("name", ""))))):
            return best_item
        names = [str(item.get("name", "")).lower() for item in items]
        matches = get_close_matches(text, names, n=1, cutoff=0.45)
        if matches:
            return items[names.index(matches[0])]
        return None
    def get_catalog_items(self, config: Dict[str, Any], enabled_only: bool = True) -> List[Dict[str, Any]]:
        order_config = self._order_config(config)
        items: List[Dict[str, Any]] = []
        for category in order_config.get("categories", []) or []:
            if not isinstance(category, dict):
                continue
            if enabled_only and category.get("enabled", True) is False:
                continue
            for item in category.get("items", []) or []:
                if not isinstance(item, dict):
                    continue
                if enabled_only and item.get("enabled", True) is False:
                    continue
                item_copy = dict(item)
                item_copy["category_id"] = category.get("id")
                item_copy["category_name"] = category.get("name")
                items.append(item_copy)
        return items
    def _order_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return config.get("modules", {}).get("order", {}) or {}
    def _configured_fulfillment_options(self, config: Dict[str, Any]) -> List[str]:
        """Return the list of fulfillment type strings configured for this tenant.

        Falls back to ["pickup", "delivery"] only as a backend default when the
        config key is absent.  This fallback is never used to interpret customer
        messages — only as a last-resort guard in validation.
        """
        order_cfg = self._order_config(config)
        raw = order_cfg.get("fulfillment_types")
        if isinstance(raw, list) and raw:
            return [str(v).strip().lower() for v in raw if str(v).strip()]
        return ["pickup", "delivery"]
    def resolve_fulfillment_type(self, value: Any, config: Dict[str, Any]) -> Optional[str]:
        """Normalise a raw customer/message value against the tenant-configured
        fulfillment options.

        Returns the canonical configured option string on a confident match, or
        None when the value cannot be resolved with confidence.

        Strategy:
        1. Exact case-insensitive match against configured options.
        2. Fuzzy (difflib) match against configured option labels only — NOT
           against a hardcoded English list.  Cutoff 0.6 avoids false positives.
        3. Return None if no confident match (caller should ask clarification).
        """
        text = str(value or "").strip().lower()
        if not text:
            return None
        options = self._configured_fulfillment_options(config)
        # 1. Exact match
        if text in options:
            return text
        # 2. Substring match — e.g. "picku" in "pickup"
        for opt in options:
            if text in opt or opt in text:
                return opt
        # 3. Fuzzy match against configured labels only
        matches = get_close_matches(text, options, n=1, cutoff=0.6)
        if matches:
            return matches[0]
        return None
    @staticmethod
    def _coerce_quantity(value: Any) -> int:
        try:
            return max(1, int(float(str(value))))
        except (TypeError, ValueError):
            return 1
    def _requested_items(self, step_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_items = step_data.get("items")
        if isinstance(raw_items, list):
            items = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_identifier = item.get("item_name") or item.get("name") or item.get("item_sku") or item.get("sku") or item.get("item_id") or item.get("id")
                if item_identifier:
                    items.append({
                        "item_id": item_identifier,
                        "quantity": item.get("quantity", 1),
                        "notes": item.get("notes"),
                    })
            if items:
                return items
        return [{
            "item_id": step_data.get("item_name") or step_data.get("item_sku") or step_data.get("item_id"),
            "quantity": step_data.get("quantity", 1),
            "notes": step_data.get("notes"),
        }]
    @staticmethod
    def _order_item_snapshot(entry: Dict[str, Any]) -> Dict[str, Any]:
        item = entry["item"]
        return {
            "item_id": item.get("id"),
            "item_name": item.get("name"),
            "quantity": entry["quantity"],
            "unit_price": entry["unit_price"],
            "line_total": entry["line_total"],
            "catalog_item_id": item.get("id") if item.get("source") == "catalog_db" or item.get("source_catalog_db") else None,
            "variant_data": {
                "description": item.get("description"),
                "category_id": item.get("category_id"),
                "category_name": item.get("category_name"),
                "stock_tracking_enabled": item.get("stock_tracking_enabled"),
                "has_image": bool(item.get("has_image")),
                "image_alt": item.get("image_alt"),
            },
            "notes": entry.get("notes"),
        }
    @staticmethod
    def _coerce_money(value: Any) -> float:
        try:
            return max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0
    @staticmethod
    def _normalized_tokens(value: str) -> set[str]:
        tokens = set()
        for token in re.findall(r"[a-z0-9]+", value.lower()):
            if len(token) < 3:
                continue
            tokens.add(token)
            if token.endswith("s") and len(token) > 3:
                tokens.add(token[:-1])
        return tokens
    @staticmethod
    def _idempotency_key(context: ExecutionContext) -> str:
        step_data = context.session_data.state.step_data
        existing = step_data.get("order_id")
        if existing:
            return f"order:{existing}"
        payload = json.dumps(step_data, sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return f"order:{context.tenant.id}:{context.session_data.conversation_id}:{digest}"

    def apply_external_configured_candidates(
        self,
        context: ExecutionContext,
        candidates: Mapping[str, Any],
    ) -> "ExternalOrderCandidateApplicationResult":
        """Authoritative backend-validated multi-field application.

        Revalidates the supplied candidates at application time through the
        existing deterministic authority (``validate_external_configured_candidates``),
        independently rejecting protected, unknown, unsupported, invalid, and
        already-collected values, then persists every accepted value through
        exactly one order-scoped state update. It never calls an LLM, never
        executes a tool, never creates a pending action, never prepares
        confirmation, and never generates customer-visible text.

        The contract is deliberately narrow (order continuation only):
          * item identity / financial / internal keys are never accepted;
          * unknown, protected, already-collected, or invalid keys are rejected;
          * only one normalized patch is written, never partial mutation.

        Outcomes:
          * A -- at least one missing candidate accepted -> one update;
          * B -- every supplied valid key already collected -> zero writes,
            ``reason_code == "already_satisfied"``, still ``handled``;
          * C -- nothing safely applicable -> ``handled`` is False so the
            existing focused collector may run.

        Outcome D (uncertain write) is not represented here: if the single
        state update mutates and then raises, the exception propagates to the
        existing outer failure owner. This method never performs a second
        update and never returns a normal handled result after a write error.
        """
        validation = self.validate_external_configured_candidates(context, candidates)
        if not validation.accepted_patch:
            # Distinguish the idempotent replay/retry case (Outcome B) from a
            # genuine fallback (Outcome C). When every supplied valid candidate
            # key already holds an accepted value, this is a write-free,
            # interpretation-free already-satisfied turn that the engine must
            # continue from authoritative state -- never a legacy re-collection.
            if self._all_order_candidates_already_satisfied(context, candidates):
                return ExternalOrderCandidateApplicationResult(
                    handled=True,
                    applied=False,
                    update_performed=False,
                    accepted_field_keys=(),
                    rejected_field_keys=validation.rejected_field_keys,
                    reason_code="already_satisfied",
                )
            return ExternalOrderCandidateApplicationResult(
                handled=False,
                applied=False,
                update_performed=False,
                accepted_field_keys=(),
                rejected_field_keys=validation.rejected_field_keys,
                reason_code=validation.reason_code or "no_eligible_candidates",
            )
        accepted_keys = tuple(sorted(validation.accepted_patch.keys()))
        accepted_patch = dict(validation.accepted_patch)

        # Single atomic authoritative update. Validation above already re-ran
        # the deterministic contract; no raw semantic value reaches state.
        state = context.session_data.state
        current = state.get_task_step_data("order") or {}
        merged = dict(current)
        merged.update(accepted_patch)
        state.update_task_step_data("order", merged)

        return ExternalOrderCandidateApplicationResult(
            handled=True,
            applied=True,
            update_performed=True,
            accepted_field_keys=accepted_keys,
            rejected_field_keys=validation.rejected_field_keys,
            reason_code="accepted",
        )

    def _all_order_candidates_already_satisfied(
        self,
        context: ExecutionContext,
        candidates: Mapping[str, Any],
    ) -> bool:
        """True when every supplied candidate key already holds an accepted value.

        Write-free idempotent-replay signal. A key is "satisfied" only when it
        maps to a known, non-protected configured field that already contains a
        value. If any supplied key is unknown, protected, or still empty, this
        is not an already-satisfied turn and the caller must treat it as a
        genuine fallback (Outcome C) instead.
        """
        if not isinstance(candidates, Mapping) or not candidates:
            return False
        fields = self.get_required_fields(context.config.config_data)
        fields_by_key = {
            str(field.get("key") or "").strip(): field
            for field in fields
            if str(field.get("key") or "").strip()
        }
        current = context.session_data.state.get_task_step_data("order") or {}
        for raw_key in candidates.keys():
            if not isinstance(raw_key, str) or not raw_key:
                return False
            if raw_key in _ORDER_SHADOW_PROTECTED_KEYS:
                return False
            if raw_key not in fields_by_key:
                return False
            if current.get(raw_key) in (None, "", [], {}):
                return False
        return True
order_module = OrderModule()
