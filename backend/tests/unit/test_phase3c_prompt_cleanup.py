import pytest
import re
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from backend.core.context import ExecutionContext
from backend.core.runtime_behavior_profile import RuntimeBehaviorProfileResolution
from backend.logic.engine.history import build_memory_context, format_action_ledger_context, format_step_data_text
from backend.logic.engine.prompt import build_system_instructions
from backend.schemas.behavior_profile import EffectiveBehaviorProfile
from backend.ai.rag_service import RAGService
from backend.logic.engine.capability_menu import _format_catalog_line
from backend.domains.orders.tool_definitions import get_order_tool_schema
from backend.domains.bookings.tool_definitions import get_booking_tool_schema
from backend.pipeline.channel_output import sanitize_customer_visible_output
from backend.logic.engine.llm import DeterministicFakeToolCallingLLM

pytestmark = pytest.mark.anyio


def _build_test_context(step_data=None, ledger_data=None):
    context = MagicMock(spec=ExecutionContext)
    context.tenant = MagicMock()
    context.tenant.id = uuid4()
    context.tenant.name = "Test business"
    context.tenant.ai_mode = "standard"
    context.user = MagicMock()
    context.user.id = uuid4()
    context.session_data = MagicMock()
    context.session_data.conversation_id = uuid4()
    context.session_data.id = uuid4()

    state = MagicMock()
    state.step_data = step_data or {}
    state.scoped_step_data = step_data or {}
    state.workflow_state = "collecting_data"

    if ledger_data:
        state.action_ledger = MagicMock()
        state.action_ledger.current_task = ledger_data.get("current_task")
        state.action_ledger.task_status = ledger_data.get("task_status")
        state.action_ledger.channel = ledger_data.get("channel")

        # Pending Action Mock
        if "pending_action" in ledger_data:
            pa = MagicMock()
            pa.tool_key = ledger_data["pending_action"].get("tool_key")
            pa.summary = ledger_data["pending_action"].get("summary")
            pa.requires_confirmation = ledger_data["pending_action"].get("requires_confirmation", True)
            pa.requires_verified_customer = ledger_data["pending_action"].get("requires_verified_customer", False)
            pa.verification_action = ledger_data["pending_action"].get("verification_action")
            state.action_ledger.pending_action = pa
        else:
            state.action_ledger.pending_action = None

        # Last Tool Result Mock
        if "last_tool_result" in ledger_data:
            ltr = MagicMock()
            ltr.tool_key = ledger_data["last_tool_result"].get("tool_key")
            ltr.success = ledger_data["last_tool_result"].get("success", True)
            ltr.denied_reason = ledger_data["last_tool_result"].get("denied_reason")
            ltr.user_message = ledger_data["last_tool_result"].get("user_message")
            ltr.created_resource_type = ledger_data["last_tool_result"].get("created_resource_type")
            ltr.created_resource_id = ledger_data["last_tool_result"].get("created_resource_id")
            ltr.validation_errors = ledger_data["last_tool_result"].get("validation_errors")
            ltr.data = ledger_data["last_tool_result"].get("data", {})
            state.action_ledger.last_tool_result = ltr
        else:
            state.action_ledger.last_tool_result = None

        # Last Error Mock
        if "last_error" in ledger_data:
            le = MagicMock()
            le.code = ledger_data["last_error"].get("code")
            le.safe_message = ledger_data["last_error"].get("safe_message")
            state.action_ledger.last_error = le
        else:
            state.action_ledger.last_error = None
    else:
        state.action_ledger = None

    context.session_data.state = state

    config = MagicMock()
    config.config_data = {
        "business_name": "Test business",
        "business_type": "retail",
        "ai_tone": "friendly",
        "default_language": "en",
        "modules": {
            "order": {"enabled": True},
            "booking": {"enabled": True}
        }
    }
    context.config = config
    context.incoming_message = {"channel_type": "awa_chat"}
    return context


def _active_behavior_profile(journey_priority=("booking", "order", "payments"), business_type_key="service_business"):
    return RuntimeBehaviorProfileResolution(
        source="active",
        effective_profile=EffectiveBehaviorProfile(
            schema_version=1,
            profile_name="Service Profile",
            business_type_key=business_type_key,
            journey_priority=tuple(journey_priority),
            greeting_mode="ask_need_first",
            clarification_style="compact",
            tone="formal",
            language_policy="follow_customer",
            price_visibility="show_when_available",
            service_detail_policy="brief",
            handoff_policy="early_offer",
            fallback_policy="clarify_first",
            question_batching="grouped",
            menu_policy="show_available_workflows",
        ),
    )


