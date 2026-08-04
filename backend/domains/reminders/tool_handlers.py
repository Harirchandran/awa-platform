from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.domains.reminders.tool_definitions import (
    ALLOWED_TRIGGER_TYPES,
    get_concierge_reminder_channels,
)
from backend.tools.registry import ToolExecutionResult


async def execute_reminders_tool(context: Any, reminder_service: Any, logger: Any) -> ToolExecutionResult:
    step_data = context.session_data.state.step_data
    timezone_str = context.config.config_data.get("timezone") or "UTC"
    scheduled_for = _resolve_scheduled_for(step_data, timezone_str)
    message_text = (
        step_data.get("message_text")
        or step_data.get("message")
        or step_data.get("note")
    )
    if not scheduled_for or not message_text:
        logger.warning("Reminder skipped because scheduled time or message text is missing")
        errors = {}
        if not scheduled_for:
            errors["scheduled_for"] = "A valid future schedule time or positive time_offset is required."
        if not message_text:
            errors["message_text"] = "Reminder message text is required."
        return ToolExecutionResult(
            tool_key="reminders.execute",
            success=False,
            user_message="I need both the reminder time and message before scheduling it.",
            validation_errors=errors,
        )
    if scheduled_for <= datetime.now(timezone.utc):
        return ToolExecutionResult(
            tool_key="reminders.execute",
            success=False,
            user_message="Please choose a reminder time in the future.",
            validation_errors={"scheduled_for": "Reminder time must be in the future."},
        )
    reminder_type = _normalize_trigger_type(step_data.get("reminder_type"))
    channel = str(step_data.get("channel") or "").strip().lower()
    channel_error = validate_concierge_reminder_channel(channel, context)
    if channel_error:
        return ToolExecutionResult(
            tool_key="reminders.execute",
            success=False,
            user_message=channel_error,
            validation_errors={"channel": channel_error},
        )
    reference = step_data.get("reference") or reminder_type
    try:
        reminder = await reminder_service.create_scheduled_message(
            tenant_id=context.tenant.id,
            customer_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            scheduled_for=scheduled_for,
            message_text=str(message_text),
            trigger_type=reminder_type,
            channel=channel,
            metadata={
                "source": "concierge_tool",
                "reference": str(reference),
            },
            idempotency_key=f"concierge:{context.session_data.conversation_id}:reminder:{reference}:{scheduled_for.isoformat()}",
        )
    except Exception as exc:
        logger.error(
            "Failed to persist reminder for conversation %s: %s",
            context.session_data.conversation_id,
            exc,
        )
        return ToolExecutionResult(
            tool_key="reminders.execute",
            success=False,
            user_message="I couldn't schedule the reminder right now. Please try again shortly.",
        )
    if not reminder:
        return ToolExecutionResult(
            tool_key="reminders.execute",
            success=False,
            user_message="I couldn't schedule the reminder with those details.",
        )
    logger.info("Reminder %s scheduled for tenant %s", reminder.get("id"), context.tenant.id)
    return ToolExecutionResult(
        tool_key="reminders.execute",
        success=True,
        user_message="I've scheduled the reminder.",
        created_resource_type="scheduled_message",
        created_resource_id=str(reminder.get("id")),
        data={
            "scheduled_for": scheduled_for.isoformat(),
            "channel": channel,
            "status": reminder.get("status"),
        },
    )


def _resolve_scheduled_for(step_data: Dict[str, Any], timezone_str: str = "UTC") -> Optional[datetime]:
    import zoneinfo

    def tenant_tz():
        try:
            return zoneinfo.ZoneInfo(timezone_str)
        except Exception:
            try:
                from dateutil import tz
                tzinfo = tz.gettz(str(timezone_str or "UTC"))
                if tzinfo is not None:
                    return tzinfo
            except Exception:
                pass
            return timezone.utc

    scheduled_for = step_data.get("scheduled_for") or step_data.get("schedule_at")
    if isinstance(scheduled_for, datetime):
        if scheduled_for.tzinfo:
            return scheduled_for.astimezone(timezone.utc)
        return scheduled_for.replace(tzinfo=tenant_tz()).astimezone(timezone.utc)
    if scheduled_for:
        try:
            val_str = str(scheduled_for).strip()
            has_tz = False
            if val_str.endswith("Z") or "+" in val_str or (len(val_str) > 10 and "-" in val_str[10:]):
                has_tz = True

            parsed = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
            if parsed.tzinfo and has_tz:
                return parsed.astimezone(timezone.utc)
            return parsed.replace(tzinfo=tenant_tz()).astimezone(timezone.utc)
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


def _normalize_trigger_type(value: Any) -> str:
    normalized = str(value or "manual").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in ALLOWED_TRIGGER_TYPES:
        return normalized
    return "custom"


def validate_concierge_reminder_channel(channel: str, context: Any) -> Optional[str]:
    normalized = channel.strip().lower()
    available_channels = get_concierge_reminder_channels(context.config.config_data)
    if not normalized:
        choices = " or ".join(
            "manual staff follow-up" if item == "manual" else item.title()
            for item in available_channels
        )
        return (
            f"Please choose a reminder delivery channel: {choices}."
            if choices
            else "This business does not have a deliverable reminder channel configured."
        )
    if normalized == "awa_chat":
        return (
            "AWA Chat cannot deliver scheduled reminders. "
            "Please use WhatsApp or manual staff follow-up."
        )
    if normalized in {"sms", "email"}:
        return (
            "This reminder channel is not connected for automatic delivery yet. "
            "Please use WhatsApp or manual staff follow-up."
        )
    if normalized not in available_channels:
        return "That reminder channel is not supported for this business."
    if normalized == "whatsapp" and not getattr(context.user, "phone_number", None):
        return "WhatsApp reminders need a customer phone number."
    return None
