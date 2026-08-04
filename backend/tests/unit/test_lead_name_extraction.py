from uuid import uuid4
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.modules.lead_capture_module import lead_capture_module
from backend.domains.leads.tool_handlers import execute_lead_capture_tool

pytestmark = pytest.mark.anyio


def make_context(step_data: dict, user_name: str = "Unknown", user_phone: str = "Unknown") -> ExecutionContext:
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Smile Clinic", plan_code="trial", status="trial"),
        user=User(id=uuid4(), phone_number=user_phone, name=user_name),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(
                step_data=step_data,
                scoped_step_data={"lead_capture": step_data}
            ),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={
            "modules": {
                "lead_capture": {
                    "enabled": True
                }
            }
        }),
        incoming_message={"text": "Hello"},
    )


async def test_validate_payload_rejects_placeholder_names():
    # Test case 1: Name is "Valued Patient" (from step_data)
    context1 = make_context(step_data={"name": "Valued Patient", "phone": "+919999999999", "interest": "General Cleaning"})
    res1 = lead_capture_module.validate_payload(context1)
    assert res1 == "Please provide your name so we can register your interest."

    # Test case 2: Name is "Unknown" (from context.user)
    context2 = make_context(step_data={"interest": "General Cleaning", "phone": "+919999999999"}, user_name="Unknown")
    res2 = lead_capture_module.validate_payload(context2)
    assert res2 == "Please provide your name so we can register your interest."

    # Test case 3: Name is "valued customer" (from step_data)
    context3 = make_context(step_data={"name": " valued customer ", "phone": "+919999999999", "interest": "General Cleaning"})
    res3 = lead_capture_module.validate_payload(context3)
    assert res3 == "Please provide your name so we can register your interest."

    # Test case 4: Valid name (from step_data)
    context4 = make_context(step_data={"name": "Anita Nair", "phone": "+919999999999", "interest": "General Cleaning"})
    res4 = lead_capture_module.validate_payload(context4)
    assert res4 is None

    # Test case 5: Valid name (from context.user)
    context5 = make_context(step_data={"interest": "General Cleaning", "phone": "+919999999999"}, user_name="Anita Nair")
    res5 = lead_capture_module.validate_payload(context5)
    assert res5 is None


async def test_tool_handler_sanitizes_placeholder_names():
    # Test case 1: LLM sends "Valued Patient" as name parameter
    context = make_context(step_data={"name": "Valued Patient", "phone": "+919999999999", "interest": "Cleaning"})
    lead_service_mock = AsyncMock()
    lead_service_mock.capture_lead.return_value = {"id": uuid4(), "created": True}
    logger_mock = MagicMock()

    await execute_lead_capture_tool(context, lead_service_mock, logger_mock)

    # capture_lead should be called with name = None since "Valued Patient" is a placeholder
    lead_service_mock.capture_lead.assert_called_once()
    called_args = lead_service_mock.capture_lead.call_args[1]
    assert called_args["lead_data"]["name"] is None

    # Test case 2: LLM sends "Valued Customer" but user.name is valid
    context2 = make_context(
        step_data={"name": "Valued Customer", "phone": "+919999999999", "interest": "Cleaning"},
        user_name="Anita Nair"
    )
    lead_service_mock2 = AsyncMock()
    lead_service_mock2.capture_lead.return_value = {"id": uuid4(), "created": True}

    await execute_lead_capture_tool(context2, lead_service_mock2, logger_mock)

    lead_service_mock2.capture_lead.assert_called_once()
    called_args2 = lead_service_mock2.capture_lead.call_args[1]
    # It should fall back to user_name "Anita Nair" since the step_data name was sanitized to None
    assert called_args2["lead_data"]["name"] == "Anita Nair"


async def test_tool_handler_accepts_valid_names():
    context = make_context(step_data={"name": "Anita Nair", "phone": "+919999999999", "interest": "Consultation"})
    lead_service_mock = AsyncMock()
    lead_service_mock.capture_lead.return_value = {"id": uuid4(), "created": True}
    logger_mock = MagicMock()

    await execute_lead_capture_tool(context, lead_service_mock, logger_mock)

    lead_service_mock.capture_lead.assert_called_once()
    called_args = lead_service_mock.capture_lead.call_args[1]
    assert called_args["lead_data"]["name"] == "Anita Nair"


async def test_prefill_does_not_extract_name_from_broad_i_am_phrases():
    for message in ["I am hungry", "I'm interested", "I am looking for pricing"]:
        context = make_context(step_data={}, user_name="Unknown", user_phone="Unknown")

        lead_capture_module.prefill_from_message(message, context)

        assert "name" not in context.session_data.state.step_data


async def test_prefill_keeps_explicit_name_intent_patterns():
    context = make_context(step_data={}, user_name="Unknown", user_phone="Unknown")

    lead_capture_module.prefill_from_message("my name is Hari, email hari@example.com", context)

    assert context.session_data.state.step_data["name"] == "Hari"
