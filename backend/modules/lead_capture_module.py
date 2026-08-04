"""
Lead Capture Module — Collects lead information and persists to the leads table.
Logic-first architecture: deterministic field collection, AI-assisted only.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from backend.domains.leads.tool_definitions import (
    DEFAULT_LEAD_CAPTURE_FIELDS,
    get_lead_capture_required_fields,
    get_lead_capture_tool_metadata,
    get_lead_capture_tool_schema,
)
from backend.domains.leads import tool_handlers as lead_tool_handlers
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.leads.service import lead_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult
import re


@dataclass(frozen=True)
class LeadConfiguredCandidateValidationResult:
    """Dry-run deterministic validation of externally supplied configured lead candidates.

    This result is OBSERVATIONAL ONLY. It records which exact configured lead keys
    would be accepted or rejected by the existing deterministic validation authority
    (``validate_input``) without mutating any authoritative lead state. It contains
    no customer-visible text, no state references, no tool information, and no
    persistence claims. Accepted and rejected field keys are deterministic sorted tuples.
    """

    accepted_patch: Mapping[str, Any]
    accepted_field_keys: tuple[str, ...]
    rejected_field_keys: tuple[str, ...]
    reason_code: str | None


@dataclass(frozen=True)
class ExternalLeadCandidateApplicationResult:
    """Immutable result of an authoritative backend-validated lead field application.

    It contains no customer values, no state references, no tool information, and no
    persistence claim. Accepted and rejected field keys are deterministic sorted tuples.
    The authoritative lead application method is the sole producer.
    """

    handled: bool
    applied: bool
    update_performed: bool
    accepted_field_keys: tuple[str, ...]
    rejected_field_keys: tuple[str, ...]
    reason_code: str | None


# Terminal configured-field types whose existing backend validation is established.
# These are the only types accepted for terminal lead payload validation.
_LEAD_TERMINAL_SUPPORTED_TYPES = frozenset({"string", "textarea", "phone", "email"})

# Keys the terminal validator must never accept (backend/internal concepts).
# Consent-sensitive keys are reported separately under consent_sensitive_fields.
_LEAD_TERMINAL_PROTECTED_KEYS = frozenset({
    "lead_id",
    "tenant_id",
    "conversation_id",
    "customer_id",
    "assigned_to",
    "lead_status",
    "status",
    "source",
    "source_channel",
    "idempotency_key",
    "created_at",
    "updated_at",
    "captured_at",
    "metadata",
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
})

# Patterned internal suffixes also rejected.
_LEAD_TERMINAL_PROTECTED_SUFFIXES = frozenset({"_id"})

# Consent-sensitive markers rejected under their own reason category.
_LEAD_TERMINAL_CONSENT_MARKERS = (
    "consent",
    "opt_in",
    "opt_out",
    "marketing_consent",
    "privacy_consent",
    "privacy_acceptance",
    "contact_consent",
)

# Bounded reason-code vocabulary for terminal validation, in deterministic precedence.
_LEAD_TERMINAL_REASON_PRECEDENCE = (
    "invalid_payload_shape",
    "protected_fields",
    "consent_sensitive_fields",
    "unknown_fields",
    "unsupported_configured_type",
    "invalid_configured_values",
    "missing_required_fields",
    "complete",
)


@dataclass(frozen=True)
class LeadTerminalPayloadValidationResult:
    """Immutable typed result of tenant-aware terminal lead payload validation.

    Purely observational. It contains no customer values, no state references, no
    tool information, no persistence claim, and no customer-visible text. The
    normalized mapping is a copy of the accepted configured values projected onto
    the exact configured keys. Diagnostic key tuples are deterministic sorted
    where semantically meaningful; configured and required order preserves the
    tenant field order. No exception text is surfaced.
    """

    valid: bool
    normalized_payload: Mapping[str, Any]
    configured_field_keys: tuple[str, ...]
    required_field_keys: tuple[str, ...]
    missing_required_field_keys: tuple[str, ...]
    invalid_field_keys: tuple[str, ...]
    rejected_field_keys: tuple[str, ...]
    reason_code: str | None


# Lead configured-field types whose existing backend validation is established.
# Excluded types (number, select, date, time, boolean, object, array) are never
# accepted by the shadow validator.
_LEAD_SHADOW_SUPPORTED_TYPES = frozenset({"string", "textarea", "phone", "email"})

# Keys the shadow must never accept (backend/internal/consent-sensitive concepts).
_LEAD_SHADOW_PROTECTED_KEYS = frozenset({
    "lead_id",
    "tenant_id",
    "conversation_id",
    "customer_id",
    "assigned_to",
    "lead_status",
    "status",
    "source",
    "source_channel",
    "idempotency_key",
    "created_at",
    "updated_at",
    "captured_at",
    "metadata",
    "tool_name",
    "tool_args",
    "consent",
    "marketing_consent",
    "privacy_consent",
    "privacy_acceptance",
    "contact_consent",
    "opt_in",
    "opt_out",
})


def _lead_required_missing_after_preview(
    context: ExecutionContext,
    preview_state: Mapping[str, Any],
) -> list[str]:
    """Applicable required configured lead keys still missing in a preview state.

    Considers only known, non-protected configured lead fields of supported type
    whose ``required`` flag is truthy. The configured alias shim (name/customer_name)
    treats the other alias's non-empty value as already collected for state-reading.
    Returns the missing keys; empty means a terminal batch would be complete.
    """
    configured_fields = lead_capture_module.get_required_fields_for_context(context)
    missing: list[str] = []
    for field in configured_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        if key in _LEAD_SHADOW_PROTECTED_KEYS:
            continue
        field_type = str(field.get("type") or "string").strip().lower()
        if field_type not in _LEAD_SHADOW_SUPPORTED_TYPES:
            continue
        if not isinstance(field.get("required", True), bool) or not field.get("required", True):
            continue
        value = preview_state.get(key)
        if value not in (None, "", [], {}):
            continue
        if key == "name" and str(preview_state.get("customer_name") or "").strip():
            continue
        if key == "customer_name" and str(preview_state.get("name") or "").strip():
            continue
        missing.append(key)
    return missing


class LeadCaptureModule(BaseModule):
    def get_module_name(self) -> str:
        return "lead_capture"

    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_lead_capture_tool_metadata(config)

    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_lead_capture_required_fields(config)

    def get_effective_configured_fields_for_context(
        self,
        context: ExecutionContext,
    ) -> List[Dict[str, Any]]:
        """Single authoritative effective configured lead field list.

        When the tenant supplies a non-empty explicit lead field list, that list is
        authoritative and returned exactly (order, keys, required flags, supported
        types preserved). No hidden default fields (name, interest, phone, email,
        preferred_contact_time, message, notes) are injected.

        When the tenant has no effective lead field definitions, the default
        fallback ``DEFAULT_LEAD_CAPTURE_FIELDS`` (name, interest) is used.

        The returned list is a copy; no mutable tenant configuration reference is
        returned and no state mutation, LLM call, tool call, or persistence occurs.
        """
        tenant_fields = self.get_required_fields(context.config.config_data)
        if isinstance(tenant_fields, list) and tenant_fields:
            return [dict(field) for field in tenant_fields if isinstance(field, dict)]
        return [dict(field) for field in DEFAULT_LEAD_CAPTURE_FIELDS]

    def get_required_fields_for_context(self, context: ExecutionContext) -> List[Dict[str, Any]]:
        """Return context-aware configured lead fields via the shared authority.

        The effective configured-field authority owns the field list and required
        flags. When an explicit tenant schema exists it is returned verbatim; the
        default fallback (name, interest) applies only when no effective tenant
        fields exist. Optional tool-schema compatibility properties (phone, email,
        preferred_contact_time) are never injected as configured fields here.
        """
        return self.get_effective_configured_fields_for_context(context)

    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_lead_capture_tool_schema(config)

    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        field_type = field_def.get("type", "string")
        if value is None or str(value).strip() == "":
            return ValidationResult(False, "Value is empty")
        if field_type == "email":
            if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', str(value)):
                return ValidationResult(False, "Please provide a valid email address")
            return ValidationResult(True)
        elif field_type == "phone":
            cleaned = re.sub(r'[\s\-\(\)]', '', str(value))
            if not re.match(r'^\+?\d{7,15}$', cleaned):
                return ValidationResult(False, "Please provide a valid phone number")
            return ValidationResult(True)
        elif field_type == "string":
            if len(str(value).strip()) < 2:
                return ValidationResult(False, "Please provide a longer response")
            return ValidationResult(True)
        return ValidationResult(True)

    def validate_external_configured_candidates(
        self,
        context: ExecutionContext,
        candidates: Mapping[str, Any],
    ) -> "LeadConfiguredCandidateValidationResult":
        """Dry-run deterministic validation of externally supplied configured lead candidates.

        This is the only external-candidate validator for the lead shadow. It never
        mutates lead state, never modifies prompt focus, never touches the action
        ledger, never executes tools, never calls an LLM, and never logs candidate
        values. It reuses the existing deterministic validation authority
        (``validate_input``) so no field-type validation is duplicated. All candidates
        are validated independently; a valid field remains accepted when another
        candidate is invalid.

        Rules:
          * reject protected/internal/consent-sensitive keys;
          * reject unknown keys (not in the configured lead field authority);
          * reject unsupported types (number, select, date, time, boolean, object, array);
          * reject nested dictionaries and lists;
          * reject empty values;
          * reject already-collected non-empty fields;
          * accept currently missing required or optional safe fields.
        """
        if not isinstance(candidates, Mapping) or not candidates:
            return LeadConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(candidates.keys())) if isinstance(candidates, Mapping) else (),
                reason_code="no_eligible_candidates",
            )

        configured_fields = self.get_required_fields_for_context(context)
        fields_by_key: dict[str, dict] = {}
        for field in configured_fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "").strip()
            if key and key not in fields_by_key:
                fields_by_key[key] = field

        current = context.session_data.state.get_task_step_data("lead_capture") or {}

        accepted_patch: dict[str, Any] = {}
        rejected: set[str] = set()
        reason_code: str | None = None

        # Name/customer_name compatibility for state-reading only: a configured key
        # may treat the other alias's non-empty value as already collected.
        def _effective_existing(key: str) -> Any:
            value = current.get(key)
            if value not in (None, ""):
                return value
            if key == "name" and str(current.get("customer_name") or "").strip():
                return current.get("customer_name")
            if key == "customer_name" and str(current.get("name") or "").strip():
                return current.get("name")
            return None

        for raw_key, value in candidates.items():
            if not isinstance(raw_key, str) or not raw_key:
                rejected.add(str(raw_key))
                reason_code = reason_code or "unknown_key"
                continue
            if raw_key in _LEAD_SHADOW_PROTECTED_KEYS:
                rejected.add(raw_key)
                reason_code = reason_code or "protected_field"
                continue
            configured = fields_by_key.get(raw_key)
            if configured is None:
                rejected.add(raw_key)
                reason_code = reason_code or "unknown_key"
                continue
            field_type = str(configured.get("type") or "string").strip().lower()
            if field_type not in _LEAD_SHADOW_SUPPORTED_TYPES:
                rejected.add(raw_key)
                reason_code = reason_code or "unsupported_type"
                continue
            # Reject nested dictionaries and lists (and other non-primitive shapes).
            if isinstance(value, (dict, list)):
                rejected.add(raw_key)
                reason_code = reason_code or "unsupported_structure"
                continue
            if value is None or value == "":
                rejected.add(raw_key)
                reason_code = reason_code or "empty_value"
                continue
            # Do not overwrite an accepted existing value; corrections remain owned
            # by the existing authoritative lead runtime.
            if _effective_existing(raw_key) not in (None, ""):
                rejected.add(raw_key)
                reason_code = reason_code or "field_already_collected"
                continue
            validation = self.validate_input(configured, value, tenant_config=context.config.config_data)
            if not validation.is_valid:
                rejected.add(raw_key)
                reason_code = reason_code or "invalid_value"
                continue
            accepted_patch[raw_key] = value

        if not accepted_patch:
            return LeadConfiguredCandidateValidationResult(
                accepted_patch={},
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(rejected)),
                reason_code=reason_code or "no_eligible_candidates",
            )
        return LeadConfiguredCandidateValidationResult(
            accepted_patch=dict(accepted_patch),
            accepted_field_keys=tuple(sorted(accepted_patch.keys())),
            rejected_field_keys=tuple(sorted(rejected)),
            reason_code=reason_code,
        )

    def apply_external_configured_candidates(
        self,
        context: ExecutionContext,
        candidates: Mapping[str, Any],
    ) -> "ExternalLeadCandidateApplicationResult":
        """Authoritative backend-validated non-terminal multi-field application.

        Re-runs ``validate_external_configured_candidates`` at application time and
        independently rejects protected, consent-sensitive, unknown, unsupported,
        invalid, nested, empty, and already-collected values. It then uses only the
        normalized accepted patch and writes every accepted field through exactly one
        lead-scoped state update -- but only when the patched preview would leave at
        least one applicable required configured field missing (non-terminal).

        It never calls an LLM, never executes a tool, never calls lead persistence,
        and never generates customer-visible text.

        Outcomes:
          * A -- at least one missing candidate accepted and required still missing
            afterward -> one atomic update, handled=True, applied=True;
          * B -- every supplied valid key already collected and a required field still
            missing -> zero writes, handled=True, applied=False,
            reason_code="already_satisfied";
          * C -- terminal batch: applying the accepted patch would complete every
            applicable required configured field -> zero writes, handled=False,
            reason_code="terminal_batch_deferred";
          * D -- nothing safely applicable -> zero writes, handled=False.
        """
        validation = self.validate_external_configured_candidates(context, candidates)
        if not validation.accepted_patch:
            # Already-satisfied replay (Outcome B): every supplied valid candidate key
            # already holds an accepted value and at least one required field is still
            # missing. This is a write-free, interpretation-free turn that the engine
            # must continue from authoritative state, never a legacy re-collection.
            # A genuine fallback (Outcome D) returns handled=False instead.
            if self._all_lead_candidates_already_satisfied(context, candidates):
                return ExternalLeadCandidateApplicationResult(
                    handled=True,
                    applied=False,
                    update_performed=False,
                    accepted_field_keys=(),
                    rejected_field_keys=tuple(sorted(validation.rejected_field_keys)),
                    reason_code="already_satisfied",
                )
            return ExternalLeadCandidateApplicationResult(
                handled=False,
                applied=False,
                update_performed=False,
                accepted_field_keys=(),
                rejected_field_keys=tuple(sorted(validation.rejected_field_keys)),
                reason_code=validation.reason_code or "no_eligible_candidates",
            )

        accepted_keys = tuple(sorted(validation.accepted_patch.keys()))
        accepted_patch = dict(validation.accepted_patch)

        state = context.session_data.state
        current = state.get_task_step_data("lead_capture") or {}
        preview = dict(current)
        preview.update(accepted_patch)

        # Outcome C: applying the accepted patch would complete every applicable
        # required configured field. Defer to the existing lead runtime; perform no
        # authoritative write (no partial application, no completion).
        if not _lead_required_missing_after_preview(context, preview):
            return ExternalLeadCandidateApplicationResult(
                handled=False,
                applied=False,
                update_performed=False,
                accepted_field_keys=(),
                rejected_field_keys=accepted_keys,
                reason_code="terminal_batch_deferred",
            )

        # Exactly one atomic lead-scoped update containing every accepted normalized
        # field. No per-field writes, no raw semantic values, no global state writes.
        merged = dict(current)
        merged.update(accepted_patch)
        state.update_task_step_data("lead_capture", merged)

        return ExternalLeadCandidateApplicationResult(
            handled=True,
            applied=True,
            update_performed=True,
            accepted_field_keys=accepted_keys,
            rejected_field_keys=tuple(sorted(validation.rejected_field_keys)),
            reason_code="accepted",
        )

    def _all_lead_candidates_already_satisfied(
        self,
        context: ExecutionContext,
        candidates: Mapping[str, Any],
    ) -> bool:
        """True when every supplied candidate key already holds an accepted value.

        Write-free already-satisfied signal. A key is "satisfied" only when it maps to
        a known, non-protected configured safe field that already contains a value
        (with name/customer_name alias compatibility). If any supplied key is unknown,
        protected, consent-sensitive, unsupported, or still empty, this is not an
        already-satisfied turn and the caller must treat it as a fallback (Outcome D).
        """
        if not isinstance(candidates, Mapping) or not candidates:
            return False
        configured_fields = self.get_required_fields_for_context(context)
        fields_by_key: dict[str, dict] = {}
        for field in configured_fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "").strip()
            if key and key not in fields_by_key:
                fields_by_key[key] = field
        current = context.session_data.state.get_task_step_data("lead_capture") or {}

        def _effective_existing(key: str) -> Any:
            value = current.get(key)
            if value not in (None, ""):
                return value
            if key == "name" and str(current.get("customer_name") or "").strip():
                return current.get("customer_name")
            if key == "customer_name" and str(current.get("name") or "").strip():
                return current.get("name")
            return None

        for raw_key, value in candidates.items():
            if not isinstance(raw_key, str) or not raw_key:
                return False
            if raw_key in _LEAD_SHADOW_PROTECTED_KEYS:
                return False
            configured = fields_by_key.get(raw_key)
            if configured is None:
                return False
            field_type = str(configured.get("type") or "string").strip().lower()
            if field_type not in _LEAD_SHADOW_SUPPORTED_TYPES:
                return False
            if value is None or value == "" or isinstance(value, (dict, list)):
                return False
            if _effective_existing(raw_key) in (None, ""):
                return False
        return True

    def validate_terminal_configured_payload(
        self,
        context: ExecutionContext,
        payload: Mapping[str, Any],
    ) -> "LeadTerminalPayloadValidationResult":
        """Tenant-aware terminal lead payload validation authority (validation-only).

        Given an active lead context and a proposed complete terminal payload, this
        resolves the exact tenant-configured lead schema, normalizes and validates
        every supplied configured value, determines whether all required configured
        fields are satisfied, and returns a typed immutable validation result.

        This is the future pre-registry validation authority for terminal lead
        handoff. It performs NO mutation, calls NO LLM, executes NO tool, invokes NO
        registry, and touches NO handler, service, repository, or event layer. It does
        not write lead state and does not persist a lead.

        Rules:
          * resolve fields only through ``get_effective_configured_fields_for_context``;
          * preserve exact tenant field keys and order;
          * use tenant/default ``required`` flags exactly;
          * support only string/textarea/phone/email;
          * validate every supplied configured value using existing ``validate_input``;
          * reject protected/internal and consent-sensitive keys;
          * reject unknown keys;
          * reject unsupported configured types;
          * reject dictionaries, lists, and nested values;
          * reject empty supplied values;
          * allow missing optional configured fields;
          * return invalid when any configured required field is missing;
          * name/customer_name alias projection (state-reading compatibility only).
        """
        if not isinstance(payload, Mapping):
            return LeadTerminalPayloadValidationResult(
                valid=False,
                normalized_payload={},
                configured_field_keys=(),
                required_field_keys=(),
                missing_required_field_keys=(),
                invalid_field_keys=(),
                rejected_field_keys=(),
                reason_code="invalid_payload_shape",
            )

        configured_fields = self.get_effective_configured_fields_for_context(context)

        # Preserve tenant field order for configured/required diagnostics.
        ordered_configured: list[dict] = []
        ordered_required: list[str] = []
        fields_by_key: dict[str, dict] = {}
        for field in configured_fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "").strip()
            if not key or key in fields_by_key:
                continue
            fields_by_key[key] = field
            ordered_configured.append(field)
            field_type = str(field.get("type") or "string").strip().lower()
            if field.get("required") is True and field_type in _LEAD_TERMINAL_SUPPORTED_TYPES:
                ordered_required.append(key)

        configured_keys = tuple(str(f.get("key") or "").strip() for f in ordered_configured)
        required_keys = tuple(ordered_required)

        flags = {
            "invalid_payload_shape": False,
            "protected_fields": False,
            "consent_sensitive_fields": False,
            "unknown_fields": False,
            "unsupported_configured_type": False,
            "invalid_configured_values": False,
            "missing_required_fields": False,
        }

        normalized: dict[str, Any] = {}
        missing_required: set[str] = set()
        invalid_keys: set[str] = set()
        rejected_keys: set[str] = set()

        def _is_protected(raw_key: str) -> bool:
            if raw_key in _LEAD_TERMINAL_PROTECTED_KEYS:
                return True
            for suffix in _LEAD_TERMINAL_PROTECTED_SUFFIXES:
                if raw_key.endswith(suffix):
                    return True
            return False

        def _is_consent_sensitive(raw_key: str) -> bool:
            lowered = raw_key.lower()
            return any(marker in lowered for marker in _LEAD_TERMINAL_CONSENT_MARKERS)

        # Keys consumed only as an alias source (name/customer_name) must not be
        # reported as unknown-key rejections.
        alias_consumed_keys: set[str] = set()

        def _resolve_source(raw_key: str):
            # name/customer_name alias projection: use the other alias's non-empty
            # scalar supplied value only as a source for projecting the exact
            # configured key. Returns (value_or_nested_sentinel, alias_key_used_or_None).
            # A directly-supplied nested value is returned as-is so the caller can
            # classify it as invalid rather than missing.
            value = payload.get(raw_key)
            if value not in (None, "") or isinstance(value, (dict, list)):
                if isinstance(value, (dict, list)):
                    return value, None
                return value, None
            if raw_key == "name" and str(payload.get("customer_name") or "").strip():
                v = payload.get("customer_name")
                if isinstance(v, (dict, list)):
                    return v, "customer_name"
                if v not in (None, ""):
                    return v, "customer_name"
            if raw_key == "customer_name" and str(payload.get("name") or "").strip():
                v = payload.get("name")
                if isinstance(v, (dict, list)):
                    return v, "name"
                if v not in (None, ""):
                    return v, "name"
            return None, None

        for key in configured_keys:
            field = fields_by_key[key]
            field_type = str(field.get("type") or "string").strip().lower()
            is_required = field.get("required") is True

            if field_type not in _LEAD_TERMINAL_SUPPORTED_TYPES:
                # Unsupported configured type. If required, it prevents completeness.
                # If supplied, it invalidates the payload (rejected). If optional and
                # omitted, it does not create a missing-required error.
                supplied = payload.get(key)
                supplied_present = supplied not in (None, "") or isinstance(supplied, (dict, list))
                if is_required:
                    missing_required.add(key)
                    flags["unsupported_configured_type"] = True
                if supplied_present:
                    flags["unsupported_configured_type"] = True
                    rejected_keys.add(key)
                continue

            source_value, alias_key = _resolve_source(key)
            if alias_key is not None:
                alias_consumed_keys.add(alias_key)

            if source_value is None:
                if is_required:
                    missing_required.add(key)
                continue

            if isinstance(source_value, (dict, list)):
                flags["invalid_configured_values"] = True
                invalid_keys.add(key)
                rejected_keys.add(key)
                continue

            # textarea is validated by the same existing string authority.
            validation_field = dict(field)
            if field_type == "textarea":
                validation_field["type"] = "string"
            validation = self.validate_input(validation_field, source_value, tenant_config=context.config.config_data)
            if not validation.is_valid:
                flags["invalid_configured_values"] = True
                invalid_keys.add(key)
                rejected_keys.add(key)
                continue

            normalized[key] = source_value

        # Reject unknown, protected, and consent-sensitive input keys.
        for raw_key in payload.keys():
            if not isinstance(raw_key, str) or not raw_key:
                flags["unknown_fields"] = True
                rejected_keys.add(str(raw_key))
                continue
            if raw_key in configured_keys:
                continue
            if raw_key in alias_consumed_keys:
                continue
            if _is_protected(raw_key):
                flags["protected_fields"] = True
                rejected_keys.add(raw_key)
                continue
            if _is_consent_sensitive(raw_key):
                flags["consent_sensitive_fields"] = True
                rejected_keys.add(raw_key)
                continue
            flags["unknown_fields"] = True
            rejected_keys.add(raw_key)

        if missing_required:
            flags["missing_required_fields"] = True

        reason_code: str | None = None
        for code in _LEAD_TERMINAL_REASON_PRECEDENCE:
            if code == "complete":
                continue
            if flags.get(code):
                reason_code = code
                break
        else:
            reason_code = "complete"

        valid = reason_code == "complete"

        return LeadTerminalPayloadValidationResult(
            valid=valid,
            normalized_payload=dict(normalized),
            configured_field_keys=configured_keys,
            required_field_keys=required_keys,
            missing_required_field_keys=tuple(
                k for k in required_keys if k in missing_required
            ),
            invalid_field_keys=tuple(sorted(invalid_keys)),
            rejected_field_keys=tuple(sorted(rejected_keys)),
            reason_code=reason_code,
        )

    def validate_payload(self, context: ExecutionContext) -> Optional[str]:
        step_data = context.session_data.state.get_task_step_data("lead_capture")

        # 1. Check if name is provided (either in context.user or step_data) and is not a placeholder
        user_name = context.user.name
        name = step_data.get("name") or user_name

        is_valid_name = False
        if name:
            normalized_name = name.strip().lower()
            if normalized_name not in {"unknown", "valued patient", "valued customer", "guest", "patient"}:
                is_valid_name = True

        if not is_valid_name:
            return "Please provide your name so we can register your interest."

        # 2. Check if we have at least one contact info (phone or email)
        user_phone = context.user.phone_number
        has_phone = (user_phone and user_phone.strip().lower() != "unknown") or bool(step_data.get("phone"))
        has_email = bool(step_data.get("email"))
        if not has_phone and not has_email:
            return "Please provide either your phone number or email address so we can contact you later."

        return None

    def prefill_from_message(self, message: str, context: ExecutionContext) -> None:
        """Best-effort extraction of details from the incoming message and customer context."""
        step_data = context.session_data.state.step_data

        # 1. Prefill name if not present and available in context.user
        if not step_data.get("name") and context.user.name and context.user.name.strip().lower() != "unknown":
            step_data["name"] = context.user.name

        # 2. Prefill phone if not present and available in context.user
        if not step_data.get("phone") and context.user.phone_number and context.user.phone_number.strip().lower() != "unknown":
            step_data["phone"] = context.user.phone_number

        if not message:
            return

        # 3. Extract name from message text using regex
        if not step_data.get("name"):
            name_match = re.search(
                r"\b(my name is|name is|this is)\s+([A-Za-z][A-Za-z .'-]{1,60})",
                message,
                flags=re.IGNORECASE,
            )
            if name_match:
                name = re.split(
                    r"\s*(?:,|\bphone\b|\bmobile\b|\bemail\b|\binterest\b)",
                    name_match.group(2).strip(),
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                if name:
                    step_data["name"] = name

        # 4. Extract phone from message text
        if not step_data.get("phone"):
            phone_match = re.search(r"(?:\+?\d[\d\s().-]{6,}\d)", message)
            if phone_match:
                step_data["phone"] = re.sub(r"[^\d+]", "", phone_match.group(0))

        # 5. Extract email from message text
        if not step_data.get("email"):
            email_match = re.search(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', message)
            if email_match:
                step_data["email"] = email_match.group(0)

        # 6. Extract preferred contact time
        if not step_data.get("preferred_contact_time"):
            time_match = re.search(
                r"\b(?:contact|call|reach)\b.*?\b(?:at|on|around|time|by)\b\s*([A-Za-z0-9\s:.-]{2,30})",
                message,
                flags=re.IGNORECASE
            )
            if time_match:
                step_data["preferred_contact_time"] = time_match.group(1).strip()

    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await lead_tool_handlers.execute_lead_capture_tool(
            context,
            lead_service=lead_service,
            logger=logger,
        )

lead_capture_module = LeadCaptureModule()
