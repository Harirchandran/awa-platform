"""Additive central LLM gateway foundation (not wired to production callers)."""

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.builder import build_gateway_registry, build_llm_gateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    CanonicalToolCall,
    CanonicalToolDefinition,
    CanonicalToolResult,
    LLMFallbackContract,
    LLMPurpose,
    LLMRequest,
    LLMResult,
    LLMTimeoutClass,
)
from backend.ai.gateway.registry import LLMProviderAdapter, LLMProviderRegistry

__all__ = [
    "CanonicalLLMMessage",
    "CanonicalToolCall",
    "CanonicalToolDefinition",
    "CanonicalToolResult",
    "LLMFallbackContract",
    "LLMGateway",
    "LLMProviderAdapter",
    "LLMProviderRegistry",
    "LLMPurpose",
    "LLMRequest",
    "LLMResult",
    "LLMTimeoutClass",
    "build_gateway_registry",
    "build_llm_gateway",
]
