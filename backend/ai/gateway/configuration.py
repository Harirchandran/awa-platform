"""Platform-owned provider configuration for the additive LLM gateway."""
from __future__ import annotations

from dataclasses import dataclass

from backend.core.config import Settings


_DEFAULT_MODELS = {
    "gemini": "gemini-3.1-flash-lite",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
}


@dataclass(frozen=True)
class ResolvedProviderConfig:
    provider: str
    model: str
    api_key_present: bool
    base_url: str | None = None


def _compatible(provider: str, model: str) -> bool:
    value = model.strip().lower()
    if not value:
        return False
    if provider == "gemini":
        return value.startswith(("gemini-", "models/gemini-"))
    if provider == "groq":
        return value.startswith(("llama", "mixtral", "gemma", "qwen", "deepseek", "openai/"))
    if provider == "openai":
        return value.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))
    return False


def resolve_provider_config(settings: Settings, provider: str) -> ResolvedProviderConfig:
    name = str(provider or "").strip().lower()
    if name not in _DEFAULT_MODELS:
        raise ValueError("unknown gateway provider")

    gateway_model = str(getattr(settings, f"LLM_GATEWAY_{name.upper()}_MODEL", "") or "").strip()
    if gateway_model and not _compatible(name, gateway_model):
        raise ValueError(f"incompatible gateway model configured for {name}")
    agent_model = str(settings.AGENT_LLM_MODEL or "").strip()
    legacy_field = {"gemini": "GEMINI_MODEL", "groq": "GROQ_MODEL", "openai": "OPENAI_MODEL"}[name]
    legacy_model = str(getattr(settings, legacy_field, "") or "").strip()
    model = (
        gateway_model
        or (agent_model if _compatible(name, agent_model) else "")
        or (legacy_model if _compatible(name, legacy_model) else "")
        or _DEFAULT_MODELS[name]
    )
    key = getattr(settings, f"{name.upper()}_API_KEY", None)
    base_url = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "groq": "https://api.groq.com/openai/v1",
        "openai": None,
    }[name]
    return ResolvedProviderConfig(
        provider=name,
        model=model,
        api_key_present=bool(str(key or "").strip()),
        base_url=base_url,
    )


def provider_api_key(settings: Settings, provider: str) -> str | None:
    """Return a key only at the construction boundary; never place it in resolved models."""
    value = getattr(settings, f"{provider.upper()}_API_KEY", None)
    return str(value).strip() if value else None


__all__ = ["ResolvedProviderConfig", "resolve_provider_config"]
