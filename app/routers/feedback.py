"""Góp ý từ người dùng (Tin tức / Thị trường).

- POST /feedback: công khai (ai cũng gửi được, nếu đăng nhập sẽ lưu tên/email).
- GET /feedback: quản trị, lọc theo quyền:
    - super-admin: đọc cả góp ý Tin tức + Thị trường
    - admin: đọc góp ý Thị trường
    - content-manager: đọc góp ý Tin tức
    - editor / support: không được xem
- PATCH /feedback/{id}: đánh dấu đã đọc (cùng quyền xem).
- DELETE /feedback/{id}: xóa (super-admin/admin).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_optional_user, require_roles
from app.db import get_db
from app.models import Feedback, RoleEnum, User
from app.schemas import FeedbackCreate, FeedbackOut

router = APIRouter(prefix="/feedback", tags=["feedback"])

_FEEDBACK_ROLES = (RoleEnum.super_admin, RoleEnum.admin, RoleEnum.content_manager)
_TYPE_NEWS = "news"
_TYPE_MARKET = "market"


def _allowed_types(role: RoleEnum) -> tuple[str, ...]:
    if role == RoleEnum.super_admin:
        return (_TYPE_NEWS, _TYPE_MARKET)
    if role == RoleEnum.admin:
        return (_TYPE_MARKET,)
    if role == RoleEnum.content_manager:
        return (_TYPE_NEWS,)
    return ()


@router.post("", response_model=FeedbackOut)
def create_feedback(
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    ftype = (payload.type or "").strip().lower()
    if ftype not in (_TYPE_NEWS, _TYPE_MARKET):
        ftype = _TYPE_NEWS
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Nội dung góp ý không được để trống.")

    fb = Feedback(
        type=ftype,
        content=content,
        user_id=user.id if user else None,
        user_name=(payload.user_name or (user.full_name if user else "") or "").strip() or None,
        user_email=(payload.user_email or (user.email if user else "") or "").strip() or None,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


@router.get("", response_model=list[FeedbackOut])
def list_feedback(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_FEEDBACK_ROLES)),
):
    allowed = _allowed_types(user.role)
    query = db.query(Feedback)
    if allowed:
        query = query.filter(Feedback.type.in_(allowed))
    return query.order_by(Feedback.id.desc()).all()


@router.patch("/{feedback_id}", response_model=FeedbackOut)
def mark_feedback_read(
    feedback_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_FEEDBACK_ROLES)),
):
    allowed = _allowed_types(user.role)
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    if fb.type not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden")
    fb.is_read = True
    db.commit()
    db.refresh(fb)
    return fb


@router.delete("/{feedback_id}")
def delete_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin, RoleEnum.admin)),
):
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    db.delete(fb)
    db.commit()
    return {"message": "Deleted"}
