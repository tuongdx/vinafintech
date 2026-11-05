from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db import get_db
from app.models import RevokedToken, RoleEnum, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _is_token_revoked(db: Session, jti: str) -> bool:
    return db.query(RevokedToken).filter(RevokedToken.jti == jti).first() is not None


def _decode_and_fetch(token: str | None, db: Session, optional: bool = False) -> User | None:
    if not token:
        if optional:
            return None
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(token)
    if not payload or "sub" not in payload or "jti" not in payload:
        if optional:
            return None
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if _is_token_revoked(db, str(payload["jti"])):
        if optional:
            return None
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        if optional:
            return None
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        if optional:
            return None
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not active")
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user = _decode_and_fetch(token, db, optional=False)
    assert user is not None
    return user


def get_optional_user(token: str | None = Depends(oauth2_scheme_optional), db: Session = Depends(get_db)) -> User | None:
    return _decode_and_fetch(token, db, optional=True)


def require_roles(*roles: RoleEnum):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return checker


# Vai trò quản trị nội bộ — luôn có quyền dùng tính năng "Tín hiệu".
ADMIN_ROLES = (RoleEnum.super_admin, RoleEnum.admin)


def require_signal_access(user: User = Depends(get_current_user)) -> User:
    """Quyền dùng tính năng **Tín hiệu** (VinaFintech Signal Engine).

    Cho phép khi:
    - role là super-admin/admin (quản trị nội bộ), HOẶC
    - tài khoản được admin CẤP RIÊNG cờ ``users.signal_access``.

    Không đăng nhập ⇒ 401; đã đăng nhập nhưng chưa được cấp ⇒ **403**.
    Đây là kiểm tra Ở SERVER — không phụ thuộc việc frontend có ẩn menu hay không.
    """
    if user.role in ADMIN_ROLES or bool(getattr(user, "signal_access", False)):
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Tài khoản của bạn chưa được cấp quyền dùng tính năng Tín hiệu.",
    )

