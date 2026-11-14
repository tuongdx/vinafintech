from datetime import datetime, timedelta
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, oauth2_scheme_optional
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_reset_token,
    get_password_hash,
    hash_token,
    token_exp_from_payload,
    verify_password,
)
from app.db import get_db
from app.models import (
    EmailVerificationToken,
    PasswordResetToken,
    PlanTierEnum,
    RevokedToken,
    RoleEnum,
    SocialAccount,
    User,
)
from app.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    GoogleLoginRequest,
    LoginRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    UserOut,
    VerifyEmailRequest,
)
from app.services import oauth_service
from app.services.email_service import build_reset_email_html, build_verify_email_html, send_email

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
    """Cấp cặp token cho phiên đăng nhập.

    Phiên được NHỚ tới khi người dùng tự đăng xuất: refresh token có hạn rất dài
    (`settings.refresh_token_expire_minutes`) và mỗi lần refresh lại được cấp mới,
    nên chỉ kết thúc khi đăng xuất (token bị thu hồi) hoặc token bị xoá ở trình duyệt.

    Tham số `remember` (tick "Ghi nhớ" ở trang đăng nhập) KHÔNG còn ảnh hưởng thời hạn
    phiên — giữ lại để tương thích API/schema; frontend dùng nó để quyết định lưu
    email + mật khẩu cho lần đăng nhập sau.
    """
    access_minutes = settings.access_token_expire_minutes
    refresh_minutes = settings.refresh_token_expire_minutes
    access = create_access_token(str(user.id), access_minutes, extra={"role": user.role.value})
    refresh = create_refresh_token(str(user.id), refresh_minutes)
    return AuthResponse(access_token=access, refresh_token=refresh, user=UserOut.model_validate(user))


def _safe_user_lookup(email: str, db: Session) -> User | None:
    return db.query(User).filter(User.email == email.lower()).first()


# Cooldown gửi lại email xác thực (in-memory, theo email) — chống spam
_VERIFY_SENT: dict[str, float] = {}


def _create_and_send_verification(db: Session, user: User) -> str:
    """Tạo token xác thực email (hash lưu DB, dùng 1 lần, có hạn) + gửi email."""
    # Vô hiệu hoá mọi token cũ chưa dùng của user (chỉ token mới nhất có hiệu lực)
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used_at.is_(None),
    ).update({EmailVerificationToken.used_at: datetime.utcnow()})
    token = generate_reset_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.utcnow() + timedelta(minutes=settings.email_verification_expire_minutes),
        )
    )
    db.commit()
    base_url = settings.app_url or settings.frontend_url
    verify_url = f"{base_url}/verify-email?token={token}"
    send_email(user.email, "VinaFintech - Xác thực email", build_verify_email_html(verify_url, user.full_name))
    return verify_url


# ---------------------------------------------------------------------------
# Đăng ký
# ---------------------------------------------------------------------------
@router.post("/register", status_code=status.HTTP_201_CREATED)
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
        # Đăng ký qua form = kích hoạt NGAY (không chặn đăng nhập chờ xác thực email).
        # Người dùng vẫn có thể yêu cầu gửi lại email xác thực ở màn hình đăng nhập nếu cần.
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": "Đăng ký thành công. Bạn có thể đăng nhập ngay.",
        "email": user.email,
    }


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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email hoặc mật khẩu không chính xác.")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tài khoản của bạn đã bị khóa.")

    # Bỏ qua yêu cầu xác thực email cho SUPER ADMIN (tài khoản nội bộ cấp cao)
    if not user.email_verified and user.role != RoleEnum.super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn chưa xác thực địa chỉ email.",
            headers={"X-Email-Verified": "false"},
        )

    _clear_rate_limit(key)
    user.last_login = datetime.utcnow()
    db.commit()
    return _issue_tokens(user, payload.remember)


