from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.core.context import ExecutionContext
from backend.domains.bookings.service import booking_service
from backend.domains.bookings.tool_handlers import resolve_booking_service
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.modules.booking_module import booking_module
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult


TOOL_KEY = "booking.availability"
MAX_PUBLIC_WINDOWS = 12
_RANGE_TIME_LANGUAGE = re.compile(
    r"\b(?:after|from|later\s+than|at\s+or\s+after|not\s+before)\s+"
    r"(?:about\s+|around\s+|at\s+)?"
    r"(?:[0-2]?\d(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)?"
    r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight)\b"
    r"(?!\s*(?:minutes?|hours?|days?|weeks?|months?|years?)\b)",
    flags=re.IGNORECASE,
)


def _clock_text(value: datetime | time) -> str:
    return value.strftime("%H:%M")


def _ceil_to_minute(value: datetime) -> datetime:
    if value.second or value.microsecond:
        return value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return value.replace(second=0, microsecond=0)


def _optional_nonnegative_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _tenant_timezone(config: Dict[str, Any]) -> tuple[str, tzinfo]:
    timezone_name = str(config.get("timezone") or "UTC").strip() or "UTC"
    try:
        return timezone_name, ZoneInfo(timezone_name)
    except Exception:
        from dateutil import tz

        fallback = tz.gettz(timezone_name)
        if fallback is None:
            raise ValueError("invalid tenant timezone")
        return timezone_name, fallback


def _parse_clock(value: Any) -> time:
    parsed = time.fromisoformat(str(value or "").strip())
    return parsed.replace(second=0, microsecond=0)


def _working_window(
    config: Dict[str, Any],
    target_date: date,
    timezone_info: tzinfo,
) -> tuple[Optional[tuple[datetime, datetime]], str]:
    schedule = (config.get("working_hours") or {}).get(target_date.strftime("%A").lower())
    if schedule is None:
        return None, "working_hours_unconfigured"

    if isinstance(schedule, dict):
        if not bool(schedule.get("is_open", False)):
            return None, "closed"
        open_value = schedule.get("open_time")
        close_value = schedule.get("close_time")
    else:
        text = str(schedule or "").strip().lower()
        if not text or text == "closed":
            return None, "closed"
        if "-" not in text:
            return None, "working_hours_invalid"
        open_value, close_value = [part.strip() for part in text.split("-", 1)]

    try:
        open_time = _parse_clock(open_value)
        close_time = _parse_clock(close_value)
    except (TypeError, ValueError):
        return None, "working_hours_invalid"

    opens_at = datetime.combine(target_date, open_time, tzinfo=timezone_info)
    closes_at = datetime.combine(target_date, close_time, tzinfo=timezone_info)
    if closes_at <= opens_at:
        return None, "working_hours_invalid"
    return (opens_at, closes_at), "open"


def _coerce_busy_intervals(rows: List[Dict[str, Any]], timezone_info: tzinfo) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for row in rows:
        raw_date = row.get("booking_date")
        raw_time = row.get("booking_time")
        try:
            busy_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
            busy_time = raw_time if isinstance(raw_time, time) else time.fromisoformat(str(raw_time))
            duration_mins = max(5, min(int(row.get("duration_mins") or 30), 1440))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid persisted booking interval") from exc
        starts_at = datetime.combine(
            busy_date,
            busy_time.replace(microsecond=0),
            tzinfo=timezone_info,
        )
        intervals.append((starts_at, starts_at + timedelta(minutes=duration_mins)))

    merged: list[tuple[datetime, datetime]] = []
    for starts_at, ends_at in sorted(intervals, key=lambda item: item[0]):
        if not merged or starts_at > merged[-1][1]:
            merged.append((starts_at, ends_at))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, ends_at))
    return merged


