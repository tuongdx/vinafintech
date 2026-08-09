from datetime import datetime, timedelta
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_optional_user, oauth2_scheme_optional
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_reset_token,
    get_password_hash,
    token_exp_from_payload,
    verify_password,
)
from app.db import get_db
from app.models import PasswordResetToken, PlanTierEnum, RevokedToken, RoleEnum, SocialAccount, User
from app.schemas import AuthResponse, ChangePasswordRequest, LoginRequest, RegisterRequest, ResetPasswordRequest, UserOut
from app.services import oauth_service
from app.services.email_service import build_reset_email_html, send_email

router = APIRouter(prefix="/auth", tags=["auth"])


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


# ---------------------------------------------------------------------------
# Rate limit login (chống brute-force) — in-memory, theo email + IP.
# ---------------------------------------------------------------------------
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}


def _login_key(email: str, ip: str | None) -> str:
    return f"{email.strip().lower()}|{ip or ''}"


def _check_rate_limit(key: str) -> None:
    now = time.time()
    window = settings.login_rate_limit_minutes * 60
    attempts = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < window]
    if len(attempts) >= settings.login_rate_limit_max:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau 15 phút.",
        )
    attempts.append(now)
    _LOGIN_ATTEMPTS[key] = attempts


def _clear_rate_limit(key: str) -> None:
    _LOGIN_ATTEMPTS.pop(key, None)


def _revoke(db: Session, token: str) -> None:
    payload = decode_token(token)
    if payload and payload.get("jti"):
        jti = str(payload["jti"])
        if not db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
            db.add(RevokedToken(jti=jti, expires_at=token_exp_from_payload(payload)))
            db.commit()


def _issue_tokens(user: User, remember: bool) -> AuthResponse:
    access_minutes = settings.access_token_expire_minutes
    refresh_minutes = settings.refresh_token_expire_minutes if remember else (60 * 24)  # 1 ngày nếu không ghi nhớ
    access = create_access_token(str(user.id), access_minutes, extra={"role": user.role.value})
    refresh = create_refresh_token(str(user.id), refresh_minutes)
    return AuthResponse(access_token=access, refresh_token=refresh, user=UserOut.model_validate(user))


def _safe_user_lookup(email: str, db: Session) -> User | None:
    return db.query(User).filter(User.email == email.lower()).first()


# ---------------------------------------------------------------------------
# Đăng ký
# ---------------------------------------------------------------------------
@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if not payload.terms_accepted:
        raise HTTPException(status_code=400, detail="Bạn phải đồng ý với Điều khoản sử dụng và Chính sách bảo mật.")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự.")

    existing = _safe_user_lookup(payload.email, db)
    if existing:
        raise HTTPException(status_code=400, detail="Email này đã được đăng ký.")

    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name.strip(),
        phone=payload.phone.strip() if payload.phone else None,
        hashed_password=get_password_hash(payload.password),
        role=RoleEnum.member,
        plan=PlanTierEnum.free,
        terms_accepted=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Đăng nhập
# ---------------------------------------------------------------------------
@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db), request: Request = None):  # type: ignore[assignment]
    ip = request.client.host if request is not None else None
    key = _login_key(payload.email, ip)
    _check_rate_limit(key)

    user = _safe_user_lookup(payload.email, db)
    # Message chung — không lộ email có tồn tại hay không
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email hoặc mật khẩu không đúng.")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tài khoản của bạn đã bị khóa.")

    _clear_rate_limit(key)
    user.last_login = datetime.utcnow()
    db.commit()
    return _issue_tokens(user, payload.remember)


# ---------------------------------------------------------------------------
# Đăng xuất — thu hồi cả access + refresh token
# ---------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: LogoutRequest | None = None,
    token: str | None = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
):
    if token:
        _revoke(db, token)
    if payload and payload.refresh_token:
        _revoke(db, payload.refresh_token)
    return None


