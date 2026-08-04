from typing import Dict, Any, List
from backend.domains.knowledge.tool_definitions import (
    get_faq_required_fields,
    get_faq_tool_schema,
    is_faq_llm_tool,
)
from backend.domains.knowledge import tool_handlers as knowledge_tool_handlers
from backend.modules.base_module import BaseModule, ValidationResult
from backend.core.context import ExecutionContext
from backend.ops.logger import logger
class FaqModule(BaseModule):
    def get_module_name(self) -> str:
        return "faq"
    def is_llm_tool(self) -> bool:
        return is_faq_llm_tool()
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_faq_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_faq_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        if value is None or str(value).strip() == "":
            return ValidationResult(False, "Value is empty")
        return ValidationResult(True)
    async def execute(self, context: ExecutionContext) -> bool:
        return await knowledge_tool_handlers.execute_faq_tool(context, logger=logger)
faq_module = FaqModule()
