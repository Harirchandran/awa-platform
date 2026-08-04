from typing import Dict, Any, List
from backend.domains.support.tool_definitions import (
    get_human_handoff_required_fields,
    get_human_handoff_tool_metadata,
    get_human_handoff_tool_schema,
)
from backend.domains.support import tool_handlers as support_tool_handlers
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.support.service import support_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult
class HumanHandoffModule(BaseModule):
    def get_module_name(self) -> str:
        return "human_handoff"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_human_handoff_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_human_handoff_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_human_handoff_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        if value is None or str(value).strip() == "":
            return ValidationResult(False, "Value is empty")
        return ValidationResult(True)
    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await support_tool_handlers.execute_human_handoff_tool(
            context,
            support_service=support_service,
            logger=logger,
        )
human_handoff_module = HumanHandoffModule()
