from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_roles
from app.core.security import get_password_hash
from app.db import get_db
from app.models import PlanTierEnum, RoleEnum, User
from app.schemas import UserCreate, UserOut, UserSettingsOut, UserSettingsUpdate, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

_ADMIN_ROLES = {
    RoleEnum.super_admin,
    RoleEnum.admin,
    RoleEnum.content_manager,
    RoleEnum.editor,
    RoleEnum.support,
}


def _can_assign_role(actor: User, new_role: RoleEnum) -> bool:
    """Super-admin gán được mọi vai trò. Admin chỉ gán member/editor/content-manager/support,
    không được gán admin/super-admin."""
    if actor.role == RoleEnum.super_admin:
        return True
    if actor.role == RoleEnum.admin:
        return new_role in {
            RoleEnum.member,
            RoleEnum.editor,
            RoleEnum.content_manager,
            RoleEnum.support,
        }
    return False


def _can_manage_user(actor: User, target: User) -> bool:
    """Super-admin quản lý được mọi tài khoản. Admin không được sửa/xóa admin khác & super-admin."""
    if actor.role == RoleEnum.super_admin:
        return True
    if actor.role == RoleEnum.admin:
        return target.role not in (RoleEnum.admin, RoleEnum.super_admin)
    return False


def _last_super_admin(db: Session) -> int:
    return (
        db.query(User).filter(User.role == RoleEnum.super_admin, User.is_active.is_(True)).count() or 0
    )


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    new_role = RoleEnum.member
    if payload.role:
        try:
            new_role = RoleEnum(payload.role)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid role")
        if not _can_assign_role(current_user, new_role):
            raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tài khoản với vai trò này.")

    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        phone=payload.phone,
        hashed_password=get_password_hash(payload.password),
        role=new_role,
        plan=payload.plan,
        plan_expires_at=payload.plan_expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/me", response_model=UserOut)
def update_my_profile(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    if payload.phone is not None:
        current_user.phone = payload.phone
    if payload.avatar is not None:
        current_user.avatar = payload.avatar
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_manage_user(current_user, user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền chỉnh sửa tài khoản này.")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.avatar is not None:
        user.avatar = payload.avatar
    if payload.is_active is not None:
        # Không thể khóa super-admin cuối cùng
        if not payload.is_active and user.role == RoleEnum.super_admin and _last_super_admin(db) <= 1:
            raise HTTPException(status_code=400, detail="Không thể khóa super admin cuối cùng.")
        user.is_active = payload.is_active
    if payload.plan is not None:
        user.plan = payload.plan
    if payload.plan_expires_at is not None:
        user.plan_expires_at = payload.plan_expires_at

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Không thể xóa tài khoản của chính mình.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_manage_user(current_user, user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tài khoản này.")
    if user.role == RoleEnum.super_admin and _last_super_admin(db) <= 1:
        raise HTTPException(status_code=400, detail="Không thể xóa super admin cuối cùng.")
    db.delete(user)
    db.commit()
    return {"message": "Deleted"}


@router.patch("/{user_id}/role", response_model=UserOut)
def update_user_role(
    user_id: int,
    role: RoleEnum,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_manage_user(current_user, user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền thay đổi vai trò của tài khoản này.")
    if not _can_assign_role(current_user, role):
        raise HTTPException(status_code=403, detail="Bạn không có quyền gán vai trò này.")
    if user.role == RoleEnum.super_admin and role != RoleEnum.super_admin and _last_super_admin(db) <= 1:
        raise HTTPException(status_code=400, detail="Không thể hạ quyền super admin cuối cùng.")
    user.role = role
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/plan", response_model=UserOut)
def update_user_plan(
    user_id: int,
    plan: PlanTierEnum,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.plan = plan
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


# ---------------------------------------------------------------------------
# Cài đặt tài khoản (thông báo + giao diện)
# ---------------------------------------------------------------------------
def _settings_of(u: User) -> UserSettingsOut:
    return UserSettingsOut(
        notify_reports=u.notify_reports if u.notify_reports is not None else True,
        notify_news=u.notify_news if u.notify_news is not None else True,
        theme=u.pref_theme or "light",
    )


@router.get("/me/settings", response_model=UserSettingsOut)
def get_my_settings(current_user: User = Depends(get_current_user)):
    return _settings_of(current_user)


@router.put("/me/settings", response_model=UserSettingsOut)
def update_my_settings(
    payload: UserSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.notify_reports is not None:
        current_user.notify_reports = payload.notify_reports
    if payload.notify_news is not None:
        current_user.notify_news = payload.notify_news
    if payload.theme:
        current_user.pref_theme = "light" if payload.theme == "light" else "dark"
    db.commit()
    db.refresh(current_user)
    return _settings_of(current_user)
