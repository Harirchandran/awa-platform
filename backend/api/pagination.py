"""
Cursor Pagination Helper.
Uses timestamp-based cursors for consistent ordering.
"""
from typing import Optional
from base64 import b64encode, b64decode
def encode_cursor(value: str) -> str:
    """Encode a cursor value (e.g. ISO timestamp) to base64."""
    return b64encode(value.encode()).decode()
def decode_cursor(cursor: str) -> Optional[str]:
    """Decode a base64 cursor back to the original value."""
    try:
        return b64decode(cursor.encode()).decode()
    except Exception:
        return None