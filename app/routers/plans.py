from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import Plan, RoleEnum, User
from app.schemas import PlanCreate, PlanOut

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)):
    return db.query(Plan).all()


@router.post("", response_model=PlanOut)
def create_plan(
    payload: PlanCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    plan = Plan(name=payload.name, tier=payload.tier, price=payload.price)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.put("/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: int,
    payload: PlanCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan.name = payload.name
    plan.tier = payload.tier
    plan.price = payload.price
    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/{plan_id}")
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.delete(plan)
    db.commit()
    return {"message": "Deleted"}


@router.post("/{plan_id}/toggle", response_model=PlanOut)
def toggle_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan.is_active = not plan.is_active
    db.commit()
    db.refresh(plan)
    return plan
