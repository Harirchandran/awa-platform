from __future__ import annotations

import ast
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from backend.ai.gateway.runtime import reset_runtime_llm_gateway
from backend.ai.gateway.telemetry import reset_configured_llm_telemetry_recorder
from backend.core.config import Settings, settings
from backend.logic.engine import field_extraction
from backend.logic.engine.core_agent_gateway import (
    CoreAgentGatewayMode,
    invoke_core_agent_model,
    resolve_core_agent_gateway_mode,
)
from backend.logic.engine.field_extraction_gateway import resolve_field_extraction_gateway_mode
from backend.logic.engine.pending_decision import decide_pending_action
from backend.logic.engine.pending_decision_gateway import resolve_pending_decision_gateway_mode
from backend.logic.engine.response_composer import (
    ResponseCompositionInput,
    ResponseKind,
    compose_customer_response,
    resolve_response_composer_mode,
)
from backend.logic.engine.response_localization import localize_customer_response
from backend.tests.unit.test_booking_order_payment_extraction_gateway_migration import (
    CapturingGateway as TransactionGateway,
    WORKFLOWS as TRANSACTION_WORKFLOWS,
    _context as transaction_context,
    _payload as transaction_payload,
    _valid_updates as transaction_updates,
)
from backend.tests.unit.test_pending_decision_gateway_migration import (
    CapturingGateway as PendingGateway,
    _context as pending_context,
)
from backend.tests.unit.test_review_extraction_gateway_migration import (
    CapturingGateway as ReviewGateway,
    _context as review_context,
    _payload as review_payload,
)
from backend.tests.unit.test_support_lead_reminder_extraction_gateway_migration import (
    CapturingGateway as OperationalGateway,
    WORKFLOWS as OPERATIONAL_WORKFLOWS,
    _context as operational_context,
    _payload as operational_payload,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKFLOWS = (
    "review", "support", "lead_capture", "reminder", "booking", "order", "payment",
)


@pytest.fixture(autouse=True)
def authoritative_runtime(monkeypatch):
    defaults = Settings(_env_file=None)
    for name in (
        "LLM_GATEWAY_ENABLED",
        "LLM_GATEWAY_SHADOW_MODE",
        "LLM_GATEWAY_CORE_AGENT_MODE",
        "LLM_GATEWAY_RESPONSE_COMPOSER_MODE",
        "LLM_GATEWAY_FIELD_EXTRACTION_MODE",
        "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS",
        "LLM_GATEWAY_PENDING_DECISION_MODE",
    ):
        monkeypatch.setattr(settings, name, getattr(defaults, name))
    reset_runtime_llm_gateway()
    reset_configured_llm_telemetry_recorder()
    yield
    reset_runtime_llm_gateway()
    reset_configured_llm_telemetry_recorder()


def test_all_default_mode_resolvers_are_authoritative():
    defaults = Settings(_env_file=None)
    assert defaults.LLM_GATEWAY_ENABLED is True
    assert defaults.LLM_GATEWAY_SHADOW_MODE is False
    assert defaults.llm_gateway_field_extraction_workflows == DEFAULT_WORKFLOWS
    assert resolve_core_agent_gateway_mode(defaults).value == "authoritative"
    assert resolve_pending_decision_gateway_mode(defaults).value == "authoritative"
    assert resolve_response_composer_mode(defaults).value == "authoritative"
    for workflow in DEFAULT_WORKFLOWS:
        assert resolve_field_extraction_gateway_mode(defaults, workflow).value == "authoritative"


def test_master_kill_switch_and_purpose_legacy_modes_are_explicit():
    defaults = Settings(_env_file=None)
    disabled = defaults.model_copy(update={"LLM_GATEWAY_ENABLED": False})
    assert resolve_core_agent_gateway_mode(disabled).value == "legacy"
    assert resolve_pending_decision_gateway_mode(disabled).value == "legacy"
    assert resolve_response_composer_mode(disabled).value == "legacy"
    assert resolve_field_extraction_gateway_mode(disabled, "booking").value == "legacy"

    assert resolve_core_agent_gateway_mode(defaults.model_copy(
        update={"LLM_GATEWAY_CORE_AGENT_MODE": "legacy"}
    )).value == "legacy"
    assert resolve_pending_decision_gateway_mode(defaults.model_copy(
        update={"LLM_GATEWAY_PENDING_DECISION_MODE": "legacy"}
    )).value == "legacy"
    assert resolve_response_composer_mode(defaults.model_copy(
        update={"LLM_GATEWAY_RESPONSE_COMPOSER_MODE": "legacy"}
    )).value == "legacy"
    assert resolve_field_extraction_gateway_mode(defaults.model_copy(
        update={"LLM_GATEWAY_FIELD_EXTRACTION_MODE": "legacy"}
    ), "booking").value == "legacy"


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", tuple(TRANSACTION_WORKFLOWS))
async def test_default_transaction_extraction_never_constructs_legacy(monkeypatch, workflow):
    monkeypatch.setattr(
        field_extraction,
        "_legacy_llm_or_default",
        lambda _injected: (_ for _ in ()).throw(AssertionError("legacy constructed")),
    )
    function = TRANSACTION_WORKFLOWS[workflow][0]
    gateway = TransactionGateway(transaction_payload(workflow, transaction_updates(workflow)))
    result = await function(transaction_context(workflow), gateway=gateway)
    assert len(gateway.requests) == 1
    assert result.workflow == workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", tuple(OPERATIONAL_WORKFLOWS))
async def test_default_operational_extraction_never_constructs_legacy(monkeypatch, workflow):
    monkeypatch.setattr(
        field_extraction,
        "_legacy_llm_or_default",
        lambda _injected: (_ for _ in ()).throw(AssertionError("legacy constructed")),
    )
    function = OPERATIONAL_WORKFLOWS[workflow][0]
    gateway = OperationalGateway(operational_payload(workflow, {}))
    result = await function(operational_context(workflow), gateway=gateway)
    assert len(gateway.requests) == 1
    assert result.workflow == workflow


@pytest.mark.asyncio
async def test_default_review_and_pending_decision_never_construct_legacy(monkeypatch):
    monkeypatch.setattr(
        field_extraction,
        "_legacy_llm_or_default",
        lambda _injected: (_ for _ in ()).throw(AssertionError("legacy constructed")),
    )
    review_gateway = ReviewGateway(review_payload(rating=5, feedback="Good"))
    review = await field_extraction.extract_review_fields(
        review_context(focus=("rating", "feedback")), gateway=review_gateway
    )
    assert review.field_updates == {"rating": 5, "feedback": "Good"}

    pending_gateway = PendingGateway()
    decision = await decide_pending_action(
        pending_context(), gateway=pending_gateway,
        legacy_llm=type("ForbiddenLegacy", (), {"ainvoke": lambda *_: pytest.fail("legacy invoked")})(),
    )
    assert decision.action == "confirm"
    assert len(pending_gateway.requests) == 1


class FailingGateway:
    def __init__(self):
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        raise RuntimeError("scripted gateway exhaustion")


class ForbiddenLegacy:
    async def ainvoke(self, _messages):
        raise AssertionError("authoritative failure must not invoke legacy")


@pytest.mark.asyncio
async def test_authoritative_exhaustion_uses_deterministic_fallbacks_only():
    gateway = FailingGateway()
    core = await invoke_core_agent_model(
        [SystemMessage(content="bounded"), HumanMessage(content="hello")],
        [],
        mode=CoreAgentGatewayMode.AUTHORITATIVE,
        authoritative_tool_result=False,
        fallback_text="safe core fallback",
        legacy_model=ForbiddenLegacy(),
        gateway=gateway,
    )
    assert core.content == "safe core fallback"

    legacy_localization_calls = 0

    async def forbidden_localization(_payload):
        nonlocal legacy_localization_calls
        legacy_localization_calls += 1
        raise AssertionError("legacy localization invoked")

    localized = await localize_customer_response(
        "Booking is pending.",
        ["Confirm", "Cancel"],
        {"target_language": "hi", "workflow": "booking"},
        provider=forbidden_localization,
        gateway=FailingGateway(),
    )
    assert localized.response_text == "Booking is pending."
    assert localized.localized is False
    assert legacy_localization_calls == 0

    composed = await compose_customer_response(
        ResponseCompositionInput(
            base_text="Please confirm Cleaning.",
            response_kind=ResponseKind.PENDING_CONFIRMATION,
            safe_facts={"workflow": "booking"},
            quick_replies=["Confirm", "Cancel"],
            required_terms=["confirm"],
            protected_terms=["Cleaning", "Confirm", "Cancel"],
            protected_numeric_tokens=[],
            allow_completion_claims=False,
            allow_failure_claims=False,
            target_language="en",
        ),
        gateway=FailingGateway(),
    )
    assert composed.response_text == "Please confirm Cleaning."
    assert composed.composed is False


def test_direct_chat_constructors_are_bounded_to_adapters_and_legacy_compatibility():
    allowed = {
        "ChatOpenAI": {
            "backend/logic/engine/llm.py",
            "backend/ai/gateway/providers/gemini.py",
            "backend/ai/gateway/providers/groq.py",
            "backend/ai/gateway/providers/openai.py",
        },
        "ChatGroq": {"backend/logic/engine/llm.py"},
        "GeminiChatOpenAI": {
            "backend/logic/engine/llm.py",
            "backend/ai/gateway/providers/gemini.py",
        },
        "AsyncOpenAI": {
            "backend/ai/rag_service.py",
            "backend/ai/providers/openai_provider.py",
            "backend/ai/providers/ollama_provider.py",
        },
    }
    found = {name: set() for name in allowed}
    for path in (ROOT / "backend").rglob("*.py"):
        if "tests" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in found:
                    found[node.func.id].add(relative)
    for constructor, paths in found.items():
        assert paths <= allowed[constructor], (constructor, paths - allowed[constructor])


def test_embeddings_are_the_only_default_non_chat_sdk_exemption():
    path = ROOT / "backend/ai/rag_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    embed_calls = [
        node for node in ast.walk(methods["embed_query"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AsyncOpenAI"
    ]
    assert len(embed_calls) == 1
    assert "search_text" in {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
