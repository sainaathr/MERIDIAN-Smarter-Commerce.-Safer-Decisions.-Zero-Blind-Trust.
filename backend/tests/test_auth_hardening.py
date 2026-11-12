import os
import time
import pytest
from pydantic import ValidationError
from httpx import AsyncClient, ASGITransport

from app.core.config import Settings
from app.core.init_db import init_database
from app.main import app
from app.api.auth_routes import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)
from app.core.config import settings


# ==============================================================================
# 1. JWT Secret Validation Tests
# ==============================================================================

def test_missing_secret_key_rejected():
    """Verify that a missing or empty SECRET_KEY raises a ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(SECRET_KEY="")
    assert "SECRET_KEY is missing" in str(exc_info.value)


def test_short_secret_key_rejected():
    """Verify that a SECRET_KEY shorter than 32 characters is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(SECRET_KEY="short-secret-key-16-chars")
    assert "too short" in str(exc_info.value)


def test_known_development_placeholder_rejected():
    """Verify that the previous development secret key is rejected as insecure."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(SECRET_KEY="dev-secret-key-change-in-production-min-32-chars")
    assert "known placeholder" in str(exc_info.value)


def test_env_example_placeholder_rejected():
    """Verify that the .env.example placeholder key is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(SECRET_KEY="change-me-to-a-secure-random-secret-key-32-chars-min")
    assert "known placeholder" in str(exc_info.value)


def test_valid_strong_secret_key_accepted():
    """Verify that a strong 32+ character random secret is accepted."""
    strong_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
    s = Settings(SECRET_KEY=strong_key)
    assert s.SECRET_KEY == strong_key


# ==============================================================================
# 2. Demo Mode Gate Tests
# ==============================================================================

def test_demo_mode_cannot_be_enabled_in_production():
    """Verify that DEMO_MODE=True cannot be set when APP_ENV=production."""
    strong_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
    with pytest.raises(ValidationError) as exc_info:
        Settings(SECRET_KEY=strong_key, APP_ENV="production", DEMO_MODE=True)
    assert "DEMO_MODE cannot be enabled in production" in str(exc_info.value)


def test_demo_mode_defaults_to_false(monkeypatch):
    """Verify that DEMO_MODE defaults to False when no env var is set."""
    monkeypatch.delenv("DEMO_MODE", raising=False)
    strong_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
    s = Settings(SECRET_KEY=strong_key)
    assert s.DEMO_MODE is False


# ==============================================================================
# 3. Password Verification & Demo Override Tests
# ==============================================================================

def test_normal_password_authentication_with_pbkdf2():
    """Verify that legitimate hashed passwords authenticate via PBKDF2."""
    hashed = hash_password("MySecurePassword123!")
    assert verify_password("MySecurePassword123!", hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_demo_password_rejected_when_demo_mode_false():
    """Verify that 'meridian2026' does NOT bypass authentication when DEMO_MODE is False."""
    original_demo_mode = settings.DEMO_MODE
    try:
        settings.DEMO_MODE = False
        hashed = hash_password("Password123!")
        # meridian2026 should fail because DEMO_MODE is False and hashed is Password123!
        assert verify_password("meridian2026", hashed) is False
        # But legitimate password still works!
        assert verify_password("Password123!", hashed) is True
    finally:
        settings.DEMO_MODE = original_demo_mode


def test_demo_password_accepted_when_demo_mode_true():
    """Verify that 'meridian2026' is accepted when DEMO_MODE is True in development."""
    original_demo_mode = settings.DEMO_MODE
    try:
        settings.DEMO_MODE = True
        hashed = hash_password("Password123!")
        assert verify_password("meridian2026", hashed) is True
    finally:
        settings.DEMO_MODE = original_demo_mode


# ==============================================================================
# 4. Token Cryptography & Expiration Tests
# ==============================================================================

def test_token_creation_and_tamper_detection():
    """Verify that altering token payload or signature invalidates it."""
    payload = {"sub": "usr-alex-01", "role": "CONSUMER"}
    token = create_access_token(payload)

    # Valid token decodes correctly
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "usr-alex-01"

    # Tampered signature fails
    parts = token.split(".")
    tampered_sig = parts[0] + "." + parts[1] + "." + parts[2][:-4] + "xxxx"
    assert decode_access_token(tampered_sig) is None

    # Tampered payload fails signature check
    tampered_payload = parts[0] + ".eyJhZG1pbiI6dHJ1ZX0." + parts[2]
    assert decode_access_token(tampered_payload) is None


def test_expired_token_rejected():
    """Verify that an expired token is rejected."""
    payload = {"sub": "usr-alex-01", "role": "CONSUMER"}
    from datetime import timedelta
    # Expired token with negative delta
    token = create_access_token(payload, expires_delta=timedelta(seconds=-10))
    assert decode_access_token(token) is None


# ==============================================================================
# 5. API Route Integration Tests (Demo vs Non-Demo)
# ==============================================================================

@pytest.mark.asyncio
async def test_api_demo_credentials_gated_by_demo_mode():
    """Verify that /api/auth/demo-credentials returns 403 when DEMO_MODE=False."""
    original_demo = settings.DEMO_MODE
    try:
        settings.DEMO_MODE = False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/auth/demo-credentials")
            assert res.status_code == 403

        settings.DEMO_MODE = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/auth/demo-credentials")
            assert res.status_code == 200
            assert res.json()["password"] == "meridian2026"
    finally:
        settings.DEMO_MODE = original_demo


@pytest.mark.asyncio
async def test_api_login_with_hashed_password():
    """Verify login with hashed password succeeds regardless of DEMO_MODE."""
    await init_database()
    from app.core.database import AsyncSessionLocal
    from app.models.entities import User

    # Explicitly ensure usr-alex-01 has a known password_hash
    async with AsyncSessionLocal() as session:
        user = await session.get(User, "usr-alex-01")
        if user:
            user.password_hash = hash_password("Password123!")
            await session.commit()

    original_demo = settings.DEMO_MODE
    try:
        settings.DEMO_MODE = False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Login with seeded user's actual password
            res = await client.post(
                "/api/auth/login",
                json={"email": "alex.rivera@demo.meridian", "password": "Password123!"},
            )
            assert res.status_code == 200
            data = res.json()
            assert "access_token" in data
            assert data["user_id"] == "usr-alex-01"

            # Demo password fails when DEMO_MODE=False
            bad_res = await client.post(
                "/api/auth/login",
                json={"email": "alex.rivera@demo.meridian", "password": "meridian2026"},
            )
            assert bad_res.status_code == 401
    finally:
        settings.DEMO_MODE = original_demo


# ==============================================================================
# 6. PayPal Sandbox Enforcement Tests
# ==============================================================================

def test_paypal_live_url_rejected():
    """Verify that configuring a live PayPal URL is rejected."""
    strong_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            SECRET_KEY=strong_key,
            PAYPAL_MODE="sandbox",
            PAYPAL_BASE_URL="https://api-m.paypal.com",
        )
    assert "Non-sandbox PayPal URL rejected" in str(exc_info.value)