# ---------------------------------------------------------------------------
# Xác thực email
# ---------------------------------------------------------------------------
@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    """Xác thực email qua token (hash lưu DB, dùng 1 lần, có thời hạn)."""
    record = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.token_hash == hash_token(payload.token))
        .first()
    )
    if not record or record.used_at is not None:
        raise HTTPException(status_code=400, detail="Liên kết xác thực không hợp lệ.")
    if record.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Liên kết xác thực đã hết hạn.")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Tài khoản không tồn tại.")

    user.email_verified = True
    record.used_at = datetime.utcnow()
    db.commit()
    return {"message": "Email đã được xác thực thành công."}


@router.post("/resend-verification")
def resend_verification(payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    """Gửi lại email xác thực — cooldown 60s, không lộ email có tồn tại hay không."""
    generic = "Nếu email tồn tại và chưa được xác thực, chúng tôi đã gửi lại email xác thực."
    user = _safe_user_lookup(payload.email, db)
    if not user or user.email_verified:
        return {"message": generic}

    now = time.time()
    last = _VERIFY_SENT.get(user.email)
    if last and now - last < settings.resend_verification_cooldown_seconds:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Vui lòng chờ 60 giây trước khi gửi lại email xác thực.",
        )
    _VERIFY_SENT[user.email] = now

    try:
        _create_and_send_verification(db, user)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Không gửi được email xác thực. Vui lòng thử lại sau.")
    return {"message": generic}


