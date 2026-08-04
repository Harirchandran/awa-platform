"""
Agentic Tools Array
Wraps existing AWA modules into LangChain/OpenAI @tool decorators.
"""
from typing import Dict, Any, Optional
from langchain_core.tools import tool
from backend.core.context import ExecutionContext
from backend.ops.logger import logger
from backend.modules.booking_module import booking_module
from backend.modules.lead_capture_module import lead_capture_module
# A global dictionary to hold the current context for tool injection
# (In a true LangChain setup, context is passed natively or via Runnable lambdas)
_current_context: Optional[ExecutionContext] = None
def set_tool_context(context: ExecutionContext):
    global _current_context
    _current_context = context
@tool
def get_available_services() -> str:
    """Returns a list of all services, wait times, and prices available for this business."""
    if not _current_context:
        return "Error: Context missing."
    services = _current_context.config.config_data.get("services", [])
    if not services:
        return "No services configured."
    return "\n".join([f"- {s.get('name')} (Price: {s.get('price')}, Duration: {s.get('duration_mins')} mins)" for s in services])
@tool
def create_booking(service_name: str, preferred_date: str, preferred_time: str) -> str:
    """
    Creates a firm booking in the calendar.
    Call this ONLY after the user has explicitly agreed to a date and time.
    """
    if not _current_context:
        return "Error: Context missing."
    # Inject synthetic step data to trick the existing deterministic module
    _current_context.session_data.state.step_data = {
        "service_id": service_name,
        "preferred_date": preferred_date,
        "preferred_time": preferred_time
    }
    # Run the existing side effect!
    import asyncio
    success = asyncio.run(booking_module.execute(_current_context))
    if success:
        return "Booking created successfully in the database!"
    else:
        return "Failed to create booking in the database. Please inform the user."
@tool
def capture_lead(name: str, interested_in: str, email: str = "Unknown", phone: str = "Unknown") -> str:
    """
    Saves the user's lead information into the CRM.
    """
    if not _current_context:
        return "Error: Context missing."
    _current_context.session_data.state.step_data = {
        "name": name,
        "interest": interested_in,
        "email": email,
        "phone": phone
    }
    import asyncio
    success = asyncio.run(lead_capture_module.execute(_current_context))
    if success:
        return "Lead successfully saved to metadata!"
    return "Failed to save lead."
# The array of tools to bind to the LLM
AGENT_TOOLS = [get_available_services, create_booking, capture_lead]