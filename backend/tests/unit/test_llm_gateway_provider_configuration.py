import ast
from pathlib import Path

import pytest

from backend.ai.gateway.builder import build_gateway_registry, build_llm_gateway
from backend.ai.gateway.configuration import resolve_provider_config
from backend.ai.gateway.providers.gemini import GeminiGatewayAdapter
from backend.ai.gateway.providers.groq import GroqGatewayAdapter
from backend.ai.gateway.providers.openai import OpenAIGatewayAdapter
from backend.core.config import Settings


ROOT = Path(__file__).resolve().parents[3]


def test_provider_model_precedence_and_incompatible_agent_model_isolation():
    settings = Settings(
        _env_file=None,
        AGENT_LLM_MODEL="gpt-4o",
        GEMINI_MODEL="gemini-2.5-flash",
        GROQ_MODEL="llama-3.3-70b-versatile",
        OPENAI_MODEL="gpt-4o-mini",
        LLM_GATEWAY_GEMINI_MODEL="gemini-3.1-flash-lite",
    )
    assert resolve_provider_config(settings, "gemini").model == "gemini-3.1-flash-lite"
    assert resolve_provider_config(settings, "groq").model == "llama-3.3-70b-versatile"
    assert resolve_provider_config(settings, "openai").model == "gpt-4o"

    with pytest.raises(ValueError, match="incompatible"):
        resolve_provider_config(Settings(_env_file=None, LLM_GATEWAY_GEMINI_MODEL="gpt-4o"), "gemini")


def test_resolved_configuration_exposes_presence_but_never_key_value():
    secret = "super-secret-provider-key"
    settings = Settings(_env_file=None, GEMINI_API_KEY=secret)
    resolved = resolve_provider_config(settings, "gemini")
    assert resolved.api_key_present is True
    assert secret not in repr(resolved)
    assert secret not in repr(settings)


def test_registry_is_deterministic_lazy_and_missing_keys_do_not_crash_build():
    settings = Settings(_env_file=None, LLM_FALLBACK_ORDER="openai,gemini,groq")
    registry = build_gateway_registry(settings)
    assert registry.registered_names() == ("openai", "gemini", "groq")
    assert isinstance(registry.get("openai"), OpenAIGatewayAdapter)
    assert isinstance(registry.get("gemini"), GeminiGatewayAdapter)
    assert isinstance(registry.get("groq"), GroqGatewayAdapter)
    assert all(registry.get(name).client_construction_count == 0 for name in registry.registered_names())


def test_builder_injection_is_instance_owned_and_gateway_disabled_is_not_a_build_blocker():
    settings = Settings(_env_file=None, LLM_GATEWAY_ENABLED=False)
    override = GeminiGatewayAdapter(model_name="gemini-test", api_key="test", client_factory=lambda timeout: None)
    first = build_llm_gateway(settings, adapter_overrides={"gemini": override})
    second = build_llm_gateway(settings)
    assert first.registry.get("gemini") is override
    assert second.registry.get("gemini") is not override
    assert settings.LLM_GATEWAY_ENABLED is False


def test_adapter_override_is_rejected_when_real_provider_execution_is_required():
    class FakeGeminiAdapter:
        provider_name = "gemini"
        model_name = "fake"

    settings = Settings(_env_file=None, AWA_INTEGRATION_MODE="live")
    with pytest.raises(ValueError, match="adapter overrides are prohibited"):
        build_gateway_registry(settings, adapter_overrides={"gemini": FakeGeminiAdapter()})


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_gateway_adapters_have_no_forbidden_domain_dependencies_and_sdk_imports_are_bounded():
    forbidden = ("backend.logic.engine", "backend.domains", "backend.tools", "backend.capabilities", "backend.db")
    for path in (ROOT / "backend/ai/gateway/providers").glob("*.py"):
        imports = _imports(path)
        assert not any(name.startswith(forbidden) for name in imports), path
    active_roots = [ROOT / "backend/logic", ROOT / "backend/api", ROOT / "backend/workers"]
    for root in active_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            imports = _imports(path)
            assert "backend.ai.gateway.builder" not in imports, path


def test_legacy_get_llm_and_factory_remain_available_without_gateway_imports():
    engine_path = ROOT / "backend/logic/engine/llm.py"
    factory_path = ROOT / "backend/ai/factory.py"
    assert "get_llm" in {node.name for node in ast.walk(ast.parse(engine_path.read_text(encoding="utf-8"))) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "LLMFactory" in {node.name for node in ast.walk(ast.parse(factory_path.read_text(encoding="utf-8"))) if isinstance(node, ast.ClassDef)}
    assert not any(name.startswith("backend.ai.gateway") for name in _imports(engine_path))
    assert not any(name.startswith("backend.ai.gateway") for name in _imports(factory_path))
