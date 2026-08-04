"""
Support Module — Collects issue details and escalates to human support.
Logic-first architecture: deterministic field collection, AI-assisted only.
"""
from typing import Dict, Any, List, Optional
from backend.domains.support.tool_definitions import (
    get_support_required_fields,
    get_support_tool_metadata,
    get_support_tool_schema,
)
from backend.domains.support import tool_handlers as support_tool_handlers
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.support.service import support_service
from backend.ops.logger import logger
class SupportModule(BaseModule):
    def get_module_name(self) -> str:
        return "support"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_support_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_support_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_support_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        field_type = field_def.get("type", "string")
        if value is None or str(value).strip() == "":
            if field_def.get("required", True):
                return ValidationResult(False, "Value is empty")
            return ValidationResult(True)
        if field_type == "string":
            configured_options = field_def.get("options")
            if isinstance(configured_options, list) and configured_options:
                allowed = {str(option).lower().strip() for option in configured_options}
                if str(value).lower().strip() not in allowed:
                    return ValidationResult(False, "Please choose one of the configured options")
            elif field_def.get("key") == "urgency":
                if str(value).lower().strip() not in ("low", "medium", "high"):
                    return ValidationResult(False, "Please specify: low, medium, or high")
            elif len(str(value).strip()) < 5:
                return ValidationResult(False, "Please provide more detail")
            return ValidationResult(True)
        return ValidationResult(True)
    async def execute(self, context: ExecutionContext) -> bool:
        return await support_tool_handlers.execute_support_tool(
            context,
            support_service=support_service,
            logger=logger,
        )
support_module = SupportModule()
