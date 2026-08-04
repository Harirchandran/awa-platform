"""Central bounded structured-output parsing."""
from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.ai.gateway.errors import LLMStructuredOutputError
from backend.ai.gateway.models import MAX_STRUCTURED_OUTPUT_CHARS


T = TypeVar("T", bound=BaseModel)


def _strip_single_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        raise LLMStructuredOutputError(safe_metadata={"reason": "invalid_fence"})
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        raise LLMStructuredOutputError(safe_metadata={"reason": "unsupported_fence"})
    return "\n".join(lines[1:-1]).strip()


def parse_structured_output(raw_content: str | dict[str, Any], schema: type[T]) -> T:
    try:
        if isinstance(raw_content, dict):
            payload: Any = raw_content
            encoded_size = len(json.dumps(payload, ensure_ascii=True, allow_nan=False))
        elif isinstance(raw_content, str):
            if len(raw_content) > MAX_STRUCTURED_OUTPUT_CHARS:
                raise LLMStructuredOutputError(safe_metadata={"reason": "oversized_output"})
            text = _strip_single_json_fence(raw_content)
            payload = json.loads(text)
            encoded_size = len(text)
        else:
            raise LLMStructuredOutputError(safe_metadata={"reason": "unsupported_output_type"})
        if encoded_size > MAX_STRUCTURED_OUTPUT_CHARS:
            raise LLMStructuredOutputError(safe_metadata={"reason": "oversized_output"})
        if not isinstance(payload, dict):
            raise LLMStructuredOutputError(safe_metadata={"reason": "not_an_object"})
        return schema.model_validate(payload)
    except LLMStructuredOutputError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        raise LLMStructuredOutputError(safe_metadata={"reason": "validation_failed"}) from exc