def _available_start_windows(
    earliest_start: datetime,
    closes_at: datetime,
    duration_mins: int,
    busy_intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    duration = timedelta(minutes=duration_mins)
    cursor = earliest_start
    windows: list[tuple[datetime, datetime]] = []

    for busy_start, busy_end in busy_intervals:
        if busy_end <= cursor or busy_start >= closes_at:
            continue
        clipped_start = max(busy_start, cursor)
        if clipped_start - cursor >= duration:
            windows.append((cursor, clipped_start - duration))
        cursor = max(cursor, busy_end)
        if cursor >= closes_at:
            break

    if closes_at - cursor >= duration:
        windows.append((cursor, closes_at - duration))
    return windows


def _public_windows(windows: list[tuple[datetime, datetime]]) -> tuple[list[Dict[str, str]], bool]:
    public = [
        {
            "first_start_time": _clock_text(first_start),
            "last_start_time": _clock_text(last_start),
        }
        for first_start, last_start in windows
    ]
    if len(public) <= MAX_PUBLIC_WINDOWS:
        return public, False
    half = MAX_PUBLIC_WINDOWS // 2
    return [*public[:half], *public[-half:]], True


def _overlaps(
    starts_at: datetime,
    ends_at: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
) -> bool:
    return any(starts_at < busy_end and busy_start < ends_at for busy_start, busy_end in busy_intervals)


def _incoming_text(context: ExecutionContext) -> str:
    incoming = context.incoming_message if isinstance(context.incoming_message, dict) else {}
    return str(incoming.get("text") or incoming.get("message") or "")


def _uses_earliest_constraint(context: ExecutionContext, step_data: Optional[Dict[str, Any]] = None) -> bool:
    payload = step_data or context.session_data.state.get_task_step_data("booking")
    if payload.get("earliest_start_time") not in (None, ""):
        return True
    return _RANGE_TIME_LANGUAGE.search(_incoming_text(context)) is not None


class BookingAvailabilityModule(BaseModule):
    """Read-only, advisory booking availability derived from backend-owned facts."""

    def get_module_name(self) -> str:
        return "booking.availability"

    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return ToolMetadata(
            risk_level="read",
            requires_confirmation=False,
            requires_verified_customer=False,
            idempotency_scope="none",
        )

    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"key": "service_name", "label": "Configured service name", "type": "string", "required": True},
            {"key": "preferred_date", "label": "Tenant-local appointment date", "type": "date", "required": True},
            {"key": "preferred_time", "label": "Optional exact tenant-local start time", "type": "time", "required": False},
            {
                "key": "earliest_start_time",
                "label": "Optional earliest tenant-local start time",
                "type": "time",
                "required": False,
            },
        ]

    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "Public configured service name; never provide an internal identifier.",
                },
                "preferred_date": {
                    "type": "string",
                    "description": "One appointment date in the tenant's local timezone, preferably YYYY-MM-DD.",
                },
                "preferred_time": {
                    "type": "string",
                    "description": "Optional exact local start time such as 17:15 or 5:15 PM. Omit to search valid start windows for the date.",
                },
                "earliest_start_time": {
                    "type": "string",
                    "description": (
                        "Optional inclusive lower bound for requests such as 'after 5 PM' or 'from 17:00'. "
                        "Use this instead of preferred_time when the customer did not request one exact slot."
                    ),
                },
            },
            "required": ["service_name", "preferred_date"],
        }

    def get_tool_validation_payload(self, context: ExecutionContext) -> Dict[str, Any]:
        """Project shared booking state onto this read tool's public arguments.

        Booking collection state can legitimately contain write-only fields such
        as a customer name. They are irrelevant to this read and must neither be
        exposed in its public schema nor make the availability check unusable.
        """
        step_data = context.session_data.state.get_task_step_data("booking")
        allowed = set(self.get_tool_schema(context.config.config_data)["properties"])
        return {key: value for key, value in step_data.items() if key in allowed}

    def cleanup_after_attempt(self, context: ExecutionContext) -> None:
        """Remove search-only arguments before a later booking write is validated."""
        state = context.session_data.state
        current = state.get_task_step_data("booking")
        range_query = _uses_earliest_constraint(context, current)
        keys = {"earliest_start_time"}
        if range_query:
            # Field extraction may have collapsed "after five" into an exact
            # preferred_time. It was only a lower bound and must not later be
            # mistaken for the customer's selected appointment.
            keys.add("preferred_time")
        payloads = [state.scoped_step_data.get("booking")]
        current_task = getattr(state.action_ledger, "current_task", None)
        if current_task == "booking" or (
            current_task is None and not isinstance(state.scoped_step_data.get("booking"), dict)
        ):
            payloads.append(state.step_data)
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key in keys:
                payload.pop(key, None)

    def validate_input(
        self,
        field_def: Dict[str, Any],
        value: Any,
        tenant_config: Dict[str, Any] | None = None,
    ) -> ValidationResult:
        key = str(field_def.get("key") or "")
        if key == "service_name":
            return ValidationResult(bool(str(value or "").strip()), "Service name is required.")
        return booking_module.validate_input(field_def, value, tenant_config=tenant_config)

    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        config = context.config.config_data
        step_data = context.session_data.state.get_task_step_data("booking")
        service_input = str(step_data.get("service_name") or "").strip()
        resolved = await resolve_booking_service(context, service_input)
        if resolved.get("status") == "unknown":
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason="service_not_found",
                user_message="I couldn't find that service in this business's configured services.",
                validation_errors={"service_name": "Service not found in configured services."},
            )
        if resolved.get("status") == "ambiguous":
            names = [str(item.get("name")) for item in resolved.get("matches") or [] if item.get("name")]
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason="service_ambiguous",
                user_message="I found more than one matching service. Please choose: " + ", ".join(names[:8]) + ".",
                validation_errors={"service_name": "Service name is ambiguous."},
            )

        service = resolved["service"]
        service_name = str(service.get("name") or "Service")
        duration_mins = booking_module._service_duration_minutes(service)
        try:
            timezone_name, timezone_info = _tenant_timezone(config)
        except Exception:
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason="tenant_timezone_invalid",
                user_message="I can't verify appointment availability because the business timezone is not configured correctly.",
            )

        try:
            target_date = booking_module._coerce_booking_date(
                step_data.get("preferred_date"),
                tenant_config=config,
            )
        except Exception:
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason="booking_date_invalid",
                user_message="Please share today or a future appointment date.",
                validation_errors={"preferred_date": "A valid future date is required."},
            )

        requested_time: Optional[time] = None
        earliest_constraint: Optional[time] = None
        earliest_constraint_value = step_data.get("earliest_start_time")
        requested_time_value = step_data.get("preferred_time")
        range_query = _uses_earliest_constraint(context, step_data)
        if earliest_constraint_value not in (None, ""):
            earliest_constraint, time_error = booking_module._parse_configured_time(earliest_constraint_value)
            error_field = "earliest_start_time"
        elif range_query and requested_time_value not in (None, ""):
            # Safe compatibility path for field extraction that retained 17:00
            # from "after five" but lost the range operator.
            earliest_constraint, time_error = booking_module._parse_configured_time(requested_time_value)
            error_field = "earliest_start_time"
        elif range_query:
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason="availability_time_constraint_missing",
                user_message="What time should I use as the earliest start for that availability search?",
                validation_errors={"earliest_start_time": "An earliest start time is required for this range request."},
            )
        elif requested_time_value not in (None, ""):
            requested_time, time_error = booking_module._parse_configured_time(requested_time_value)
            error_field = "preferred_time"
        else:
            time_error = None

        parsed_constraint = earliest_constraint if range_query or earliest_constraint_value not in (None, "") else requested_time
        if parsed_constraint is None and time_error is not None:
            message = (
                "Please specify AM or PM for that time so I check the correct window."
                if time_error == "ambiguous_time"
                else "Please share a time such as 17:00 or 5 PM."
            )
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason=time_error or "booking_time_invalid",
                user_message=message,
                validation_errors={error_field: message},
            )
        if parsed_constraint is not None and time_error is not None:
            # The bounded parser returns a value alongside ambiguous_time. Do
            # not silently choose AM or PM for either an exact or range query.
            message = "Please specify AM or PM for that time so I check the correct window."
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason=time_error,
                user_message=message,
                validation_errors={error_field: message},
            )

        now = datetime.now(timezone_info)
        window, schedule_status = _working_window(config, target_date, timezone_info)
        if schedule_status == "closed":
            return self._closed_result(
                service_name,
                duration_mins,
                target_date,
                timezone_name,
                requested_time,
                now,
            )
        if window is None:
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason=schedule_status,
                user_message="I can't verify availability because working hours for that day are not configured correctly.",
            )

        opens_at, closes_at = window
        settings = config.get("booking_settings") if isinstance(config.get("booking_settings"), dict) else {}
        minimum_notice = _optional_nonnegative_int(settings.get("minimum_notice_minutes")) or 0
        earliest_start = max(opens_at, _ceil_to_minute(now + timedelta(minutes=minimum_notice)))
        if earliest_constraint is not None:
            earliest_start = max(
                earliest_start,
                datetime.combine(target_date, earliest_constraint, tzinfo=timezone_info),
            )
        advance_days = _optional_nonnegative_int(settings.get("advance_booking_days"))
        outside_advance_window = (
            advance_days is not None and target_date > now.date() + timedelta(days=advance_days)
        )

        try:
            rows = await booking_service.list_busy_intervals(context.tenant.id, target_date)
            busy_intervals = _coerce_busy_intervals(list(rows or []), timezone_info)
        except Exception as exc:
            logger.warning(
                "Booking availability read failed",
                extra={"extra_data": {"error_type": type(exc).__name__}},
            )
            return ToolExecutionResult(
                tool_key=TOOL_KEY,
                success=False,
                denied_reason="availability_read_failed",
                user_message="I couldn't verify live appointment availability right now. Please try again shortly.",
            )

        windows = [] if outside_advance_window else _available_start_windows(
            earliest_start,
            closes_at,
            duration_mins,
            busy_intervals,
        )
        public_windows, windows_truncated = _public_windows(windows)
        data: Dict[str, Any] = {
            "service_name": service_name,
            "service_duration_minutes": duration_mins,
            "preferred_date": target_date.isoformat(),
            "timezone": timezone_name,
            "business_hours": {
                "opens_at": _clock_text(opens_at),
                "closes_at": _clock_text(closes_at),
            },
            "has_available_start_window": bool(windows),
            "available_start_windows": public_windows,
            "windows_truncated": windows_truncated,
            "availability_is_advisory": True,
            "checked_at": now.isoformat(),
        }
        if earliest_constraint is not None:
            data["query_constraint"] = {
                "type": "earliest_start_time",
                "inclusive": True,
                "start_time": _clock_text(earliest_constraint),
            }

        if outside_advance_window:
            data["reason"] = "outside_advance_booking_window"
            data["advance_booking_days"] = advance_days

        if requested_time is not None:
            requested_start = datetime.combine(target_date, requested_time, tzinfo=timezone_info)
            requested_end = requested_start + timedelta(minutes=duration_mins)
            if outside_advance_window:
                reason = "outside_advance_booking_window"
                requested_available = False
            elif requested_start < opens_at:
                reason = "before_opening_time"
                requested_available = False
            elif requested_start < earliest_start:
                reason = "past_or_inside_minimum_notice"
                requested_available = False
            elif requested_end > closes_at:
                reason = "ends_after_closing_time"
                requested_available = False
            elif _overlaps(requested_start, requested_end, busy_intervals):
                reason = "overlaps_existing_booking"
                requested_available = False
            else:
                reason = "available"
                requested_available = True
            data["requested_slot"] = {
                "start_time": _clock_text(requested_start),
                "end_time": _clock_text(requested_end),
                "available": requested_available,
                "reason": reason,
            }
            if requested_available:
                message = (
                    f"{_clock_text(requested_start)} is available for {service_name} on "
                    f"{target_date.isoformat()} and would end at {_clock_text(requested_end)} "
                    f"({timezone_name}). Availability will be rechecked when the booking is confirmed."
                )
            else:
                message = self._unavailable_message(
                    service_name,
                    target_date,
                    requested_start,
                    reason,
                    public_windows,
                    timezone_name,
                )
        elif public_windows:
            rendered = "; ".join(
                f"{item['first_start_time']} to {item['last_start_time']}"
                for item in public_windows
            )
            constraint_text = (
                f" at or after {_clock_text(earliest_constraint)}"
                if earliest_constraint is not None
                else ""
            )
            message = (
                f"For {service_name} on {target_date.isoformat()}{constraint_text}, "
                f"valid start windows are {rendered} ({timezone_name}). "
                "Availability will be rechecked when the booking is confirmed."
            )
        elif outside_advance_window:
            message = (
                f"That date is outside this business's {advance_days}-day advance booking window. "
                "Please choose an earlier date."
            )
        else:
            message = (
                f"I couldn't find an available start window for {service_name} on {target_date.isoformat()}. "
                "Please choose another date or ask about a specific time."
            )

        return ToolExecutionResult(
            tool_key=TOOL_KEY,
            success=True,
            user_message=message,
            data=data,
        )

    @staticmethod
    def _closed_result(
        service_name: str,
        duration_mins: int,
        target_date: date,
        timezone_name: str,
        requested_time: Optional[time],
        checked_at: datetime,
    ) -> ToolExecutionResult:
        data: Dict[str, Any] = {
            "service_name": service_name,
            "service_duration_minutes": duration_mins,
            "preferred_date": target_date.isoformat(),
            "timezone": timezone_name,
            "has_available_start_window": False,
            "available_start_windows": [],
            "windows_truncated": False,
            "reason": "business_closed",
            "availability_is_advisory": True,
            "checked_at": checked_at.isoformat(),
        }
        if requested_time is not None:
            data["requested_slot"] = {
                "start_time": _clock_text(requested_time),
                "available": False,
                "reason": "business_closed",
            }
        return ToolExecutionResult(
            tool_key=TOOL_KEY,
            success=True,
            user_message=(
                f"The business is closed on {target_date.isoformat()}, so no {service_name} start time is available that day."
            ),
            data=data,
        )

    @staticmethod
    def _unavailable_message(
        service_name: str,
        target_date: date,
        requested_start: datetime,
        reason: str,
        public_windows: list[Dict[str, str]],
        timezone_name: str,
    ) -> str:
        if reason == "ends_after_closing_time" and public_windows:
            latest = public_windows[-1]["last_start_time"]
            return (
                f"{_clock_text(requested_start)} is too late for {service_name} on {target_date.isoformat()}; "
                f"the latest currently valid start is {latest} ({timezone_name})."
            )
        if reason == "before_opening_time":
            detail = "it is before opening time"
        elif reason == "past_or_inside_minimum_notice":
            detail = "it is in the past or inside the configured minimum-notice window"
        elif reason == "overlaps_existing_booking":
            detail = "it overlaps an existing appointment"
        elif reason == "outside_advance_booking_window":
            detail = "the date is outside the configured advance-booking window"
        else:
            detail = "that start time is not available"
        return (
            f"{_clock_text(requested_start)} is not available for {service_name} on {target_date.isoformat()} because {detail}. "
            "Please choose another time or date."
        )


booking_availability_module = BookingAvailabilityModule()
