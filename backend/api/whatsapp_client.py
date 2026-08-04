"""
WhatsApp Outbound API Client — Response Dispatch (Part VI §31)
Sends outbound messages via Meta Cloud API.
"""
import re
import httpx
from typing import Optional
from uuid import uuid4
from backend.core.config import settings
from backend.ops.logger import logger

def format_confirmation_tags(text: str) -> str:
    if not text:
        return text
    def replace_confirmation(match):
        content = match.group(1).strip()
        if not content:
            return ""
        lines = []
        for line in content.splitlines():
            line_str = line.strip()
            if line_str:
                # Strip leading list markers if present (e.g. - or * or •)
                line_str = re.sub(r'^[-\*\u2022\u25E6\u25AA]\s*', '', line_str)
                if line_str:
                    if ":" in line_str:
                        parts = line_str.split(":", 1)
                        prefix = parts[0].strip().upper()
                        val = parts[1].strip()
                        lines.append(f"• **{prefix}:** {val}")
                    else:
                        lines.append(f"• {line_str}")
        if not lines:
            return ""
        divider = "────────────────"
        return f"\n\n**{divider}**\n**CONFIRMATION DETAILS:**\n" + "\n".join(lines) + f"\n**{divider}**\n\n"

    # Replace <confirmation>...</confirmation> blocks using regex
    return re.sub(r'<confirmation>(.*?)</confirmation>', replace_confirmation, text, flags=re.DOTALL)

def format_markdown_for_whatsapp(text: str) -> str:
    """
    Format standard Markdown formatting to WhatsApp-compatible styling rules.
    - Convert # Heading structures to *HEADING* (uppercase) with clean double newlines
    - Convert **bold** and __bold__ to *bold*
    - Convert *italic* and _italic_ to _italic_
    - Convert markdown links [Text](url) to Text: url
    - Handle nested bullet lists and normalize bullets to unicode '•'
    - Handle table formatting fallback by converting tables into clean mobile-friendly lists
    - Strip language specifiers from triple backtick code blocks (e.g. ```python -> ```)
    - Clean up excess consecutive blank lines
    """
    if not text:
        return text

    # Format XML confirmation tags
    text = format_confirmation_tags(text)

    # 1. Convert italics: *italic* -> _italic_
    text = re.sub(r"(?<![a-zA-Z0-9_*])\*([^* \t\n][^*\n]*[^* \t\n]?)\*(?![a-zA-Z0-9_*])", r"_\1_", text)
    text = re.sub(r"(?<![a-zA-Z0-9_*])_([^_ \t\n][^_]*[^_ \t\n]?)_(?![a-zA-Z0-9_*])", r"_\1_", text)

    # 2. Convert headers: e.g. # Heading -> *HEADING*
    def replace_header(match):
        header_text = match.group(1).strip().upper()
        return f"\n\n*{header_text}*\n\n"
    text = re.sub(r"^[ \t]*#{1,6}\s+(.+)$", replace_header, text, flags=re.MULTILINE)

    # 3. Convert bold: **bold** -> *bold*
    text = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", text)

    # 4. Convert bold: __bold__ -> *bold*
    text = re.sub(r"__([^_]+)__", r"*\1*", text)

    # 5. Convert markdown links: [Text](url) -> Text: url
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1: \2", text)

    # 6. Clean code block language specifiers: ```python -> ```
    text = re.sub(r"```[a-zA-Z0-9+-]+\s*", "```\n", text)

    # 7. Handle table delimiter lines (e.g. |---|---|)
    text = re.sub(r"^[ \t]*\|[\s|:-]+\|[ \t]*$\n?", "", text, flags=re.MULTILINE)

    # 8. Process line-by-line for table conversion and list indentation preservation
    lines = text.splitlines()
    in_table = False
    table_headers = []
    processed_lines = []

    for line in lines:
        stripped = line.strip()

        # Check if line is a table row (starts and ends with | after stripping, or has multiple pipes)
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 2:
            in_table = True
            cols = [c.strip() for c in stripped.split("|")[1:-1]]
            if not table_headers:
                table_headers = cols
                processed_lines.append("*" + " | ".join(cols).upper() + "*")
            else:
                row_items = []
                for idx, col in enumerate(cols):
                    header = table_headers[idx] if idx < len(table_headers) else f"Col {idx+1}"
                    row_items.append(f"{header}: {col}")
                processed_lines.append("• " + ", ".join(row_items))
        else:
            if in_table:
                in_table = False
                table_headers = []

            # Match bullet points: optional spaces + (- or * or •) + content
            bullet_match = re.match(r"^([ \t]*)([-*•])\s+(.+)$", line)
            if bullet_match:
                indent = bullet_match.group(1) or ""
                content = bullet_match.group(3)
                processed_lines.append(f"{indent}• {content}")
            else:
                # Preserve list indentation for numbered lists
                numbered_match = re.match(r"^([ \t]*)(\d+\.)\s+(.+)$", line)
                if numbered_match:
                    indent = numbered_match.group(1) or ""
                    num = numbered_match.group(2)
                    content = numbered_match.group(3)
                    processed_lines.append(f"{indent}{num} {content}")
                else:
                    processed_lines.append(line.rstrip())

    text = "\n".join(processed_lines)

    # 9. Clean up line whitespace and merge consecutive empty lines
    lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            if line.startswith(" ") or line.startswith("\t"):
                lines.append(line.rstrip())
            else:
                lines.append(cleaned)
        elif line == "":
            lines.append("")

    result = []
    last_empty = False
    for line in lines:
        if line == "":
            if not last_empty:
                result.append("")
                last_empty = True
        else:
            result.append(line)
            last_empty = False

    return "\n".join(result).strip()

