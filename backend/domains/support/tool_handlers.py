from typing import Any

from backend.core.states import ConversationState
from backend.tools.registry import ToolExecutionResult


async def execute_support_tool(context: Any, support_service: Any, logger: Any) -> bool:
    """
    Log support ticket and escalate the conversation.
    Writes to audit log for tracking.
    """
    step_data = context.session_data.state.step_data
    try:
        config_data = getattr(getattr(context, "config", None), "config_data", {}) or {}
        incoming_message = getattr(context, "incoming_message", None) or {}
        success = await support_service.create_support_request(
            tenant_id=context.tenant.id,
            actor_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            customer_id=context.user.id,
            issue_description=step_data.get("issue_description", "Support request"),
            urgency=step_data.get("urgency", "medium"),
            customer_contact_present=bool(context.user.phone_number),
            config=config_data,
            source_channel=incoming_message.get("channel_type") or "public_chat",
        )
        if success:
            context.session_data.state.workflow_state = ConversationState.ESCALATED
            context.session_data.state.last_escalation_reason = step_data.get("issue_description", "Support request")
        return success
    except Exception as e:
        logger.error(f"Support module execution failed: {e}")
        return False


async def execute_human_handoff_tool(context: Any, support_service: Any, logger: Any) -> ToolExecutionResult:
    reason = (
        context.session_data.state.step_data.get("reason")
        or context.session_data.state.step_data.get("handoff_reason")
        or "Customer requested a human handoff"
    )
    try:
        config_data = getattr(getattr(context, "config", None), "config_data", {}) or {}
        incoming_message = getattr(context, "incoming_message", None) or {}
        escalation, route = await support_service.create_handoff(
            tenant_id=context.tenant.id,
            conversation_id=context.session_data.conversation_id,
            customer_id=context.user.id,
            actor_id=context.user.id,
            reason=str(reason),
            config=config_data,
            source="human_handoff_module",
            source_channel=incoming_message.get("channel_type") or "public_chat",
            urgency="high",
            resume_mode="customer",
        )
    except Exception as exc:
        logger.error(
            "Failed to persist human handoff for conversation %s: %s",
            context.session_data.conversation_id,
            exc,
        )
        return ToolExecutionResult(
            tool_key="human_handoff.execute",
            success=False,
            user_message="I couldn't connect you to the team right now. Please try again shortly.",
        )
    if not escalation:
        return ToolExecutionResult(
            tool_key="human_handoff.execute",
            success=False,
            user_message="I couldn't connect you to the team right now. Please try again shortly.",
        )
    context.session_data.state.workflow_state = ConversationState.ESCALATED
    context.session_data.state.last_escalation_reason = str(reason)
    logger.info(
        "Human handoff persisted for customer %s in tenant %s",
        context.user.id,
        context.tenant.id,
    )
    return ToolExecutionResult(
        tool_key="human_handoff.execute",
        success=True,
        user_message="I've asked the team to step in. A person can continue from here.",
        created_resource_type="conversation_escalation",
        created_resource_id=str(context.session_data.conversation_id),
        data={"reason": str(reason), "status": escalation.get("status"), "routing": route},
    )
