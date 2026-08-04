from __future__ import annotations

import json
import re
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from backend.core.config import settings
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.core import agentic_engine
from backend.logic.engine.field_extraction import LeadFieldExtraction, SupportFieldExtraction
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.tools.registry import ToolExecutionResult

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)

UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _lead_config(*, enabled=True, fields=None):
    lead_fields = fields or [
        {"key": "name", "label": "Name", "type": "string", "required": True},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False},
        {"key": "email", "label": "Email", "type": "email", "required": False},
        {"key": "interest", "label": "Interest", "type": "string", "required": True},
        {"key": "message", "label": "Message", "type": "string", "required": False},
        {"key": "preferred_contact_time", "label": "Preferred Contact Time", "type": "string", "required": False},
    ]
    return {
        "modules": {
            "lead_capture": {"enabled": enabled, "fields": lead_fields},
            "support": {"enabled": True},
        }
    }


def _support_config(*, enabled=True, fields=None):
    support_fields = fields or [
        {"key": "issue_description", "label": "Issue Description", "type": "string", "required": True},
        {"key": "urgency", "label": "Urgency", "type": "string", "required": False},
        {"key": "customer_name", "label": "Name", "type": "string", "required": False},
        {"key": "phone", "label": "Phone", "type": "string", "required": False},
        {"key": "email", "label": "Email", "type": "string", "required": False},
        {"key": "preferred_contact_time", "label": "Preferred Contact Time", "type": "string", "required": False},
    ]
    return {
        "modules": {
            "lead_capture": {"enabled": True},
            "support": {"enabled": enabled, "fields": support_fields},
        }
    }


def _context(
    module_name: str,
    *,
    step_data=None,
    config_data=None,
    enabled=True,
    message_text="black-box workflow reply",
):
    tenant_id = uuid4()
    config = config_data or (_lead_config(enabled=enabled) if module_name == "lead_capture" else _support_config(enabled=enabled))
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=dict(step_data or {}),
        scoped_step_data={module_name: dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = module_name
    state.action_ledger.task_status = "collecting"
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="Test User"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=module_name,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config),
        incoming_message={"text": message_text, "channel_type": "awa_chat"},
    )


def _route(context):
    from backend.pipeline.message_router import message_router

    return message_router.route_message(
        context.incoming_message.get("text", ""),
        context.config.config_data,
        current_task=context.session_data.state.action_ledger.current_task,
        current_module=context.session_data.current_module,
    )


def _semantic_lead_extraction(**kwargs):
    defaults = {
        "detected_language": "black-box",
        "workflow": "lead_capture",
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": {},
        "field_confidence": {},
        "clarification_needed": False,
    }
    defaults.update(kwargs)
    return LeadFieldExtraction(**defaults)


def _semantic_support_extraction(**kwargs):
    defaults = {
        "detected_language": "black-box",
        "workflow": "support",
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": {},
        "field_confidence": {},
        "clarification_needed": False,
    }
    defaults.update(kwargs)
    return SupportFieldExtraction(**defaults)


async def _install_lead_extraction_stub(monkeypatch, extraction=None, exc: Exception | None = None):
    async def fake_extract(ctx):
        if exc:
            raise exc
        return extraction

    monkeypatch.setattr("backend.logic.engine.core.extract_lead_fields", fake_extract)


async def _install_support_extraction_stub(monkeypatch, extraction=None, exc: Exception | None = None):
    async def fake_extract(ctx):
        if exc:
            raise exc
        return extraction

    monkeypatch.setattr("backend.logic.engine.core.extract_support_fields", fake_extract)


async def test_active_lead_semantic_extraction_applies_contact_interest_and_message(monkeypatch):
    context = _context("lead_capture", message_text="black-box mixed-language lead details")
    await _install_lead_extraction_stub(
        monkeypatch,
        _semantic_lead_extraction(
            field_updates={
                "customer_name": "Asha Nair",
                "phone": "+10000000000",
                "email": "asha@example.com",
                "interest": "Consultation",
                "message": "Needs details",
            },
            field_confidence={
                "customer_name": 0.95,
                "phone": 0.95,
                "email": 0.95,
                "interest": 0.95,
                "message": 0.95,
            },
        ),
    )

    result = await agentic_engine._handle_semantic_lead_field_extraction(context, _route(context))

    assert result is None
    step_data = context.session_data.state.get_task_step_data("lead_capture")
    assert step_data["name"] == "Asha Nair"
    assert step_data["phone"] == "+10000000000"
    assert step_data["email"] == "asha@example.com"
    assert step_data["interest"] == "Consultation"
    assert step_data["message"] == "Needs details"


