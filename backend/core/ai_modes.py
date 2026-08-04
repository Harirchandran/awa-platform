from __future__ import annotations

from typing import Any, Iterable

AI_MODE_STANDARD = "standard"
AI_MODE_ADVANCED = "advanced"
LEGACY_AI_MODE_CONCIERGE = "concierge"

CANONICAL_AI_MODES = (AI_MODE_STANDARD, AI_MODE_ADVANCED)
LEGACY_AI_MODE_ALIASES = {
    LEGACY_AI_MODE_CONCIERGE: AI_MODE_ADVANCED,
}
VALID_AI_MODE_INPUTS = frozenset((*CANONICAL_AI_MODES, *LEGACY_AI_MODE_ALIASES))


def normalize_ai_mode(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in LEGACY_AI_MODE_ALIASES:
        return LEGACY_AI_MODE_ALIASES[cleaned]
    if cleaned in CANONICAL_AI_MODES:
        return cleaned
    return AI_MODE_STANDARD


def normalize_known_ai_mode(value: Any) -> str | None:
    cleaned = str(value or "").strip().lower()
    if cleaned in LEGACY_AI_MODE_ALIASES:
        return LEGACY_AI_MODE_ALIASES[cleaned]
    if cleaned in CANONICAL_AI_MODES:
        return cleaned
    return None


def is_known_ai_mode_input(value: Any) -> bool:
    return normalize_known_ai_mode(value) is not None


def normalize_allowed_ai_modes(value: Iterable[Any] | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    modes: list[str] = []
    for item in value:
        mode = normalize_known_ai_mode(item)
        if mode and mode not in modes:
            modes.append(mode)
    return tuple(modes) or default
