from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import Feature, RoleEnum, User
from app.schemas import FeatureCreate, FeatureOut
from app.services import feature_flags

router = APIRouter(prefix="/features", tags=["features"])


@router.get("", response_model=list[FeatureOut])
def list_features(db: Session = Depends(get_db)):
    # Đảm bảo các dòng block website luôn tồn tại (mặc định MỞ cho tất cả).
    feature_flags.ensure_block_features(db)
    return db.query(Feature).all()


@router.post("", response_model=FeatureOut)
def create_feature(
    payload: FeatureCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    feature = Feature(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        access_level=payload.access_level,
        enabled=payload.enabled,
    )
    db.add(feature)
    db.commit()
    db.refresh(feature)
    return feature


@router.put("/{feature_id}", response_model=FeatureOut)
def update_feature(
    feature_id: int,
    payload: FeatureCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.admin, RoleEnum.super_admin)),
):
    feature = db.query(Feature).filter(Feature.id == feature_id).first()
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    feature.key = payload.key
    feature.name = payload.name
    feature.description = payload.description
    feature.access_level = payload.access_level
    feature.enabled = payload.enabled
    db.commit()
    db.refresh(feature)
    return feature


@router.delete("/{feature_id}")
def delete_feature(
    feature_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    feature = db.query(Feature).filter(Feature.id == feature_id).first()
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    db.delete(feature)
    db.commit()
    return {"message": "Deleted"}


@router.post("/free-mode")
def toggle_free_mode(
    enabled: bool,
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    return {"free_mode": enabled}
