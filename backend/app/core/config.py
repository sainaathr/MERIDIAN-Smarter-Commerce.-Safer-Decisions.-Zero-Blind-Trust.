from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Optional, Set

DISALLOWED_SECRET_KEYS: Set[str] = {
    "dev-secret-key-change-in-production-min-32-chars",
    "change-me-to-a-secure-random-secret-key-32-chars-min",
    "generate-a-secure-random-secret-key-min-32-chars-e-g-python-secrets-token-hex-32",
    "your_secret_key_here",
    "secret",
    "changeme",
    "password",
    "12345678",
}


class Settings(BaseSettings):
    APP_ENV: str = "development"
    DEBUG: bool = True
    DEMO_MODE: bool = False
    SECRET_KEY: str = ""

    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./meridian.db"

    # Gemini AI
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.0-flash"
    GEMINI_TIMEOUT_SECONDS: int = 30
    GEMINI_MAX_RETRIES: int = 3

    # PayPal Sandbox
    PAYPAL_CLIENT_ID: Optional[str] = None
    PAYPAL_CLIENT_SECRET: Optional[str] = None
    PAYPAL_BASE_URL: str = "https://api-m.sandbox.paypal.com"
    PAYPAL_MODE: str = "sandbox"
    PAYPAL_CURRENCY: str = "USD"
    PAYPAL_TIMEOUT_SECONDS: int = 20
    PAYPAL_MAX_RETRIES: int = 2
    PAYPAL_WEBHOOK_ID: Optional[str] = None

    # PayPal Orders v2 Create Order Idempotency Retention Contract
    PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS: int = 21600
    PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED: bool = False

    def get_effective_order_idempotency_retention_seconds(self) -> int:
        val = self.PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS
        if val <= 0:
            return 21600
        if val > 21600:
            if not self.PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED:
                return 21600
            return min(val, 259200)
        return val

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        # 1. SECRET_KEY validation
        secret = (self.SECRET_KEY or "").strip()
        if not secret:
            raise ValueError(
                "SECURITY CONFIGURATION ERROR: SECRET_KEY is missing. "
                "You must configure a strong, random secret key (minimum 32 characters) via environment variable or .env file. "
                "Generate one using: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(secret) < 32:
            raise ValueError(
                f"SECURITY CONFIGURATION ERROR: SECRET_KEY is too short ({len(secret)} characters). "
                "Minimum length is 32 characters."
            )
        secret_lower = secret.lower()
        if secret_lower in DISALLOWED_SECRET_KEYS or any(disallowed in secret_lower for disallowed in [
            "dev-secret-key-change-in-production",
            "change-me-to-a-secure-random",
            "generate-a-secure-random",
            "your_secret_key_here",
            "placeholder",
        ]):
            raise ValueError(
                "SECURITY CONFIGURATION ERROR: SECRET_KEY cannot be set to a known placeholder or default development value. "
                "Generate a cryptographically random secret key using: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        # 2. DEMO_MODE validation
        if self.APP_ENV.lower() in ("production", "prod") and self.DEMO_MODE:
            raise ValueError(
                "SECURITY VIOLATION: DEMO_MODE cannot be enabled in production environments. "
                "Set DEMO_MODE=false or change APP_ENV."
            )

        # 3. PayPal Sandbox validation
        if self.PAYPAL_MODE.lower() != "sandbox":
            raise ValueError(
                f"SECURITY VIOLATION: MERIDIAN only permits 'sandbox' mode. Found PAYPAL_MODE='{self.PAYPAL_MODE}'."
            )
        if "sandbox" not in self.PAYPAL_BASE_URL.lower() and "localhost" not in self.PAYPAL_BASE_URL.lower() and "127.0.0.1" not in self.PAYPAL_BASE_URL.lower():
            raise ValueError(
                f"SECURITY VIOLATION: Non-sandbox PayPal URL rejected: '{self.PAYPAL_BASE_URL}'. Live PayPal endpoints are strictly prohibited."
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
