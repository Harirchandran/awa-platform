"""
Interactive Parser (Phase 9d)
Extracts AI-generated option hints (e.g. [Haircut] [Coloring] [Spa]) from
response text and converts them into WhatsApp-native interactive payloads.
Rules:
  - If ≤3 options found → WhatsApp Reply Buttons
  - If >3 options found → WhatsApp List (single section)
  - If no options found → returns None (caller sends plain text)
"""
import re
from typing import Optional, Tuple
# Regex pattern: matches text inside square brackets like [Option 1]
OPTION_PATTERN = re.compile(r'\[([^\[\]]{1,80})\]')
UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
INTERNAL_QUICK_REPLY_PATTERN = re.compile(
    r"(?i)(?:\b(?:action|action_id|intent|tool|tool_name|workflow_action|payload|internal|uuid|token|secret)\b|"
    r"\b[a-z][a-z0-9_]*\.(?:execute|confirm|cancel|update)\b|"
    r"\b[a-z][a-z0-9_]*_(?:execute|confirm|cancel|update)\b|"
    r"(?:<function=|\(function=|tool_call|toolregistry|system:|developer:))"
)
COMPLETION_CLAIM_PATTERN = re.compile(
    r"(?i)\b(?:booked|booking confirmed|order placed|order confirmed|payment paid|payment completed|"
    r"paid successfully|completed successfully|successfully completed|done successfully|"
    r"reminder scheduled|review submitted|ticket created)\b"
)
BUTTON_TITLE_LIMIT = 20
LIST_ROW_TITLE_LIMIT = 24
LIST_OPTION_LIMIT = 10
def normalize_option_label(option: str, max_length: int = BUTTON_TITLE_LIMIT) -> str:
    """Normalize a WhatsApp option label without creating empty/invalid titles."""
    cleaned = UUID_PATTERN.sub("", str(option or ""))
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip("[]")
    if not cleaned:
        return ""
    return cleaned[:max_length].strip()
def _quick_reply_dedupe_key(label: str) -> str:
    return re.sub(r"[\W_]+", "", str(label or "").casefold())
def _is_safe_quick_reply_label(label: str) -> bool:
    if not label:
        return False
    if UUID_PATTERN.search(label):
        return False
    if INTERNAL_QUICK_REPLY_PATTERN.search(label):
        return False
    if COMPLETION_CLAIM_PATTERN.search(label):
        return False
    if re.search(r"(?i)\b(?:id|action|intent|tool|payload)\s*[:=]", label):
        return False
    return True
def sanitize_quick_replies(options: list[str], max_options: int = LIST_OPTION_LIMIT) -> list[str]:
    """Sanitize, dedupe, and bound explicit quick-reply labels for channel payloads."""
    sanitized: list[str] = []
    seen = set()
    for option in options or []:
        max_length = BUTTON_TITLE_LIMIT if len(options or []) <= 3 else LIST_ROW_TITLE_LIMIT
        label = normalize_option_label(str(option or ""), max_length=max_length)
        if not _is_safe_quick_reply_label(label):
            continue
        key = _quick_reply_dedupe_key(label)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        sanitized.append(label)
        if len(sanitized) >= max_options:
            break
    return sanitized
def quick_reply_labels_from_text(response_text: str) -> list[str]:
    return sanitize_quick_replies(OPTION_PATTERN.findall(str(response_text or "")))
