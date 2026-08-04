import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.core.config import Settings


ROOT = Path(__file__).resolve().parents[3]


def test_gateway_configuration_defaults_are_authoritative_and_valid():
    settings = Settings(_env_file=None)
    assert settings.LLM_GATEWAY_ENABLED is True
    assert settings.LLM_GATEWAY_SHADOW_MODE is False
    assert settings.LLM_GATEWAY_CORE_AGENT_MODE == "authoritative"
    assert settings.LLM_GATEWAY_RESPONSE_COMPOSER_MODE == "authoritative"
    assert settings.LLM_GATEWAY_FIELD_EXTRACTION_MODE == "authoritative"
    assert settings.LLM_GATEWAY_PENDING_DECISION_MODE == "authoritative"
    assert settings.llm_gateway_field_extraction_workflows == (
        "review", "support", "lead_capture", "reminder", "booking", "order", "payment",
    )
    assert settings.llm_gateway_fallback_order == ("gemini", "groq", "openai")


@pytest.mark.parametrize("order", ["", "gemini,gemini", "gemini,anthropic", "fake,groq"])
def test_gateway_configuration_rejects_empty_duplicate_unknown_and_fake_orders(order):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_FALLBACK_ORDER=order)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _module_level_imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def test_authoritative_callers_do_not_own_legacy_constructors_at_module_scope():
    for relative in (
        "backend/logic/engine/core.py",
        "backend/logic/engine/field_extraction.py",
        "backend/logic/engine/pending_decision.py",
        "backend/logic/engine/response_localization.py",
        "backend/logic/engine/core_agent_gateway.py",
        "backend/logic/engine/field_extraction_gateway.py",
        "backend/logic/engine/pending_decision_gateway.py",
        "backend/logic/engine/response_composer.py",
    ):
        imported = _module_level_imported_names(ROOT / relative)
        assert "get_llm" not in imported, relative
        assert "run_provider_fallback" not in imported, relative
        assert "LLMFactory" not in imported, relative


def test_legacy_ai_client_has_no_eager_global_instance():
    tree = ast.parse((ROOT / "backend/ai/client.py").read_text(encoding="utf-8"))
    eager = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(getattr(node, "value", None), ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "AIClient"
    ]
    assert eager == []


def test_gateway_boundary_has_no_forbidden_reverse_dependencies():
    protected_files = [
        ROOT / "backend/tools/registry.py",
        ROOT / "backend/logic/engine/canonical_control.py",
        ROOT / "backend/api/routes/public_chat.py",
    ]
    protected_files.extend((ROOT / "backend/domains").glob("*/tool_handlers.py"))
    protected_files.extend((ROOT / "backend/domains").glob("*/service.py"))
    for path in protected_files:
        assert not any(name.startswith("backend.ai.gateway") for name in _imports(path)), path


def test_gateway_production_package_does_not_import_workflow_domain_or_tool_registry():
    gateway_dir = ROOT / "backend/ai/gateway"
    forbidden_prefixes = (
        "backend.logic.engine",
        "backend.domains",
        "backend.tools",
        "backend.capabilities",
    )
    for path in gateway_dir.glob("*.py"):
        imports = _imports(path)
        assert not any(name.startswith(forbidden_prefixes) for name in imports), path
