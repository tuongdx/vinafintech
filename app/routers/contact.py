"""Tin nhắn gửi từ trang "Liên hệ" (frontend).

- POST /contact: công khai, ai cũng gửi được (không cần đăng nhập).
- GET /contact: danh sách dành cho quản trị (super-admin/admin/support).
- PATCH /contact/{id}: đánh dấu đã đọc (super-admin/admin/support).
- DELETE /contact/{id}: xóa (super-admin/admin).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import ContactMessage, RoleEnum, User
from app.schemas import ContactMessageCreate, ContactMessageOut

router = APIRouter(prefix="/contact", tags=["contact"])


@router.post("", response_model=ContactMessageOut)
def create_contact_message(payload: ContactMessageCreate, db: Session = Depends(get_db)):
    msg = ContactMessage(
        full_name=payload.full_name.strip(),
        email=payload.email.strip(),
        phone=(payload.phone or "").strip() or None,
        subject=payload.subject.strip(),
        message=payload.message.strip(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


@router.get("", response_model=list[ContactMessageOut])
def list_contact_messages(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin, RoleEnum.admin, RoleEnum.support)),
):
    return db.query(ContactMessage).order_by(ContactMessage.id.desc()).all()


@router.patch("/{message_id}", response_model=ContactMessageOut)
def mark_contact_message_read(
    message_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin, RoleEnum.admin, RoleEnum.support)),
):
    msg = db.query(ContactMessage).filter(ContactMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.is_read = True
    db.commit()
    db.refresh(msg)
    return msg


@router.delete("/{message_id}")
def delete_contact_message(
    message_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin, RoleEnum.admin)),
):
    msg = db.query(ContactMessage).filter(ContactMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    db.delete(msg)
    db.commit()
    return {"message": "Deleted"}
