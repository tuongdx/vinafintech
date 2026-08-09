from datetime import datetime, timedelta, timezone
import secrets
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(subject: str, expires_minutes: int | None = None, extra: dict[str, Any] | None = None) -> str:
    """Access token — kèm ``jti`` để có thể thu hồi khi logout."""
    now = datetime.now(timezone.utc)
    ttl = expires_minutes if expires_minutes is not None else settings.access_token_expire_minutes
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid4()),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ttl),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(subject: str, expires_minutes: int | None = None) -> str:
    """Refresh token — có thể bị thu hồi (revoke) khi logout / rotate."""
    now = datetime.now(timezone.utc)
    ttl = expires_minutes if expires_minutes is not None else settings.refresh_token_expire_minutes
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid4()),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(minutes=ttl),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None


def generate_reset_token() -> str:
    """Token reset mật khẩu — ngẫu nhiên, dùng 1 lần, có thời hạn."""
    return secrets.token_urlsafe(32)


def token_exp_from_payload(payload: dict[str, Any]) -> datetime:
    exp = payload.get("exp")
    if exp:
        try:
            return datetime.fromtimestamp(int(exp), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    return datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
