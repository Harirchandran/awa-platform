from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from backend.domains.reminders.tool_definitions import (
    ALLOWED_TRIGGER_TYPES as DOMAIN_ALLOWED_TRIGGER_TYPES,
    get_reminders_required_fields,
    get_reminders_tool_metadata,
    get_reminders_tool_schema,
)
from backend.domains.reminders import tool_handlers as reminders_tool_handlers
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.reminders.service import reminder_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult
class RemindersModule(BaseModule):
    ALLOWED_TRIGGER_TYPES = DOMAIN_ALLOWED_TRIGGER_TYPES
    def get_module_name(self) -> str:
        return "reminders"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_reminders_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_reminders_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_reminders_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        if value is None or str(value).strip() == "":
            return ValidationResult(False, "Value is empty")
        key = str(field_def.get("key") or "")
        field_type = str(field_def.get("type") or "string").lower()
        if key in {"scheduled_for", "schedule_at"}:
            try:
                datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            except ValueError:
                return ValidationResult(False, "Please provide a valid schedule time")
        if field_type == "phone":
            normalized = "".join(character for character in str(value) if character.isdigit() or character == "+")
            if normalized.startswith("+"):
                normalized = normalized[1:]
            if not normalized.isdigit() or not 7 <= len(normalized) <= 15:
                return ValidationResult(False, "Please provide a valid phone number")
        if field_type == "email":
            normalized = str(value).strip()
            local, separator, domain = normalized.partition("@")
            if not separator or not local or "." not in domain or any(character.isspace() for character in normalized):
                return ValidationResult(False, "Please provide a valid email address")
        return ValidationResult(True)
    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await reminders_tool_handlers.execute_reminders_tool(
            context,
            reminder_service=reminder_service,
            logger=logger,
        )
    def _resolve_scheduled_for(self, step_data: Dict[str, Any], timezone_str: str = "UTC") -> Optional[datetime]:
        import zoneinfo
        scheduled_for = step_data.get("scheduled_for") or step_data.get("schedule_at")
        if isinstance(scheduled_for, datetime):
            if scheduled_for.tzinfo:
                return scheduled_for.astimezone(timezone.utc)
            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                tz = timezone.utc
            return scheduled_for.replace(tzinfo=tz).astimezone(timezone.utc)
        if scheduled_for:
            try:
                val_str = str(scheduled_for).strip()
                has_tz = False
                if val_str.endswith("Z") or "+" in val_str or (len(val_str) > 10 and "-" in val_str[10:]):
                    has_tz = True

                parsed = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
                if parsed.tzinfo and has_tz:
                    return parsed.astimezone(timezone.utc)
                try:
                    tz = zoneinfo.ZoneInfo(timezone_str)
                except Exception:
                    tz = timezone.utc
                return parsed.replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                return None
        time_offset = step_data.get("time_offset")
        if time_offset is None or str(time_offset).strip() == "":
            return None
        try:
            minutes = int(time_offset)
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        return datetime.now(timezone.utc) + timedelta(minutes=minutes)
    def _normalize_trigger_type(self, value: Any) -> str:
        normalized = str(value or "manual").strip().lower().replace(" ", "_").replace("-", "_")
        if normalized in self.ALLOWED_TRIGGER_TYPES:
            return normalized
        return "custom"
    def _validate_channel_for_concierge(self, channel: str, context: ExecutionContext) -> Optional[str]:
        return reminders_tool_handlers.validate_concierge_reminder_channel(channel, context)
reminders_module = RemindersModule()
