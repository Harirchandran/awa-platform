import hashlib
import hmac
import secrets
from backend.core.config import settings
def create_public_token(prefix: str) -> str:
    """Create an opaque token for public chat links or browser sessions."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"
def hash_public_chat_token(token: str, purpose: str) -> str:
    """Hash a token before persistence so raw public tokens are never stored."""
    secret = (
        settings.PUBLIC_CHAT_SESSION_SECRET
        if purpose == "session"
        else settings.PUBLIC_CHAT_LINK_SECRET
    )
    return hmac.new(
        secret.encode("utf-8"),
        f"{purpose}:{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()