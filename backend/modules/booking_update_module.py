from typing import Dict, Any, List, Optional
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.bookings.tool_definitions import (
    get_booking_update_tool_metadata,
    get_booking_update_tool_schema,
)
from backend.domains.bookings import tool_handlers as booking_tool_handlers
from backend.domains.bookings.service import booking_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult

class BookingUpdateModule(BaseModule):
    def get_module_name(self) -> str:
        return "booking.update"

    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_booking_update_tool_metadata(config)

    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"key": "booking_id", "label": "Booking ID", "type": "string", "required": True},
            {"key": "action", "label": "Action", "type": "string", "required": True},
            {"key": "new_date", "label": "New Date", "type": "string", "required": False},
            {"key": "new_time", "label": "New Time", "type": "string", "required": False},
        ]

    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_booking_update_tool_schema(config)

    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        if field_def.get("required") and (value is None or value == ""):
            return ValidationResult(False, f"{field_def.get('label', 'Field')} is required")
        return ValidationResult(True)

    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await booking_tool_handlers.execute_booking_update_tool(
            context,
            module=self,
            booking_service=booking_service,
            logger=logger,
        )

booking_update_module = BookingUpdateModule()