async def test_active_support_semantic_extraction_applies_issue_contact_and_urgency(monkeypatch):
    context = _context("support", message_text="black-box support details")
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(
            field_updates={
                "issue_summary": "Need help with account access",
                "customer_name": "Asha Nair",
                "phone": "+10000000000",
                "email": "asha@example.com",
                "urgency": "high",
            },
            field_confidence={
                "issue_summary": 0.95,
                "customer_name": 0.95,
                "phone": 0.95,
                "email": 0.95,
                "urgency": 0.95,
            },
        ),
    )

    result = await agentic_engine._handle_semantic_support_field_extraction(context, _route(context))

    assert result is None
    step_data = context.session_data.state.get_task_step_data("support")
    assert step_data["issue_description"] == "Need help with account access"
    assert step_data["customer_name"] == "Asha Nair"
    assert step_data["phone"] == "+10000000000"
    assert step_data["email"] == "asha@example.com"
    assert step_data["urgency"] == "high"


async def test_non_english_lead_and_support_inputs_use_semantic_stubs(monkeypatch):
    lead_context = _context("lead_capture", message_text="black-box non-English lead input")
    support_context = _context("support", message_text="black-box mixed-language support input")
    await _install_lead_extraction_stub(
        monkeypatch,
        _semantic_lead_extraction(
            field_updates={"interest": "Consultation"},
            field_confidence={"interest": 0.96},
        ),
    )
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(
            field_updates={"issue_summary": "Need help with billing"},
            field_confidence={"issue_summary": 0.96},
        ),
    )

    lead_result = await agentic_engine._handle_semantic_lead_field_extraction(lead_context, _route(lead_context))
    support_result = await agentic_engine._handle_semantic_support_field_extraction(support_context, _route(support_context))

    assert lead_result is None
    assert support_result is None
    assert lead_context.session_data.state.get_task_step_data("lead_capture")["interest"] == "Consultation"
    assert support_context.session_data.state.get_task_step_data("support")["issue_description"] == "Need help with billing"


async def test_typo_partial_input_during_active_lead_and_support_does_not_reset(monkeypatch):
    lead_context = _context("lead_capture", step_data={"interest": "Consultation"}, message_text="black-box typo")
    support_context = _context("support", step_data={"issue_description": "Need help with billing"}, message_text="fragment")
    await _install_lead_extraction_stub(monkeypatch, _semantic_lead_extraction(confidence=0.2))
    await _install_support_extraction_stub(monkeypatch, _semantic_support_extraction(confidence=0.2))

    lead_result = await agentic_engine._handle_semantic_lead_field_extraction(lead_context, _route(lead_context))
    support_result = await agentic_engine._handle_semantic_support_field_extraction(support_context, _route(support_context))

    assert lead_result is not None
    assert support_result is not None
    assert lead_context.session_data.state.action_ledger.current_task == "lead_capture"
    assert support_context.session_data.state.action_ledger.current_task == "support"
    assert lead_context.session_data.state.get_task_step_data("lead_capture")["interest"] == "Consultation"
    assert support_context.session_data.state.get_task_step_data("support")["issue_description"] == "Need help with billing"


async def test_low_confidence_lead_and_support_extraction_preserves_state(monkeypatch):
    lead_context = _context("lead_capture", step_data={"interest": "Consultation"})
    support_context = _context("support", step_data={"issue_description": "Need help with billing"})
    await _install_lead_extraction_stub(
        monkeypatch,
        _semantic_lead_extraction(confidence=0.1, field_updates={"phone": "+10000000000"}),
    )
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(confidence=0.1, field_updates={"urgency": "high"}),
    )

    lead_result = await agentic_engine._handle_semantic_lead_field_extraction(lead_context, _route(lead_context))
    support_result = await agentic_engine._handle_semantic_support_field_extraction(support_context, _route(support_context))

    assert lead_result is not None
    assert support_result is not None
    assert lead_context.session_data.state.get_task_step_data("lead_capture") == {"interest": "Consultation"}
    assert support_context.session_data.state.get_task_step_data("support") == {"issue_description": "Need help with billing"}


