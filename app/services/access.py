"""Trợ giúp gói hội viên: xác định trả phí/hạn & hạn mức miễn phí theo tháng."""
from __future__ import annotations

from datetime import datetime

from app.core.config import settings
from app.models import PlanTierEnum, User

_PAID_TIERS = {
    PlanTierEnum.basic,
    PlanTierEnum.premium,
    PlanTierEnum.pro,
    PlanTierEnum.monthly,
    PlanTierEnum.yearly,
}

PLAN_LABELS = {
    PlanTierEnum.free: "Miễn phí",
    PlanTierEnum.basic: "Hội viên",
    PlanTierEnum.premium: "Hội viên",
    PlanTierEnum.pro: "Hội viên",
    PlanTierEnum.monthly: "Theo tháng",
    PlanTierEnum.yearly: "Theo năm",
}


def products() -> list[dict]:
    """2 gói hội viên để member tự mua (giá lấy từ settings)."""
    return [
        {
            "code": "monthly",
            "name": "Theo tháng",
            "price": int(settings.membership_monthly_price),
            "months": 1,
            "tier": PlanTierEnum.monthly,
        },
        {
            "code": "yearly",
            "name": "Theo năm",
            "price": int(settings.membership_yearly_price),
            "months": 12,
            "tier": PlanTierEnum.yearly,
        },
    ]


def _utcnow() -> datetime:
    return datetime.utcnow()


def is_paid(plan) -> bool:
    return plan in _PAID_TIERS


def is_active_member(user: User) -> bool:
    """Trả phí và còn hạn (hoặc không đặt hạn) → coi là hội viên đang mở gói."""
    if not is_paid(user.plan):
        return False
    exp = user.plan_expires_at
    if exp is None:
        return True
    if exp.tzinfo is not None:
        exp = exp.replace(tzinfo=None)
    return exp > _utcnow()


def period_key(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).strftime("%Y-%m")


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    dim = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return dt.replace(year=year, month=month, day=min(dt.day, dim[month - 1]))


def activate_subscription(db, user: User, product_code: str) -> dict | None:
    """Mở gói cho member (mock thanh toán): set plan + hạn tháng/năm.

    Trả về product dict nếu hợp lệ, None nếu mã gói sai.
    """
    code = (product_code or "").strip().lower()
    for p in products():
        if p["code"] == code:
            # Gia hạn: nếu đang còn hạn thì cộng dồn từ ngày hết hạn hiện tại
            now = _utcnow()
            base = user.plan_expires_at if user.plan_expires_at and user.plan_expires_at > now else now
            user.plan = p["tier"]
            user.plan_expires_at = _add_months(base, p["months"])
            user.membership_amount = int(p["price"])
            if user.membership_started_at is None:
                user.membership_started_at = now
            db.commit()
            db.refresh(user)
            return p
    return None
