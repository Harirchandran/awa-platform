from typing import Any, Dict, Optional

from backend.tools.registry import ToolExecutionResult


async def execute_payments_tool(
    context: Any,
    payment_service: Any,
    create_razorpay_payment_link: Any,
    logger: Any,
) -> ToolExecutionResult:
    step_data = context.session_data.state.step_data

    FORBIDDEN_FIELDS = {
        "provider", "status", "payment_status", "paid", "success", "captured",
        "failed", "refunded", "payment_link_url", "provider_payment_id",
        "provider_order_id", "transaction_id", "signature", "webhook_payload",
        "refund_id", "settlement_id", "order_id", "booking_id", "invoice_id",
        "tenant_id", "user_id", "customer_id", "payment_id", "payment_type", "type"
    }

    for field in FORBIDDEN_FIELDS:
        if step_data.get(field) not in (None, ""):
            logger.warning(f"Rejecting payment tool call: forbidden field '{field}' supplied directly in tool args.")
            return ToolExecutionResult(
                tool_key="payments.execute",
                success=False,
                user_message="I cannot complete the payment request due to invalid or restricted fields.",
                validation_errors={field: "This field is restricted and cannot be supplied directly."},
            )

    amount = _parse_amount(step_data.get("amount"))
    if amount is None and not step_data.get("order_id"):
        logger.warning("Payment request skipped because amount/order_id is missing")
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            user_message="I need a valid payment amount before creating the payment request.",
            validation_errors={"amount": "A positive amount is required unless an order is linked."},
        )
    currency = str(step_data.get("currency") or "INR").upper()
    payment_type = "custom_payment"
    config_data = getattr(getattr(context, "config", None), "config_data", {}) or {}
    module_config = config_data.get("modules", {}).get("payments", {}) or {}
    provider = str(module_config.get("provider") or "manual").lower()
    reference = step_data.get("reference") or "conversation"
    metadata = {
        "source": "concierge_tool",
        "reference": str(reference),
        "customer_payment_collection": True,
    }
    if step_data.get("description"):
        metadata["description"] = str(step_data["description"])

    if provider == "razorpay":
        if amount is None:
            return ToolExecutionResult(
                tool_key="payments.execute",
                success=False,
                user_message="I need a valid amount before creating a Razorpay payment link.",
                validation_errors={"amount": "A positive amount is required for Razorpay payment links."},
            )
        from backend.core.config import settings
        if not module_config.get("use_platform_razorpay"):
            return ToolExecutionResult(
                tool_key="payments.execute",
                success=False,
                user_message=(
                    "Razorpay customer payment links are not enabled for this business. "
                    "A team member needs to configure customer payment collection before I can create a link."
                ),
                validation_errors={"provider": "Razorpay customer payment links are not enabled for this business."},
            )
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return ToolExecutionResult(
                tool_key="payments.execute",
                success=False,
                user_message=(
                    "Razorpay customer payment links are not configured right now. "
                    "Please use manual payment follow-up or ask the team to configure payment credentials."
                ),
                validation_errors={"provider": "Razorpay customer payment links are not configured right now."},
            )

    if provider not in {"manual", "external", "razorpay", "upi", "cash", "bank_transfer"}:
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            user_message="That payment provider is not supported for customer payment collection.",
            validation_errors={"provider": "Unsupported customer payment provider."},
        )

    if provider == "manual":
        metadata["manual_payment_required"] = True

    incoming_msg = getattr(context, "incoming_message", None)
    seq_no = 0
    if incoming_msg:
        if isinstance(incoming_msg, dict):
            seq_no = incoming_msg.get("conversation_sequence_number", 0)
        else:
            seq_no = getattr(incoming_msg, "conversation_sequence_number", 0)
    idempotency_key = f"concierge:{context.session_data.conversation_id}:seq:{seq_no}:payment:{reference}:{amount}"

    # 1. Phase 1: Local DB Reservation
    try:
        payment = await payment_service.create_payment_request(
            tenant_id=context.tenant.id,
            amount=amount,
            currency=currency,
            payment_type=payment_type,
            provider=provider,
            status="draft",
            customer_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            payment_url=None,
            provider_payment_link_id=None,
            provider_order_id=None,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        logger.error(
            "Failed to reserve draft payment request for conversation %s: %s",
            context.session_data.conversation_id,
            exc,
        )
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            user_message="I couldn't create the payment request right now. Please try again shortly.",
        )

    if not payment:
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            user_message="I couldn't create the payment request with those details.",
        )

    # Check: If the payment was already sent or paid, return early (idempotency key claimed and finalized)
    if payment.get("status") in {"sent", "paid"}:
        logger.info("Payment request %s already processed with status %s", payment.get("id"), payment.get("status"))
        user_message = "I've already created a payment link for this request." if provider == "razorpay" else "I've already recorded this payment request."
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=True,
            user_message=user_message,
            created_resource_type="payment_request",
            created_resource_id=str(payment.get("id")),
            data={
                "amount": amount,
                "currency": currency,
                "status": payment.get("status"),
                "provider": payment.get("provider"),
                "payment_url": payment.get("payment_url"),
            },
        )

    # 2. Phase 2: Call External Provider API
    if provider == "razorpay":
        try:
            razorpay_result = await create_razorpay_payment_link(
                context=context,
                amount=amount,
                currency=currency,
                reference=str(reference),
                description=str(step_data.get("description") or f"{payment_type} payment"),
                module_config=module_config,
            )
            if razorpay_result.get("blocked"):
                # Mark payment as failed locally
                await payment_service.update_payment_request_provider_details(
                    tenant_id=context.tenant.id,
                    payment_id=payment["id"],
                    status="failed",
                )
                return ToolExecutionResult(
                    tool_key="payments.execute",
                    success=False,
                    user_message=razorpay_result["message"],
                    validation_errors={"provider": razorpay_result["message"]},
                )
            payment_url = razorpay_result.get("payment_url")
            provider_payment_link_id = razorpay_result.get("provider_payment_link_id")
            provider_order_id = razorpay_result.get("provider_order_id")
            if not payment_url:
                # Mark payment as failed locally
                await payment_service.update_payment_request_provider_details(
                    tenant_id=context.tenant.id,
                    payment_id=payment["id"],
                    status="failed",
                )
                return ToolExecutionResult(
                    tool_key="payments.execute",
                    success=False,
                    user_message="Razorpay did not return a valid payment URL. Please try again later.",
                )
        except Exception as exc:
            logger.error("Razorpay link creation encountered exception for payment %s: %s", payment["id"], exc)
            try:
                await payment_service.update_payment_request_provider_details(
                    tenant_id=context.tenant.id,
                    payment_id=payment["id"],
                    status="failed",
                )
            except Exception as db_exc:
                logger.error("Failed to mark payment %s as failed after Razorpay API exception: %s", payment["id"], db_exc)
            return ToolExecutionResult(
                tool_key="payments.execute",
                success=False,
                user_message="I couldn't generate the Razorpay payment link. Please try again shortly.",
            )

        # 3. Phase 3: Finalize DB Record (Success or DB Failure post-Razorpay)
        metadata["provider_scope"] = "platform_razorpay"
        try:
            payment = await payment_service.update_payment_request_provider_details(
                tenant_id=context.tenant.id,
                payment_id=payment["id"],
                status="sent",
                payment_url=payment_url,
                provider_payment_link_id=provider_payment_link_id,
                provider_order_id=provider_order_id,
                metadata=metadata,
            )
            if not payment:
                raise RuntimeError("Post-provider DB update returned None")
        except Exception as exc:
            logger.critical(
                "CRITICAL: Failed to finalize payment request %s in DB after successful Razorpay link creation! "
                "Provider Link ID: %s, Payment URL: %s. Exception: %s",
                payment["id"],
                provider_payment_link_id,
                payment_url,
                exc,
            )
            return ToolExecutionResult(
                tool_key="payments.execute",
                success=False,
                user_message="The payment link was generated but could not be finalized locally. Please contact support.",
            )

    logger.info("Payment request %s finalized for tenant %s", payment.get("id"), context.tenant.id)
    if provider == "razorpay" and payment.get("payment_url"):
        user_message = "I've created a payment link for this request."
    elif provider == "manual":
        user_message = "I've recorded a manual payment request. The business team will share collection instructions or confirm payment manually."
    else:
        user_message = "I've recorded the payment request with the selected payment provider."
    return ToolExecutionResult(
        tool_key="payments.execute",
        success=True,
        user_message=user_message,
        created_resource_type="payment_request",
        created_resource_id=str(payment.get("id")),
        data={
            "amount": amount,
            "currency": currency,
            "status": payment.get("status"),
            "provider": payment.get("provider"),
            "payment_url": payment.get("payment_url"),
        },
    )


def _parse_amount(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None