# ---------------------------------------------------------------------------
# Đăng nhập bằng Google (Google Identity Services — ID Token từ popup)
# ---------------------------------------------------------------------------
@router.post("/google", response_model=AuthResponse)
def google_login(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    if not oauth_service.is_configured("google"):
        raise HTTPException(status_code=400, detail="Đăng nhập Google chưa được cấu hình.")

    try:
        profile = oauth_service.verify_google_id_token(payload.id_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Token Google không hợp lệ hoặc đã hết hạn.") from exc

    provider_user_id = profile.get("provider_user_id")
    if not provider_user_id:
        raise HTTPException(status_code=400, detail="Không xác định được tài khoản Google.")

    # Case 1: Google Account đã liên kết với VinaFintech
    account = (
        db.query(SocialAccount)
        .filter(SocialAccount.provider == "google", SocialAccount.provider_user_id == provider_user_id)
        .first()
    )
    if account:
        user = account.user
        # Google luôn xác thực email — cập nhật nếu thiếu
        if not user.email_verified:
            user.email_verified = True
    else:
        email = profile.get("email")
        # Case 3: Email Google trùng với tài khoản Email/Password đã tồn tại
        user = _safe_user_lookup(email, db) if email else None
        if user:
            if user.email_verified and not profile.get("email_verified"):
                # An toàn: yêu cầu đăng nhập bằng mật khẩu trước khi liên kết
                raise HTTPException(
                    status_code=409,
                    detail="Email này đã thuộc tài khoản khác. Vui lòng đăng nhập bằng mật khẩu để liên kết Google.",
                )
            # Google đã xác thực email này → đánh dấu verified + liên kết
            user.email_verified = True
        else:
            # Case 2: Chưa tồn tại → tạo tài khoản mới
            user = User(
                email=email or "",
                full_name=profile.get("full_name") or "",
                avatar=profile.get("avatar") or "",
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                role=RoleEnum.member,
                plan=PlanTierEnum.free,
                terms_accepted=True,
                email_verified=bool(profile.get("email_verified")),
            )
            db.add(user)
            db.flush()
        db.add(
            SocialAccount(
                user_id=user.id,
                provider="google",
                provider_user_id=provider_user_id,
                provider_email=email,
            )
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tài khoản của bạn đã bị khóa.")

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
    generic = "Nếu địa chỉ email tồn tại trong hệ thống, chúng tôi đã gửi hướng dẫn đặt lại mật khẩu."
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

    base_url = settings.app_url or settings.frontend_url
    reset_url = f"{base_url}/reset-password?token={token}"
    send_email(user.email, "VinaFintech - Đặt lại mật khẩu", build_reset_email_html(reset_url))
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


@router.get("/reset-password/validate")
def validate_reset_token(token: str, db: Session = Depends(get_db)):
    """Kiểm tra token đặt lại mật khẩu (dùng 1 lần, có hạn) trước khi hiển thị form."""
    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if not record or record.used_at is not None:
        return {"valid": False, "expired": False, "reason": "invalid"}
    if record.expires_at < datetime.utcnow():
        return {"valid": False, "expired": True, "reason": "expired"}
    return {"valid": True, "expired": False, "reason": "ok"}


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
# OAuth Google
# ---------------------------------------------------------------------------
@router.get("/google-config")
def google_config(request: Request):
    """Trả client_id để frontend dựng nút Google Sign-In (Google Identity Services).
    Không expose secret — chỉ client_id (public) và redirect_uri đang dùng thật
    (hữu ích để đối chiếu với Google Cloud Console khi deploy domain mới)."""
    configured = oauth_service.is_configured("google")
    return {
        "client_id": settings.google_client_id,
        "configured": configured,
        "redirect_uri": oauth_service.callback_url("google", request) if configured else "",
    }


@router.get("/oauth/{provider}/authorize")
def oauth_authorize(provider: str, request: Request, next: str | None = Query(default=None), db: Session = Depends(get_db)):
    if provider not in oauth_service._PROVIDERS:
        raise HTTPException(status_code=404, detail="Provider không hợp lệ.")
    base_url = oauth_service.public_base_url(request)
    if not oauth_service.is_configured(provider):
        return RedirectResponse(url=f"{base_url}/Login?oauth_error=not_configured&provider={provider}")
    redirect_uri = oauth_service.callback_url(provider, request)
    state = oauth_service.store_state(next, redirect_uri)
    return RedirectResponse(url=oauth_service.authorize_url(provider, state, redirect_uri))


def _oauth_callback(
    db: Session,
    provider: str,
    request: Request,
    code: str | None,
    state: str | None,
    oauth_error: str | None,
):
    base_url = oauth_service.public_base_url(request)
    state_item = oauth_service.consume_state(state) or {}
    next_url = state_item.get("next") or "/"
    # Google yêu cầu redirect_uri của bước đổi code TRÙNG với bước authorize.
    redirect_uri = state_item.get("redirect_uri") or oauth_service.callback_url(provider, request)

    if oauth_error:
        return RedirectResponse(url=f"{base_url}/Login?oauth_error={quote(oauth_error)}")

    if not code:
        return RedirectResponse(url=f"{base_url}/Login?oauth_error=access_denied")

    try:
        profile = oauth_service.exchange_code(provider, code, redirect_uri)
    except Exception:  # noqa: BLE001
        return RedirectResponse(url=f"{base_url}/Login?oauth_error=oauth_failed&provider={provider}")

    if not profile.get("provider_user_id"):
        return RedirectResponse(url=f"{base_url}/Login?oauth_error=oauth_failed&provider={provider}")

    account = (
        db.query(SocialAccount)
        .filter(SocialAccount.provider == provider, SocialAccount.provider_user_id == profile["provider_user_id"])
        .first()
    )

    if account:
        user = account.user
        # Google đã xác thực email → đảm bảo trạng thái verified (tránh chặn login sau)
        if not user.email_verified:
            user.email_verified = True
    else:
        email = profile.get("email")
        if not email:
            # Nhà cung cấp không trả email → yêu cầu bổ sung (không tạo tài khoản lỗi)
            return RedirectResponse(url=f"{base_url}/Login?oauth_error=email_required&provider={provider}")

        user = _safe_user_lookup(email, db)
        if user:
            # Liên kết provider với tài khoản email đã tồn tại — Google đã xác thực email
            user.email_verified = True
            db.add(
                SocialAccount(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=profile["provider_user_id"],
                    provider_email=email,
                )
            )
        else:
            # Tài khoản mới: kích hoạt ngay (email_verified=True) — KHÔNG cần gửi mail xác thực
            user = User(
                email=email,
                full_name=profile.get("full_name") or "",
                avatar=profile.get("avatar") or "",
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                role=RoleEnum.member,
                plan=PlanTierEnum.free,
                terms_accepted=True,
                email_verified=True,
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
            f"{base_url}/Login?oauth=success"
            f"&access_token={tokens.access_token}&refresh_token={tokens.refresh_token}&next={q}"
        )
    )


@router.get("/oauth/google/callback")
def oauth_google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _oauth_callback(db, "google", request, code, state, error)
