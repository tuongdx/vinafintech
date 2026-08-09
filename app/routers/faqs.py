from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import FAQ, RoleEnum, User
from app.schemas import FAQCreate, FAQOut

router = APIRouter(prefix="/faqs", tags=["faqs"])


@router.get("", response_model=list[FAQOut])
def list_faqs(category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(FAQ).filter(FAQ.is_visible.is_(True))
    if category:
        query = query.filter(FAQ.category == category)
    return query.all()


@router.get("/all", response_model=list[FAQOut])
def list_all_faqs(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    """Danh sách toàn bộ FAQ (kể cả ẩn) dành cho trang quản trị."""
    return db.query(FAQ).order_by(FAQ.id.desc()).all()


@router.post("", response_model=FAQOut)
def create_faq(
    payload: FAQCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    faq = FAQ(
        question=payload.question,
        answer=payload.answer,
        category=payload.category or "",
        is_visible=payload.is_visible,
    )
    db.add(faq)
    db.commit()
    db.refresh(faq)
    return faq


@router.put("/{faq_id}", response_model=FAQOut)
def update_faq(
    faq_id: int,
    payload: FAQCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    faq = db.query(FAQ).filter(FAQ.id == faq_id).first()
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    faq.question = payload.question
    faq.answer = payload.answer
    faq.category = payload.category or ""
    faq.is_visible = payload.is_visible
    db.commit()
    db.refresh(faq)
    return faq


@router.delete("/{faq_id}")
def delete_faq(
    faq_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    faq = db.query(FAQ).filter(FAQ.id == faq_id).first()
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    db.delete(faq)
    db.commit()
    return {"message": "Deleted"}