# ---------------------------------------------------------------------------
# Refresh token (rotate — thu hồi token cũ)
# ---------------------------------------------------------------------------
@router.post("/refresh-token", response_model=AuthResponse)
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if not data or data.get("type") != "refresh" or "sub" not in data or "jti" not in data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token không hợp lệ.")

    if db.query(RevokedToken).filter(RevokedToken.jti == str(data["jti"])).first():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token đã bị thu hồi.")

    try:
        user_id = int(data["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token không hợp lệ.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tài khoản không hợp lệ.")

    # Rotate: thu hồi refresh cũ, cấp cặp mới
    db.add(RevokedToken(jti=str(data["jti"]), expires_at=token_exp_from_payload(data)))
    db.commit()
    return _issue_tokens(user, remember=True)


# ---------------------------------------------------------------------------
# Thông tin user hiện tại
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


# ---------------------------------------------------------------------------
# Quên mật khẩu — token ngẫu nhiên, có hạn, dùng 1 lần, không lộ email tồn tại
# ---------------------------------------------------------------------------
@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = _safe_user_lookup(payload.email, db)
    # Luôn trả cùng message (chống enum email)
    generic = "Nếu email tồn tại, chúng tôi đã gửi link đặt lại mật khẩu."
    if not user:
        return {"message": generic}

    token = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=settings.reset_token_expire_minutes),
        )
    )
    db.commit()

    reset_url = f"{settings.frontend_url}/reset-password?token={token}"
    send_email(user.email, "TuniX - Đặt lại mật khẩu", build_reset_email_html(reset_url))
    return {"message": generic}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == payload.token).first()
    if not record or record.used_at is not None or record.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Link đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự.")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Tài khoản không tồn tại.")

    user.hashed_password = get_password_hash(payload.password)
    record.used_at = datetime.utcnow()
    db.commit()
    return {"message": "Mật khẩu đã được cập nhật. Bạn có thể đăng nhập."}


# ---------------------------------------------------------------------------
# Đổi mật khẩu (khi đã đăng nhập)
# ---------------------------------------------------------------------------
@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự.")
    current_user.hashed_password = get_password_hash(payload.new_password)
    db.commit()
    return {"message": "Đã đổi mật khẩu thành công."}


# ---------------------------------------------------------------------------
# OAuth Google / Facebook
# ---------------------------------------------------------------------------
@router.get("/oauth/{provider}/authorize")
def oauth_authorize(provider: str, next: str | None = Query(default=None), db: Session = Depends(get_db)):
    if provider not in oauth_service._PROVIDERS:
        raise HTTPException(status_code=404, detail="Provider không hợp lệ.")
    if not oauth_service.is_configured(provider):
        return RedirectResponse(url=f"{settings.frontend_url}/Login?oauth_error=not_configured&provider={provider}")
    state = oauth_service.store_state(next)
    return RedirectResponse(url=oauth_service.authorize_url(provider, state))


def _oauth_callback(db: Session, provider: str, code: str | None, state: str | None, oauth_error: str | None):
    next_url = oauth_service.consume_state(state)
    if not next_url:
        next_url = "/"

    if oauth_error:
        return RedirectResponse(url=f"{settings.frontend_url}/Login?oauth_error={quote(oauth_error)}")

    if not code:
        return RedirectResponse(url=f"{settings.frontend_url}/Login?oauth_error=access_denied")

    try:
        profile = oauth_service.exchange_code(provider, code)
    except Exception:  # noqa: BLE001
        return RedirectResponse(url=f"{settings.frontend_url}/Login?oauth_error=oauth_failed&provider={provider}")

    if not profile.get("provider_user_id"):
        return RedirectResponse(url=f"{settings.frontend_url}/Login?oauth_error=oauth_failed&provider={provider}")

    account = (
        db.query(SocialAccount)
        .filter(SocialAccount.provider == provider, SocialAccount.provider_user_id == profile["provider_user_id"])
        .first()
    )

    if account:
        user = account.user
    else:
        email = profile.get("email")
        if not email:
            # Facebook không trả email → yêu cầu bổ sung (không tạo tài khoản lỗi)
            return RedirectResponse(url=f"{settings.frontend_url}/Login?oauth_error=email_required&provider={provider}")

        user = _safe_user_lookup(email, db)
        if user:
            # Liên kết provider với tài khoản email đã tồn tại
            db.add(
                SocialAccount(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=profile["provider_user_id"],
                    provider_email=email,
                )
            )
        else:
            user = User(
                email=email,
                full_name=profile.get("full_name") or "",
                avatar=profile.get("avatar") or "",
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                role=RoleEnum.member,
                plan=PlanTierEnum.free,
                terms_accepted=True,
            )
            db.add(user)
            db.flush()
            db.add(
                SocialAccount(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=profile["provider_user_id"],
                    provider_email=email,
                )
            )

    user.last_login = datetime.utcnow()
    db.commit()

    tokens = _issue_tokens(user, remember=True)
    q = quote(next_url)
    return RedirectResponse(
        url=(
            f"{settings.frontend_url}/Login?oauth=success"
            f"&access_token={tokens.access_token}&refresh_token={tokens.refresh_token}&next={q}"
        )
    )


@router.get("/oauth/google/callback")
def oauth_google_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _oauth_callback(db, "google", code, state, error)


@router.get("/oauth/facebook/callback")
def oauth_facebook_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _oauth_callback(db, "facebook", code, state, error)
