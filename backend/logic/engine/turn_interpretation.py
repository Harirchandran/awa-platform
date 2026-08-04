"""Canonical semantic-turn model, coordinator, and deterministic turn planner."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.core.context import ExecutionContext
from backend.ops.logger import logger


INTERPRETATION_VERSION = 1
_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_INTERNAL_FIELD = re.compile(r"(^id$|_id$|uuid|token|secret|trace|raw|payload|backend|otp|password)", re.I)


class TurnKind(str, Enum):
    ACTIVE_WORKFLOW_ANSWER = "active_workflow_answer"
    NEW_WORKFLOW_REQUEST = "new_workflow_request"
    CAPABILITY_REQUEST = "capability_request"
    GREETING = "greeting"
    CANCEL = "cancel"
    CHANGE = "change"
    CONTINUE = "continue"
    TANGENT = "tangent"
    CONFIRMATION = "confirmation"
    UNKNOWN = "unknown"


class WorkflowKey(str, Enum):
    BOOKING = "booking"
    ORDER = "order"
    PAYMENTS = "payments"
    LEAD_CAPTURE = "lead_capture"
    SUPPORT = "support"
    REMINDERS = "reminders"
    REVIEWS = "reviews"
    FAQ = "faq"
    HUMAN_HANDOFF = "human_handoff"


class WorkflowAction(str, Enum):
    NONE = "none"
    CONTINUE = "continue"
    SWITCH = "switch"
    CANCEL = "cancel"
    CHANGE = "change"
    CONFIRM = "confirm"
    TANGENT = "tangent"
    CLARIFY = "clarify"
    UNKNOWN = "unknown"


class ConfirmationIntent(str, Enum):
    NONE = "none"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    CHANGE = "change"
    UNCLEAR = "unclear"


class ClarificationReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    MISSING_CONTEXT = "missing_context"
    AMBIGUOUS_FIELD_VALUE = "ambiguous_field_value"
    MULTIPLE_WORKFLOW_CANDIDATES = "multiple_workflow_candidates"
    UNSUPPORTED_WORKFLOW = "unsupported_workflow"
    SEMANTIC_PROVIDER_FAILED = "semantic_provider_failed"
    UNRECOGNIZED_REQUEST = "unrecognized_request"


class InterpretationSource(str, Enum):
    SHARED_EXTRACTOR = "shared_extractor"
    PENDING_DECISION = "pending_decision"
    SEMANTIC_GATEWAY = "semantic_gateway"
    LEGACY_ROUTER = "legacy_router"
    SAFE_FALLBACK = "safe_fallback"


class InterpretationReason(str, Enum):
    ACTIVE_PROMPT_FIELD_ANSWER = "active_prompt_field_answer"
    ACTIVE_WORKFLOW_CONTINUATION = "active_workflow_continuation"
    EXPLICIT_CAPABILITY_REQUEST = "explicit_capability_request"
    EXPLICIT_CANCEL = "explicit_cancel"
    EXPLICIT_CHANGE = "explicit_change"
    EXPLICIT_CONTINUE = "explicit_continue"
    EXPLICIT_SWITCH = "explicit_switch"
    FAQ_TANGENT = "faq_tangent"
    PENDING_ACTION_REPLY = "pending_action_reply"
    ROUTER_WORKFLOW_CANDIDATE = "router_workflow_candidate"
    SEMANTIC_EXTRACTION_REUSED = "semantic_extraction_reused"
    SEMANTIC_GATEWAY_CANDIDATE = "semantic_gateway_candidate"
    SHADOW_FAILURE = "shadow_failure"
    UNKNOWN_ROUTE = "unknown_route"
    WORKFLOW_UNAVAILABLE = "workflow_unavailable"


class DisagreementReason(str, Enum):
    TURN_KIND_MISMATCH = "turn_kind_mismatch"
    CANDIDATE_WORKFLOW_MISMATCH = "candidate_workflow_mismatch"
    CAPABILITY_REQUEST_MISMATCH = "capability_request_mismatch"
    CANCEL_MISMATCH = "cancel_mismatch"
    CHANGE_MISMATCH = "change_mismatch"
    CONTINUE_MISMATCH = "continue_mismatch"
    TANGENT_MISMATCH = "tangent_mismatch"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    FIELD_KEY_MISMATCH = "field_key_mismatch"
    UNKNOWN_UNDERSTOOD_MISMATCH = "unknown_understood_mismatch"
    CONFIDENCE_BUCKET_MISMATCH = "confidence_bucket_mismatch"


_WORKFLOW_ALIASES = {
    "payment": "payments",
    "reminder": "reminders",
    "review": "reviews",
    "lead": "lead_capture",
    "handoff": "human_handoff",
    "knowledge": "faq",
}
_WORKFLOW_VALUES = {item.value for item in WorkflowKey}


class TurnInterpretationMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


def resolve_turn_interpretation_mode(
    explicit_mode: Any = None,
    *,
    legacy_shadow_enabled: bool = False,
) -> TurnInterpretationMode:
    """Resolve the platform switch; invalid explicit values fail closed to canonical authority."""
    if explicit_mode not in (None, ""):
        try:
            return TurnInterpretationMode(str(explicit_mode).strip().lower())
        except ValueError:
            logger.warning("Invalid TURN_INTERPRETATION_MODE; using authoritative mode")
            return TurnInterpretationMode.AUTHORITATIVE
    if legacy_shadow_enabled:
        return TurnInterpretationMode.SHADOW
    return TurnInterpretationMode.AUTHORITATIVE


@dataclass(frozen=True)
class TurnPlan:
    """Validation-driven semantic plan. It grants no permission to execute a tool."""

    response_path: str
    active_workflow: Optional[str]
    candidate_workflow: Optional[str]
    preserve_active_workflow: bool
    switch_requested: bool
    clear_current_workflow: bool
    capability_menu_requested: bool
    tangent_requested: bool
    clarification_requested: bool
    tool_scope_hint: Optional[str]
    reason_codes: tuple[str, ...]
    secondary_workflows: tuple[str, ...] = ()


class TurnPlanner:
    """Apply a validated frame to state-independent planning decisions."""

    def plan(self, frame: TurnInterpretation, runtime_available_modules: Iterable[str]) -> TurnPlan:
        available = {
            value for item in runtime_available_modules
            for value in [_safe_normalize_workflow(item)] if value
        }
        active = _safe_normalize_workflow(frame.active_workflow)
        candidate = _safe_normalize_workflow(frame.candidate_workflow)
        candidate_allowed = bool(
            candidate
            and (candidate in available or candidate == WorkflowKey.FAQ.value)
            and (frame.candidate_workflow_available is not False or candidate == WorkflowKey.FAQ.value)
        )
        secondary_workflows = tuple(
            dict.fromkeys(
                workflow
                for item in frame.secondary_workflows
                for workflow in [_safe_normalize_workflow(item)]
                if (
                    workflow
                    and workflow != candidate
                    and (
                        workflow in available
                        or workflow == WorkflowKey.FAQ.value
                    )
                )
            )
        )
        switch = bool(frame.explicit_switch and active and candidate_allowed and candidate != active)
        preserve = bool(active and not switch and not (frame.turn_kind == TurnKind.CANCEL and frame.cancel_requested))
        response_path = {
            TurnKind.CAPABILITY_REQUEST: "capability_menu",
            TurnKind.GREETING: "greeting",
            TurnKind.TANGENT: "tangent",
            TurnKind.CONFIRMATION: "pending_action",
            TurnKind.UNKNOWN: "clarification" if active else "general",
        }.get(frame.turn_kind, "workflow")
        scope = active if frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER else candidate if candidate_allowed else None
        if frame.turn_kind in {TurnKind.CAPABILITY_REQUEST, TurnKind.GREETING, TurnKind.UNKNOWN}:
            scope = None
        return TurnPlan(
            response_path=response_path,
            active_workflow=active,
            candidate_workflow=candidate if candidate_allowed else None,
            preserve_active_workflow=preserve,
            switch_requested=switch,
            clear_current_workflow=bool(frame.cancel_requested and frame.turn_kind == TurnKind.CANCEL),
            capability_menu_requested=frame.turn_kind == TurnKind.CAPABILITY_REQUEST,
            tangent_requested=frame.turn_kind == TurnKind.TANGENT,
            clarification_requested=bool(frame.needs_clarification),
            tool_scope_hint=scope,
            reason_codes=tuple(str(code) for code in frame.reason_codes),
            secondary_workflows=secondary_workflows,
        )


def normalize_workflow_key(value: Any) -> Optional[str]:
    if value in (None, "", "unknown", "ambiguous", "greeting", "cancel", "thanks"):
        return None
    normalized = _WORKFLOW_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
    if normalized not in _WORKFLOW_VALUES:
        raise ValueError("unsupported workflow key")
    return normalized


def _safe_normalize_workflow(value: Any) -> Optional[str]:
    try:
        return normalize_workflow_key(value)
    except (TypeError, ValueError):
        return None


def has_meaningful_field_extraction(
    extraction: Any,
) -> bool:
    """Return whether field extraction contains usable semantic evidence."""

    if extraction is None:
        return False

    field_updates = dict(
        getattr(extraction, "field_updates", {}) or {}
    )
    extraction_workflow = _safe_normalize_workflow(
        getattr(extraction, "workflow", None)
    )
    extraction_action = str(
        getattr(
            extraction,
            "workflow_action",
            "unknown",
        )
        or "unknown"
    )
    extraction_confidence = float(
        getattr(extraction, "confidence", 0.0)
        or 0.0
    )

    return bool(
        field_updates
        or (
            extraction_workflow is not None
            and extraction_action != "unknown"
            and extraction_confidence >= 0.5
        )
    )


def _safe_field_key(value: Any) -> str:
    key = str(value or "").strip()
    if not _FIELD_KEY.fullmatch(key) or _INTERNAL_FIELD.search(key):
        raise ValueError("invalid canonical field key")
    return key


def _validate_candidate_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list) and len(value) <= 20 and all(
        item is None or isinstance(item, (str, int, float, bool)) for item in value
    ):
        return value
    raise ValueError("nested or unsupported candidate field value")


class TurnInterpretation(BaseModel):
    """Validated presentation-neutral evidence. It has no execution methods."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    interpretation_version: Literal[1] = INTERPRETATION_VERSION
    turn_kind: TurnKind = TurnKind.UNKNOWN
    active_workflow: Optional[WorkflowKey] = None
    candidate_workflow: Optional[WorkflowKey] = None
    secondary_workflows: list[WorkflowKey] = Field(
        default_factory=list,
        max_length=4,
    )
    candidate_workflow_available: Optional[bool] = None
    workflow_action: WorkflowAction = WorkflowAction.UNKNOWN
    field_updates: Dict[str, Any] = Field(default_factory=dict, max_length=30)
    field_confidence: Dict[str, float] = Field(default_factory=dict, max_length=30)
    requested_fields: list[str] = Field(default_factory=list, max_length=20)
    explicit_switch: bool = False
    cancel_requested: bool = False
    change_requested: bool = False
    continue_requested: bool = False
    confirmation_intent: ConfirmationIntent = ConfirmationIntent.NONE
    capability_request: bool = False
    tangent: bool = False
    detected_language: Optional[str] = Field(default=None, max_length=32)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = True
    clarification_reason: Optional[ClarificationReason] = None
    source: InterpretationSource = InterpretationSource.SAFE_FALLBACK
    reason_codes: list[InterpretationReason] = Field(default_factory=list, max_length=12)

    @field_validator("active_workflow", "candidate_workflow", mode="before")
    @classmethod
    def normalize_workflow(cls, value: Any) -> Any:
        return normalize_workflow_key(value)

    @field_validator("secondary_workflows", mode="before")
    @classmethod
    def normalize_secondary_workflows(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, (list, tuple)):
            raise ValueError("secondary workflows must be a list")
        return list(
            dict.fromkeys(
                normalized
                for item in value
                for normalized in [normalize_workflow_key(item)]
                if normalized is not None
            )
        )

    @field_validator("field_updates", mode="before")
    @classmethod
    def validate_field_updates(cls, value: Any) -> Dict[str, Any]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("field updates must be an object")
        return {_safe_field_key(key): _validate_candidate_value(item) for key, item in value.items()}

    @field_validator("field_confidence", mode="before")
    @classmethod
    def validate_field_confidence(cls, value: Any) -> Dict[str, float]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("field confidence must be an object")
        result: Dict[str, float] = {}
        for key, confidence in value.items():
            numeric = float(confidence)
            if numeric < 0.0 or numeric > 1.0:
                raise ValueError("field confidence out of bounds")
            result[_safe_field_key(key)] = numeric
        return result

    @field_validator("requested_fields", mode="before")
    @classmethod
    def validate_requested_fields(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, (list, tuple)):
            raise ValueError("requested fields must be a list")
        return list(dict.fromkeys(_safe_field_key(item) for item in value))

    @field_validator("detected_language")
    @classmethod
    def validate_language(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z]{2,8}(?:-[a-z0-9]{2,8})?", normalized):
            return None
        return normalized

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "TurnInterpretation":
        unknown_confidence = set(self.field_confidence) - set(self.field_updates)
        if unknown_confidence:
            raise ValueError("field confidence without candidate field")
        if self.turn_kind == TurnKind.CAPABILITY_REQUEST and not self.capability_request:
            raise ValueError("capability turn must set capability_request")
        if self.turn_kind == TurnKind.TANGENT and not self.tangent:
            raise ValueError("tangent turn must set tangent")
        candidate = _safe_normalize_workflow(self.candidate_workflow)
        if candidate:
            self.secondary_workflows = [
                workflow
                for workflow in self.secondary_workflows
                if _safe_normalize_workflow(workflow) != candidate
            ]
        return self


