import logging
import json
import re
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import contextvars
# Context Var for Trace ID propagation
_trace_id_ctx = contextvars.ContextVar("trace_id", default="unknown")
_tenant_id_ctx = contextvars.ContextVar("tenant_id", default="unknown")
class PIIMasker(logging.Filter):
    """
    Masks PII in log messages: phone numbers, emails, names, and message content.
    DPDP §8(4) / GDPR Art. 32: Security safeguards for personal data.
    """
    # Phone numbers
    PHONE_REGEX = re.compile(r'\b(?:\+?(\d{1,3}))?[-. (]*(\d{3})[-. )]*(\d{3})[-. ]*(\d{4})\b')
    # Email addresses
    EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    # Name patterns (after common labels)
    NAME_REGEX = re.compile(r'(?:name[=:]\s*|"name"\s*:\s*")([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', re.IGNORECASE)

    def mask_string(self, text: str) -> str:
        text = self.PHONE_REGEX.sub('[REDACTED_PHONE]', text)
        text = self.EMAIL_REGEX.sub('[REDACTED_EMAIL]', text)
        return self.NAME_REGEX.sub(
            lambda m: m.group(0).replace(m.group(1), '[REDACTED_NAME]'),
            text
        )

    def filter(self, record):
        path = str(getattr(record, "pathname", "") or "").replace("/", "\\").lower()
        legacy_llm_path = any(marker in path for marker in (
            "\\backend\\ai\\providers\\",
            "\\backend\\ai\\factory.py",
            "\\backend\\ai\\client.py",
            "\\backend\\ai\\rag_service.py",
            "\\backend\\ai\\prompt_registry.py",
            "\\backend\\ai\\prompt_resolver.py",
            "\\backend\\logic\\engine\\history.py",
            "\\backend\\observability\\provider_rate_limiter.py",
        ))
        if legacy_llm_path and not getattr(record, "llm_safe_event", False):
            # Old f-strings may already contain response bodies, URLs,
            # identifiers, or exception text. Replace the whole record.
            record.msg = "legacy_llm_log_redacted"
            record.args = ()
            record.exc_info = None
            record.stack_info = None
            record.extra_data = {
                "schema_version": "1",
                "event": "legacy_llm_log_redacted",
                "outcome": "error" if record.levelno >= logging.WARNING else "success",
            }
            record.llm_safe_event = True
        if isinstance(record.msg, str):
            record.msg = self.mask_string(record.msg)
        return True

class JsonFormatter(logging.Formatter):
    def format(self, record):
        safe_llm_event = bool(getattr(record, "llm_safe_event", False))
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(), # Applied after filter
            "worker_version": getattr(record, "worker_version", "unknown")
        }
        if not safe_llm_event:
            log_record["trace_id"] = _trace_id_ctx.get()
            log_record["tenant_id"] = _tenant_id_ctx.get()
        # Merge extra fields — also mask PII in extra_data
        if hasattr(record, "extra_data"):
            extra = record.extra_data
            if isinstance(extra, dict):
                masker = PIIMasker()
                for key, value in extra.items():
                    if isinstance(value, str):
                        extra[key] = masker.mask_string(value)
            log_record.update(extra)
        return json.dumps(log_record)
def setup_logger():
    logger = logging.getLogger("awa_backend")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(PIIMasker())
    logger.addHandler(handler)
    return logger
logger = setup_logger()
def set_trace_context(trace_id: str, tenant_id: str):
    _trace_id_ctx.set(str(trace_id))
    _tenant_id_ctx.set(str(tenant_id))
