import os
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.core.database import get_db
from app.models.entities import User
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])

JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_SECONDS = 86400  # 24 hours


def get_jwt_secret() -> str:
    secret = (getattr(settings, "SECRET_KEY", "") or "").strip()
    if not secret or len(secret) < 32:
        raise RuntimeError("SECURITY ERROR: Valid SECRET_KEY (min 32 chars) must be configured.")
    return secret


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64decode(data: str) -> bytes:
    padding = "=" * (4 - (len(data) % 4)) if len(data) % 4 != 0 else ""
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"pbkdf2_sha256$100000${salt}${dk.hex()}"


def verify_password(password: str, hashed: Optional[str]) -> bool:
    # Gated demo override: ONLY permitted when DEMO_MODE=True and APP_ENV != "production"
    if getattr(settings, "DEMO_MODE", False) and getattr(settings, "APP_ENV", "").lower() not in ("production", "prod"):
        if password == "meridian2026":
            return True

    if not hashed:
        return False

    # Check if format is pbkdf2_sha256$iterations$salt$hash
    parts = hashed.split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        try:
            iterations = int(parts[1])
            salt = parts[2].encode("utf-8")
            expected_hash = parts[3]
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(dk.hex(), expected_hash)
        except Exception:
            return False

    # Plaintext equality fallback strictly in DEMO_MODE only
    if getattr(settings, "DEMO_MODE", False) and getattr(settings, "APP_ENV", "").lower() not in ("production", "prod"):
        if hashed == password:
            return True

    return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    jwt_secret = get_jwt_secret()
    to_encode = data.copy()
    now = int(time.time())
    expire = now + (int(expires_delta.total_seconds()) if expires_delta else TOKEN_EXPIRY_SECONDS)
    to_encode.update({"iat": now, "exp": expire})

    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))

    message = f"{header_b64}.{payload_b64}".encode("utf-8")
    sig = hmac.new(jwt_secret.encode("utf-8"), message, hashlib.sha256).digest()
    sig_b64 = _b64encode(sig)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> Optional[dict]:
    try:
        jwt_secret = get_jwt_secret()
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        message = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(jwt_secret.encode("utf-8"), message, hashlib.sha256).digest()
        actual_sig = _b64decode(sig_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None

        return payload
    except Exception:
        return None


# Request & Response Schemas
class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    full_name: str
    email: str
    role: str


class CurrentUserResponse(BaseModel):
    user_id: str
    full_name: str
    email: str
    role: str
    is_active: bool


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    normalized_email = req.email.strip().lower()

    # Query for matching user (support alex@meridian.demo alias for primary demo user usr-alex-01)
    if normalized_email in ["alex@meridian.demo", "alex.rivera@demo.meridian"]:
        stmt = select(User).where(or_(User.email.ilike(normalized_email), User.id == "usr-alex-01"))
    else:
        stmt = select(User).where(User.email.ilike(normalized_email))

    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(req.password, getattr(user, "password_hash", None)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        {
            "sub": user.id,
            "email": user.email,
            "name": user.full_name,
            "role": str(user.role.value if hasattr(user.role, "value") else user.role),
        }
    )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=str(user.role.value if hasattr(user.role, "value") else user.role),
    )


@router.post("/logout")
async def logout():
    return {"message": "Successfully logged out"}


@router.get("/me", response_model=CurrentUserResponse)
async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return CurrentUserResponse(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=str(user.role.value if hasattr(user.role, "value") else user.role),
        is_active=user.is_active,
    )


@router.get("/demo-credentials")
async def demo_credentials():
    # Only return demo credentials if DEMO_MODE is explicitly enabled and not in production
    if not getattr(settings, "DEMO_MODE", False) or getattr(settings, "APP_ENV", "").lower() in ("production", "prod"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo credentials endpoint is disabled. Set DEMO_MODE=true in non-production environments to enable.",
        )

    return {
        "email": "alex@meridian.demo",
        "alias_email": "alex.rivera@demo.meridian",
        "password": "meridian2026",
        "user_id": "usr-alex-01",
        "full_name": "Alex Rivera",
        "role": "CONSUMER",
    }
