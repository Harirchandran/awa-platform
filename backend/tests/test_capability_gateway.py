import pytest
from uuid import uuid4
from backend.capabilities.gateway import CapabilityGateway
from backend.capabilities.models import CapabilityRequest, CapabilityDecision
from backend.core.context import ExecutionContext, Tenant, User, ConversationSession, TenantConfig, SessionState
from backend.core.states import ConversationState
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.tools.registry import ToolDefinition
from backend.billing.usage_guard import UsageGuard, UsageLimitExceeded

pytestmark = pytest.mark.anyio

class FakeUsageDb:
    async def get_tenant_usage_summary(self, tenant_id):
        return {}
    async def get_subscription_plan_by_code(self, plan_code):
        return {"plan_code": plan_code, "limits_json": {"allowed_modules": ["booking"], "allowed_ai_modes": ["standard"]}}
    async def upsert_cost_daily(self, **kwargs):
        return True

class FakeModule(BaseModule):
    def __init__(self, name="booking"):
        self.name = name
    def get_module_name(self) -> str:
        return self.name
    def get_required_fields(self, config):
        return []
    def validate_input(self, field_def, value):
        return ValidationResult(True)
    async def execute(self, context):
        return True

def make_context(plan_code="trial", status="trial", modules=None):
    tenant_id = uuid4()
    module_config = modules if modules is not None else {
        "booking": {"enabled": True, "fields": []},
    }
    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id,
            name="Test Tenant",
            plan_code=plan_code,
            status=status,
        ),
        user=User(id=uuid4(), phone_number="+15550000000", name="Customer"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="booking",
            state=SessionState(workflow_state=ConversationState.EXECUTING),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Test Tenant",
                "modules": module_config,
            },
        ),
        incoming_message={"text": "test", "channel_type": "awa_chat"},
    )

async def test_gateway_enabled_module_is_allowed():
    gateway = CapabilityGateway()
    context = make_context()
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(risk_level="write", requires_confirmation=False, requires_verified_customer=False)

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "allowed"
    assert decision.reason_code == "allowed"
    assert decision.safe_to_execute is True

async def test_gateway_disabled_module_is_denied():
    gateway = CapabilityGateway()
    context = make_context(modules={"booking": {"enabled": False}})
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(risk_level="write", requires_confirmation=False, requires_verified_customer=False)

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "denied"
    assert decision.reason_code == "module_disabled:booking"
    assert decision.safe_to_execute is False

async def test_gateway_plan_blocked_is_denied():
    gateway = CapabilityGateway()
    # "support" module is not in "trial" plan defaults
    context = make_context(plan_code="trial", modules={"support": {"enabled": True}})
    tool_def = ToolDefinition("support.execute", "support_execute", "support", "desc", lambda x: True, "audit", FakeModule("support"))
    metadata = ToolMetadata(risk_level="write", requires_confirmation=False, requires_verified_customer=False)

    request = CapabilityRequest.from_context("support.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "denied"
    assert decision.reason_code == "module_not_allowed:support"
    assert decision.safe_to_execute is False

async def test_gateway_unsupported_channel_is_denied():
    gateway = CapabilityGateway()
    context = make_context()
    context.incoming_message.update({
        "channel_type": "whatsapp", "source_type": "webhook",
        "tenant_id": str(context.tenant.id), "conversation_id": str(context.session_data.conversation_id),
        "channel_account_id": str(uuid4()), "whatsapp_account_id": None,
    })
    context.incoming_message["whatsapp_account_id"] = context.incoming_message["channel_account_id"]
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(supported_channels=("awa_chat",))

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "denied"
    assert decision.reason_code == "channel_not_supported:booking:whatsapp"
    assert decision.safe_to_execute is False

async def test_gateway_confirmation_required_returns_needs_confirmation():
    gateway = CapabilityGateway()
    context = make_context()
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(requires_confirmation=True)

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "needs_confirmation"
    assert decision.reason_code == "confirmation_required:booking"
    assert decision.safe_to_execute is False
    assert decision.required_action == "confirm"
    assert "pending_action" in decision.audit_metadata

async def test_gateway_verified_customer_required_returns_needs_verification():
    gateway = CapabilityGateway()
    context = make_context()
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(requires_confirmation=False, requires_verified_customer=True)

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "needs_verification"
    assert decision.reason_code == "customer_verification_required:booking"
    assert decision.safe_to_execute is False
    assert decision.required_action == "verify"
    assert "required_next_step" in decision.audit_metadata

async def test_gateway_escalated_conversation_blocks_normal_tools():
    gateway = CapabilityGateway()
    context = make_context()
    context.session_data.state.workflow_state = ConversationState.ESCALATED
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(allowed_when_paused=False)

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "escalated"
    assert decision.reason_code == "chat_paused:booking"
    assert decision.safe_to_execute is False

async def test_gateway_unknown_tool_returns_unknown_tool():
    gateway = CapabilityGateway()
    context = make_context()
    request = CapabilityRequest.from_context("unknown.execute", context)

    decision = await gateway.evaluate(request, context, None, None)

    assert decision.status == "unavailable"
    assert decision.reason_code == "unknown_tool"
    assert decision.safe_to_execute is False

async def test_gateway_invalid_payload_returns_invalid_payload():
    gateway = CapabilityGateway()
    context = make_context()
    class CustomModule(FakeModule):
        def get_tool_schema(self, config):
            return {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", CustomModule("booking"))
    metadata = ToolMetadata()

    request = CapabilityRequest.from_context("booking.execute", context, args={})
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "denied"
    assert decision.reason_code == "argument_validation_failed:booking"
    assert decision.safe_to_execute is False

def test_gateway_decision_contains_no_secrets():
    decision = CapabilityDecision(
        status="allowed",
        reason_code="allowed",
        user_message="Safe",
        audit_metadata={"some_public_meta": "yes"},
        safe_to_execute=True
    )
    dumped = decision.model_dump()
    for secret_key in ["key", "secret", "token", "password", "credential", "auth"]:
        assert secret_key not in dumped or secret_key == "required_action" or dumped[secret_key] is None

async def test_gateway_whatsapp_bypasses_customer_verification():
    gateway = CapabilityGateway()
    context = make_context()
    whatsapp_account_id = uuid4()
    context.incoming_message.update({
        "channel_type": "whatsapp", "source_type": "webhook",
        "tenant_id": str(context.tenant.id), "conversation_id": str(context.session_data.conversation_id),
        "channel_account_id": str(whatsapp_account_id), "whatsapp_account_id": str(whatsapp_account_id),
    })
    tool_def = ToolDefinition("booking.execute", "booking_execute", "booking", "desc", lambda x: True, "audit", FakeModule("booking"))
    metadata = ToolMetadata(requires_confirmation=False, requires_verified_customer=True)

    request = CapabilityRequest.from_context("booking.execute", context)
    decision = await gateway.evaluate(request, context, tool_def, metadata)

    assert decision.status == "allowed"
    assert decision.reason_code == "allowed"
    assert decision.safe_to_execute is True