class LegacyInterpretationComparison(BaseModel):
    """PII-safe comparison metadata; candidate values are intentionally absent."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    matched: bool
    shadow_turn_kind: TurnKind
    active_workflow: Optional[WorkflowKey] = None
    candidate_workflow: Optional[WorkflowKey] = None
    field_names: list[str] = Field(default_factory=list, max_length=30)
    confidence_bucket: Literal["low", "medium", "high"]
    legacy_route: str = Field(default="unavailable", max_length=40)
    legacy_response_source: Literal["authoritative_runtime"] = "authoritative_runtime"
    disagreement_reason_codes: list[DisagreementReason] = Field(default_factory=list, max_length=12)
    config_version: int = Field(ge=1)
    channel: Literal["awa_chat", "whatsapp", "unknown"] = "unknown"

    @field_validator("active_workflow", "candidate_workflow", mode="before")
    @classmethod
    def normalize_workflow(cls, value: Any) -> Any:
        return normalize_workflow_key(value)

    @field_validator("field_names", mode="before")
    @classmethod
    def validate_field_names(cls, value: Any) -> list[str]:
        return sorted({_safe_field_key(item) for item in (value or [])})

    @field_validator("legacy_route", mode="before")
    @classmethod
    def bound_legacy_route(cls, value: Any) -> str:
        route = str(value or "unavailable").strip().lower()
        return route if re.fullmatch(r"[a-z0-9_]{1,40}", route) else "unavailable"


@dataclass(frozen=True)
class ShadowTurnSnapshot:
    text: str
    channel: str
    active_workflow: Optional[str]
    requested_fields: tuple[str, ...]
    config_version: int
    pending_action: bool
    pending_status: str
    tenant_local_time: str = ""
    scoped_field_names: tuple[str, ...] = ()


def _confidence_bucket(confidence: float) -> Literal["low", "medium", "high"]:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _dedupe_enum(values: Iterable[Any], enum_type: type[Enum]) -> list[Any]:
    seen = []
    for value in values:
        try:
            normalized = enum_type(value)
        except (TypeError, ValueError):
            continue
        if normalized not in seen:
            seen.append(normalized)
    return seen


class TurnInterpretationCoordinator:
    """Builds and compares a shadow frame solely from evidence already computed by runtime."""

    def capture(self, context: ExecutionContext) -> ShadowTurnSnapshot:
        state = context.session_data.state
        ledger = state.action_ledger
        active = _safe_normalize_workflow(ledger.current_task or context.session_data.current_module)
        focus = state.prompt_focus
        requested = tuple(focus.requested_fields) if focus and _safe_normalize_workflow(focus.workflow) == active else ()
        scoped = state.get_task_step_data(ledger.current_task or context.session_data.current_module) if active else {}
        scoped_field_names = tuple(sorted(
            key for key in scoped if _FIELD_KEY.fullmatch(str(key)) and not _INTERNAL_FIELD.search(str(key))
        ))
        timezone_name = str(context.config.config_data.get("timezone") or "UTC")
        try:
            tenant_now = datetime.now(ZoneInfo(timezone_name))
        except Exception:
            tenant_now = datetime.now(timezone.utc)
        return ShadowTurnSnapshot(
            text=str((context.incoming_message or {}).get("text") or ""),
            channel=str((context.incoming_message or {}).get("channel_type") or "unknown"),
            active_workflow=active,
            requested_fields=requested,
            config_version=context.config.version,
            pending_action=ledger.pending_action is not None,
            pending_status=str(ledger.task_status or "idle"),
            tenant_local_time=tenant_now.replace(microsecond=0).isoformat(),
            scoped_field_names=scoped_field_names,
        )

    def fallback_snapshot(self, context: ExecutionContext) -> ShadowTurnSnapshot:
        """Create a minimal safe frame input if richer snapshot capture unexpectedly fails."""
        return ShadowTurnSnapshot(
            text=str((context.incoming_message or {}).get("text") or ""),
            channel=str((context.incoming_message or {}).get("channel_type") or "unknown"),
            active_workflow=None,
            requested_fields=(),
            config_version=max(1, int(getattr(context.config, "version", 1) or 1)),
            pending_action=False,
            pending_status="unknown",
        )

    def interpret_turn(
        self,
        snapshot: ShadowTurnSnapshot,
        *,
        evidence: Optional[Dict[str, Any]] = None,
        runtime_available_modules: Iterable[str] = (),
    ) -> TurnInterpretation:
        evidence = evidence or {}
        route = evidence.get("routing_decision")
        extraction = evidence.get("field_extraction")
        pending = evidence.get("pending_decision")
        semantic = evidence.get("semantic_gateway_evidence")
        semantic_failure = bool(evidence.get("semantic_failure"))
        available = {
            normalized for item in runtime_available_modules
            for normalized in [_safe_normalize_workflow(item)] if normalized
        }
        text = snapshot.text.strip().lower()
        active = snapshot.active_workflow
        route_intent = _safe_normalize_workflow(getattr(route, "intent", None)) if route else None
        route_reason = str(getattr(route, "reason", "") or "")
        route_confidence = float(getattr(route, "confidence", 0.0) or 0.0)
        capability_request = self._is_capability_request(text)

        if pending is not None or snapshot.pending_action:
            return self._from_pending(snapshot, pending, semantic_failure=semantic_failure)

        field_updates: Dict[str, Any] = {}
        field_confidence: Dict[str, float] = {}
        detected_language = None
        extraction_confidence = 0.0
        extraction_action = "unknown"
        extraction_workflow = None
        if extraction is not None:
            field_updates = dict(getattr(extraction, "field_updates", {}) or {})
            field_confidence = dict(getattr(extraction, "field_confidence", {}) or {})
            extraction_confidence = float(getattr(extraction, "confidence", 0.0) or 0.0)
            extraction_action = str(getattr(extraction, "workflow_action", "unknown") or "unknown")
            extraction_workflow = _safe_normalize_workflow(getattr(extraction, "workflow", None))
            detected_language = getattr(extraction, "detected_language", None)

        extraction_has_evidence = (
            has_meaningful_field_extraction(extraction)
        )

        if not extraction_has_evidence and semantic is not None:
            return self._from_semantic_gateway(
                snapshot,
                semantic,
                available=available,
            )

        text_candidate = self._explicit_candidate(text, active)
        candidate = text_candidate if text_candidate == "human_handoff" else extraction_workflow or route_intent or text_candidate
        field_confidence = {key: value for key, value in field_confidence.items() if key in field_updates}
        candidate_available = candidate in available if candidate else None
        confidence = extraction_confidence if extraction is not None else route_confidence
        reasons: list[InterpretationReason] = []
        source = InterpretationSource.SHARED_EXTRACTOR if extraction is not None else InterpretationSource.LEGACY_ROUTER
        if extraction is not None:
            reasons.append(InterpretationReason.SEMANTIC_EXTRACTION_REUSED)

        cancel_requested = self._has_explicit_cancel(text) or extraction_action == "cancel"
        change_requested = self._has_explicit_change(text) or extraction_action == "change"
        continue_requested = self._has_explicit_continue(text) or extraction_action == "continue"
        explicit_switch = bool(
            active and candidate and candidate != active
            # Router disagreement is evidence, never switch authority. A switch
            # requires explicit customer language or shared semantic evidence.
            and ("cancel" in text or self._has_explicit_switch_request(text, candidate) or extraction_action == "switch")
        )

        if capability_request:
            reasons.append(InterpretationReason.EXPLICIT_CAPABILITY_REQUEST)
            return TurnInterpretation(
                turn_kind=TurnKind.CAPABILITY_REQUEST,
                active_workflow=active,
                workflow_action=WorkflowAction.NONE,
                requested_fields=list(snapshot.requested_fields),
                capability_request=True,
                confidence=max(confidence, 0.95),
                needs_clarification=False,
                source=source,
                reason_codes=reasons,
            )
        if explicit_switch:
            reasons.extend([InterpretationReason.EXPLICIT_SWITCH])
            if cancel_requested:
                reasons.append(InterpretationReason.EXPLICIT_CANCEL)
            return TurnInterpretation(
                turn_kind=TurnKind.NEW_WORKFLOW_REQUEST,
                active_workflow=active,
                candidate_workflow=candidate,
                candidate_workflow_available=candidate_available,
                workflow_action=WorkflowAction.SWITCH,
                field_updates=field_updates,
                field_confidence=field_confidence,
                requested_fields=list(snapshot.requested_fields),
                explicit_switch=True,
                cancel_requested=cancel_requested,
                confidence=max(confidence, 0.8),
                needs_clarification=False,
                source=source,
                reason_codes=reasons,
            )
        if cancel_requested:
            reasons.append(InterpretationReason.EXPLICIT_CANCEL)
            return TurnInterpretation(
                turn_kind=TurnKind.CANCEL,
                active_workflow=active,
                workflow_action=WorkflowAction.CANCEL,
                requested_fields=list(snapshot.requested_fields),
                cancel_requested=True,
                confidence=max(confidence, 0.95),
                needs_clarification=False,
                source=source,
                reason_codes=reasons,
            )
        if change_requested:
            reasons.append(InterpretationReason.EXPLICIT_CHANGE)
            return TurnInterpretation(
                turn_kind=TurnKind.CHANGE,
                active_workflow=active,
                workflow_action=WorkflowAction.CHANGE,
                field_updates=field_updates,
                field_confidence=field_confidence,
                requested_fields=list(snapshot.requested_fields),
                change_requested=True,
                confidence=max(confidence, 0.9),
                needs_clarification=not bool(field_updates),
                clarification_reason=ClarificationReason.MISSING_CONTEXT if not field_updates else None,
                source=source,
                reason_codes=reasons,
            )
        if continue_requested and not field_updates:
            reasons.append(InterpretationReason.EXPLICIT_CONTINUE)
            return TurnInterpretation(
                turn_kind=TurnKind.CONTINUE,
                active_workflow=active,
                workflow_action=WorkflowAction.CONTINUE,
                requested_fields=list(snapshot.requested_fields),
                continue_requested=True,
                confidence=max(confidence, 0.95),
                needs_clarification=False,
                source=source,
                reason_codes=reasons,
            )
        tangent = bool(getattr(route, "is_tangent", False) or extraction_action == "tangent")
        if tangent:
            reasons.append(InterpretationReason.FAQ_TANGENT)
            return TurnInterpretation(
                turn_kind=TurnKind.TANGENT,
                active_workflow=active,
                candidate_workflow=candidate,
                candidate_workflow_available=candidate_available,
                workflow_action=WorkflowAction.TANGENT,
                requested_fields=list(snapshot.requested_fields),
                tangent=True,
                confidence=max(confidence, 0.75),
                needs_clarification=False,
                source=source,
                reason_codes=reasons,
            )
        if active and (field_updates or extraction_action == "continue" or route_reason.startswith("active_task")):
            reasons.append(
                InterpretationReason.ACTIVE_PROMPT_FIELD_ANSWER
                if field_updates or snapshot.requested_fields
                else InterpretationReason.ACTIVE_WORKFLOW_CONTINUATION
            )
            return TurnInterpretation(
                turn_kind=TurnKind.ACTIVE_WORKFLOW_ANSWER,
                active_workflow=active,
                candidate_workflow=active,
                candidate_workflow_available=active in available if available else None,
                workflow_action=WorkflowAction.CONTINUE,
                field_updates=field_updates,
                field_confidence=field_confidence,
                requested_fields=list(snapshot.requested_fields),
                continue_requested=continue_requested,
                detected_language=detected_language,
                confidence=max(confidence, 0.75 if field_updates else confidence),
                needs_clarification=semantic_failure or confidence < 0.5,
                clarification_reason=ClarificationReason.SEMANTIC_PROVIDER_FAILED if semantic_failure else (
                    ClarificationReason.LOW_CONFIDENCE if confidence < 0.5 else None
                ),
                source=source,
                reason_codes=reasons,
            )
        if candidate and candidate not in {WorkflowKey.FAQ.value}:
            reasons.append(InterpretationReason.ROUTER_WORKFLOW_CANDIDATE)
            if candidate_available is False:
                reasons.append(InterpretationReason.WORKFLOW_UNAVAILABLE)
            return TurnInterpretation(
                turn_kind=TurnKind.NEW_WORKFLOW_REQUEST,
                active_workflow=active,
                candidate_workflow=candidate,
                candidate_workflow_available=candidate_available,
                workflow_action=WorkflowAction.CONTINUE,
                field_updates=field_updates,
                field_confidence=field_confidence,
                detected_language=detected_language,
                confidence=confidence,
                needs_clarification=semantic_failure or confidence < 0.5 or candidate_available is False,
                clarification_reason=(
                    ClarificationReason.SEMANTIC_PROVIDER_FAILED if semantic_failure
                    else ClarificationReason.UNSUPPORTED_WORKFLOW if candidate_available is False
                    else ClarificationReason.LOW_CONFIDENCE if confidence < 0.5
                    else None
                ),
                source=source,
                reason_codes=reasons,
            )
        if route_intent is None and str(getattr(route, "intent", "")) == "greeting":
            return TurnInterpretation(
                turn_kind=TurnKind.GREETING,
                workflow_action=WorkflowAction.NONE,
                confidence=max(confidence, 0.9),
                needs_clarification=False,
                source=source,
            )
        reasons.append(InterpretationReason.UNKNOWN_ROUTE)
        return TurnInterpretation(
            turn_kind=TurnKind.UNKNOWN,
            active_workflow=active,
            workflow_action=WorkflowAction.UNKNOWN,
            requested_fields=list(snapshot.requested_fields),
            confidence=confidence,
            needs_clarification=True,
            clarification_reason=(
                ClarificationReason.SEMANTIC_PROVIDER_FAILED if semantic_failure
                else ClarificationReason.UNRECOGNIZED_REQUEST
            ),
            source=InterpretationSource.SAFE_FALLBACK if semantic_failure else source,
            reason_codes=reasons,
        )

    def compare_with_legacy(
        self,
        frame: TurnInterpretation,
        *,
        evidence: Optional[Dict[str, Any]],
        config_version: int,
        channel: str,
    ) -> LegacyInterpretationComparison:
        evidence = evidence or {}
        route = evidence.get("routing_decision")
        extraction = evidence.get("field_extraction")
        pending = evidence.get("pending_decision")
        legacy_intent_raw = getattr(route, "intent", None)
        legacy_candidate = None
        try:
            legacy_candidate = normalize_workflow_key(legacy_intent_raw)
        except ValueError:
            legacy_candidate = None
        disagreements: list[DisagreementReason] = []
        if frame.candidate_workflow and legacy_candidate and frame.candidate_workflow != legacy_candidate:
            disagreements.append(DisagreementReason.CANDIDATE_WORKFLOW_MISMATCH)
        legacy_capability = self._is_capability_request_from_route(route)
        if frame.capability_request != legacy_capability:
            disagreements.append(DisagreementReason.CAPABILITY_REQUEST_MISMATCH)
        legacy_cancel = str(legacy_intent_raw or "") == "cancel" or getattr(pending, "action", None) == "cancel"
        if frame.cancel_requested != legacy_cancel and not frame.explicit_switch:
            disagreements.append(DisagreementReason.CANCEL_MISMATCH)
        legacy_action = str(
            getattr(pending, "action", None)
            or getattr(extraction, "workflow_action", None)
            or "unknown"
        )
        if legacy_action != "unknown":
            if (frame.workflow_action == WorkflowAction.CHANGE) != (legacy_action == "change"):
                disagreements.append(DisagreementReason.CHANGE_MISMATCH)
            if (frame.workflow_action == WorkflowAction.CONTINUE) != (legacy_action == "continue"):
                disagreements.append(DisagreementReason.CONTINUE_MISMATCH)
        legacy_tangent = bool(getattr(route, "is_tangent", False) or getattr(extraction, "workflow_action", None) == "tangent")
        if frame.tangent != legacy_tangent:
            disagreements.append(DisagreementReason.TANGENT_MISMATCH)
        legacy_confirmation = getattr(pending, "action", None) == "confirm"
        if (frame.confirmation_intent == ConfirmationIntent.CONFIRM) != legacy_confirmation:
            disagreements.append(DisagreementReason.CONFIRMATION_MISMATCH)
        if pending is not None:
            pending_kind = {
                "confirm": TurnKind.CONFIRMATION,
                "cancel": TurnKind.CANCEL,
                "change": TurnKind.CHANGE,
                "continue": TurnKind.CONTINUE,
                "tangent": TurnKind.TANGENT,
            }.get(legacy_action, TurnKind.UNKNOWN)
            if frame.turn_kind != pending_kind:
                disagreements.append(DisagreementReason.TURN_KIND_MISMATCH)
        legacy_fields = set(getattr(extraction, "field_updates", {}) or getattr(pending, "field_updates", {}) or {})
        if set(frame.field_updates) != legacy_fields:
            disagreements.append(DisagreementReason.FIELD_KEY_MISMATCH)
        legacy_unknown = str(legacy_intent_raw or "unknown") in {"unknown", "ambiguous"} and extraction is None and pending is None
        if (frame.turn_kind == TurnKind.UNKNOWN) != legacy_unknown:
            disagreements.append(DisagreementReason.UNKNOWN_UNDERSTOOD_MISMATCH)
        legacy_confidence = float(
            getattr(extraction, "confidence", None)
            or getattr(pending, "confidence", None)
            or getattr(route, "confidence", 0.0)
            or 0.0
        )
        if _confidence_bucket(frame.confidence) != _confidence_bucket(legacy_confidence):
            disagreements.append(DisagreementReason.CONFIDENCE_BUCKET_MISMATCH)
        legacy_route = str(getattr(route, "route_type", None) or ("pending_action" if pending else "unavailable"))
        return LegacyInterpretationComparison(
            matched=not disagreements,
            shadow_turn_kind=frame.turn_kind,
            active_workflow=frame.active_workflow,
            candidate_workflow=frame.candidate_workflow,
            field_names=list(frame.field_updates),
            confidence_bucket=_confidence_bucket(frame.confidence),
            legacy_route=legacy_route,
            disagreement_reason_codes=_dedupe_enum(disagreements, DisagreementReason),
            config_version=config_version,
            channel=channel if channel in {"awa_chat", "whatsapp"} else "unknown",
        )

    def observe(
        self,
        context: ExecutionContext,
        snapshot: ShadowTurnSnapshot,
    ) -> tuple[TurnInterpretation, LegacyInterpretationComparison]:
        available = (context.incoming_message or {}).get("_runtime_available_modules") or ()
        frame = self.interpret_turn(snapshot, evidence=context.shadow_turn_evidence, runtime_available_modules=available)
        comparison = self.compare_with_legacy(
            frame,
            evidence=context.shadow_turn_evidence,
            config_version=snapshot.config_version,
            channel=snapshot.channel,
        )
        context.turn_interpretation = frame
        context.turn_interpretation_comparison = comparison
        logger.info(
            "turn_interpretation_created",
            extra={"extra_data": {
                "event": "turn_interpretation_created",
                "interpretation_version": frame.interpretation_version,
                "turn_kind": frame.turn_kind,
                "active_workflow": frame.active_workflow,
                "candidate_workflow": frame.candidate_workflow,
                "field_names": sorted(frame.field_updates),
                "requested_fields": list(frame.requested_fields),
                "confidence_bucket": _confidence_bucket(frame.confidence),
                "source": frame.source,
                "reason_codes": list(frame.reason_codes),
                "config_version": snapshot.config_version,
                "channel": comparison.channel,
            }},
        )
        event = "turn_interpretation_legacy_match" if comparison.matched else "turn_interpretation_legacy_disagreement"
        logger.info(
            event,
            extra={"extra_data": {"event": event, **comparison.model_dump(mode="json")}},
        )
        return frame, comparison

    def safe_failure(self, context: ExecutionContext, snapshot: ShadowTurnSnapshot, exc: Exception) -> TurnInterpretation:
        frame = TurnInterpretation(
            turn_kind=TurnKind.UNKNOWN,
            active_workflow=snapshot.active_workflow,
            requested_fields=list(snapshot.requested_fields),
            confidence=0.0,
            needs_clarification=True,
            clarification_reason=ClarificationReason.SEMANTIC_PROVIDER_FAILED,
            source=InterpretationSource.SAFE_FALLBACK,
            reason_codes=[InterpretationReason.SHADOW_FAILURE],
        )
        context.turn_interpretation = frame
        logger.warning(
            "turn_interpretation_failed",
            extra={"extra_data": {"event": "turn_interpretation_failed", "reason_code": "shadow_failure", "error_type": type(exc).__name__}},
        )
        return frame

    @staticmethod
    def _is_capability_request(text: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        return any(phrase in normalized for phrase in (
            "what can you do", "what do you do", "how can you help", "what can you help", "show capabilities"
        ))

    @staticmethod
    def _is_capability_request_from_route(route: Any) -> bool:
        return bool(route and str(getattr(route, "intent", "")) == "greeting" and str(getattr(route, "reason", "")) == "greeting")

    @staticmethod
    def _has_explicit_cancel(text: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        return bool(
            re.fullmatch(r"cancel|stop|reset|start over|never ?mind|discard|clear", normalized)
            or re.search(r"\b(?:cancel|stop|discard)\s+(?:this|my|the)\b", normalized)
            or normalized.startswith("actually cancel ")
        )

    @staticmethod
    def _has_explicit_change(text: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        return bool(
            re.fullmatch(r"change|edit|modify|update", normalized)
            or re.search(r"\b(?:change|edit|modify|update)\s+(?:this|my|the)\b", normalized)
        )

    @staticmethod
    def _has_explicit_continue(text: str) -> bool:
        return bool(re.fullmatch(r"(?:continue|proceed|go ahead|yes|ok|okay|sure)", text.strip()))

    @staticmethod
    def _has_explicit_switch_request(text: str, candidate: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        patterns = {
            "booking": r"\b(?:book|schedule)\b|\bwant (?:an )?appointment\b",
            "order": r"\b(?:place|make|start) (?:an )?order\b|\bwant to order\b",
            "payments": r"\b(?:make|start|send) (?:a )?payment\b|\bwant to pay\b",
            "reminders": r"\b(?:set|create) (?:a )?reminder\b|\bremind me\b",
            "reviews": r"\b(?:leave|write|submit) (?:a )?(?:review|feedback)\b|\bwant to leave feedback\b",
            "support": r"\b(?:get|request|contact) support\b",
            "human_handoff": r"\b(?:talk|speak) to (?:a )?(?:human|person|agent|representative)\b|\bhuman please\b",
            "lead_capture": r"\b(?:contact|call) me\b|\bsign me up\b",
        }
        return bool(re.search(patterns.get(candidate, r"a^"), normalized))

    @staticmethod
    def _explicit_candidate(text: str, active: Optional[str]) -> Optional[str]:
        candidates = []
        keyword_map = {
            "booking": ("book", "booking", "appointment"),
            "order": ("order", "buy", "purchase"),
            "payments": ("payment", "pay"),
            "lead_capture": ("interested", "contact me", "sign up"),
            "support": ("support", "issue", "problem"),
            "reminders": ("remind", "reminder"),
            "reviews": ("review", "rating", "feedback"),
            "human_handoff": ("human", "person", "agent", "representative"),
        }
        for workflow, keywords in keyword_map.items():
            if workflow != active and any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in keywords):
                candidates.append(workflow)
        if "human_handoff" in candidates:
            return "human_handoff"
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _from_semantic_gateway(
        snapshot: ShadowTurnSnapshot,
        semantic: Any,
        *,
        available: set[str],
    ) -> TurnInterpretation:
        """Convert validated semantic gateway evidence into a canonical frame."""

        turn_kind = TurnKind(str(semantic.turn_kind))
        workflow_action = WorkflowAction(str(semantic.workflow_action))
        candidate = _safe_normalize_workflow(
            semantic.candidate_workflow
        )
        secondary_workflows = list(
            dict.fromkeys(
                workflow
                for item in getattr(
                    semantic,
                    "secondary_workflows",
                    (),
                )
                for workflow in [_safe_normalize_workflow(item)]
                if workflow and workflow != candidate
            )
        )

        if turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER:
            candidate = snapshot.active_workflow

        candidate_available = (
            candidate in available
            if candidate is not None
            else None
        )
        needs_clarification = bool(
            semantic.clarification_needed
            or candidate_available is False
        )

        if candidate_available is False:
            clarification_reason = (
                ClarificationReason.UNSUPPORTED_WORKFLOW
            )
        elif needs_clarification:
            clarification_reason = (
                ClarificationReason.UNRECOGNIZED_REQUEST
            )
        else:
            clarification_reason = None

        reason_codes = [
            InterpretationReason.SEMANTIC_GATEWAY_CANDIDATE
        ]
        if candidate_available is False:
            reason_codes.append(
                InterpretationReason.WORKFLOW_UNAVAILABLE
            )

        return TurnInterpretation(
            turn_kind=turn_kind,
            active_workflow=snapshot.active_workflow,
            candidate_workflow=candidate,
            secondary_workflows=secondary_workflows,
            candidate_workflow_available=candidate_available,
            workflow_action=workflow_action,
            requested_fields=list(snapshot.requested_fields),
            explicit_switch=semantic.explicit_switch,
            cancel_requested=semantic.cancel_requested,
            change_requested=semantic.change_requested,
            continue_requested=semantic.continue_requested,
            capability_request=semantic.capability_request,
            tangent=semantic.tangent,
            detected_language=semantic.detected_language,
            confidence=semantic.confidence,
            needs_clarification=needs_clarification,
            clarification_reason=clarification_reason,
            source=InterpretationSource.SEMANTIC_GATEWAY,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _from_pending(
        snapshot: ShadowTurnSnapshot,
        pending: Any,
        *,
        semantic_failure: bool,
    ) -> TurnInterpretation:
        action = str(getattr(pending, "action", "unknown") or "unknown")
        confidence = float(getattr(pending, "confidence", 0.0) or 0.0)
        updates = dict(getattr(pending, "field_updates", {}) or {})
        mapping = {
            "confirm": (TurnKind.CONFIRMATION, WorkflowAction.CONFIRM, ConfirmationIntent.CONFIRM),
            "cancel": (TurnKind.CANCEL, WorkflowAction.CANCEL, ConfirmationIntent.CANCEL),
            "change": (TurnKind.CHANGE, WorkflowAction.CHANGE, ConfirmationIntent.CHANGE),
            "continue": (TurnKind.CONTINUE, WorkflowAction.CONTINUE, ConfirmationIntent.NONE),
            "tangent": (TurnKind.TANGENT, WorkflowAction.TANGENT, ConfirmationIntent.NONE),
        }
        kind, workflow_action, confirmation = mapping.get(
            action,
            (TurnKind.UNKNOWN, WorkflowAction.UNKNOWN, ConfirmationIntent.UNCLEAR),
        )
        return TurnInterpretation(
            turn_kind=kind,
            active_workflow=snapshot.active_workflow,
            workflow_action=workflow_action,
            field_updates=updates,
            requested_fields=list(snapshot.requested_fields),
            cancel_requested=action == "cancel",
            change_requested=action == "change",
            continue_requested=action == "continue",
            confirmation_intent=confirmation,
            tangent=action == "tangent",
            confidence=confidence,
            needs_clarification=semantic_failure or action == "unknown" or confidence < 0.5,
            clarification_reason=(
                ClarificationReason.SEMANTIC_PROVIDER_FAILED if semantic_failure
                else ClarificationReason.LOW_CONFIDENCE if confidence < 0.5
                else ClarificationReason.MISSING_CONTEXT if action == "unknown"
                else None
            ),
            source=InterpretationSource.PENDING_DECISION if pending is not None else InterpretationSource.SAFE_FALLBACK,
            reason_codes=[InterpretationReason.PENDING_ACTION_REPLY],
        )


turn_interpretation_coordinator = TurnInterpretationCoordinator()
