"""Typed, testable provider capability declarations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    text_generation: bool = True
    structured_json_prompting: bool = True
    canonical_tools: bool = True
    system_messages: bool = True
    assistant_tool_history: bool = True
    usage_metadata: bool = True
    provider_native_metadata: bool = False
    tool_model_prefixes: tuple[str, ...] = ()

    def supports_tools_for(self, model: str) -> bool:
        if not self.canonical_tools:
            return False
        return not self.tool_model_prefixes or model.lower().startswith(self.tool_model_prefixes)


__all__ = ["ProviderCapabilities"]
