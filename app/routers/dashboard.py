from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import News, PlanTierEnum, RoleEnum, SupportTicket, User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    users_total = db.query(func.count(User.id)).scalar() or 0
    admins_total = db.query(func.count(User.id)).filter(User.role.in_([RoleEnum.admin, RoleEnum.super_admin])).scalar() or 0
    members_total = db.query(func.count(User.id)).filter(User.role == RoleEnum.member).scalar() or 0
    paid_members_total = db.query(func.count(User.id)).filter(User.plan.in_([PlanTierEnum.basic, PlanTierEnum.premium, PlanTierEnum.pro])).scalar() or 0
    tickets_total = db.query(func.count(SupportTicket.id)).scalar() or 0
    news_total = db.query(func.count(News.id)).scalar() or 0

    return {
        "users_total": users_total,
        "admins_total": admins_total,
        "members_total": members_total,
        "paid_members_total": paid_members_total,
        "tickets_total": tickets_total,
        "news_total": news_total,
    }