async def test_provider_failure_lead_and_support_extraction_preserves_state(monkeypatch):
    lead_context = _context("lead_capture", step_data={"interest": "Consultation"})
    support_context = _context("support", step_data={"issue_description": "Need help with billing"})
    await _install_lead_extraction_stub(monkeypatch, exc=ValueError("invalid semantic json"))
    await _install_support_extraction_stub(monkeypatch, exc=TimeoutError("semantic timeout"))

    lead_result = await agentic_engine._handle_semantic_lead_field_extraction(lead_context, _route(lead_context))
    support_result = await agentic_engine._handle_semantic_support_field_extraction(support_context, _route(support_context))

    assert lead_result is not None
    assert support_result is not None
    assert lead_context.session_data.state.get_task_step_data("lead_capture") == {"interest": "Consultation"}
    assert support_context.session_data.state.get_task_step_data("support") == {"issue_description": "Need help with billing"}


async def test_invalid_phone_email_is_rejected_by_backend_validation(monkeypatch):
    context = _context("lead_capture", step_data={"interest": "Consultation"})
    await _install_lead_extraction_stub(
        monkeypatch,
        _semantic_lead_extraction(
            field_updates={"phone": "bad-phone", "email": "bad-email"},
            field_confidence={"phone": 0.95, "email": 0.95},
        ),
    )

    result = await agentic_engine._handle_semantic_lead_field_extraction(context, _route(context))

    assert result is not None
    assert "valid" in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("lead_capture") == {"interest": "Consultation"}


async def test_unsupported_support_urgency_is_rejected(monkeypatch):
    context = _context("support", step_data={"issue_description": "Need help with billing"})
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(
            field_updates={"urgency": "critical"},
            field_confidence={"urgency": 0.95},
        ),
    )

    result = await agentic_engine._handle_semantic_support_field_extraction(context, _route(context))

    assert result is not None
    assert result.quick_replies == ["low", "medium", "high"]
    assert context.session_data.state.get_task_step_data("support") == {"issue_description": "Need help with billing"}


async def test_disabled_lead_and_support_modules_do_not_run_semantic_extraction(monkeypatch):
    lead_context = _context("lead_capture", enabled=False)
    support_context = _context("support", enabled=False)
    called = {"lead": False, "support": False}

    async def fake_lead(ctx):
        called["lead"] = True
        return _semantic_lead_extraction(field_updates={"interest": "Consultation"})

    async def fake_support(ctx):
        called["support"] = True
        return _semantic_support_extraction(field_updates={"urgency": "high"})

    monkeypatch.setattr("backend.logic.engine.core.extract_lead_fields", fake_lead)
    monkeypatch.setattr("backend.logic.engine.core.extract_support_fields", fake_support)

    lead_result = await agentic_engine._handle_semantic_lead_field_extraction(lead_context, _route(lead_context))
    support_result = await agentic_engine._handle_semantic_support_field_extraction(support_context, _route(support_context))

    assert lead_result is None
    assert support_result is None
    assert called == {"lead": False, "support": False}


async def test_unknown_unsafe_fields_are_rejected_without_partial_apply(monkeypatch):
    context = _context("support", step_data={"issue_description": "Need help with billing"})
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(
            field_updates={"urgency": "high", "ticket_id": str(uuid4())},
            field_confidence={"urgency": 0.95, "ticket_id": 0.95},
        ),
    )

    result = await agentic_engine._handle_semantic_support_field_extraction(context, _route(context))

    assert result is not None
    assert context.session_data.state.get_task_step_data("support") == {"issue_description": "Need help with billing"}


async def test_semantic_tangent_does_not_overwrite_stable_lead_or_support_fields(monkeypatch):
    lead_context = _context("lead_capture", step_data={"interest": "Consultation"})
    support_context = _context("support", step_data={"issue_description": "Need help with billing", "urgency": "medium"})
    await _install_lead_extraction_stub(
        monkeypatch,
        _semantic_lead_extraction(
            workflow_action="tangent",
            field_updates={"interest": "Different"},
            field_confidence={"interest": 0.99},
        ),
    )
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(
            workflow_action="tangent",
            field_updates={"urgency": "high"},
            field_confidence={"urgency": 0.99},
        ),
    )

    lead_result = await agentic_engine._handle_semantic_lead_field_extraction(lead_context, _route(lead_context))
    support_result = await agentic_engine._handle_semantic_support_field_extraction(support_context, _route(support_context))

    assert lead_result is not None
    assert support_result is not None
    assert lead_context.session_data.state.get_task_step_data("lead_capture")["interest"] == "Consultation"
    assert support_context.session_data.state.get_task_step_data("support")["urgency"] == "medium"


