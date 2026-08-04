from typing import Any

from backend.tools.registry import ToolExecutionResult


async def execute_lead_capture_tool(context: Any, lead_service: Any, logger: Any) -> ToolExecutionResult:
    """
    Persist lead data to both the new leads table and legacy customer metadata.
    Updates the customer record with collected lead information.
    """
    step_data = context.session_data.state.step_data

    name = step_data.get("name")
    if name:
        normalized_name = name.strip().lower()
        if normalized_name in {"valued patient", "valued customer", "unknown", "guest", "patient"}:
            name = None

    phone = step_data.get("phone")
    email = step_data.get("email")
    interest = step_data.get("interest")
    preferred_contact_time = step_data.get("preferred_contact_time")

    if not name and context.user.name:
        user_name_lower = context.user.name.strip().lower()
        if user_name_lower not in {"unknown", "valued patient", "valued customer", "guest", "patient"}:
            name = context.user.name

    if not phone and context.user.phone_number and context.user.phone_number.strip().lower() != "unknown":
        phone = context.user.phone_number

    try:
        lead = await lead_service.capture_lead(
            tenant_id=context.tenant.id,
            customer_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            lead_data={
                "name": name,
                "phone": phone,
                "email": email,
                "interest": interest,
                "preferred_contact_time": preferred_contact_time,
                "step_data": dict(step_data),
            },
        )
        lead_id = lead["id"]
        logger.info(
            "Lead capture persisted",
            extra={
                "domain": "leads",
                "action": "capture",
                "tenant_id": str(context.tenant.id),
                "record_id": str(lead_id),
                "outcome": "created" if lead.get("created") else "updated",
            },
        )

        return ToolExecutionResult(
            tool_key="lead_capture.execute",
            success=True,
            user_message="I've saved your details. A team member will reach out to you shortly.",
            created_resource_type="lead",
            created_resource_id=str(lead_id),
            data={
                "name": name,
                "interest": interest,
                "phone": phone,
                "email": email,
            },
        )
    except Exception as e:
        logger.error(f"Lead capture execution failed: {e}")
        return ToolExecutionResult(
            tool_key="lead_capture.execute",
            success=False,
            user_message="I couldn't save your request right now. Please try again later.",
        )