# Meta WhatsApp Cloud API base URL
WA_API_BASE = f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_API_VERSION}"
MAX_RETRIES = 2
class WhatsAppClient:
    """
    Sends outbound messages to customers via Meta WhatsApp Cloud API.
    """
    def __init__(self):
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.api_token = settings.WHATSAPP_API_TOKEN
    def _mock_send(self, message_type: str, to_phone: str) -> dict:
        msg_id = f"mock_whatsapp_{message_type}_{uuid4().hex}"
        logger.info(f"Mock WhatsApp {message_type} sent: {msg_id} to {to_phone}")
        return {"success": True, "message_id": msg_id, "mock": True}
    def _mock_media_upload(self) -> dict:
        media_id = f"mock_media_{uuid4().hex}"
        logger.info(f"Mock WhatsApp media uploaded: {media_id}")
        return {"success": True, "media_id": media_id, "mock": True}
    def _is_mock_mode(self) -> bool:
        return settings.effective_whatsapp_mode == "mock"
    async def send_text_message(
        self,
        to_phone: str,
        message_text: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> dict:
        """
        Send a text message to a WhatsApp user.
        Args:
            to_phone: Recipient phone number (with country code, no +)
            message_text: Message body text
            phone_number_id: Override phone number ID (for multi-account tenants)
        Returns:
            dict with success status and message_id or error
        """
        message_text = format_markdown_for_whatsapp(message_text)
        if self._is_mock_mode():
            return self._mock_send("text", to_phone)
        pid = phone_number_id
        token = access_token
        if not pid:
            logger.error("WhatsApp phone_number_id not configured")
            return {"success": False, "error": "phone_number_id not configured"}
        if not token:
            logger.error("WhatsApp API token not configured")
            return {"success": False, "error": "api token not configured"}
        url = f"{WA_API_BASE}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        message_text = format_confirmation_tags(message_text)
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": message_text}
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    msg_id = data.get("messages", [{}])[0].get("id", "unknown")
                    logger.info(f"WhatsApp text sent: {msg_id} to {to_phone}")
                    return {"success": True, "message_id": msg_id}
                else:
                    error_data = response.json() if "application/json" in response.headers.get("Content-Type", "") else response.text
                    err_code = error_data.get("error", {}).get("code") if isinstance(error_data, dict) else None
                    if err_code == 131048:
                        logger.error(f"Rate Limiting hit (131048) on attempt {attempt}")
                        # Exponential backoff is handled upstream in worker.py, we just return the specific error
                    else:
                        logger.warning(f"WhatsApp API error (attempt {attempt}): {response.status_code} - {error_data}")
                    if attempt == MAX_RETRIES:
                        return {"success": False, "error": error_data, "status_code": response.status_code, "error_code": err_code}
            except httpx.TimeoutException:
                logger.warning(f"WhatsApp API timeout (attempt {attempt})")
                if attempt == MAX_RETRIES:
                    return {"success": False, "error": "Request timed out"}
            except Exception as e:
                logger.error(f"WhatsApp API exception: {e}")
                return {"success": False, "error": str(e)}
        return {"success": False, "error": "Max retries exceeded"}

    async def send_image_message(
        self,
        to_phone: str,
        image_url: str,
        caption: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> dict:
        """Send a WhatsApp image by safe URL with a short caption."""
        if self._is_mock_mode():
            return self._mock_send("image", to_phone)
        pid = phone_number_id
        token = access_token
        if not pid:
            return {"success": False, "error": "phone_number_id not configured"}
        if not token:
            return {"success": False, "error": "api token not configured"}
        if not image_url.startswith(("https://", "http://")):
            return {"success": False, "error": "image_url_not_public"}
        url = f"{WA_API_BASE}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        caption = format_confirmation_tags(caption)
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "image",
            "image": {
                "link": image_url,
                "caption": caption[:1024],
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id", "unknown")
                logger.info(f"WhatsApp image sent: {msg_id} to {to_phone}")
                return {"success": True, "message_id": msg_id, "message_type": "image"}
            return {"success": False, "error": response.json(), "status_code": response.status_code}
        except Exception as e:
            logger.error(f"Image send error: {e}")
            return {"success": False, "error": str(e)}
    async def send_template_message(
        self,
        to_phone: str,
        template_name: str,
        language_code: str = "en",
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
        components: Optional[list] = None,
    ) -> dict:
        """
        Send a template message (for 24h+ conversations or notifications).
        """
        if self._is_mock_mode():
            return self._mock_send("template", to_phone)
        pid = phone_number_id
        token = access_token
        if not pid:
            return {"success": False, "error": "phone_number_id not configured"}
        if not token:
            return {"success": False, "error": "api token not configured"}
        url = f"{WA_API_BASE}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code}
            }
        }
        if components:
            payload["template"]["components"] = components
        # Similar retry logic applies to template, interactive, and media but we'll try once for efficiency here
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id", "unknown")
                logger.info(f"Template message sent: {msg_id} to {to_phone}")
                return {"success": True, "message_id": msg_id}
            else:
                return {"success": False, "error": response.text}
        except Exception as e:
            logger.error(f"Template send error: {e}")
            return {"success": False, "error": str(e)}
    async def send_interactive_message(
        self,
        to_phone: str,
        interactive_data: dict,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> dict:
        """
        Send an interactive message (Buttons, Lists, Product formats).
        """
        if self._is_mock_mode():
            return self._mock_send("interactive", to_phone)
        pid = phone_number_id
        token = access_token
        if not pid:
            return {"success": False, "error": "phone_number_id not configured"}
        if not token:
            return {"success": False, "error": "api token not configured"}
        url = f"{WA_API_BASE}/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        if isinstance(interactive_data, dict) and "body" in interactive_data and isinstance(interactive_data["body"], dict) and "text" in interactive_data["body"]:
            body_text = interactive_data["body"]["text"]
            if body_text:
                interactive_data["body"]["text"] = format_confirmation_tags(body_text)
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "interactive",
            "interactive": interactive_data
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                msg_id = data.get("messages", [{}])[0].get("id", "unknown")
                logger.info(f"Interactive message sent: {msg_id} to {to_phone}")
                return {"success": True, "message_id": msg_id}
            else:
                return {"success": False, "error": response.json(), "status_code": response.status_code}
        except Exception as e:
            logger.error(f"Interactive send error: {e}")
            return {"success": False, "error": str(e)}
    async def upload_media(
        self,
        media_content: bytes,
        mime_type: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> dict:
        """
        Uploads media to Meta and returns a media_id for subsequent messages.
        """
        if self._is_mock_mode():
            return self._mock_media_upload()
        pid = phone_number_id
        token = access_token
        if not pid:
            return {"success": False, "error": "phone_number_id not configured"}
        if not token:
            return {"success": False, "error": "api token not configured"}
        url = f"{WA_API_BASE}/{pid}/media"
        headers = {
            "Authorization": f"Bearer {token}",
        }
        files = {
            'file': ('media_file', media_content, mime_type),
            'messaging_product': (None, 'whatsapp')
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, files=files, headers=headers)
            if response.status_code == 200:
                media_id = response.json().get("id")
                return {"success": True, "media_id": media_id}
            return {"success": False, "error": response.text, "status_code": response.status_code}
        except Exception as e:
            logger.error(f"Media upload error: {e}")
            return {"success": False, "error": str(e)}
# Singleton
whatsapp_client = WhatsAppClient()
