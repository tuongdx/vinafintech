"""OAuth 2.0 / OpenID Connect cho Google & Facebook.

- Luồng chuẩn Authorization Code, verify hoàn toàn ở BACKEND (không tin dữ liệu frontend gửi lên).
- ``state`` ngẫu nhiên được lưu server-side (in-memory, có TTL) để chống CSRF.
- Khi chưa cấu hình client id/secret trong .env, các endpoint authorize sẽ trả hướng dẫn
  (dev mode) thay vì lỗi 500.
"""
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings

logger = logging.getLogger("tunix.oauth")

_PROVIDERS = {"google", "facebook"}

# state -> {next, created_at}; in-memory (single process) — note trong report.
_STATE_STORE: dict[str, dict[str, Any]] = {}
_STATE_TTL_SECONDS = 600  # 10 phút


def _clean_state() -> None:
    now = time.time()
    expired = [k for k, v in _STATE_STORE.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        _STATE_STORE.pop(k, None)


def store_state(next_url: str | None) -> str:
    _clean_state()
    state = secrets.token_urlsafe(24)
    _STATE_STORE[state] = {"next": next_url or "/", "created_at": time.time()}
    return state


def consume_state(state: str | None) -> str | None:
    if not state:
        return None
    _clean_state()
    item = _STATE_STORE.pop(state, None)
    return item["next"] if item else None


def _client_config(provider: str) -> tuple[str, str, str] | None:
    if provider == "google":
        if settings.google_client_id and settings.google_client_secret and settings.google_redirect_uri:
            return settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri
    elif provider == "facebook":
        if settings.facebook_client_id and settings.facebook_client_secret and settings.facebook_redirect_uri:
            return settings.facebook_client_id, settings.facebook_client_secret, settings.facebook_redirect_uri
    return None


def is_configured(provider: str) -> bool:
    return _client_config(provider) is not None


def authorize_url(provider: str, state: str) -> str:
    if provider == "google":
        return (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urlencode(
                {
                    "client_id": settings.google_client_id,
                    "redirect_uri": settings.google_redirect_uri,
                    "response_type": "code",
                    "scope": "openid email profile",
                    "state": state,
                    "prompt": "select_account",
                }
            )
        )
    if provider == "facebook":
        return (
            "https://www.facebook.com/v19.0/dialog/oauth?"
            + urlencode(
                {
                    "client_id": settings.facebook_client_id,
                    "redirect_uri": settings.facebook_redirect_uri,
                    "state": state,
                    "scope": "email",
                }
            )
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _http(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any]:
    req = Request(url, data=data, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as resp:
        import json

        return json.loads(resp.read().decode("utf-8"))


def _google_exchange_code(code: str) -> dict[str, Any]:
    client_id, client_secret, redirect_uri = _client_config("google")  # type: ignore[misc]
    body = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    token = _http(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access_token = token.get("access_token")
    if not access_token:
        raise RuntimeError("Google token exchange failed")
    info = _http(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "Mozilla/5.0"},
    )
    return {
        "provider_user_id": str(info.get("sub", "")),
        "email": (info.get("email") or "").lower(),
        "full_name": info.get("name") or "",
        "avatar": info.get("picture") or "",
    }


def _facebook_exchange_code(code: str) -> dict[str, Any]:
    client_id, client_secret, redirect_uri = _client_config("facebook")  # type: ignore[misc]
    token = _http(
        "https://graph.facebook.com/v19.0/oauth/access_token?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "client_secret": client_secret,
                "code": code,
            }
        )
    )
    access_token = token.get("access_token")
    if not access_token:
        raise RuntimeError("Facebook token exchange failed")
    info = _http(
        "https://graph.facebook.com/v19.0/me?"
        + urlencode({"fields": "id,name,email,picture.type(large)", "access_token": access_token})
    )
    avatar = ""
    pic = info.get("picture") or {}
    if isinstance(pic, dict):
        pic_data = pic.get("data") or {}
        avatar = pic_data.get("url") or ""
    return {
        "provider_user_id": str(info.get("id", "")),
        "email": (info.get("email") or "").lower(),
        "full_name": info.get("name") or "",
        "avatar": avatar,
    }


def exchange_code(provider: str, code: str) -> dict[str, Any]:
    if provider == "google":
        return _google_exchange_code(code)
    if provider == "facebook":
        return _facebook_exchange_code(code)
    raise ValueError(f"Unsupported provider: {provider}")
