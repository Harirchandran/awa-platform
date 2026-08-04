import contextvars
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# ContextVar to hold the active request/message latency metrics dictionary
# Initialized as None; set at the start of worker message processing loop
latency_metrics_var: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "latency_metrics", default=None
)

ALLOWED_METRICS_KEYS = {
    "queue_wait_ms",
    "worker_processing_ms",
    "provider_latency_ms",
    "provider_llm_call_count",
    "provider_input_estimated_tokens",
    "provider_total_estimated_tokens",
    "tool_execution_ms",
    "db_latency_ms",
    "response_publish_ms",
    "end_to_end_response_ms",
    "queue_depth_before",
    "queue_depth_after",
    "active_worker_count",
    "active_session_locks",
    "provider_throttle_wait_ms",
    "session_lock_wait_ms",
    "session_lock_held_ms",
    "session_lock_acquired",
    "session_lock_timeout_count",
    "rate_limiter_configured_rpm",
    "rate_limiter_effective_rpm",
    "rate_limiter_safety_margin_percent",
    "rate_limiter_burst_cap",
    "provider_calls_allowed",
    "provider_calls_denied",
    "provider_wait_timeouts",
    "provider_actual_429s",
}

def now_ms() -> float:
    """Return a monotonic millisecond timestamp for duration measurements."""
    return time.monotonic_ns() / 1_000_000.0

def elapsed_ms(start_ms: float) -> int:
    """Return the elapsed time in milliseconds since start_ms."""
    delta = (time.monotonic_ns() / 1_000_000.0) - start_ms
    return max(0, int(delta))

def wall_time_ms() -> int:
    """Return the current wall-clock epoch in milliseconds."""
    return int(time.time() * 1000)

def utc_now_iso() -> str:
    """Return the current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()

def safe_int_ms(value: Any) -> Optional[int]:
    """Safely convert a value to integer milliseconds, returning None on failure."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return int(value)
        return int(float(value))
    except (TypeError, ValueError):
        return None

def merge_latency_metrics(existing: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safely merge new metrics into an existing dictionary.
    Ensures keys present in both are summed if they are numeric, or overwritten.
    Only allows whitelisted keys.
    """
    result = redact_metrics_payload(existing or {})
    redacted_new = redact_metrics_payload(new)
    for key, val in redacted_new.items():
        if val is None:
            continue
        curr = result.get(key)
        if curr is not None and isinstance(curr, (int, float)) and isinstance(val, (int, float)):
            result[key] = curr + val
        else:
            result[key] = val
    return result

def redact_metrics_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter a metrics dictionary to contain ONLY whitelisted metric keys.
    Prevents any secrets, prompts, or tenant data leakage.
    """
    if not isinstance(payload, dict):
        return {}
    redacted = {}
    for key in ALLOWED_METRICS_KEYS:
        if key in payload:
            val = payload[key]
            if val is not None:
                val_int = safe_int_ms(val)
                if val_int is not None:
                    redacted[key] = val_int
    return redacted