def _option_id(prefix: str, index: int, option: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", option.lower()).strip("_")[:24] or "option"
    return f"{prefix}_{index}_{slug}"
def build_plain_text_fallback(body_text: str, options: list[str]) -> str:
    """Return readable plain text when WhatsApp interactive delivery is unavailable."""
    body = (body_text or "Please choose an option:").strip()
    visible_options = [normalize_option_label(option, LIST_ROW_TITLE_LIMIT) for option in options]
    visible_options = [option for option in visible_options if option]
    if not visible_options:
        return body
    return f"{body}\n\nOptions: " + ", ".join(visible_options)
def strip_interactive_hints(response_text: str) -> str:
    clean_text = OPTION_PATTERN.sub('', str(response_text or "")).strip()
    clean_text = re.sub(r'[ \t]{2,}', ' ', clean_text)
    return re.sub(r'\n[ \t]*\n[ \t]*\n+', '\n\n', clean_text).strip()
def parse_interactive_hints(response_text: str, button_text: str = "View Options") -> Tuple[str, Optional[dict]]:
    """
    Parse response text for interactive option hints.
    Args:
        response_text: AI-generated response that may contain [option] hints
        button_text: WhatsApp list button label text
    Returns:
        Tuple of (clean_text, interactive_data or None)
        - clean_text: response with [brackets] removed
        - interactive_data: WhatsApp-compliant interactive payload, or None
    """
    matches = OPTION_PATTERN.findall(response_text)
    if not matches or len(matches) < 2:
        # No meaningful options found — send as plain text
        return response_text, None
    # Remove bracket options from the display text
    clean_text = strip_interactive_hints(response_text)
    # Deduplicate while preserving order
    seen = set()
    unique_options = []
    for opt in matches:
        opt_clean = re.sub(r"\s+", " ", str(opt or "")).strip().strip("[]")
        if opt_clean and opt_clean.lower() not in seen:
            seen.add(opt_clean.lower())
            unique_options.append(opt_clean)
    unique_options = [option for option in unique_options if _is_safe_quick_reply_label(option)][:LIST_OPTION_LIMIT]
    if len(unique_options) < 2:
        return response_text, None

    # Check if any option exceeds the limits (20 for buttons, 24 for lists)
    limit = BUTTON_TITLE_LIMIT if len(unique_options) <= 3 else LIST_ROW_TITLE_LIMIT
    if any(len(opt) > limit for opt in unique_options):
        # Gracefully degrade to a plain-text numbered list
        lines = [clean_text, ""] if clean_text else []
        for i, opt in enumerate(unique_options, 1):
            lines.append(f"{i}. {opt}")
        return "\n".join(lines).strip(), None

    if len(unique_options) <= 3:
        # WhatsApp Reply Buttons (max 3)
        interactive_data = _build_button_payload(clean_text, sanitize_quick_replies(unique_options, max_options=3))
    else:
        # WhatsApp List (for >3 options, max 10 rows)
        interactive_data = _build_list_payload(clean_text, sanitize_quick_replies(unique_options, max_options=LIST_OPTION_LIMIT), button_text=button_text)
    return clean_text, interactive_data
def build_interactive_payload_from_options(
    body_text: str,
    options: list[str],
    button_text: str = "View Options",
) -> Optional[dict]:
    """Build channel-compatible interactive data from explicit structured quick replies."""
    unique_options = sanitize_quick_replies(options)
    if len(unique_options) < 2:
        return None
    if len(unique_options) <= 3:
        return _build_button_payload(body_text, unique_options)
    return _build_list_payload(body_text, unique_options[:LIST_OPTION_LIMIT], button_text=button_text)
def _build_button_payload(body_text: str, options: list[str]) -> dict:
    """
    Build WhatsApp interactive button payload.
    Max 3 buttons, each button title max 20 chars.
    """
    buttons = []
    for i, option in enumerate(options[:3]):
        buttons.append({
            "type": "reply",
            "reply": {
                "id": _option_id("btn", i, option),
                "title": normalize_option_label(option, BUTTON_TITLE_LIMIT)
            }
        })
    return {
        "type": "button",
        "body": {"text": body_text or "Please choose an option:"},
        "action": {
            "buttons": buttons
        }
    }
def _build_list_payload(body_text: str, options: list[str], button_text: str = "View Options") -> dict:
    """
    Build WhatsApp interactive list payload.
    Max 10 rows per section, row title max 24 chars.
    """
    rows = []
    for i, option in enumerate(options[:10]):
        rows.append({
            "id": _option_id("list", i, option),
            "title": normalize_option_label(option, LIST_ROW_TITLE_LIMIT),
        })
    return {
        "type": "list",
        "body": {"text": body_text or "Please select from the options below:"},
        "action": {
            "button": button_text,
            "sections": [
                {
                    "title": "Options",
                    "rows": rows
                }
            ]
        }
    }