async def test_quick_reply_text_is_processed_like_normal_message_through_semantic_stub(monkeypatch):
    context = _context("support", step_data={"issue_description": "Need help with billing"}, message_text="black-box quick reply")
    await _install_support_extraction_stub(
        monkeypatch,
        _semantic_support_extraction(
            field_updates={"urgency": "low"},
            field_confidence={"urgency": 0.98},
        ),
    )

    result = await agentic_engine._handle_semantic_support_field_extraction(context, _route(context))

    assert result is None
    assert context.session_data.state.get_task_step_data("support")["urgency"] == "low"


async def test_lead_support_field_extraction_prompts_omit_internal_ids(monkeypatch):
    from backend.logic.engine import field_extraction

    internal_id = str(uuid4())
    captured: list[str] = []

    class FakeLLM:
        async def ainvoke(self, messages):
            captured.append(str(messages[-1].content))
            workflow = "lead_capture" if len(captured) == 1 else "support"
            return AIMessage(
                content=json.dumps(
                    {
                        "workflow": workflow,
                        "workflow_action": "continue",
                        "confidence": 0.9,
                        "field_updates": {},
                        "field_confidence": {},
                        "clarification_needed": False,
                        "clarification_fields": [],
                        "safe_response_hint": None,
                    }
                )
            )

    lead_cfg = _lead_config(
        fields=[
            {"key": "lead_id", "label": f"Lead {internal_id}", "type": "string", "required": False},
            {"key": "interest", "label": "Interest", "type": "string", "required": True},
        ]
    )
    support_cfg = _support_config(
        fields=[
            {"key": "ticket_id", "label": f"Ticket {internal_id}", "type": "string", "required": False},
            {"key": "issue_description", "label": "Issue Description", "type": "string", "required": True},
        ]
    )
    lead_context = _context(
        "lead_capture",
        config_data=lead_cfg,
        step_data={"interest": internal_id, "lead_id": internal_id},
        message_text=f"black-box [tenant_id] [customer_id] {internal_id}",
    )
    support_context = _context(
        "support",
        config_data=support_cfg,
        step_data={"issue_description": internal_id, "ticket_id": internal_id},
        message_text=f"black-box [ticket_id] {internal_id}",
    )
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: FakeLLM())

    await field_extraction.extract_lead_fields(lead_context)
    await field_extraction.extract_support_fields(support_context)

    payload = "\n".join(captured)
    assert internal_id not in payload
    assert "tenant_id" not in payload
    assert "customer_id" not in payload
    assert "lead_id" not in payload
    assert "ticket_id" not in payload
    assert not UUID_PATTERN.search(payload)


async def test_pending_action_confirmation_still_uses_slice2_semantics(monkeypatch):
    context = _context("lead_capture")
    args = {"name": "Asha", "interest": "Consultation", "phone": "+10000000000"}
    context.session_data.state.update_task_step_data("lead_capture", args)
    context.session_data.state.prepare_pending_action(
        tool_key="lead_capture.execute",
        args=args,
        summary="Please review this lead request.",
        requires_confirmation=True,
        requires_verified_customer=False,
        channel_type="awa_chat",
    )

    async def fake_check_message_allowed(ctx):
        return None

    async def fake_hydrate(ctx):
        return None

    async def fake_decide(ctx):
        return PendingActionDecision(action="confirm", confidence=0.98)

    async def fake_execute_tool(tool_key, ctx):
        return ToolExecutionResult(tool_key=tool_key, success=True, user_message="Lead saved.")

    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", fake_check_message_allowed)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", fake_hydrate)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute_tool)

    result = await agentic_engine.execute_step(context)

    assert result.response_text == "Lead saved."
    assert context.session_data.state.action_ledger.pending_action is None
