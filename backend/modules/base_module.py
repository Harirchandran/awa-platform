from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from backend.core.context import ExecutionContext
class ValidationResult:
    def __init__(self, is_valid: bool, error_message: Optional[str] = None):
        self.is_valid = is_valid
        self.error_message = error_message
@dataclass(frozen=True)
class ToolMetadata:
    """Backend-enforced safety metadata for LLM-callable tools."""
    risk_level: str = "sensitive_write"
    requires_confirmation: bool = True
    requires_verified_customer: bool = True
    supported_channels: tuple[str, ...] = ("awa_chat", "whatsapp")
    idempotency_scope: str = "conversation"
    allowed_when_paused: bool = False
class BaseModule(ABC):
    """
    Abstract Base Class for all product modules (Booking, Lead, etc.)
    Enforces consistent interface for the Workflow Engine.
    """
    @abstractmethod
    def get_module_name(self) -> str:
        """Returns the unique module identifier (e.g., 'booking')"""
        pass
    @abstractmethod
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Returns list of field definitions required by this module
        based on the tenant configuration.
        """
        pass
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns a JSON schema representing the arguments required to execute this tool.
        This schema is passed to the LLM for native tool calling.
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for field in self.get_required_fields(config):
            key = field.get("key")
            if not key:
                continue
            field_type = str(field.get("type") or "string").lower()
            if field_type == "number":
                schema_type = "number"
            elif field_type in {"integer", "int"}:
                schema_type = "integer"
            elif field_type == "boolean":
                schema_type = "boolean"
            else:
                schema_type = "string"
            properties[key] = {
                "type": schema_type,
                "description": field.get("label", key),
            }
            options = field.get("options")
            if isinstance(options, list) and options:
                properties[key]["enum"] = [str(option) for option in options]
            if field.get("required") and not field.get("applies_when"):
                required.append(key)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    def is_llm_tool(self) -> bool:
        """
        Whether this module should be exposed as a native LLM tool.
        Read-only modules that cannot return useful tool results should opt out.
        """
        return True
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        """Return backend-enforced safety metadata for this module's tool."""
        return ToolMetadata()
    @abstractmethod
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        """
        Validates a specific field value against its definition.
        """
        pass
    @abstractmethod
    async def execute(self, context: ExecutionContext) -> bool:
        """
        Final execution step (e.g., creating the booking in DB).
        Returns True if successful.
        """
        pass
    def validate_payload(self, context: ExecutionContext) -> Optional[str]:
        """
        Optional hook to validate the cross-field payload before asking for confirmation.
        Returns an error string if invalid, or None if valid.
        """
        return None