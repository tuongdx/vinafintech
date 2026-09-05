"""Gói hội viên: trạng thái + member tự mua gói (mock thanh toán) + hạn mức miễn phí."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import MemberQuota, User
from app.services import access, feature_flags

router = APIRouter(prefix="/membership", tags=["membership"])


class SubscribeRequest(BaseModel):
    product: str


def _quota_row(db: Session, user: User) -> MemberQuota:
    period = access.period_key()
    row = (
        db.query(MemberQuota)
        .filter(MemberQuota.user_id == user.id, MemberQuota.period == period)
        .first()
    )
    if row is None:
        row = MemberQuota(user_id=user.id, period=period, ai_questions=0, ai_new_chats=0)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/status")
def membership_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    active = access.is_active_member(user)
    # Block 'ai_unlimited' MỞ → mọi user không giới hạn lượt hỏi/trò chuyện.
    ai_open = feature_flags.is_open(db, "ai_unlimited")
    unlimited = active or ai_open
    q = _quota_row(db, user)
    # Các tính năng đang KHOÁ hội viên (membership mới giúp user mở được)
    perks = [
        {"key": r["key"], "name": r["name"], "description": r["description"]}
        for r in feature_flags.block_rows(db)
        if not r["enabled"]
    ]
    return {
        "plan": user.plan.value if hasattr(user.plan, "value") else str(user.plan),
        "plan_label": access.PLAN_LABELS.get(user.plan, "Miễn phí"),
        "active": active,
        "subscribed_at": user.membership_started_at.isoformat() if user.membership_started_at else None,
        "amount": user.membership_amount,
        "expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        "products": access.products(),
        "perks": perks,
        "quota": {
            "period": q.period,
            "ai_questions_used": q.ai_questions,
            "ai_questions_max": None if unlimited else 0,
            "ai_new_chats_used": q.ai_new_chats,
            "ai_new_chats_max": None if unlimited else 0,
        },
    }


@router.post("/subscribe")
def subscribe(
    payload: SubscribeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mở gói hội viên (mock thanh toán). Chọn 'monthly' (99k/tháng) hoặc 'yearly' (999k/năm)."""
    product = access.activate_subscription(db, user, payload.product)
    if product is None:
        raise HTTPException(
            status_code=400,
            detail="Gói không hợp lệ. Chỉ chọn 'monthly' hoặc 'yearly'.",
        )
    return {
        "ok": True,
        "message": f"Đã mở gói {product['name']}.",
        "product": {k: v for k, v in product.items() if k != "tier"},
        "expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
    }
