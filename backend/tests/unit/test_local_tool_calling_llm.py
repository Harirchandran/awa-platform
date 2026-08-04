import pytest
from langchain_core.messages import HumanMessage
from backend.logic.engine import llm as llm_module
pytestmark = pytest.mark.anyio
async def test_deterministic_fake_llm_is_opt_in_and_tool_call_capable(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "test")
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")
    model = llm_module.get_llm().bind_tools([
        {
            "type": "function",
            "function": {
                "name": "booking_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ])
    response = await model.ainvoke([HumanMessage(content="Book cleaning tomorrow at 10 AM for Asha")])
    assert response.tool_calls
    assert response.tool_calls[0]["name"] == "booking_execute"
    assert response.tool_calls[0]["args"]["service_name"] == "Dental Cleaning"
    assert response.tool_calls[0]["args"]["preferred_date"] == "tomorrow"
async def test_deterministic_fake_llm_extracts_toothpaste_order(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "test")
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")
    model = llm_module.get_llm().bind_tools([
        {
            "type": "function",
            "function": {
                "name": "order_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ])
    response = await model.ainvoke([HumanMessage(content="I want 2 Toothpaste for pickup. My phone is +919999999999.")])
    assert response.tool_calls
    assert response.tool_calls[0]["name"] == "order_execute"
    assert response.tool_calls[0]["args"]["item_name"] == "Toothpaste"
    assert response.tool_calls[0]["args"]["quantity"] == 2
async def test_deterministic_fake_llm_extracts_lead_capture_before_booking_terms(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "test")
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")
    model = llm_module.get_llm().bind_tools([
        {
            "type": "function",
            "function": {
                "name": "lead_capture_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "booking_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ])
    response = await model.ainvoke([
        HumanMessage(content="I am interested in dental cleaning. My name is Lead Probe and my phone is +919888888888.")
    ])
    assert response.tool_calls
    assert response.tool_calls[0]["name"] == "lead_capture_execute"
    assert response.tool_calls[0]["args"]["name"] == "Lead Probe"
    assert response.tool_calls[0]["args"]["phone"] == "+919888888888"
    assert response.tool_calls[0]["args"]["interest"] == "dental cleaning"
async def test_deterministic_fake_llm_extracts_reminder_before_catalog_item(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "test")
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")
    model = llm_module.get_llm().bind_tools([
        {
            "type": "function",
            "function": {
                "name": "reminders_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "order_execute",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ])
    response = await model.ainvoke([
        HumanMessage(content="Remind me in 1 minute in this chat to floss after dinner. My phone is +919999999999.")
    ])
    assert response.tool_calls
    assert response.tool_calls[0]["name"] == "reminders_execute"
    assert response.tool_calls[0]["args"]["time_offset"] == 1
    assert response.tool_calls[0]["args"]["message_text"] == "floss after dinner"
async def test_deterministic_fake_llm_is_blocked_in_live_mode(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "live")
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "deterministic_fake")
    with pytest.raises(RuntimeError, match="not allowed in live"):
        llm_module.get_llm()
