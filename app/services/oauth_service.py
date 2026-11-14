"""OAuth 2.0 / OpenID Connect cho Google.

- Luồng chuẩn Authorization Code, verify hoàn toàn ở BACKEND (không tin dữ liệu frontend gửi lên).
- ``state`` ngẫu nhiên được lưu server-side (in-memory, có TTL) để chống CSRF.
- Khi chưa cấu hình client id/secret trong .env, các endpoint authorize sẽ trả hướng dẫn
  (dev mode) thay vì lỗi 500.
"""
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from app.core.config import settings

logger = logging.getLogger("tunix.oauth")

_PROVIDERS = {"google"}

# state -> {next, redirect_uri, created_at}; in-memory (single process) — note trong report.
_STATE_STORE: dict[str, dict[str, Any]] = {}
_STATE_TTL_SECONDS = 600  # 10 phút


def _clean_state() -> None:
    now = time.time()
    expired = [k for k, v in _STATE_STORE.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        _STATE_STORE.pop(k, None)


def store_state(next_url: str | None, redirect_uri: str | None = None) -> str:
    _clean_state()
    state = secrets.token_urlsafe(24)
    _STATE_STORE[state] = {
        "next": next_url or "/",
        "redirect_uri": redirect_uri or "",
        "created_at": time.time(),
    }
    return state


def consume_state(state: str | None) -> dict[str, Any] | None:
    if not state:
        return None
    _clean_state()
    return _STATE_STORE.pop(state, None)


def request_origin(request: Any) -> str:
    """Origin công khai mà trình duyệt đang dùng (đúng cả khi chạy sau proxy/HTTPS)."""
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _origin_for_request(request: Any) -> str:
    """Origin của request, nâng lên https nếu domain đã khai báo trong .env dùng https.

    Trường hợp proxy không chuyển ``X-Forwarded-Proto``: request vẫn thấy ``http``
    trong khi domain thật chạy ``https`` → nếu giữ ``http`` thì Google báo
    ``redirect_uri_mismatch``.
    """
    origin = request_origin(request)
    parsed = urlparse(origin)
    configured = (settings.frontend_url or "").strip() or (settings.google_redirect_uri or "").strip()
    configured_parsed = urlparse(configured) if configured else None
    if (
        configured_parsed
        and configured_parsed.scheme == "https"
        and parsed.scheme == "http"
        and configured_parsed.netloc.lower() == parsed.netloc.lower()
    ):
        return f"https://{parsed.netloc}"
    return origin


def _same_endpoint(a: str, b: str) -> bool:
    ua, ub = urlparse(a), urlparse(b)
    return (ua.scheme.lower(), ua.netloc.lower(), ua.path.rstrip("/")) == (
        ub.scheme.lower(),
        ub.netloc.lower(),
        ub.path.rstrip("/"),
    )


def callback_url(provider: str, request: Any) -> str:
    """``redirect_uri`` gửi Google — phải TRÙNG KHỚP Authorized redirect URI ở Console.

    Ưu tiên biến môi trường, nhưng nếu biến đó còn trỏ domain/path cũ (ví dụ
    ``https://tunix.onrender.com`` trong khi website đã chạy ở ``https://tunix.vn``)
    thì dùng host thật của request. Nếu không, Google sẽ redirect popup sang domain
    không tồn tại ⇒ trình duyệt hiện "Not Found".
    """
    derived = f"{_origin_for_request(request)}{settings.api_prefix}/auth/oauth/{provider}/callback"
    configured = (settings.google_redirect_uri or "").strip() if provider == "google" else ""
    if not configured or _same_endpoint(configured, derived):
        return derived
    logger.warning(
        "OAuth redirect_uri cấu hình (%s) không khớp domain/đường dẫn thật (%s) — dùng giá trị thật. "
        "Cần khai báo %s trong Google Cloud Console → Authorized redirect URIs.",
        configured,
        derived,
        derived,
    )
    return derived


def public_base_url(request: Any) -> str:
    """URL gốc website dùng cho redirect sau khi đăng nhập.

    Giống ``callback_url``: nếu ``FRONTEND_URL`` trỏ sai host so với domain đang
    chạy thì dùng domain thật để không đẩy người dùng sang một trang "Not Found".
    """
    origin = _origin_for_request(request)
    configured = (settings.frontend_url or "").strip().rstrip("/")
    if not configured:
        return origin
    if configured.lower() == origin.lower():
        return configured
    logger.warning(
        "FRONTEND_URL (%s) không khớp host đang chạy (%s) — dùng host thật cho OAuth.",
        configured,
        origin,
    )
    return origin


def _client_config(provider: str) -> tuple[str, str] | None:
    """Trả (client_id, client_secret). ``redirect_uri`` suy ra theo request hiện tại."""
    if provider == "google":
        if settings.google_client_id and settings.google_client_secret:
            return settings.google_client_id, settings.google_client_secret
    return None


def is_configured(provider: str) -> bool:
    return _client_config(provider) is not None


def authorize_url(provider: str, state: str, redirect_uri: str) -> str:
    if provider == "google":
        return (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urlencode(
                {
                    "client_id": settings.google_client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": "openid email profile",
                    "state": state,
                    "prompt": "select_account",
                }
            )
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _http(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any]:
    req = Request(url, data=data, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as resp:
        import json

        return json.loads(resp.read().decode("utf-8"))


def _google_exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    client_id, client_secret = _client_config("google")  # type: ignore[misc]
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


def exchange_code(provider: str, code: str, redirect_uri: str) -> dict[str, Any]:
    if provider == "google":
        return _google_exchange_code(code, redirect_uri)
    raise ValueError(f"Unsupported provider: {provider}")


# ---------------------------------------------------------------------------
# Google Identity Services — xác thực ID Token từ popup (POST /api/auth/google)
# ---------------------------------------------------------------------------
def verify_google_id_token(id_token: str) -> dict[str, Any]:
    """Xác thực ĐẦY ĐỦ Google ID Token ở backend.

    - Token hợp lệ (Google tokeninfo endpoint).
    - ``aud`` == GOOGLE_CLIENT_ID của VinaFintech.
    - ``iss`` hợp lệ (accounts.google.com).
    - Token chưa hết hạn.
    - Dùng ``sub`` làm định danh duy nhất của Google Account.

    Không tin bất kỳ dữ liệu nào từ frontend ngoài chính ``id_token``.
    """
    import httpx

    resp = httpx.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"id_token": str(id_token)},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError("Google token invalid")

    info = resp.json()
    if not isinstance(info, dict):
        raise RuntimeError("Google token invalid")

    # 1) Audience phải đúng GOOGLE_CLIENT_ID của VinaFintech
    if str(info.get("aud", "")) != settings.google_client_id:
        raise RuntimeError("Google token audience mismatch")

    # 2) Issuer hợp lệ
    if str(info.get("iss", "")) not in ("accounts.google.com", "https://accounts.google.com"):
        raise RuntimeError("Google token issuer mismatch")

    # 3) Chưa hết hạn
    exp = info.get("exp")
    if not exp or int(exp) < time.time():
        raise RuntimeError("Google token expired")

    # 4) Phải có sub (định danh duy nhất Google Account)
    sub = str(info.get("sub", "") or "")
    if not sub:
        raise RuntimeError("Google token missing sub")

    return {
        "provider_user_id": sub,
        "email": (info.get("email") or "").lower(),
        "full_name": info.get("name") or "",
        "avatar": info.get("picture") or "",
        "email_verified": bool(info.get("email_verified")),
    }
