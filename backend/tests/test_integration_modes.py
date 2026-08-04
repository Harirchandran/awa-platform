import os
import pytest
from pydantic import ValidationError
from backend.core.config import Settings

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ.keys()):
        if (
            key.startswith("AWA_")
            or key.startswith("ENABLE_")
            or key.startswith("AUTH_")
            or key.startswith("WHATSAPP_")
            or key.startswith("BILLING_")
            or key in [
                "ENV", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "JWT_SECRET",
                "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
                "GROQ_API_KEY", "AGENT_LLM_PROVIDER", "AGENT_LLM_MODEL", "ROUTING_LLM_PROVIDER"
            ]
        ):
            monkeypatch.delenv(key, raising=False)

def production_settings(**overrides):
    values = {
        "ENV": "production",
        "LOG_LEVEL": "INFO",
        "AWA_INTEGRATION_MODE": "live",
        "AUTH_MODE": "postgres",
        "WHATSAPP_MODE": "live",
        "BILLING_MODE": "live",
        "ENABLE_MOCK_AUTH": False,
        "ENABLE_MOCK_WHATSAPP_SIGNUP": False,
        "ENABLE_MOCK_BILLING": False,
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_JWT_SECRET": "jwt-secret",
        "SUPABASE_JWT_ISSUER": "https://example.supabase.co/auth/v1",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        "JWT_SECRET": "a" * 32,
        "WHATSAPP_TOKEN_ENCRYPTION_KEY": "fernet-key",
        "WHATSAPP_API_TOKEN": "whatsapp-api-token",
        "WHATSAPP_APP_SECRET": "whatsapp-app-secret",
        "WHATSAPP_VERIFY_TOKEN": "verify-token",
        "WHATSAPP_PHONE_NUMBER_ID": "phone-number-id",
        "PUBLIC_CHAT_LINK_SECRET": "prod-public-chat-link-secret",
        "PUBLIC_CHAT_SESSION_SECRET": "prod-public-chat-session-secret",
        "CUSTOMER_OTP_MODE": "live",
        "CUSTOMER_OTP_WEBHOOK_URL": "https://example.com/otp",
        "CUSTOMER_OTP_WEBHOOK_SECRET": "otp-webhook-secret",
        "CORS_ORIGINS": "https://app.example.com",
        "RAZORPAY_KEY_ID": "rzp_live_key",
        "RAZORPAY_KEY_SECRET": "rzp_live_secret",
        "RAZORPAY_WEBHOOK_SECRET": "rzp_webhook_secret",
        "ROUTING_LLM_PROVIDER": "groq",
        "AGENT_LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "groq-key",
        "AWA_PUBLIC_CHAT_BASE_URL": "https://example.com/chat",
        "PUBLIC_CHAT_CAPTCHA_MODE": "turnstile",
        "TURNSTILE_SECRET_KEY": "turnstile_secret_key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
def test_mock_mode_enables_local_integration_mocks():
    settings = Settings(
        ENV="local",
        AWA_INTEGRATION_MODE="mock",
        ENABLE_MOCK_AUTH=True,
        ENABLE_MOCK_WHATSAPP_SIGNUP=True,
        ENABLE_MOCK_BILLING=True,
        _env_file=None,
    )
    assert settings.allow_mock_auth is True
    assert settings.allow_mock_whatsapp_signup is True
    assert settings.allow_mock_billing is True
def test_live_mode_disables_mocks_even_when_legacy_flags_are_true():
    settings = Settings(
        ENV="local",
        AWA_INTEGRATION_MODE="live",
        AUTH_MODE="supabase",
        WHATSAPP_MODE="live",
        BILLING_MODE="live",
        ENABLE_MOCK_AUTH=True,
        ENABLE_MOCK_WHATSAPP_SIGNUP=True,
        ENABLE_MOCK_BILLING=True,
        _env_file=None,
    )
    assert settings.allow_mock_auth is False
    assert settings.allow_mock_whatsapp_signup is False
    assert settings.allow_mock_billing is False
def test_service_live_override_disables_mocks_in_global_mock_mode():
    settings = Settings(
        ENV="local",
        AWA_INTEGRATION_MODE="mock",
        AUTH_MODE="supabase",
        WHATSAPP_MODE="live",
        BILLING_MODE="live",
        ENABLE_MOCK_AUTH=True,
        ENABLE_MOCK_WHATSAPP_SIGNUP=True,
        ENABLE_MOCK_BILLING=True,
        _env_file=None,
    )
    assert settings.allow_mock_auth is False
    assert settings.allow_mock_whatsapp_signup is False
    assert settings.allow_mock_billing is False
def test_production_rejects_unsafe_integration_settings():
    with pytest.raises(ValidationError):
        Settings(
            ENV="production",
            AWA_INTEGRATION_MODE="mock",
            ENABLE_MOCK_AUTH=True,
            ENABLE_WEBHOOK_SIGNATURE_BYPASS=True,
            _env_file=None,
        )
def test_production_rejects_missing_live_billing_credentials():
    with pytest.raises(ValidationError, match="RAZORPAY_KEY_ID"):
        production_settings(RAZORPAY_KEY_ID=None)
def test_production_rejects_missing_configured_llm_key():
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        production_settings(GROQ_API_KEY=None)
def test_production_rejects_missing_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        production_settings(JWT_SECRET=None)
def test_production_accepts_live_settings_with_required_secrets():
    settings = production_settings()
    assert settings.effective_auth_mode == "postgres"
    assert settings.effective_whatsapp_mode == "live"
    assert settings.effective_billing_mode == "live"
