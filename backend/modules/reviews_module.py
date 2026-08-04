from typing import Dict, Any, List, Optional
from backend.domains.reviews import tool_handlers as reviews_tool_handlers
from backend.domains.reviews.tool_definitions import (
    get_reviews_required_fields,
    get_reviews_tool_metadata,
    get_reviews_tool_schema,
)
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.domains.reviews.service import review_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult
class ReviewsModule(BaseModule):
    def get_module_name(self) -> str:
        return "reviews"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_reviews_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_reviews_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_reviews_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        if value is None or str(value).strip() == "":
            return ValidationResult(False, "Value is empty")
        key = str(field_def.get("key") or "")
        field_type = str(field_def.get("type") or "string").lower()
        if key == "rating":
            if self._parse_rating(value) is None:
                return ValidationResult(False, "Please provide a rating from 1 to 5")
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
        return await reviews_tool_handlers.execute_reviews_tool(
            context,
            review_service=review_service,
            logger=logger,
        )
    def _parse_rating(self, value: Any) -> Optional[int]:
        if value is None or str(value).strip() == "":
            return None
        try:
            rating = int(value)
        except (TypeError, ValueError):
            return None
        return rating if 1 <= rating <= 5 else None
    def _review_user_message(self, review: Dict[str, Any], rating: Optional[int]) -> str:
        status = review.get("status")
        public_url = review.get("public_review_url")
        if status == "public_review_requested" and public_url:
            return (
                "Thanks for the positive feedback. If you're comfortable sharing it publicly, "
                f"please use this review link: {public_url}"
            )
        if status == "negative_feedback_escalated":
            return "Thanks for telling us. I've shared this feedback with the team so they can follow up."
        if rating:
            return "Thanks, I've saved your review."
        return "I've created the review request."
reviews_module = ReviewsModule()
