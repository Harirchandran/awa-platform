from typing import Optional
import logging
from backend.core.config import settings
PREFIX = "fernet:v1:"
_logger = logging.getLogger(__name__)
def _get_fernet():
    from cryptography.fernet import Fernet
    if not settings.WHATSAPP_TOKEN_ENCRYPTION_KEY:
        return None
    return Fernet(settings.WHATSAPP_TOKEN_ENCRYPTION_KEY.encode("utf-8"))
def encrypt_optional_secret(value: Optional[str]) -> Optional[str]:
    if not value or value.startswith(PREFIX):
        return value
    fernet = _get_fernet()
    if not fernet:
        # M-5: Prevent silent plaintext storage in non-local environments
        if settings.ENV not in ("local", "docker", "test"):
            raise RuntimeError(
                "WHATSAPP_TOKEN_ENCRYPTION_KEY is required to encrypt secrets "
                f"in ENV={settings.ENV!r}. Set it or switch to a local environment."
            )
        _logger.warning("WHATSAPP_TOKEN_ENCRYPTION_KEY not set — storing secret in plaintext")
        return value
    token = fernet.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{PREFIX}{token}"
def decrypt_optional_secret(value: Optional[str]) -> Optional[str]:
    if not value or not value.startswith(PREFIX):
        return value
    fernet = _get_fernet()
    if not fernet:
        raise RuntimeError("WHATSAPP_TOKEN_ENCRYPTION_KEY is required to decrypt stored WhatsApp tokens")
    encrypted = value.removeprefix(PREFIX)
    return fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")