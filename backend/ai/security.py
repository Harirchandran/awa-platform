import re
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_UNTRUSTED_LINE_CHARS = 600
REDACTED_UNTRUSTED_INSTRUCTION = "[redacted prompt-control instruction]"
SECURITY_REFUSAL_MESSAGE = (
    "I can't share internal instructions, developer messages, secrets, or tool details. "
    "I can still help with this business's services, bookings, orders, or support."
)
PROMPT_SECURITY_PATTERNS = (
    re.compile(r"\b(ignore|forget|disregard|override)\s+(all\s+)?(previous|prior|above|system|developer)\s+instructions\b", re.IGNORECASE),
    re.compile(r"\b(reveal|show|print|leak|dump|display|repeat)\s+(the\s+)?(system|developer|hidden|internal)\s+(prompt|message|instructions|rules)\b", re.IGNORECASE),
    re.compile(r"\b(reveal|show|print|leak|dump|display|repeat)\s+(the\s+)?(hidden|internal)\s+(system|developer)\s+(prompt|message|instructions|rules)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(is|are)\s+(your\s+)?(system|developer|hidden|internal)\s+(prompt|message|instructions|rules)\b", re.IGNORECASE),
    re.compile(r"(^|\n)\s*(system|developer|assistant|tool)\s*:", re.IGNORECASE),
    re.compile(r"\b(jailbreak|prompt injection|dan mode|bypass safety|bypass policy)\b", re.IGNORECASE),
    re.compile(r"\b(exfiltrate|steal)\s+(data|secrets|api keys|tokens)\b", re.IGNORECASE),
    re.compile(r"\b(call|run|execute|use)\s+(the\s+|a\s+)?(disabled|hidden|unauthorized|forbidden)(\s+[a-z0-9_.-]+){0,4}\s+(tool|function|action)\b", re.IGNORECASE),
    re.compile(r"\b(without|skip|bypass|ignore)\s+(confirmation|customer verification|verification|approval)\b", re.IGNORECASE),
    re.compile(r"\b(mark|set)\s+(the\s+)?(payment|invoice|bill)\s+(as\s+)?(paid|refunded|failed)\b", re.IGNORECASE),
)
def is_prompt_security_request(message: str) -> bool:
    text = (message or "").strip()
    return bool(text and any(pattern.search(text) for pattern in PROMPT_SECURITY_PATTERNS))
def sanitize_untrusted_text(text: object, max_chars: int = 4000) -> str:
    """Clean customer, tenant, or knowledge text before adding it to a prompt."""
    raw = str(text or "")
    raw = re.sub(r"(?i)BEGIN_UNTRUSTED_CONTEXT|END_UNTRUSTED_CONTEXT", "[REDACTED]", raw)
    raw = CONTROL_CHAR_PATTERN.sub("", raw)[:max_chars]
    safe_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            safe_lines.append("")
            continue
        if is_prompt_security_request(stripped):
            safe_lines.append(REDACTED_UNTRUSTED_INSTRUCTION)
            continue
        if len(line) > MAX_UNTRUSTED_LINE_CHARS:
            line = f"{line[:MAX_UNTRUSTED_LINE_CHARS]}..."
        safe_lines.append(line)
    return "\n".join(safe_lines).strip()
def format_untrusted_context_block(label: str, text: object, max_chars: int = 4000) -> str:
    """Delimiter-wrap untrusted context so the LLM treats it as data, not instructions."""
    sanitized = sanitize_untrusted_text(text, max_chars=max_chars)
    if not sanitized:
        return ""
    quoted = "\n".join(f"> {line}" if line else ">" for line in sanitized.splitlines())
    return (
        f"{label}\n"
        "BEGIN_UNTRUSTED_CONTEXT\n"
        f"{quoted}\n"
        "END_UNTRUSTED_CONTEXT"
    )
