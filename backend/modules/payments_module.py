from typing import Dict, Any, List, Optional
from backend.domains.payments.tool_definitions import (
    get_payments_required_fields,
    get_payments_tool_metadata,
    get_payments_tool_schema,
)
from backend.domains.payments import tool_handlers as payments_tool_handlers
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.core.context import ExecutionContext
from backend.core.config import settings
from backend.domains.payments.service import payment_service
from backend.ops.logger import logger
from backend.tools.registry import ToolExecutionResult
class PaymentsModule(BaseModule):
    def get_module_name(self) -> str:
        return "payments"
    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return get_payments_tool_metadata(config)
    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return get_payments_required_fields(config)
    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return get_payments_tool_schema(config)
    def validate_input(self, field_def: Dict[str, Any], value: Any, tenant_config: Dict[str, Any] | None = None) -> ValidationResult:
        if value is None or str(value).strip() == "":
            return ValidationResult(False, "Value is empty")
        return ValidationResult(True)
    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        return await payments_tool_handlers.execute_payments_tool(
            context,
            payment_service=payment_service,
            create_razorpay_payment_link=self._create_razorpay_payment_link,
            logger=logger,
        )
    def _parse_amount(self, value: Any) -> Optional[float]:
        if value is None or str(value).strip() == "":
            return None
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        return amount if amount > 0 else None
    async def _create_razorpay_payment_link(
        self,
        context: ExecutionContext,
        amount: float,
        currency: str,
        reference: str,
        description: str,
        module_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not module_config.get("use_platform_razorpay"):
            return {
                "blocked": True,
                "message": (
                    "Razorpay customer payment links are not enabled for this business. "
                    "A team member needs to configure customer payment collection before I can create a link."
                ),
            }
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return {
                "blocked": True,
                "message": (
                    "Razorpay customer payment links are not configured right now. "
                    "Please use manual payment follow-up or ask the team to configure payment credentials."
                ),
            }
        try:
            import razorpay
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            payload = {
                "amount": int(round(float(amount) * 100)),
                "currency": currency,
                "description": description[:250],
                "reference_id": reference[:40],
                "customer": {
                    "name": getattr(context.user, "name", None) or "",
                    "contact": getattr(context.user, "phone_number", None) or "",
                },
                "notify": {"sms": False, "email": False},
                "notes": {
                    "tenant_id": str(context.tenant.id),
                    "conversation_id": str(context.session_data.conversation_id),
                    "customer_id": str(context.user.id),
                    "scope": "business_customer_payment",
                },
            }
            import asyncio
            link = await asyncio.to_thread(client.payment_link.create, payload)
        except Exception as exc:
            logger.error("Razorpay customer payment link creation failed for tenant %s: %s", context.tenant.id, exc)
            return {
                "blocked": True,
                "message": "I couldn't create the Razorpay payment link right now. Please use manual follow-up or try again later.",
            }
        payment_url = link.get("short_url") or link.get("url")
        if not payment_url:
            return {
                "blocked": True,
                "message": "Razorpay did not return a payment URL. Please use manual follow-up or try again later.",
            }
        return {
            "blocked": False,
            "payment_url": payment_url,
            "provider_payment_link_id": link.get("id"),
            "provider_order_id": link.get("order_id"),
        }
payments_module = PaymentsModule()
