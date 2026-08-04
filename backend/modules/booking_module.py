from typing import Dict, Any, List, Optional
import re
import hashlib
import json
from difflib import get_close_matches
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from backend.domains.bookings.tool_definitions import (
    DEFAULT_BOOKING_FIELDS,
    get_booking_required_fields,
    get_booking_tool_metadata,
    get_booking_tool_schema,
)
from backend.domains.bookings import tool_handlers as booking_tool_handlers
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.bookings.service import booking_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult
# Fuzzy-match targets for common date/time words (handles typos)
COMMON_DATE_WORDS = [
    "today", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "next week", "next month",
]
COMMON_TIME_WORDS = ["morning", "afternoon", "evening", "night", "noon"]

_TWENTY_FOUR_HOUR_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TWELVE_HOUR_TIME = re.compile(r"^(?P<hour>0?[1-9]|1[0-2])(?::(?P<minute>[0-5]\d))?\s*(?P<meridiem>am|pm)$", re.IGNORECASE)
_AMBIGUOUS_TIME = re.compile(r"^(?:(?:at|around|by)\s+)?(?P<hour>[1-9]|1[0-2])(?::(?P<minute>[0-5]\d))?$", re.IGNORECASE)
_TIME_TOKEN_INPUT = re.compile(r"^\d{1,2}(?::\d{2})?\s*(?:am|pm)?$", re.IGNORECASE)
class BookingModule(BaseModule):
    DEFAULT_FIELDS = DEFAULT_BOOKING_FIELDS
    def get_module_name(self) -> str:
        return "booking"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_booking_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_booking_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_booking_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        field_type = field_def.get("type", "string")
        if value is None or value == "":
            return ValidationResult(False, "Value is empty")
        if field_type == "date":
            val_str = str(value).strip().lower()
            tenant_today = self._tenant_today(tenant_config)
            if val_str == "yesterday":
                return ValidationResult(False, "Please choose today or a future date")
            # Fuzzy-match against common date words (handles typos like "tommorroew" → "tomorrow")
            fuzzy_hits = get_close_matches(val_str, COMMON_DATE_WORDS, n=1, cutoff=0.55)
            if fuzzy_hits:
                val_str = fuzzy_hits[0]
            if val_str == "yesterday":
                return ValidationResult(False, "Please choose today or a future date")
            if val_str == "today":
                return ValidationResult(True)
            if val_str == "tomorrow":
                return ValidationResult(True)
            # Handle day names
            if val_str in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
                return ValidationResult(True)
            try:
                parsed = date.fromisoformat(val_str)
            except ValueError:
                try:
                    from dateutil import parser
                    dayfirst = self._is_dayfirst(tenant_config)
                    # Fuzzy parsing for things like "next week Monday" or "Oct 12"
                    parsed = parser.parse(val_str, fuzzy=True, dayfirst=dayfirst).date()
                except Exception:
                    parsed = None
            if parsed:
                if parsed < tenant_today:
                    return ValidationResult(False, "Please choose today or a future date")
                return ValidationResult(True)
            else:
                return ValidationResult(False, "Invalid date format. Please specify a date like 'Tomorrow', 'Oct 15', or 'YYYY-MM-DD'")
        elif field_type == "number":
            if not str(value).isdigit():
                return ValidationResult(False, "Must be a number")
            return ValidationResult(True)
        elif field_type == "email":
            if "@" not in str(value):
                return ValidationResult(False, "Invalid email address")
            return ValidationResult(True)
        elif field_type == "time":
            _, reason = self._parse_configured_time(value)
            if reason is None:
                return ValidationResult(True)
            if reason == "ambiguous_time":
                return ValidationResult(False, "Please specify AM or PM for that time so I do not book the wrong slot.")
            return ValidationResult(False, "Invalid time format. Please specify a time like '10 AM' or choose Morning, Afternoon, or Evening")
        return ValidationResult(True)
    def validate_payload(self, context: ExecutionContext) -> Optional[str]:
        try:
            booking_date = self._coerce_booking_date(
                self._configured_temporal_value(context, "date"),
                tenant_config=context.config.config_data,
            )
            booking_time = self._coerce_booking_time(
                self._configured_temporal_value(context, "time"),
                tenant_config=context.config.config_data,
            )
        except Exception:
            return None # Fallback to field validation if coercion fails
        if not booking_date or not booking_time:
            return None
        tzinfo = self._tenant_timezone(context.config.config_data)
        now = datetime.now(tzinfo)
        # Create timezone aware datetime for the booking
        booking_dt = datetime.combine(booking_date, booking_time)
        if booking_dt.tzinfo is None:
            booking_dt = booking_dt.replace(tzinfo=now.tzinfo)
        ambiguous_past_message = self._ambiguous_past_time_message(context, booking_date, booking_dt, now)
        if ambiguous_past_message:
            return ambiguous_past_message
        if booking_dt < now:
            return "The requested time is in the past. Please choose a future date and time."
        availability_error = self._validate_collected_time_availability(
            context,
            booking_date,
            booking_time,
        )
        if availability_error:
            return availability_error
        return None

    def normalize_configured_field_value(
        self,
        field_def: Dict[str, Any],
        value: Any,
        *,
        tenant_config: Dict[str, Any] | None = None,
    ) -> Any:
        """Normalize temporal values while retaining the tenant-configured key."""
        field_type = str(field_def.get("type") or "").lower()
        if field_type == "date":
            return self._coerce_booking_date(value, tenant_config=tenant_config).isoformat()
        if field_type == "time":
            parsed, reason = self._parse_configured_time(value)
            return parsed.strftime("%H:%M") if parsed and reason is None else value
        return value

    def configured_time_input_kind(self, value: Any) -> str:
        """Classify a customer time without adding a second parsing authority."""
        _, reason = self._parse_configured_time(value)
        if reason is None:
            return "valid"
        if reason == "ambiguous_time":
            return "ambiguous"
        return "invalid" if _TIME_TOKEN_INPUT.fullmatch(str(value or "").strip()) else "not_time"

    def _configured_temporal_value(self, context: ExecutionContext, field_type: str) -> Any:
        step_data = context.session_data.state.get_task_step_data(self.get_module_name())
        for field in self.get_required_fields(context.config.config_data):
            if str(field.get("type") or "").lower() != field_type:
                continue
            key = str(field.get("key") or "")
            if key and step_data.get(key) not in (None, ""):
                return step_data.get(key)
        legacy_keys = ("preferred_date", "date") if field_type == "date" else ("preferred_time", "time")
        return next((step_data.get(key) for key in legacy_keys if step_data.get(key) not in (None, "")), None)
    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await booking_tool_handlers.execute_booking_tool(
            context,
            module=self,
            booking_service=booking_service,
            logger=logger,
        )
    def prefill_from_message(self, message: str, context: ExecutionContext) -> None:
        """Best-effort extraction for customers who provide all booking details at once."""
        step_data = context.session_data.state.step_data
        name_key = self._name_field_key(context)
        if context.user:
            user_phone = getattr(context.user, "phone_number", None)
            if user_phone and user_phone != "unknown" and not step_data.get("phone"):
                step_data["phone"] = user_phone

            user_name = getattr(context.user, "name", None)
            if user_name and user_name != "unknown" and name_key and not step_data.get(name_key):
                step_data[name_key] = user_name

        if not message:
            return
        lowered = message.lower()
        if not step_data.get("service_name"):
            service = self._resolve_service(message, context.config.config_data.get("services", []))
            if service:
                step_data["service_name"] = service.get("name")
        if not step_data.get("phone"):
            phone_match = re.search(r"(?:\+?\d[\d\s().-]{6,}\d)", message)
            if phone_match:
                step_data["phone"] = re.sub(r"[^\d+]", "", phone_match.group(0))
        if name_key and not step_data.get(name_key):
            name_match = re.search(
                r"\b(?:my name is|name is|i am|i'm)\s+([A-Za-z][A-Za-z .'-]{1,60})",
                message,
                flags=re.IGNORECASE,
            )
            if name_match:
                name = re.split(
                    r"\s*(?:,|\bphone\b|\bmobile\b|\bno allergies\b|\ballerg)",
                    name_match.group(1).strip(),
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                if name:
                    step_data[name_key] = name
        date_key = self._configured_temporal_key(context, "date")
        if date_key and not step_data.get(date_key):
            date_text = self._extract_date_text(lowered)
            if date_text:
                step_data[date_key] = self.normalize_configured_field_value(
                    {"type": "date"}, date_text, tenant_config=context.config.config_data
                )
        time_key = self._configured_temporal_key(context, "time")
        if time_key and not step_data.get(time_key):
            time_text = self._extract_time_text(message)
            if time_text:
                step_data[time_key] = self.normalize_configured_field_value(
                    {"type": "time"}, time_text, tenant_config=context.config.config_data
                )
        if "allergies" not in step_data:
            allergy_match = re.search(r"\b(no allergies|allerg(?:y|ies)[:\s]+[^,.]+)", message, flags=re.IGNORECASE)
            if allergy_match:
                step_data["allergies"] = allergy_match.group(1).strip()
    def _name_field_key(self, context: ExecutionContext) -> Optional[str]:
        for field in self.get_required_fields(context.config.config_data):
            key = str(field.get("key") or "")
            if key in {"patient_name", "customer_name", "name"} or "name" in key:
                return key
        return None
    @staticmethod
    def _resolve_service(message: str, services: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        normalized = message.lower()
        for service in services or []:
            service_id = str(service.get("id") or "").lower()
            name = str(service.get("name") or "").lower()
            if service_id and re.search(rf"(?<![a-z0-9]){re.escape(service_id)}(?![a-z0-9])", normalized):
                return service
            if name and name in normalized:
                return service
        names = [str(service.get("name") or "").lower() for service in services or []]
        matches = get_close_matches(normalized, names, n=1, cutoff=0.45)
        if matches:
            return (services or [])[names.index(matches[0])]
        return None
    @staticmethod
    def _extract_date_text(lowered: str) -> Optional[str]:
        for word in COMMON_DATE_WORDS:
            if word in lowered:
                return word
        iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", lowered)
        if iso_match:
            return iso_match.group(0)
        return None
    @staticmethod
    def _extract_time_text(message: str) -> Optional[str]:
        twenty_four_hour_match = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", message)
        if twenty_four_hour_match:
            return twenty_four_hour_match.group(0)
        time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", message, flags=re.IGNORECASE)
        if time_match:
            return time_match.group(0)
        for word in COMMON_TIME_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", message, flags=re.IGNORECASE):
                return word
        return None
    def _is_dayfirst(self, tenant_config: Dict[str, Any] | None) -> bool:
        """Determine if tenant locale uses day-first format (DD/MM/YYYY)."""
        if not tenant_config:
            return False
        date_format = tenant_config.get("date_format", "").lower()
        if "dd/mm" in date_format or "d/m" in date_format:
            return True
        if "mm/dd" in date_format:
            return False
        timezone_str = tenant_config.get("timezone", "UTC")
        us_timezones = {
            "us/eastern", "us/central", "us/mountain", "us/pacific", "us/alaska", "us/hawaii", "us/arizona",
            "america/new_york", "america/chicago", "america/denver", "america/los_angeles", "america/phoenix",
            "america/anchorage", "america/adak", "america/honolulu", "america/boise", "america/indiana/indianapolis",
            "america/kentucky/louisville", "america/north_dakota/center", "america/metlakatla", "america/sitka"
        }
        if timezone_str.lower() in us_timezones or timezone_str.lower().startswith("us/"):
            return False
        return True

    @staticmethod
    def _tenant_timezone(tenant_config: Dict[str, Any] | None):
        timezone_str = (tenant_config or {}).get("timezone", "UTC")
        try:
            return ZoneInfo(str(timezone_str or "UTC"))
        except Exception:
            try:
                from dateutil import tz
                tzinfo = tz.gettz(str(timezone_str or "UTC"))
                if tzinfo is not None:
                    return tzinfo
            except Exception:
                pass
            return timezone.utc

    def _tenant_today(self, tenant_config: Dict[str, Any] | None = None) -> date:
        return datetime.now(self._tenant_timezone(tenant_config)).date()

    def _coerce_booking_date(self, value: Any, tenant_config: Dict[str, Any] | None = None) -> date:
        """Convert collected date text into a date object for asyncpg."""
        tenant_today = self._tenant_today(tenant_config)
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        text = str(value or "").strip().lower()
        # Fuzzy-correct typos before coercion
        fuzzy_hits = get_close_matches(text, COMMON_DATE_WORDS, n=1, cutoff=0.55)
        if fuzzy_hits:
            text = fuzzy_hits[0]
        if not text or text == "today":
            return tenant_today
        if text == "yesterday":
            raise ValueError("Booking date cannot be in the past")
        if text == "tomorrow":
            return tenant_today + timedelta(days=1)
        # Handle day names — find the next occurrence of that day
        day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        if text in day_names:
            target_day = day_names.index(text)
            days_ahead = target_day - tenant_today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return tenant_today + timedelta(days=days_ahead)
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            try:
                from dateutil import parser
                dayfirst = self._is_dayfirst(tenant_config)
                parsed = parser.parse(text, fuzzy=True, dayfirst=dayfirst).date()
            except Exception:
                parsed = datetime.fromisoformat(text).date()
        if parsed < tenant_today:
            raise ValueError("Booking date cannot be in the past")
        return parsed

    def _coerce_booking_time(self, value: Any, tenant_config: Dict[str, Any] | None = None) -> Optional[time]:
        """Convert collected time text into a time object for asyncpg."""
        if isinstance(value, time):
            return value
        if isinstance(value, datetime):
            return value.time().replace(microsecond=0)
        parsed, _ = self._parse_configured_time(value)
        if parsed is not None:
            return parsed
        if value is None or value == "":
            return None
        raise ValueError("Invalid booking time")

    @staticmethod
    def _parse_configured_time(value: Any) -> tuple[Optional[time], Optional[str]]:
        """Parse the bounded customer time grammar used by all booking paths."""
        if value is None or str(value).strip() == "":
            return None, "invalid_format"
        if isinstance(value, time):
            return value.replace(microsecond=0), None
        if isinstance(value, datetime):
            return value.time().replace(microsecond=0), None
        text = str(value).strip().lower()
        time_of_day = {
            "morning": time(10, 0),
            "afternoon": time(14, 0),
            "evening": time(17, 0),
            "night": time(20, 0),
            "noon": time(12, 0),
        }
        if text in time_of_day:
            return time_of_day[text], None
        if _TWENTY_FOUR_HOUR_TIME.fullmatch(text):
            return time.fromisoformat(text), None
        meridiem_match = _TWELVE_HOUR_TIME.fullmatch(text)
        if meridiem_match:
            hour = int(meridiem_match.group("hour"))
            minute = int(meridiem_match.group("minute") or 0)
            if meridiem_match.group("meridiem").casefold() == "pm" and hour != 12:
                hour += 12
            elif meridiem_match.group("meridiem").casefold() == "am" and hour == 12:
                hour = 0
            return time(hour, minute), None
        ambiguous_match = _AMBIGUOUS_TIME.fullmatch(text)
        if ambiguous_match:
            return time(int(ambiguous_match.group("hour")), int(ambiguous_match.group("minute") or 0)), "ambiguous_time"
        return None, "invalid_format"

    @staticmethod
    def _time_text_has_explicit_meridiem(value: Any) -> bool:
        return bool(re.search(r"\b(?:am|pm)\b", str(value or ""), flags=re.IGNORECASE))

    @staticmethod
    def _time_text_is_ambiguous_hour(value: Any) -> bool:
        text = str(value or "").strip().lower()
        if not text or re.search(r"\b(?:am|pm)\b", text):
            return False
        return re.search(r"(?:^|\b(?:at|around|by)\s+)([1-9]|1[0-2])(?::[0-5]\d)?\b", text) is not None

    def _ambiguous_past_time_message(
        self,
        context: ExecutionContext,
        booking_date: date,
        booking_dt: datetime,
        now: datetime,
    ) -> Optional[str]:
        preferred_time_text = self._configured_temporal_value(context, "time")
        if (
            booking_dt < now
            and booking_date == now.date()
            and self._time_text_is_ambiguous_hour(preferred_time_text)
            and not self._time_text_has_explicit_meridiem(preferred_time_text)
        ):
            return "Please specify AM or PM for that time so I do not book the wrong slot."
        return None

    def _configured_temporal_key(self, context: ExecutionContext, field_type: str) -> Optional[str]:
        for field in self.get_required_fields(context.config.config_data):
            if str(field.get("type") or "").lower() == field_type and field.get("key"):
                return str(field["key"])
        return "preferred_date" if field_type == "date" else "preferred_time"

    def _validate_collected_time_availability(
        self,
        context: ExecutionContext,
        booking_date: date,
        booking_time: time,
    ) -> Optional[str]:
        """Reject an unavailable configured time before persisting the field."""
        step_data = context.session_data.state.get_task_step_data(self.get_module_name())
        selected_service = self._resolve_service(
            str(step_data.get("service_name") or ""),
            context.config.config_data.get("services") or [],
        )
        if not selected_service:
            return None
        return self._validate_working_hours(
            context.config.config_data.get("working_hours") or {},
            booking_date,
            booking_time,
            self._service_duration_minutes(selected_service),
        )

    def _validate_booking_rules(
        self,
        context: ExecutionContext,
        service_key: str,
        service_name: str,
        booking_date: date,
        booking_time: Optional[time],
        duration_mins: int,
        settings: Dict[str, Any],
    ) -> Optional[str]:
        if not booking_time:
            return "Please choose a specific appointment time."
        confirmation_mode = str(settings.get("confirmation_mode") or "auto_confirm").strip().lower()
        approval_conditions = {str(item).strip().lower() for item in settings.get("approval_required_for") or []}
        service_markers = {service_key.strip().lower(), service_name.strip().lower()}
        if confirmation_mode == "approval_required" or (approval_conditions & service_markers):
            return (
                "This appointment needs staff approval before it can be booked. "
                "I can collect the details, but a team member must confirm the slot."
            )
        tzinfo = self._tenant_timezone(context.config.config_data)
        now = datetime.now(tzinfo)
        booking_dt = datetime.combine(booking_date, booking_time)
        booking_dt = booking_dt.replace(tzinfo=tzinfo)
        ambiguous_past_message = self._ambiguous_past_time_message(context, booking_date, booking_dt, now)
        if ambiguous_past_message:
            return ambiguous_past_message
        if booking_dt < now:
            return "The requested time is in the past. Please choose a future date and time."
        minimum_notice = self._optional_int_setting(settings, "minimum_notice_minutes")
        if minimum_notice is not None and booking_dt < now + timedelta(minutes=minimum_notice):
            return f"Please choose a time at least {minimum_notice} minutes from now."
        advance_days = self._optional_int_setting(settings, "advance_booking_days")
        if advance_days is not None and booking_date > now.date() + timedelta(days=advance_days):
            return f"Please choose a date within the next {advance_days} days."
        working_hours_error = self._validate_working_hours(
            context.config.config_data.get("working_hours") or {},
            booking_date,
            booking_time,
            duration_mins,
        )
        if working_hours_error:
            return working_hours_error
        return None
    def _validate_working_hours(
        self,
        working_hours: Dict[str, Any],
        booking_date: date,
        booking_time: time,
        duration_mins: int,
    ) -> Optional[str]:
        day_name = booking_date.strftime("%A").lower()
        schedule = working_hours.get(day_name)
        if schedule is None:
            return None
        is_open = False
        open_text = "09:00"
        close_text = "17:00"
        if isinstance(schedule, dict):
            is_open = bool(schedule.get("is_open", False))
            open_text = str(schedule.get("open_time") or open_text)
            close_text = str(schedule.get("close_time") or close_text)
        else:
            text = str(schedule or "").strip().lower()
            if not text or text == "closed":
                is_open = False
            elif "-" in text:
                is_open = True
                open_text, close_text = [part.strip() for part in text.split("-", 1)]
            else:
                is_open = True
                open_text = text
        if not is_open:
            return "The business is closed on that day. Please choose another date."
        try:
            open_time = time.fromisoformat(open_text)
            close_time = time.fromisoformat(close_text)
        except ValueError:
            logger.warning("Invalid working-hours config for booking day %s", day_name)
            return None
        booking_start = datetime.combine(booking_date, booking_time)
        booking_end = booking_start + timedelta(minutes=duration_mins)
        day_open = datetime.combine(booking_date, open_time)
        day_close = datetime.combine(booking_date, close_time)
        if booking_start < day_open or booking_end > day_close:
            return "That time is outside business hours. Please choose an available time during working hours."
        return None
    @staticmethod
    def _service_duration_minutes(service: Dict[str, Any] | None) -> int:
        if not service:
            return 30
        try:
            duration = int(service.get("duration_mins") or service.get("duration_minutes") or 30)
        except (TypeError, ValueError):
            duration = 30
        return max(5, duration)
    @staticmethod
    def _optional_int_setting(settings: Dict[str, Any], key: str) -> Optional[int]:
        value = settings.get(key)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    def _int_setting(self, settings: Dict[str, Any], key: str, default: int) -> int:
        value = self._optional_int_setting(settings, key)
        return default if value is None else max(0, value)
    @staticmethod
    def _idempotency_key(
        context: ExecutionContext,
        service_name: str,
        booking_date: date,
        booking_time: Optional[time],
    ) -> str:
        payload = {
            "conversation_id": str(context.session_data.conversation_id),
            "service_name": service_name,
            "booking_date": booking_date.isoformat(),
            "booking_time": booking_time.isoformat() if booking_time else None,
            "step_data": context.session_data.state.step_data,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]
        return f"booking:{context.tenant.id}:{digest}"
booking_module = BookingModule()
