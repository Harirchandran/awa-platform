"""Authoritative configured booking-field collection.

This module deliberately has no booking-creation, confirmation, payment, or
customer-prose authority.  It turns one customer turn into one safe exact-key
state patch (or a small machine-readable rejection) while the engine retains
control and response composition authority.  A structured extraction may
contribute several fields, but the whole candidate batch validates or fails
together.

The provider booking schema is still canonical.  Arbitrary tenant keys can be
accepted only when an extractor returns that exact configured key; teaching
the provider schema about arbitrary tenant fields is the next contract
boundary, not something this coordinator guesses from prose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Awaitable, Callable, Mapping

from backend.logic.engine.field_extraction import BookingFieldExtraction, extract_booking_fields
from backend.logic.engine.configured_option_resolver import resolve_exact_configured_option
from backend.ops.logger import logger


_SUPPORTED_TYPES = frozenset({"string", "number", "date", "time", "email", "phone", "select"})
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE = re.compile(r"^\+?[0-9 ()-]{7,40}$")
_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_EXPLICIT_CANCEL = re.compile(r"^\s*(?:cancel|stop|never mind|nevermind)\b", re.I)
_RICH_SEPARATOR = re.compile(r"[,;\n]|[.!?]\s+")
_TEMPORAL_SIGNAL = re.compile(
    r"\b(?:today|tomorrow|tonight|next\s+(?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"morning|afternoon|evening|noon|midnight|"
    r"at\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})(?::\d{2})?"
    r"(?:\s*(?:am|pm))?|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.I,
)
_PROTECTED_BOOKING_KEYS = frozenset({"service_name", "service_id"})
_PRIMITIVE_CANDIDATE_TYPES = (str, int, float)


@dataclass(frozen=True)
class ConfiguredBookingField:
    key: str
    label: str
    field_type: str
    required: bool
    order: int
    options: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionResult:
    """Safe facts for the engine/response layer; never contains customer values."""

    handled: bool
    accepted: bool = False
    reason_code: str = "no_candidate"
    focused_field: ConfiguredBookingField | None = None
    patch: Mapping[str, Any] = field(default_factory=dict)
    remaining_fields: tuple[ConfiguredBookingField, ...] = ()
    control_disposition: str = "collect"
    candidate_disposition: str = "none"

    @property
    def next_field(self) -> ConfiguredBookingField | None:
        return self.remaining_fields[0] if self.remaining_fields else None

    @property
    def ready_for_confirmation(self) -> bool:
        return self.handled and self.accepted and not self.remaining_fields


@dataclass(frozen=True)
class ExternalCandidateApplicationResult:
    """Typed outcome of an authoritative external-candidate application.

    This result replaces the earlier Boolean/CollectionResult signal because
    the caller must safely distinguish four outcomes that the previous shape
    could not tell apart:

      * applied new values (``handled`` + ``applied`` + ``update_performed``);
      * already satisfied with no write (``handled`` only,
        ``reason_code == "already_satisfied"``);
      * not handled because candidates were invalid/unavailable
        (``handled`` is False, existing focused collector may run);
      * uncertain write failure (propagated as an exception, never a normal
        handled result).

    It is deliberately fact-only: no customer values, no state references, no
    customer-visible text, and no tool data. Only exact sorted configured keys
    are exposed so the engine can continue from authoritative booking state.
    """

    handled: bool
    applied: bool
    update_performed: bool
    accepted_field_keys: tuple[str, ...] = ()
    rejected_field_keys: tuple[str, ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True)
class ConfiguredCandidateValidationResult:
    """Dry-run deterministic validation of externally supplied configured-field candidates.

    This result is OBSERVATIONAL ONLY. It records which exact configured keys
    would be accepted or rejected by the existing deterministic validation
    authority (``_validated_patch``) without mutating any authoritative
    booking state. It contains no customer-visible text, no tool data, and no
    persistence claims.
    """

    accepted_patch: Mapping[str, Any]
    accepted_field_keys: tuple[str, ...]
    rejected_field_keys: tuple[str, ...]
    reason_code: str | None


class ConfiguredFieldCollectionCoordinator:
    """Collect configured booking fields before general controls.

    Precedence is intentionally narrow: explicit cancel may preempt this
    coordinator; an exact configured option or a usable candidate batch wins
    over resume, continue, general, defer, and unknown semantic
    classifications. Other controls are left for the canonical control layer
    when there is no value.
    """

    def resolve_fields(self, context: Any) -> tuple[ConfiguredBookingField, ...]:
        modules = (context.config.config_data or {}).get("modules", {}) if isinstance(context.config.config_data, dict) else {}
        booking = modules.get("booking", {}) if isinstance(modules, Mapping) else {}
        raw_fields = booking.get("fields", []) if isinstance(booking, Mapping) else []
        result: list[ConfiguredBookingField] = []
        for index, raw in enumerate(raw_fields if isinstance(raw_fields, list) else []):
            if not isinstance(raw, Mapping) or raw.get("enabled", True) is False:
                continue
            key = str(raw.get("key") or "").strip()
            # Service selection is a booking-domain prerequisite, never a
            # tenant-configured customer field.  The engine persists it through
            # the authoritative configured-service resolver before this
            # coordinator receives a custom-field turn.
            if key in {"service_name", "service_id"}:
                continue
            field_type = str(raw.get("type") or "string").strip().lower()
            if not key or field_type not in _SUPPORTED_TYPES:
                continue
            result.append(ConfiguredBookingField(
                key=key,
                label=str(raw.get("label") or key.replace("_", " "))[:120],
                field_type=field_type,
                required=bool(raw.get("required", False)),
                order=index,
                options=tuple(raw.get("options") or ()) if isinstance(raw.get("options"), list) else (),
                metadata=dict(raw),
            ))
        return tuple(result)

    def resolve_current_field(self, context: Any) -> tuple[ConfiguredBookingField | None, tuple[ConfiguredBookingField, ...]]:
        fields = self.resolve_fields(context)
        state = context.session_data.state
        current = state.get_task_step_data("booking")
        missing = tuple(item for item in fields if item.required and current.get(item.key) in (None, ""))
        return (missing[0] if missing else None), missing

    def is_active(self, context: Any) -> bool:
        state = context.session_data.state
        active = state.action_ledger.current_task or context.session_data.current_module
        modules = (context.config.config_data or {}).get("modules", {}) if isinstance(context.config.config_data, dict) else {}
        booking = modules.get("booking", {}) if isinstance(modules, Mapping) else {}
        return active == "booking" and isinstance(booking, Mapping) and booking.get("enabled") is True and not state.action_ledger.pending_action

    def validate_external_candidates(
        self,
        context: Any,
        candidates: Mapping[str, Any],
    ) -> ConfiguredCandidateValidationResult:
        """Dry-run deterministic validation of externally supplied configured candidates.

        This is the only external-candidate validator. It never mutates booking
        state, never modifies prompt focus, never touches the action ledger,
        never executes tools, never calls an LLM, and never logs candidate
        values. It reuses the existing deterministic validation authority
        (``_validated_patch``) so no field-type validation is duplicated.
        """
        if not isinstance(candidates, Mapping) or not candidates:
            return ConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(candidates.keys())) if isinstance(candidates, Mapping) else (),
                reason_code="no_eligible_candidates",
            )

        fields = self.resolve_fields(context)
        fields_by_key = {item.key: item for item in fields}
        current = context.session_data.state.get_task_step_data("booking")

        raw_candidates: dict[str, tuple[ConfiguredBookingField, Any]] = {}
        rejected: set[str] = set()
        for raw_key, value in candidates.items():
            if not isinstance(raw_key, str) or not raw_key:
                rejected.add(str(raw_key))
                continue
            if raw_key in _PROTECTED_BOOKING_KEYS:
                rejected.add(raw_key)
                continue
            configured = fields_by_key.get(raw_key)
            if configured is None:
                rejected.add(raw_key)
                continue
            if current.get(raw_key) not in (None, ""):
                rejected.add(raw_key)
                continue
            if type(value) not in _PRIMITIVE_CANDIDATE_TYPES or value in (None, ""):
                rejected.add(raw_key)
                continue
            raw_candidates[raw_key] = (configured, value)

        if not raw_candidates:
            reason = "no_eligible_candidates"
            if rejected:
                raw_key = next(iter(rejected))
                if raw_key in _PROTECTED_BOOKING_KEYS:
                    reason = "protected_field"
                elif raw_key not in fields_by_key:
                    reason = "unknown_field"
                else:
                    reason = "field_already_collected"
            return ConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(rejected)),
                reason_code=reason,
            )

        patch, reason = self._validated_patch(context, raw_candidates)
        accepted_keys = tuple(sorted(patch.keys()))
        if not patch:
            return ConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(set(raw_candidates.keys()) | rejected)),
                reason_code=reason or "invalid_format",
            )
        return ConfiguredCandidateValidationResult(
            accepted_patch=dict(patch),
            accepted_field_keys=accepted_keys,
            rejected_field_keys=tuple(sorted(set(raw_candidates.keys()) - set(patch.keys()) | rejected)),
            reason_code=reason,
        )

    def apply_external_candidates(
        self,
        context: Any,
        candidates: Mapping[str, Any],
    ) -> "ExternalCandidateApplicationResult":
        """Authoritative backend-validated multi-field application.

        Revalidates the supplied candidates at application time through the
        existing deterministic authority (``validate_external_candidates``),
        then persists every accepted value through exactly one booking-state
        update. It never calls an LLM, never executes a tool, never creates a
        pending action, and never generates customer-visible text.

        The contract is deliberately narrow (booking continuation only):
          * service keys (service_name/service_id) are never accepted;
          * unknown, protected, already-collected, or invalid keys are rejected;
          * only one normalized patch is written, never partial mutation.

        Returns a typed :class:`ExternalCandidateApplicationResult` so the
        engine can distinguish an applied write, an idempotent already-satisfied
        replay, and a genuine fallback without re-interpreting the turn:

          * Outcome A -- at least one missing candidate accepted -> one update;
          * Outcome B -- every supplied valid key already collected -> zero
            writes, ``reason_code == "already_satisfied"``, still ``handled``;
          * Outcome C -- nothing safely applicable -> ``handled`` is False so
            the existing focused collector may run.

        Outcome D (uncertain write) is not represented here: if the single
        state update mutates and then raises, the exception propagates to the
        existing outer failure owner. This method never performs a second
        update and never returns a normal handled result after a write error.
        """
        validation = self.validate_external_candidates(context, candidates)
        if not validation.accepted_patch:
            # Distinguish the idempotent replay/retry case (Outcome B) from a
            # genuine fallback (Outcome C). When every supplied valid candidate
            # key already holds an accepted value, this is a write-free,
            # interpretation-free already-satisfied turn that the engine must
            # continue from authoritative state -- never a legacy re-collection.
            if self._all_candidates_already_satisfied(context, candidates):
                return ExternalCandidateApplicationResult(
                    handled=True,
                    applied=False,
                    update_performed=False,
                    accepted_field_keys=(),
                    rejected_field_keys=validation.rejected_field_keys,
                    reason_code="already_satisfied",
                )
            return ExternalCandidateApplicationResult(
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
        merged_patch = state.get_task_step_data("booking")
        merged_patch.update(accepted_patch)
        state.update_task_step_data("booking", merged_patch)

        return ExternalCandidateApplicationResult(
            handled=True,
            applied=True,
            update_performed=True,
            accepted_field_keys=accepted_keys,
            rejected_field_keys=validation.rejected_field_keys,
            reason_code="accepted",
        )

    def _all_candidates_already_satisfied(
        self,
        context: Any,
        candidates: Mapping[str, Any],
    ) -> bool:
        """True when every supplied candidate key already holds an accepted value.

        This is the write-free idempotent-replay signal. A key is "satisfied"
        only when it maps to a known, non-protected configured field that
        already contains a value. If any supplied key is unknown, protected, or
        still empty, this is not an already-satisfied turn and the caller must
        treat it as a genuine fallback (Outcome C) instead.
        """
        if not isinstance(candidates, Mapping) or not candidates:
            return False
        fields_by_key = {item.key: item for item in self.resolve_fields(context)}
        current = context.session_data.state.get_task_step_data("booking")
        for raw_key in candidates.keys():
            if not isinstance(raw_key, str) or not raw_key:
                return False
            if raw_key in _PROTECTED_BOOKING_KEYS:
                return False
            if raw_key not in fields_by_key:
                return False
            if current.get(raw_key) in (None, ""):
                return False
        return True

    async def collect(self, context: Any, *, semantic_extractor: Callable[[Any], Awaitable[BookingFieldExtraction]] = extract_booking_fields) -> CollectionResult:
        if not self.is_active(context):
            return CollectionResult(False)
        focused, missing_before = self.resolve_current_field(context)
        if focused is None:
            return CollectionResult(False)
        # service_id is an established schema compatibility shim whose public
        # collection value is service_name.  Stage 1 does not migrate that
        # special schema; leave it to the existing protected service path.
        if focused.key == "service_id":
            return CollectionResult(False, focused_field=focused, remaining_fields=missing_before)
        text = str((context.incoming_message or {}).get("text") or "").strip()
        if _EXPLICIT_CANCEL.match(text):
            return CollectionResult(True, focused_field=focused, reason_code="explicit_cancel", control_disposition="cancel")

        candidate, source = self._configured_option_candidate(context, focused, text)
        rich_turn = self._looks_like_multi_field_utterance(context, focused, text)
        rich_string_turn = focused.field_type == "string" and rich_turn
        if candidate is None and not rich_string_turn:
            candidate = self._deterministic_candidate(focused, text)
            source = "deterministic" if candidate is not None else "none"
        raw_candidates: dict[str, tuple[ConfiguredBookingField, Any]] = {}
        rejection_reason: str | None = None
        if candidate is not None:
            raw_candidates[focused.key] = (focused, candidate)
        else:
            raw_candidates, rejection_reason, source = await self._semantic_candidates(
                context,
                focused,
                semantic_extractor,
                force_structured=rich_string_turn,
                allow_batch=rich_turn,
            )
        if rejection_reason:
            self._observe(context, focused, "collect", source, rejection_reason, len(missing_before))
            return CollectionResult(
                True,
                focused_field=focused,
                reason_code=rejection_reason,
                remaining_fields=missing_before,
                candidate_disposition=source,
            )
        if not raw_candidates:
            self._observe(context, focused, "collect", "no_candidate", "no_candidate", len(missing_before))
            return CollectionResult(False, focused_field=focused, remaining_fields=missing_before)

        patch, reason = self._validated_patch(context, raw_candidates)
        if reason:
            self._observe(context, focused, "collect", source, reason, len(missing_before))
            return CollectionResult(True, focused_field=focused, reason_code=reason, remaining_fields=missing_before, candidate_disposition=source)
        if not patch:
            self._observe(context, focused, "collect", source, "no_candidate", len(missing_before))
            return CollectionResult(False, focused_field=focused, remaining_fields=missing_before)

        # This is the single exact-key state mutation for the turn. The worker's
        # existing SessionManager transaction durably commits the resulting
        # session exactly once after the engine returns. Include the current
        # view because legacy sessions may still have booking state only in
        # step_data; update_task_step_data cannot merge a scoped bag that does
        # not exist yet.
        state = context.session_data.state
        merged_patch = state.get_task_step_data("booking")
        merged_patch.update(patch)
        state.update_task_step_data("booking", merged_patch)
        _, remaining_after = self.resolve_current_field(context)
        self._observe(context, focused, "collect", source, "accepted", len(remaining_after))
        return CollectionResult(
            True, accepted=True, reason_code="accepted", focused_field=focused,
            patch=patch, remaining_fields=remaining_after,
            candidate_disposition=source,
        )

    def _configured_option_candidate(self, context: Any, focused: ConfiguredBookingField, text: str) -> tuple[Any | None, str]:
        if focused.field_type == "select":
            matches = [option for option in focused.options if str(option).casefold() == text.casefold()]
            return (matches[0], "configured_option") if len(matches) == 1 else (None, "none")
        if focused.key == "service_name":
            match = resolve_exact_configured_option(customer_text=text, active_workflow="booking", requested_fields=("service_name",), config_data=context.config.config_data, runtime_available_modules={"booking"})
            if match and set(match.field_updates).issubset({"service_name", "service_id"}):
                return match.field_updates.get("service_name"), "configured_option"
        return None, "none"

    def _deterministic_candidate(self, focused: ConfiguredBookingField, text: str) -> Any | None:
        if not text or len(text) > 320:
            return None
        kind = focused.field_type
        if focused.key == "service_name":
            # A booking service is a configuration-owned option, never an
            # arbitrary string field.  Only the option resolver may accept it.
            return None
        if kind == "string":
            # Do not let a recognisable temporal answer satisfy an earlier
            # free-text field merely because older prompt focus exposed more
            # than one missing key.
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) or re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", text):
                return None
            return text
        if kind == "number" and _NUMBER.fullmatch(text):
            return text
        if kind == "email" and _EMAIL.fullmatch(text):
            return text
        if kind == "phone" and _PHONE.fullmatch(text):
            return text
        if kind == "select":
            # A non-empty answer to a closed choice is a candidate too, so the
            # caller receives option_not_allowed instead of a silent resume.
            return text
        if kind == "date" and (re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) or text.casefold() in {"today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}):
            return text
        if kind == "time":
            # Booking owns time grammar and normalization. The coordinator
            # merely decides whether this turn belongs to the focused field.
            from backend.modules.booking_module import booking_module
            if booking_module.configured_time_input_kind(text) != "not_time":
                return text
        return None

    def _looks_like_multi_field_utterance(
        self,
        context: Any,
        focused: ConfiguredBookingField,
        text: str,
    ) -> bool:
        """Keep rich prose out of the focused free-text deterministic path."""
        if not text:
            return False
        current = context.session_data.state.get_task_step_data("booking")
        missing = [
            item for item in self.resolve_fields(context)
            if current.get(item.key) in (None, "")
        ]
        if len(missing) < 2:
            return False
        if _RICH_SEPARATOR.search(text):
            return True
        temporal_signals = list(_TEMPORAL_SIGNAL.finditer(text))
        if focused.field_type == "string":
            return bool(temporal_signals)
        return len(temporal_signals) >= 2

    async def _semantic_candidates(
        self,
        context: Any,
        focused: ConfiguredBookingField,
        extractor: Callable[[Any], Awaitable[BookingFieldExtraction]],
        *,
        force_structured: bool = False,
        allow_batch: bool = False,
    ) -> tuple[dict[str, tuple[ConfiguredBookingField, Any]], str | None, str]:
        """Map one structured extraction to an all-or-nothing configured batch."""
        state = context.session_data.state
        prompt_focus = state.prompt_focus
        try:
            # The shared extractor has a deliberately narrow prompt-focus
            # shortcut for simple one-field answers. Suppress that shortcut
            # for a rich free-text turn so the existing structured provider
            # can see and return every canonical candidate. The focus is
            # restored before any validation or durable mutation.
            if force_structured:
                state.prompt_focus = None
            extraction = await extractor(context)
        except Exception:
            return {}, None, "semantic_failed"
        finally:
            if force_structured:
                state.prompt_focus = prompt_focus
        context.shadow_turn_evidence["field_extraction"] = extraction
        updates = getattr(extraction, "field_updates", None)
        confidence = getattr(extraction, "field_confidence", {}) or {}
        if not isinstance(updates, Mapping) or not updates:
            return {}, None, "semantic_failed" if not isinstance(updates, Mapping) else "none"
        if len(updates) > 1 and not allow_batch:
            # Preserve the existing general semantic path for short focused
            # replies whose extractor happens to repeat contextual fields.
            # Multi-field authority here is reserved for turns that contain
            # concrete grouped-input signals.
            return {}, None, "semantic_deferred"
        if not isinstance(confidence, Mapping):
            return {}, "semantic_rejected", "semantic_rejected"

        fields = self.resolve_fields(context)
        current = state.get_task_step_data("booking")
        missing = tuple(item for item in fields if current.get(item.key) in (None, ""))
        fields_by_key = {item.key: item for item in fields}
        batch: dict[str, tuple[ConfiguredBookingField, Any]] = {}
        overall_confidence = getattr(extraction, "confidence", 0.0)
        has_protected = False
        rejection_reasons = []
        for raw_key_value, value in updates.items():
            if not isinstance(raw_key_value, str) or not raw_key_value:
                rejection_reasons.append("unmapped_field")
                continue
            raw_key = raw_key_value
            if raw_key in _PROTECTED_BOOKING_KEYS:
                # A configured service may already have been selected from
                # this rich turn. Ignore the extractor's duplicate service
                # value while retaining independent configured candidates.
                if current.get("service_name") not in (None, ""):
                    continue
                has_protected = True
                continue
            configured_exact = fields_by_key.get(raw_key)
            if configured_exact is not None and configured_exact not in missing:
                rejection_reasons.append("field_not_missing")
                continue
            resolved = self._resolve_semantic_key(raw_key, missing)
            if resolved is None:
                rejection_reasons.append("unmapped_field")
                continue
            if resolved.key in batch:
                rejection_reasons.append("ambiguous_field")
                continue
            try:
                score = float(confidence.get(raw_key, overall_confidence))
            except (TypeError, ValueError):
                rejection_reasons.append("low_confidence")
                continue
            if score < 0.8:
                rejection_reasons.append("low_confidence")
                continue
            if type(value) not in _PRIMITIVE_CANDIDATE_TYPES or value in (None, ""):
                rejection_reasons.append("semantic_rejected")
                continue
            batch[resolved.key] = (resolved, value)

        if has_protected:
            # The extraction includes service_name/service_id alongside
            # customer fields.  Defer the entire turn to the legacy engine
            # path which handles service resolution and field application
            # atomically.
            return {}, None, "semantic_deferred"
        if not batch:
            primary_reason = rejection_reasons[0] if rejection_reasons else "semantic_rejected"
            return {}, primary_reason, "semantic_rejected"
        return batch, None, "semantic"

    def _resolve_semantic_key(
        self,
        raw_key: str,
        missing: tuple[ConfiguredBookingField, ...],
    ) -> ConfiguredBookingField | None:
        """One-way compatibility adapter: canonical alias -> one missing key."""
        exact = next((item for item in missing if item.key == raw_key), None)
        if exact is not None:
            return exact
        aliases = {
            "booking_date": "date", "preferred_date": "date", "date": "date",
            "booking_time": "time", "preferred_time": "time", "time": "time",
            "customer_name": "string", "patient_name": "string", "name": "string",
            "phone": "phone", "phone_number": "phone", "contact_phone": "phone",
            "email": "email", "email_address": "email", "contact_email": "email",
        }
        alias_type = aliases.get(raw_key)
        if not alias_type:
            return None
        candidates = [field for field in missing if field.field_type == alias_type]
        return candidates[0] if len(candidates) == 1 else None

    def _validated_patch(
        self,
        context: Any,
        candidates: Mapping[str, tuple[ConfiguredBookingField, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        """Normalize and validate a candidate batch without durable mutation."""
        patch: dict[str, Any] = {}
        candidate_types: set[str] = set()
        first_reason = None
        for key, (configured_field, candidate) in candidates.items():
            normalized, reason = self._normalize(configured_field, candidate)
            if reason:
                first_reason = first_reason or reason
                continue
            validation_reason = self._validate(context, configured_field, normalized)
            if validation_reason:
                first_reason = first_reason or validation_reason
                continue
            try:
                normalized = self.normalized_value(context, configured_field, normalized)
            except Exception:
                first_reason = first_reason or "invalid_format"
                continue
            patch[key] = normalized
            candidate_types.add(configured_field.field_type)

        if not patch:
            return {}, first_reason or "invalid_format"

        if {"date", "time"}.issubset(candidate_types):
            domain_reason = self._validate_atomic_domain_patch(context, patch)
            if domain_reason:
                salvaged_patch = {k: v for k, v in patch.items() if candidates[k][0].field_type != "time"}
                if salvaged_patch and not self._validate_atomic_domain_patch(context, salvaged_patch):
                    patch = salvaged_patch
                else:
                    return {}, domain_reason
        return patch, None

    def _normalize(self, focused: ConfiguredBookingField, value: Any) -> tuple[Any | None, str | None]:
        raw = str(value).strip()
        if not raw:
            return None, "invalid_format"
        if focused.field_type == "string":
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) or re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", raw):
                return None, "ambiguous_value"
            return raw[:500], None
        if focused.field_type == "number":
            try:
                parsed = Decimal(raw)
            except InvalidOperation:
                return None, "invalid_format"
            return (int(parsed) if parsed == parsed.to_integral() else float(parsed)), None
        if focused.field_type == "email":
            return (raw.casefold(), None) if _EMAIL.fullmatch(raw) else (None, "invalid_format")
        if focused.field_type == "phone":
            return (raw, None) if _PHONE.fullmatch(raw) else (None, "invalid_format")
        if focused.field_type == "select":
            matches = [option for option in focused.options if str(option).casefold() == raw.casefold()]
            return (matches[0], None) if len(matches) == 1 else (None, "option_not_allowed")
        return raw, None

    def _validate(self, context: Any, focused: ConfiguredBookingField, value: Any) -> str | None:
        metadata = dict(focused.metadata)
        if focused.field_type in {"string", "email", "phone", "select"}:
            min_length = metadata.get("min_length", metadata.get("minLength"))
            max_length = metadata.get("max_length", metadata.get("maxLength"))
            if isinstance(min_length, int) and len(str(value)) < min_length:
                return "invalid_format"
            if isinstance(max_length, int) and len(str(value)) > max_length:
                return "invalid_format"
            pattern = metadata.get("pattern")
            if isinstance(pattern, str):
                try:
                    if re.fullmatch(pattern, str(value)) is None:
                        return "invalid_format"
                except re.error:
                    return "invalid_format"
        if focused.field_type == "number":
            minimum = metadata.get("minimum", metadata.get("min"))
            maximum = metadata.get("maximum", metadata.get("max"))
            if isinstance(minimum, (int, float)) and value < minimum:
                return "invalid_format"
            if isinstance(maximum, (int, float)) and value > maximum:
                return "invalid_format"
            return None
        if focused.field_type not in {"date", "time"}:
            return None
        try:
            from backend.modules.booking_module import booking_module
            generic = booking_module.validate_input(metadata, value, tenant_config=context.config.config_data)
            if not generic.is_valid:
                message = str(generic.error_message or "").lower()
                if focused.field_type == "time" and "specify am or pm" in message:
                    return "ambiguous_value"
                return "past_date" if "future" in message or "past" in message else "invalid_format"
            normalized = booking_module.normalize_configured_field_value(metadata, value, tenant_config=context.config.config_data)
        except Exception:
            return "invalid_format"
        if focused.field_type == "time":
            current = context.session_data.state.get_task_step_data("booking")
            temporal_fields = [field for field in self.resolve_fields(context) if field.field_type == "date"]
            if not any(current.get(field.key) not in (None, "") for field in temporal_fields):
                # validate_payload defaults a missing date to today. That is
                # correct at execution but not while independently collecting
                # a configured time, where it would misclassify a normalized
                # AM/PM value as a same-day ambiguous past value.
                return None
        # Re-run the bounded domain policy with a temporary candidate. It is
        # restored before the authoritative exact-key patch above.
        domain_error = self._booking_payload_error(context, {focused.key: normalized})
        if domain_error:
            return self._domain_reason(domain_error)
        # Persist the normalized temporal value, not the parser input.
        return None

    def _validate_atomic_domain_patch(self, context: Any, patch: Mapping[str, Any]) -> str | None:
        """Validate temporal relationships with every candidate visible together."""
        domain_error = self._booking_payload_error(context, patch)
        return self._domain_reason(domain_error) if domain_error else None

    def _booking_payload_error(self, context: Any, patch: Mapping[str, Any]) -> str | None:
        """Run booking-domain validation against a fully restored state view."""
        from backend.modules.booking_module import booking_module

        state = context.session_data.state
        had_scoped = "booking" in state.scoped_step_data
        before_scoped = dict(state.scoped_step_data.get("booking") or {})
        before_step = dict(state.step_data or {})
        before_task = state.action_ledger.current_task
        candidate = state.get_task_step_data("booking")
        candidate.update(dict(patch))
        try:
            # Avoid the authoritative mutation method here. Validation gets a
            # temporary merged view; only collect() calls update_task_step_data.
            state.scoped_step_data["booking"] = dict(candidate)
            state.step_data = dict(candidate)
            state.action_ledger.current_task = "booking"
            try:
                return booking_module.validate_payload(context)
            except Exception:
                return "booking domain validation failed"
        finally:
            if had_scoped:
                state.scoped_step_data["booking"] = before_scoped
            else:
                state.scoped_step_data.pop("booking", None)
            state.step_data = before_step
            state.action_ledger.current_task = before_task

    def _domain_reason(self, domain_error: Any) -> str:
        error = str(domain_error).lower()
        if "past" in error or "future" in error:
            return "past_date"
        if "hour" in error:
            return "outside_working_hours"
        if "available" in error:
            return "slot_unavailable"
        return "invalid_format"

    def normalized_value(self, context: Any, focused: ConfiguredBookingField, value: Any) -> Any:
        from backend.modules.booking_module import booking_module
        return booking_module.normalize_configured_field_value(dict(focused.metadata), value, tenant_config=context.config.config_data)

    def _observe(self, context: Any, focused: ConfiguredBookingField, control: str, candidate: str, reason: str, remaining: int) -> None:
        logger.info("configured_booking_field_collection", extra={"extra_data": {
            "event": "configured_booking_field_collection", "workflow": "booking",
            "config_version": int(context.session_data.config_version), "focused_configured_key": focused.key,
            "configured_type": focused.field_type, "control_disposition": control,
            "candidate_disposition": candidate, "validation_reason_code": reason,
            "commit_outcome": "pending_worker_commit" if reason == "accepted" else "not_committed",
            "remaining_field_count": remaining,
        }})


configured_field_collection_coordinator = ConfiguredFieldCollectionCoordinator()


@dataclass(frozen=True)
class WorkflowFieldDefinition:
    key: str
    label: str
    field_type: str
    required: bool
    order: int
    options: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowFieldCandidate:
    key: str
    value: Any
    confidence: float
    source: str


@dataclass(frozen=True)
class DomainEntityCandidate:
    entity_key: str
    match_label: str | None
    match_id: str | None
    confidence: float
    source: str


@dataclass(frozen=True)
class SecondaryObjective:
    objective_type: str
    question_text: str | None
    confidence: float


@dataclass(frozen=True)
class WorkflowTurnEvidence:
    turn_intent: str
    confidence: float
    fields: tuple[WorkflowFieldCandidate, ...] = ()
    entities: tuple[DomainEntityCandidate, ...] = ()
    secondary_objectives: tuple[SecondaryObjective, ...] = ()
    clarification_needed: bool = False
    clarification_reason: str | None = None


@dataclass(frozen=True)
class WorkflowCollectionResult:
    handled: bool
    accepted: bool = False
    reason_code: str = "no_candidate"
    focused_field: WorkflowFieldDefinition | None = None
    patch: Mapping[str, Any] = field(default_factory=dict)
    remaining_fields: tuple[WorkflowFieldDefinition, ...] = ()
    secondary_answer_facts: tuple[str, ...] = ()


def collect_global(
    context: Any,
    *,
    workflow: str,
) -> WorkflowCollectionResult:
    from backend.logic.engine.workflow_field_policies import WORKFLOW_FIELD_POLICIES

    policy = WORKFLOW_FIELD_POLICIES.get(workflow)
    if not policy:
        return WorkflowCollectionResult(handled=False)

    return WorkflowCollectionResult(handled=False)
