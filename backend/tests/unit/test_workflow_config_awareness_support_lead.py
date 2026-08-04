from __future__ import annotations

from uuid import uuid4

from backend.core.context import ConversationSession, ExecutionContext, PendingAction, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.leads.tool_definitions import get_lead_capture_tool_schema
from backend.logic.engine.core import _set_runtime_available_modules, _unavailable_active_workflow_response
from backend.modules.lead_capture_module import lead_capture_module
from backend.modules.support_module import support_module
from backend.tools.registry import ToolRegistry


def _config():
    return {
        "escalation_message": "A team member can continue from here.",
        "modules": {
            "lead_capture": {
                "enabled": True,
                "fields": [
                    {"key": "name", "label": "Name", "type": "string", "required": True},
                    {"key": "interest", "label": "Interest", "type": "string", "required": True},
                    {"key": "company", "label": "Company", "type": "string", "required": True},
                    {"key": "lead_type", "label": "Lead type", "type": "select", "required": False, "options": ["demo", "quote"]},
                ],
            },
            "support": {
                "enabled": True,
                "fields": [
                    {"key": "issue_description", "label": "Issue", "type": "string", "required": True},
                    {"key": "urgency", "label": "Urgency", "type": "select", "required": True, "options": ["normal", "urgent"]},
                    {"key": "category", "label": "Category", "type": "select", "required": False, "options": ["billing", "technical"]},
                ],
            },
            "human_handoff": {"enabled": True, "fields": [{"key": "reason", "label": "Reason", "type": "string", "required": True}]},
        },
    }


def _context(config=None):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Support Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(workflow_state=ConversationState.EXECUTING),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config or _config()),
        incoming_message={"text": "help", "channel_type": "awa_chat"},
    )


def _registry_with(module):
    registry = ToolRegistry()
    registry.register_module(module)
    return registry


def test_lead_schema_uses_configured_required_fields_and_options():
    schema = get_lead_capture_tool_schema(_config())

    assert schema["required"] == ["name", "interest", "company"]
    assert schema["properties"]["lead_type"]["enum"] == ["demo", "quote"]


def test_lead_required_fields_from_config_are_enforced_by_registry_schema_guard():
    registry = _registry_with(lead_capture_module)
    tool = registry.get_tool("lead_capture.execute")
    ctx = _context()

    denial = registry._schema_denial(
        tool,
        ctx,
        {"name": "Asha", "interest": "Consultation"},
    )

    assert denial is not None
    assert denial.validation_errors["company"] == "This required field is missing."


def test_support_configured_urgency_and_category_options_are_backend_validated():
    registry = _registry_with(support_module)
    tool = registry.get_tool("support.execute")
    ctx = _context()

    denial = registry._schema_denial(
        tool,
        ctx,
        {"issue_description": "Need help with billing issue", "urgency": "critical", "category": "sales"},
    )

    assert denial is not None
    assert denial.validation_errors["urgency"] == "Value is not one of the supported options."
    assert denial.validation_errors["category"] == "Value is not one of the supported options."
    assert support_module.validate_input(
        {"key": "urgency", "type": "string", "options": ["normal", "urgent"]},
        "urgent",
        _config(),
    ).is_valid


def test_stale_lead_support_or_handoff_pending_action_after_disable_does_not_execute():
    for tool_key, available in [
        ("lead_capture.execute", {"support"}),
        ("support.execute", {"lead_capture"}),
        ("human_handoff.execute", {"faq"}),
    ]:
        ctx = _context()
        ctx.session_data.state.action_ledger.pending_action = PendingAction(
            tool_key=tool_key,
            args={"name": "Asha", "issue_description": "Need help", "reason": "Need a person"},
            summary="Please review this request.",
            requires_confirmation=True,
        )
        _set_runtime_available_modules(ctx, available)

        result = _unavailable_active_workflow_response(ctx, available)

        assert result is not None
        assert "currently unavailable" in result.response_text
        assert "Confirm" not in result.quick_replies
        assert ctx.session_data.state.action_ledger.pending_action is None
