"""Tài sản tự khai báo ngoài cổ phiếu (Crypto / Tiền gửi NH / Vàng-bạc).

- GET    /api/assets?class=crypto|deposit|gold   → danh sách khoản của user
- POST   /api/assets                             → thêm 1 khoản
- PUT    /api/assets/{id}                        → cập nhật khoản
- DELETE /api/assets/{id}                        → xoá khoản
Yêu cầu đăng nhập (JWT). Mỗi khoản có `amount` = SỐ TIỀN ĐẦU TƯ (VND).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import User, UserAsset, UserAssetClass
from app.schemas import UserAssetCreate, UserAssetOut, UserAssetUpdate

router = APIRouter(prefix="/assets", tags=["assets"])

_CLASSES = {"crypto", "deposit", "gold"}


def _to_out(row: UserAsset) -> UserAssetOut:
    return UserAssetOut(
        id=row.id,
        asset_class=row.asset_class.value if hasattr(row.asset_class, "value") else str(row.asset_class),
        label=row.label or "",
        quantity=row.quantity,
        unit_price=row.unit_price,
        amount=float(row.amount or 0),
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[UserAssetOut])
def list_assets(
    asset_class: str | None = Query(default=None, alias="class", description="crypto | deposit | gold"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(UserAsset).filter(UserAsset.user_id == user.id)
    if asset_class:
        if asset_class not in _CLASSES:
            raise HTTPException(status_code=400, detail="asset_class không hợp lệ")
        q = q.filter(UserAsset.asset_class == UserAssetClass(asset_class))
    rows = q.order_by(UserAsset.created_at.desc()).all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=UserAssetOut)
def create_asset(payload: UserAssetCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.asset_class not in _CLASSES:
        raise HTTPException(status_code=400, detail="asset_class không hợp lệ")
    row = UserAsset(
        user_id=user.id,
        asset_class=UserAssetClass(payload.asset_class),
        label=(payload.label or "").strip(),
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        amount=float(payload.amount or 0),
        note=(payload.note or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.put("/{asset_id}", response_model=UserAssetOut)
def update_asset(
    asset_id: int,
    payload: UserAssetUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(UserAsset).filter(UserAsset.id == asset_id, UserAsset.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Khoản không tồn tại")
    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.quantity is not None:
        row.quantity = payload.quantity
    if payload.unit_price is not None:
        row.unit_price = payload.unit_price
    if payload.amount is not None:
        row.amount = float(payload.amount)
    if payload.note is not None:
        row.note = payload.note.strip() or None
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(UserAsset).filter(UserAsset.id == asset_id, UserAsset.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Khoản không tồn tại")
    db.delete(row)
    db.commit()
    return {"ok": True}
