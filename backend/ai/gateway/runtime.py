"""Lazy process-local access to the production gateway."""
from __future__ import annotations

from threading import Lock

from backend.ai.gateway.builder import build_llm_gateway
from backend.ai.gateway.gateway import LLMGateway
from backend.core.config import settings


_lock = Lock()
_gateway: LLMGateway | None = None


def get_runtime_llm_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        with _lock:
            if _gateway is None:
                _gateway = build_llm_gateway(settings)
    return _gateway


def reset_runtime_llm_gateway() -> None:
    """Explicit test/configuration reset; production callers never mutate the registry."""
    global _gateway
    with _lock:
        _gateway = None


__all__ = ["get_runtime_llm_gateway", "reset_runtime_llm_gateway"]