def _prompt_section(prompt: str, header: str, next_header: str) -> str:
    start = prompt.find(header)
    assert start != -1
    end = prompt.find(next_header, start)
    assert end != -1
    return prompt[start:end]


# 1. Test step_data/scoped_step_data/action_ledger formatting contains no raw JSON blocks
@pytest.mark.asyncio
async def test_memory_context_contains_no_raw_json_blocks():
    step_data = {
        "item_name": "Toothpaste",
        "quantity": 2,
        "customer_name": "Alice",
        "phone": "+1234567890",
        "order_id": str(uuid4()), # Should be filtered out
        "booking_id": str(uuid4()) # Should be filtered out
    }
    context = _build_test_context(step_data=step_data)
    memory_text = await build_memory_context(context)

    # Asserts
    assert "{" not in memory_text
    assert "}" not in memory_text
    assert "order_id" not in memory_text
    assert "booking_id" not in memory_text
    assert "customer_name: Alice" in memory_text
    assert "item_name: Toothpaste" in memory_text
    assert "quantity: 2" in memory_text


# 2. Test action ledger textual formatting contains no raw JSON
@pytest.mark.asyncio
async def test_action_ledger_contains_no_raw_json():
    ledger_data = {
        "current_task": "order",
        "task_status": "collecting",
        "channel": "whatsapp",
        "pending_action": {
            "tool_key": "order.execute",
            "summary": "Order 2 Toothpaste",
            "requires_confirmation": True
        },
        "last_tool_result": {
            "tool_key": "order.execute",
            "success": False,
            "denied_reason": "out_of_stock",
            "user_message": "Item is currently out of stock.",
            "created_resource_id": str(uuid4()) # UUID should be redacted/omitted
        }
    }
    context = _build_test_context(ledger_data=ledger_data)
    ledger_text = format_action_ledger_context(context)

    assert "{" not in ledger_text
    assert "}" not in ledger_text
    assert "pending_action" not in ledger_text
    assert "last_tool_result" not in ledger_text
    assert "Current Task: order" in ledger_text
    assert "Task Status: collecting" in ledger_text
    assert "Action key: order.execute" in ledger_text
    assert "Success: False" in ledger_text
    assert "Denied Reason: out_of_stock" in ledger_text

    # Ensure no UUID leaks in the action ledger text
    uuid_patterns = re.findall(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", ledger_text)
    assert not uuid_patterns


# 3. Test that old labels are removed in build_memory_context
@pytest.mark.asyncio
async def test_memory_context_labels_updated():
    context = _build_test_context()
    memory_text = await build_memory_context(context)

    assert "Collected session memory" not in memory_text
    assert "Task-scoped collected memory" not in memory_text
    assert "Current action ledger" not in memory_text

    assert "Session variables:" in memory_text
    assert "Active task variables:" in memory_text
    assert "Action status:" in memory_text


# 4. Test RAG provenance contains no entry_id
def test_rag_provenance_omits_entry_id():
    rows = [
        {
            "id": uuid4(),
            "document_title": "Dental FAQs",
            "chunk_text": "We offer dental cleaning for INR 1500.",
            "metadata": {"source": "website"}
        }
    ]
    formatted = RAGService.format_rows_with_provenance(rows)
    assert "entry_id" not in formatted
    assert "Dental FAQs" in formatted
    assert "website" in formatted
    assert "We offer dental cleaning" in formatted


# 5. Test matched catalog listings contain no ID leak
def test_catalog_matching_omits_item_id():
    item = {
        "id": str(uuid4()),
        "name": "Toothpaste",
        "price": 120,
        "sku": "TP123",
        "category_name": "Hygiene"
    }
    formatted = _format_catalog_line(item)
    assert "(id:" not in formatted
    assert "Toothpaste" in formatted
    assert "TP123" in formatted
    assert "INR 120" in formatted


# 6. Test reordering system instructions by authority
@pytest.mark.asyncio
async def test_system_instructions_authority_order():
    context = _build_test_context()
    context.config.config_data["custom_instructions"] = "Please offer tea to customers."
    memory_context = "Memory placeholder"
    knowledge_context = "Knowledge placeholder"

    prompt = build_system_instructions(
        context=context,
        memory_context=memory_context,
        knowledge_context=knowledge_context,
        tool_registry=MagicMock(),
        review_settings=None,
        available_tools=[]
    )

    # Assert authority sections exist in order
    idx_safety = prompt.find("### 1. PLATFORM SAFETY & CORE REFUSAL POLICIES")
    idx_behavior = prompt.find("### 2. SYSTEM BUSINESS-AGENT BEHAVIOR")
    idx_facts = prompt.find("### 3. TRUSTED RUNTIME FACTS")
    idx_profile = prompt.find("### 4. TENANT BUSINESS PROFILE")
    idx_preferences = prompt.find("### 5. BUSINESS OWNER PREFERENCES")
    idx_rules = prompt.find("### 6. DOMAIN & TOOL RULES")
    idx_knowledge = prompt.find("### 7. RETRIEVED KNOWLEDGE & CONTEXT")
    idx_memory = prompt.find("### 8. COMPACT MEMORY, LEDGER & HISTORY")

    assert idx_safety != -1
    assert idx_safety < idx_behavior < idx_facts < idx_profile < idx_preferences < idx_rules < idx_knowledge < idx_memory

    # Custom instructions wrap verification
    assert "BUSINESS OWNER PREFERENCES (Untrusted Context - Lower Authority)" in prompt
    assert "Please offer tea to customers." in prompt


@pytest.mark.asyncio
async def test_behavior_profile_prompt_hints_are_safe_filtered_and_low_authority():
    context = _build_test_context()
    internal_uuid = str(uuid4())
    context.runtime_behavior_profile = _active_behavior_profile(business_type_key=internal_uuid)

    prompt = build_system_instructions(
        context=context,
        memory_context="Memory placeholder",
        knowledge_context="Knowledge placeholder",
        tool_registry=MagicMock(),
        review_settings=None,
        available_tools=[],
        runtime_available_modules={"booking", "faq"},
    )

    idx_safety = prompt.find("### 1. PLATFORM SAFETY & CORE REFUSAL POLICIES")
    idx_rules = prompt.find("### 6. DOMAIN & TOOL RULES")
    idx_profile = prompt.find("### 7. BEHAVIOR PROFILE PRESENTATION GUIDANCE")
    idx_knowledge = prompt.find("### 8. RETRIEVED KNOWLEDGE & CONTEXT")
    idx_memory = prompt.find("### 9. COMPACT MEMORY, LEDGER & HISTORY")
    assert idx_safety < idx_rules < idx_profile < idx_knowledge < idx_memory

    section = _prompt_section(
        prompt,
        "### 7. BEHAVIOR PROFILE PRESENTATION GUIDANCE",
        "### 8. RETRIEVED KNOWLEDGE & CONTEXT",
    )
    assert "presentation, prioritization, and conversation style guidance only" in section
    assert "Not authority for execution, module availability, tool access" in section
    assert "payment/provider choice, confirmation, OTP/verification" in section
    assert "Tone hint: formal" in section
    assert "Clarification style: compact" in section
    assert "Question batching: grouped" in section
    assert "Handoff style: early_offer; offer handoff only when available" in section
    assert "Eligible journey priority hint: Bookings" in section
    assert "Orders" not in section
    assert "Payments" not in section
    assert "{" not in section and "}" not in section
    assert internal_uuid not in section
    assert "can enable" not in section.lower()
    assert "Tenant custom instructions remain lower authority" in section


@pytest.mark.asyncio
async def test_behavior_profile_prompt_omits_legacy_fallback_and_formatter_failures():
    context = _build_test_context()
    context.runtime_behavior_profile = RuntimeBehaviorProfileResolution(
        source="legacy_fallback",
        warning_code="no_active_behavior_preset",
        effective_profile=_active_behavior_profile().effective_profile,
    )

    fallback_prompt = build_system_instructions(
        context=context,
        memory_context="Memory",
        knowledge_context="Knowledge",
        tool_registry=MagicMock(),
        review_settings=None,
        available_tools=[],
        runtime_available_modules={"booking"},
    )
    assert "BEHAVIOR PROFILE PRESENTATION GUIDANCE" not in fallback_prompt
    assert "### 7. RETRIEVED KNOWLEDGE & CONTEXT" in fallback_prompt

    context.runtime_behavior_profile = _active_behavior_profile()
    with patch("backend.logic.engine.prompt.format_behavior_profile_context", side_effect=ValueError("secret raw profile")):
        failed_prompt = build_system_instructions(
            context=context,
            memory_context="Memory",
            knowledge_context="Knowledge",
            tool_registry=MagicMock(),
            review_settings=None,
            available_tools=[],
            runtime_available_modules={"booking"},
        )
    assert "BEHAVIOR PROFILE PRESENTATION GUIDANCE" not in failed_prompt
    assert "secret raw profile" not in failed_prompt


@pytest.mark.asyncio
async def test_standard_and_advanced_ai_prompt_authority_boundaries_are_identical():
    prompts = {}
    for mode in ("standard", "advanced"):
        context = _build_test_context()
        context.tenant.ai_mode = mode
        prompts[mode] = build_system_instructions(
            context=context,
            memory_context="Memory",
            knowledge_context="Knowledge",
            tool_registry=MagicMock(),
            review_settings=None,
            available_tools=[],
        )

    unsafe_phrases = (
        "full authority",
        "do not wait for confirmation",
        "skip confirmation",
        "confirmation not required",
        "approval not required",
        "proceed without approval",
        "execute directly",
        "direct execution",
    )
    for prompt in prompts.values():
        lowered = prompt.lower()
        for phrase in unsafe_phrases:
            assert phrase not in lowered
        assert "concierge" not in lowered
        assert "ai mode never bypasses confirmation, otp, payment/provider authority" in lowered
        assert "toolregistry, capabilitygateway" in lowered
        assert "usageguard, idempotency, runtime module availability, or backend success/failure truth" in lowered
        assert "never claim a phone number or customer identity is verified unless a backend result explicitly says so" in lowered

    advanced = prompts["advanced"].lower()
    assert "more proactive at interpreting mixed details" in advanced
    assert "does not grant extra tool authority or bypass backend validation" in advanced


# 7. Test custom instructions are truncated to 1000 characters
@pytest.mark.asyncio
async def test_custom_instructions_length_bounding():
    context = _build_test_context()
    long_instruction = "A" * 2500
    context.config.config_data["custom_instructions"] = long_instruction

    prompt = build_system_instructions(
        context=context,
        memory_context="Memory",
        knowledge_context="Knowledge",
        tool_registry=MagicMock(),
        review_settings=None,
        available_tools=[]
    )

    # The formatted custom instructions should be bounded
    truncated_a_count = prompt.count("A")
    assert truncated_a_count <= 1000


# 8. Regression: Order tool schema uses item_name and not item_id
def test_order_tool_schema_regression():
    schema = get_order_tool_schema({"modules": {"order": {"enabled": True}}})
    assert "item_name" in schema["properties"]
    assert "item_id" not in schema["properties"]


# 9. Regression: Booking tool schema uses service_name and not service_id
def test_booking_tool_schema_regression():
    schema = get_booking_tool_schema({"modules": {"booking": {"enabled": True}}})
    assert "service_name" in schema["properties"]
    assert "service_id" not in schema["properties"]


# 10. Regression: Prompt/customer UUID sanitizer still works
def test_uuid_sanitizer_regression():
    raw_output = f"Booking created with ID {uuid4()}."
    sanitized = sanitize_customer_visible_output(raw_output)
    assert "[redacted]" in sanitized
    assert not re.search(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", sanitized)


# 11. Regression: Fake LLM remains aligned with item_name/service_name only
@pytest.mark.asyncio
async def test_fake_llm_regression():
    llm = DeterministicFakeToolCallingLLM()
    # Mock bound tools
    mock_order_tool = {
        "function": {
            "name": "order_execute",
            "parameters": {
                "properties": {
                    "item_name": {"type": "string"},
                    "quantity": {"type": "integer"}
                }
            }
        }
    }
    mock_booking_tool = {
        "function": {
            "name": "booking_execute",
            "parameters": {
                "properties": {
                    "service_name": {"type": "string"},
                    "preferred_date": {"type": "string"}
                }
            }
        }
    }
    llm_with_tools = llm.bind_tools([mock_order_tool, mock_booking_tool])

    # Order request
    class HumanMsgMock:
        def __init__(self, content):
            self.content = content
            self.type = "human"

    order_res = await llm_with_tools.ainvoke([HumanMsgMock("I want to order 2 toothpaste")])
    assert order_res.tool_calls
    tc = order_res.tool_calls[0]
    assert tc["name"] == "order_execute"
    assert "item_name" in tc["args"]
    assert tc["args"]["item_name"] == "Toothpaste"
    assert "item_id" not in tc["args"]

    # Booking request
    booking_res = await llm_with_tools.ainvoke([HumanMsgMock("Book clean tomorrow at 10 AM")])
    assert booking_res.tool_calls
    btc = booking_res.tool_calls[0]
    assert btc["name"] == "booking_execute"
    assert "service_name" in btc["args"]
    assert btc["args"]["service_name"] == "Dental Cleaning"
    assert "service_id" not in btc["args"]
