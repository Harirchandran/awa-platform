import os
import socket
import ipaddress
import re
from typing import Literal
from urllib.parse import urlparse
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

def is_unsafe_url_for_production(url: str) -> tuple[bool, str]:
    """
    Validate url against SSRF and security requirements in production.
    Returns (is_unsafe, reason).
    """
    if not url:
        return True, "missing_webhook_url"

    try:
        parsed = urlparse(url)
    except Exception:
        return True, "invalid_url_format"

    if parsed.scheme != "https":
        return True, "unsafe_scheme_production"

    if parsed.username or parsed.password:
        return True, "credentials_in_url"

    hostname = parsed.hostname
    if not hostname:
        return True, "missing_hostname"

    hostname_lower = hostname.lower().strip()
    if hostname_lower == "localhost":
        return True, "localhost_blocked"

    # Check simple local domain extensions
    if hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
        return True, "internal_domain_blocked"

    # DNS Resolution & IP check
    try:
        # Resolve hostname to all IP addresses
        addr_info = socket.getaddrinfo(hostname, None)
    except Exception:
        return True, "dns_resolution_failed"

    for item in addr_info:
        ip_str = item[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True, "invalid_ip_format"

        # Check all unsafe IP properties (IPv4 and IPv6 loopback, private, multicast, reserved)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True, "private_or_local_ip_blocked"

    return False, "valid"


def _resolve_env_file():
    """Load .env.local only in local/test environments. In Docker/production, env vars are injected."""
    env = os.environ.get("ENV", "local")
    if env in ("local", "test"):
        candidate = os.path.join(os.path.dirname(__file__), "../.env.local")
        if os.path.exists(candidate):
            return candidate
    return None


class Settings(BaseSettings):
    ENV: str = "local"
    LOG_LEVEL: str = "INFO"
    AWA_INTEGRATION_MODE: Literal["mock", "test", "live"] = "test"
    AUTH_MODE: Literal["mock", "supabase", "postgres"] | None = None
    WHATSAPP_MODE: Literal["mock", "test", "live"] | None = None
    BILLING_MODE: Literal["mock", "test", "live"] | None = None

    # Database
    DATABASE_URL: str = "postgresql://postgres:replace_with_the_same_postgres_password@127.0.0.1:5432/postgres"
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None

    # Redis
    REDIS_URL: str = "redis://:replace_with_the_same_redis_password@127.0.0.1:6379/0"

    # Supabase
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_JWT_SECRET: str | None = None
    SUPABASE_JWT_AUDIENCE: str = "authenticated"
    SUPABASE_JWT_ISSUER: str | None = None

    # Backend-owned Auth JWT configurations
    JWT_SECRET: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # WhatsApp
    WHATSAPP_API_TOKEN: str | None = None
    WHATSAPP_VERIFY_TOKEN: str | None = None
    WHATSAPP_APP_SECRET: str | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_GRAPH_API_VERSION: str = "v21.0"
    WHATSAPP_TOKEN_ENCRYPTION_KEY: str | None = None

    # Meta Embedded Signup
    FACEBOOK_APP_ID: str | None = None
    FACEBOOK_APP_SECRET: str | None = None
    FACEBOOK_CONFIG_ID: str | None = None

    # Payments & Billing (Razorpay)
    RAZORPAY_KEY_ID: str | None = None
    RAZORPAY_KEY_SECRET: str | None = None
    RAZORPAY_WEBHOOK_SECRET: str | None = None

    # AI Providers
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    ROUTING_LLM_PROVIDER: str = "groq"
    ROUTING_LLM_MODEL: str = "llama3-8b-8192"
    AGENT_LLM_PROVIDER: str = "groq"
    AGENT_LLM_MODEL: str = "llama3-70b-8192"
    AWA_REAL_PROVIDER_REQUIRED: bool = False
    OLLAMA_BASE_URL: str | None = None
    # Platform-owned gateway runtime. Explicit environment overrides provide the
    # bounded emergency rollback; tenant configuration cannot alter these values.
    LLM_GATEWAY_ENABLED: bool = True
    LLM_GATEWAY_SHADOW_MODE: bool = False
    LLM_FALLBACK_ORDER: str = "gemini,groq,openai"
    LLM_GATEWAY_GEMINI_MODEL: str = ""
    LLM_GATEWAY_GROQ_MODEL: str = ""
    LLM_GATEWAY_OPENAI_MODEL: str = ""
    # Platform-owned core-agent rollout. The global gateway switch remains the
    # outer kill switch; tenants cannot select this mode, a provider, or a model.
    LLM_GATEWAY_CORE_AGENT_MODE: Literal["legacy", "shadow", "authoritative"] = "authoritative"
    # Platform-owned safe-facts response-composer rollout. The global gateway
    # switch is the outer kill switch; tenant configuration cannot override it.
    LLM_GATEWAY_RESPONSE_COMPOSER_MODE: Literal["legacy", "shadow", "authoritative"] = "authoritative"
    # Platform-owned field-extraction rollout. Every migrated workflow is
    # authoritative by default; the allowlist remains explicitly overridable.
    LLM_GATEWAY_FIELD_EXTRACTION_MODE: Literal["legacy", "shadow", "authoritative"] = "authoritative"
    LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS: str = (
        "review,support,lead_capture,reminder,booking,order,payment"
    )
    # Platform-owned pending-action semantic decision rollout. Tenant
    # configuration cannot override this process-level safety control.
    LLM_GATEWAY_PENDING_DECISION_MODE: Literal["legacy", "shadow", "authoritative"] = "authoritative"
    # Platform-owned, content-free LLM observability. These settings are never
    # read from tenant configuration and cannot affect inference decisions.
    LLM_TELEMETRY_ENABLED: bool = True
    LLM_TELEMETRY_SAMPLE_RATE: float = Field(default=1.0, ge=0.0, le=1.0)
    LLM_TELEMETRY_INCLUDE_USAGE: bool = True
    LLM_TELEMETRY_CORRELATION_KEY: SecretStr | None = Field(default=None, repr=False)
    # Platform-owned customer-turn semantic authority. An explicit value wins;
    # when unset the retired shadow flag still opts deployments into shadow.
    TURN_INTERPRETATION_MODE: str | None = None
    ENABLE_TURN_INTERPRETATION_SHADOW: bool = False

    # Platform-owned, observational-only booking configured-turn shadow. This
    # feeds no authoritative path; tenant configuration cannot enable it.
    ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW: bool = False

    # Platform-owned, default-disabled authoritative application of backend-
    # validated Iteration 2 complete-turn candidates to an active booking. This
    # requires ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW=true as a prerequisite gate.
    # Tenant configuration cannot enable it.
    ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY: bool = False

    # Platform-owned, observational-only order configured-turn shadow (Iteration 6B).
    # This feeds no authoritative path; tenant configuration cannot enable it.
    ENABLE_ORDER_CONFIGURED_TURN_SHADOW: bool = False

    # Platform-owned, default-disabled authoritative application of backend-
    # validated Iteration 6B complete-turn candidates to an active order. This
    # requires ENABLE_ORDER_CONFIGURED_TURN_SHADOW=true as a prerequisite gate.
    # Tenant configuration cannot enable it.
    ENABLE_ORDER_CONFIGURED_TURN_APPLY: bool = False

    # Platform-owned, observational-only lead configured-turn shadow (Iteration 7B).
    # This feeds no authoritative path; tenant configuration cannot enable it. It is
    # independent of the booking and order shadow flags.
    ENABLE_LEAD_CONFIGURED_TURN_SHADOW: bool = False

    # Platform-owned, default-disabled authoritative application of backend-validated
    # Iteration 7B complete-turn candidates to an active non-terminal lead continuation.
    # Requires ENABLE_LEAD_CONFIGURED_TURN_SHADOW=true as a prerequisite gate. Tenant
    # configuration cannot enable it. Independent of the booking and order apply flags.
    ENABLE_LEAD_CONFIGURED_TURN_APPLY: bool = False

    # Platform-owned, default-disabled authoritative terminal lead handoff. When an
    # active lead receives a complete terminal batch of safe configured fields, the
    # engine revalidates the candidates, builds an exact tenant-configured terminal
    # payload, replaces the lead-scoped state once, and executes lead_capture.execute
    # through the ToolRegistry exactly once. Requires all three lead flags enabled.
    # Keep disabled until lead persistence and event idempotency hardening (7D-D) is
    # complete. Tenant configuration cannot enable it.
    ENABLE_LEAD_CONFIGURED_TURN_TERMINAL_APPLY: bool = False

    # Provider Rate Limiting
    ENABLE_PROVIDER_RATE_LIMITER: bool = False
    PROVIDER_RATE_LIMIT_FAIL_OPEN: bool = False
    PROVIDER_RATE_LIMIT_MAX_WAIT_MS: int = 15000
    PROVIDER_GEMINI_RPM_LIMIT: int = 15
    PROVIDER_GEMINI_TPM_LIMIT: int = 1000000
    PROVIDER_OPENAI_RPM_LIMIT: int = 60
    PROVIDER_OPENAI_TPM_LIMIT: int = 90000
    PROVIDER_GROQ_RPM_LIMIT: int = 30
    PROVIDER_GROQ_TPM_LIMIT: int = 6000
    PROVIDER_OLLAMA_RPM_LIMIT: int = 999999
    PROVIDER_OLLAMA_TPM_LIMIT: int = 999999

    # Provider Rate Limiting Tuning (Phase 17E)
    PROVIDER_RATE_LIMIT_SAFETY_MARGIN_PERCENT: int = 20
    PROVIDER_RATE_LIMIT_MIN_EFFECTIVE_RPM: int = 1
    PROVIDER_GEMINI_EFFECTIVE_RPM_LIMIT: int | None = None
    PROVIDER_RATE_LIMIT_BURST_CAP_PERCENT: int = 50
    PROVIDER_GEMINI_BURST_CAP: int | None = None

    # Worker Session Locks & Concurrency
    ENABLE_WORKER_SESSION_LOCKS: bool = False
    WORKER_SESSION_LOCK_TTL_MS: int = 120000
    WORKER_SESSION_LOCK_MAX_WAIT_MS: int = 15000
    WORKER_SESSION_LOCK_RENEW_INTERVAL_MS: int = 30000
    WORKER_SESSION_LOCK_FAIL_OPEN: bool = False
    WORKER_CONCURRENCY_MODE: str = "sequential"
    WORKER_TASK_POOL_SIZE: int = 1



    # Security / Rate Limiting
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    RATE_LIMIT_PER_SECOND: int = 10
    AWA_PUBLIC_CHAT_BASE_URL: str = "http://localhost:5173"
    PUBLIC_CHAT_LINK_SECRET: str = "local-" + "public-chat-link-secret"
    PUBLIC_CHAT_SESSION_SECRET: str = "local-" + "public-chat-session-secret"
    PUBLIC_CHAT_RATE_LIMIT: int = 120
    PUBLIC_CHAT_CAPTCHA_MODE: Literal["disabled", "mock", "turnstile"] = "disabled"
    TURNSTILE_SECRET_KEY: str | None = None
    CUSTOMER_OTP_MODE: Literal["disabled", "mock", "live"] = "mock"
    CUSTOMER_OTP_WEBHOOK_URL: str | None = None
    CUSTOMER_OTP_WEBHOOK_SECRET: str | None = None
    CUSTOMER_OTP_TTL_SECONDS: int = 600
    CUSTOMER_OTP_VERIFIED_TTL_SECONDS: int = 86400
    CUSTOMER_OTP_MAX_ATTEMPTS: int = 5

    # Explicit local-development shortcuts. Keep false by default so production
    # cannot accidentally accept fake auth, unsigned webhooks, or mock providers.
    ENABLE_MOCK_AUTH: bool = False
    ENABLE_MOCK_SUPER_ADMIN: bool = False
    ENABLE_WEBHOOK_SIGNATURE_BYPASS: bool = False
    ENABLE_MOCK_BILLING: bool = False
    ENABLE_MOCK_WHATSAPP_SIGNUP: bool = False
    ALLOW_MANUAL_INVITE_LINKS: bool = False

    # External Support Notifications (Partially Resolved / Webhook Provider)
    SUPPORT_NOTIFICATION_MODE: Literal["disabled", "webhook"] = "disabled"
    SUPPORT_NOTIFICATION_WEBHOOK_URL: str | None = None
    SUPPORT_NOTIFICATION_TIMEOUT_SECONDS: int = 3
    SUPPORT_NOTIFICATION_INCLUDE_MESSAGE_SNIPPET: bool = False

    # Tenant-owned media uploads (Phase 16)
    MEDIA_STORAGE_DIR: str = "tmp/media"
    MEDIA_MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024

    def __repr_args__(self):
        sensitive = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")
        for name, value in super().__repr_args__():
            yield name, "**********" if value and any(marker in name.upper() for marker in sensitive) else value

    @property
    def effective_auth_mode(self) -> str:
        return self.AUTH_MODE or ("mock" if self.AWA_INTEGRATION_MODE == "mock" else "postgres")

    @property
    def effective_whatsapp_mode(self) -> str:
        return self.WHATSAPP_MODE or self.AWA_INTEGRATION_MODE

    @property
    def effective_billing_mode(self) -> str:
        return self.BILLING_MODE or self.AWA_INTEGRATION_MODE

    @property
    def effective_jwt_secret(self) -> str:
        return self.JWT_SECRET or self.SUPABASE_JWT_SECRET or ("dummy_jwt_secret_" + "fallback_change_me")

    @property
    def allow_mock_auth(self) -> bool:
        if self.ENV == "production":
            return False
        if self.AUTH_MODE is not None:
            return self.AUTH_MODE == "mock" and self.ENABLE_MOCK_AUTH
        return self.AWA_INTEGRATION_MODE != "live" and self.ENABLE_MOCK_AUTH

    @property
    def allow_mock_whatsapp_signup(self) -> bool:
        if self.ENV == "production":
            return False
        if self.WHATSAPP_MODE is not None:
            return self.WHATSAPP_MODE == "mock" and self.ENABLE_MOCK_WHATSAPP_SIGNUP
        return self.AWA_INTEGRATION_MODE != "live" and self.ENABLE_MOCK_WHATSAPP_SIGNUP

    @property
    def allow_mock_billing(self) -> bool:
        if self.ENV == "production":
            return False
        if self.BILLING_MODE is not None:
            return self.BILLING_MODE == "mock" and self.ENABLE_MOCK_BILLING
        return self.AWA_INTEGRATION_MODE != "live" and self.ENABLE_MOCK_BILLING

    @property
    def allow_webhook_signature_bypass(self) -> bool:
        return self.ENV != "production" and self.ENABLE_WEBHOOK_SIGNATURE_BYPASS

    @property
    def llm_gateway_fallback_order(self) -> tuple[str, ...]:
        return tuple(part.strip().lower() for part in self.LLM_FALLBACK_ORDER.split(",") if part.strip())

    @property
    def llm_gateway_field_extraction_workflows(self) -> tuple[str, ...]:
        return tuple(
            part.strip().lower()
            for part in self.LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS.split(",")
            if part.strip()
        )

    @model_validator(mode="after")
    def validate_integration_safety(self):
        gateway_order = self.llm_gateway_fallback_order
        known_gateway_providers = {"gemini", "groq", "openai"}
        if not gateway_order:
            raise ValueError("LLM_FALLBACK_ORDER must not be empty")
        if len(gateway_order) != len(set(gateway_order)):
            raise ValueError("LLM_FALLBACK_ORDER must not contain duplicates")
        unknown_gateway_providers = set(gateway_order) - known_gateway_providers
        if unknown_gateway_providers:
            raise ValueError("LLM_FALLBACK_ORDER must contain only: gemini, groq, openai")
        extraction_workflows = self.llm_gateway_field_extraction_workflows
        known_extraction_workflows = {
            "review", "support", "lead_capture", "reminder", "booking", "order", "payment",
        }
        if len(extraction_workflows) != len(set(extraction_workflows)):
            raise ValueError("LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS must not contain duplicates")
        unknown_extraction_workflows = set(extraction_workflows) - known_extraction_workflows
        if unknown_extraction_workflows:
            raise ValueError(
                "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS contains an unknown workflow"
            )
        if self.ENV == "production":
            unsafe = []
            if self.AWA_INTEGRATION_MODE != "live":
                unsafe.append("AWA_INTEGRATION_MODE must be live")
            if self.effective_auth_mode != "postgres":
                unsafe.append("AUTH_MODE must be postgres in production (Supabase and mock auth are blocked)")
            if self.effective_whatsapp_mode != "live":
                unsafe.append("WHATSAPP_MODE must be live")
            if self.effective_billing_mode != "live":
                unsafe.append("BILLING_MODE must be live")
            if self.ENABLE_MOCK_AUTH:
                unsafe.append("ENABLE_MOCK_AUTH must be false")
            if self.ENABLE_MOCK_SUPER_ADMIN:
                unsafe.append("ENABLE_MOCK_SUPER_ADMIN must be false")
            if self.ENABLE_MOCK_WHATSAPP_SIGNUP:
                unsafe.append("ENABLE_MOCK_WHATSAPP_SIGNUP must be false")
            if self.ENABLE_MOCK_BILLING:
                unsafe.append("ENABLE_MOCK_BILLING must be false")
            if self.ENABLE_WEBHOOK_SIGNATURE_BYPASS:
                unsafe.append("ENABLE_WEBHOOK_SIGNATURE_BYPASS must be false")
            if self.ALLOW_MANUAL_INVITE_LINKS:
                unsafe.append("ALLOW_MANUAL_INVITE_LINKS must be false")
            if self.SUPPORT_NOTIFICATION_MODE == "webhook":
                if not self.SUPPORT_NOTIFICATION_WEBHOOK_URL:
                    unsafe.append("SUPPORT_NOTIFICATION_WEBHOOK_URL is required when SUPPORT_NOTIFICATION_MODE is webhook")
                else:
                    is_unsafe, reason = is_unsafe_url_for_production(self.SUPPORT_NOTIFICATION_WEBHOOK_URL)
                    if is_unsafe:
                        unsafe.append(f"SUPPORT_NOTIFICATION_WEBHOOK_URL is unsafe: {reason}")
            if self.LOG_LEVEL.upper() == "DEBUG":
                unsafe.append("LOG_LEVEL must not be DEBUG in production")
            if "localhost" in self.AWA_PUBLIC_CHAT_BASE_URL.lower():
                unsafe.append("AWA_PUBLIC_CHAT_BASE_URL must not be localhost in production")
            if self.PUBLIC_CHAT_CAPTCHA_MODE in {"disabled", "mock"}:
                unsafe.append("PUBLIC_CHAT_CAPTCHA_MODE must be turnstile in production")
            if self.PUBLIC_CHAT_CAPTCHA_MODE == "turnstile" and not self.TURNSTILE_SECRET_KEY:
                unsafe.append("TURNSTILE_SECRET_KEY is required when PUBLIC_CHAT_CAPTCHA_MODE is turnstile")
            if self.CUSTOMER_OTP_MODE in {"disabled", "mock"}:
                unsafe.append("CUSTOMER_OTP_MODE must be live")
            if self.CUSTOMER_OTP_MODE == "live" and not self.CUSTOMER_OTP_WEBHOOK_URL:
                unsafe.append("CUSTOMER_OTP_WEBHOOK_URL is required when CUSTOMER_OTP_MODE is live")
            if self.CUSTOMER_OTP_MODE == "live":
                if not self.CUSTOMER_OTP_WEBHOOK_SECRET:
                    unsafe.append("CUSTOMER_OTP_WEBHOOK_SECRET is required when CUSTOMER_OTP_MODE is live")
                elif self.CUSTOMER_OTP_WEBHOOK_URL:
                    is_unsafe, reason = is_unsafe_url_for_production(self.CUSTOMER_OTP_WEBHOOK_URL)
                    if is_unsafe:
                        unsafe.append(f"CUSTOMER_OTP_WEBHOOK_URL is unsafe: {reason}")
            if not self.JWT_SECRET or len(self.JWT_SECRET) < 32 or "change_me" in self.JWT_SECRET.lower() or "dummy" in self.JWT_SECRET.lower():
                unsafe.append("JWT_SECRET must be configured with a strong secret (at least 32 characters) in production")
            if not self.WHATSAPP_TOKEN_ENCRYPTION_KEY:
                unsafe.append("WHATSAPP_TOKEN_ENCRYPTION_KEY is required")
            if not self.WHATSAPP_APP_SECRET:
                unsafe.append("WHATSAPP_APP_SECRET is required")
            if not self.WHATSAPP_VERIFY_TOKEN:
                unsafe.append("WHATSAPP_VERIFY_TOKEN is required")
            if not self.WHATSAPP_API_TOKEN:
                unsafe.append("WHATSAPP_API_TOKEN is required")
            if not self.WHATSAPP_PHONE_NUMBER_ID:
                unsafe.append("WHATSAPP_PHONE_NUMBER_ID is required")
            if self.effective_billing_mode == "live":
                if not self.RAZORPAY_KEY_ID:
                    unsafe.append("RAZORPAY_KEY_ID is required when BILLING_MODE is live")
                if not self.RAZORPAY_KEY_SECRET:
                    unsafe.append("RAZORPAY_KEY_SECRET is required when BILLING_MODE is live")
                if not self.RAZORPAY_WEBHOOK_SECRET:
                    unsafe.append("RAZORPAY_WEBHOOK_SECRET is required when BILLING_MODE is live")
            provider_keys = {
                "groq": ("GROQ_API_KEY", self.GROQ_API_KEY),
                "openai": ("OPENAI_API_KEY", self.OPENAI_API_KEY),
                "gemini": ("GEMINI_API_KEY", self.GEMINI_API_KEY),
            }
            for provider_field, provider in (
                ("ROUTING_LLM_PROVIDER", self.ROUTING_LLM_PROVIDER),
                ("AGENT_LLM_PROVIDER", self.AGENT_LLM_PROVIDER),
            ):
                provider_name = (provider or "").strip().lower()
                if provider_name not in provider_keys:
                    unsafe.append(f"{provider_field} must be one of: groq, openai, gemini")
                    continue
                key_name, key_value = provider_keys[provider_name]
                if not key_value:
                    unsafe.append(f"{key_name} is required when {provider_field}={provider_name}")
            weak_public_chat_link_secret = (
                not self.PUBLIC_CHAT_LINK_SECRET
                or "local" in self.PUBLIC_CHAT_LINK_SECRET.lower()
                or "change_me" in self.PUBLIC_CHAT_LINK_SECRET.lower()
            )
            weak_public_chat_session_secret = (
                not self.PUBLIC_CHAT_SESSION_SECRET
                or "local" in self.PUBLIC_CHAT_SESSION_SECRET.lower()
                or "change_me" in self.PUBLIC_CHAT_SESSION_SECRET.lower()
            )
            if weak_public_chat_link_secret:
                unsafe.append("PUBLIC_CHAT_LINK_SECRET must be changed")
            if weak_public_chat_session_secret:
                unsafe.append("PUBLIC_CHAT_SESSION_SECRET must be changed")
            if "*" in [origin.strip() for origin in self.CORS_ORIGINS.split(",")]:
                unsafe.append("CORS_ORIGINS must not contain '*'")
            # M-2: Ensure all CORS origins use HTTPS in production
            for origin in self.CORS_ORIGINS.split(","):
                origin = origin.strip()
                if origin and origin.startswith("http://"):
                    unsafe.append(f"CORS_ORIGINS must use HTTPS in production (found: {origin})")
                    break
            if unsafe:
                raise ValueError("Unsafe production integration settings: " + "; ".join(unsafe))
        if self.AWA_REAL_PROVIDER_REQUIRED:
            unsafe = []
            if self.AWA_INTEGRATION_MODE in {"mock", "test"}:
                unsafe.append("AWA_INTEGRATION_MODE must not be mock or test when AWA_REAL_PROVIDER_REQUIRED=true")
            provider_keys = {
                "groq": ("GROQ_API_KEY", self.GROQ_API_KEY),
                "openai": ("OPENAI_API_KEY", self.OPENAI_API_KEY),
                "gemini": ("GEMINI_API_KEY", self.GEMINI_API_KEY),
            }
            for provider_field, provider in (
                ("ROUTING_LLM_PROVIDER", self.ROUTING_LLM_PROVIDER),
                ("AGENT_LLM_PROVIDER", self.AGENT_LLM_PROVIDER),
            ):
                provider_name = (provider or "").strip().lower()
                if provider_name not in provider_keys:
                    unsafe.append(f"{provider_field} must be one of: groq, openai, gemini when AWA_REAL_PROVIDER_REQUIRED=true")
                    continue
                key_name, key_value = provider_keys[provider_name]
                if not key_value:
                    unsafe.append(f"{key_name} is required when {provider_field}={provider_name} and AWA_REAL_PROVIDER_REQUIRED=true")
            if unsafe:
                raise ValueError("Unsafe real-provider-required settings: " + "; ".join(unsafe))
        return self

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_ignore_empty=True,
        extra="ignore"
    )

settings = Settings()
