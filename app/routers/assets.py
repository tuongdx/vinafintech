"""Tài sản ngoài cổ phiếu (Crypto / Tiền gửi NH / Vàng-bạc).

- GET    /api/assets?class=crypto|deposit|gold   → danh sách khoản của user
- POST   /api/assets                             → Crypto/Vàng-bạc: MUA gộp theo tên (vị thế);
                                                    Tiền gửi NH: thêm 1 khoản gửi mới
- POST   /api/assets/sell                        → Crypto/Vàng-bạc: BÁN bớt vị thế (hết thì tự xoá)
- PUT    /api/assets/{id}                        → cập nhật khoản
- DELETE /api/assets/{id}                        → xoá khoản

Yêu cầu đăng nhập (JWT). Với Crypto/Vàng-bạc mỗi TÊN = 1 VỊ THẾ duy nhất:
`quantity` = tổng số lượng đang giữ, `unit_price` = giá vốn bình quân,
`amount` = TỔNG SỐ TIỀN ĐẦU TƯ (VNĐ) = quantity × giá vốn BQ.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import User, UserAsset, UserAssetClass
from app.schemas import UserAssetCreate, UserAssetOut, UserAssetSell, UserAssetUpdate

router = APIRouter(prefix="/assets", tags=["assets"])

_CLASSES = {"crypto", "deposit", "gold"}
_TRADE_CLASSES = {"crypto", "gold"}  # loại có mua/bán gộp vị thế theo tên


def _find_position(db: Session, user: User, cls: str, label: str) -> UserAsset | None:
    """Tìm vị thế (1 dòng duy nhất) theo tên — không phân biệt hoa thường."""
    norm = (label or "").strip().lower()
    return (
        db.query(UserAsset)
        .filter(
            UserAsset.user_id == user.id,
            UserAsset.asset_class == UserAssetClass(cls),
            func.lower(UserAsset.label) == norm,
        )
        .first()
    )


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
    label = (payload.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Thiếu tên khoản")

    # ----- Crypto / Vàng-bạc: MUA (gộp vị thế theo tên) -----
    if payload.asset_class in _TRADE_CLASSES:
        qty = payload.quantity
        price = payload.unit_price
        if not (qty and qty > 0 and price and price > 0):
            raise HTTPException(status_code=400, detail="Mua cần Số lượng và Đơn giá > 0")
        amount = round(float(qty) * float(price), 2)
        ex = _find_position(db, user, payload.asset_class, label)
        if ex:
            old_qty = float(ex.quantity or 0)
            new_qty = old_qty + float(qty)
            new_amt = float(ex.amount or 0) + amount
            ex.quantity = new_qty
            ex.amount = round(new_amt, 2)
            ex.unit_price = round(new_amt / new_qty, 2) if new_qty else ex.unit_price
            ex.note = (payload.note or "").strip() or ex.note
            db.commit()
            db.refresh(ex)
            return _to_out(ex)
        row = UserAsset(
            user_id=user.id,
            asset_class=UserAssetClass(payload.asset_class),
            label=label,
            quantity=float(qty),
            unit_price=float(price),
            amount=amount,
            note=(payload.note or "").strip() or None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_out(row)

    # ----- Tiền gửi ngân hàng: mỗi lần gửi = 1 khoản riêng -----
    amount = float(payload.amount or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nhập số tiền gửi > 0")
    row = UserAsset(
        user_id=user.id,
        asset_class=UserAssetClass(payload.asset_class),
        label=label,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        amount=amount,
        note=(payload.note or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/sell")
def sell_asset(payload: UserAssetSell, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.asset_class not in _TRADE_CLASSES:
        raise HTTPException(status_code=400, detail="Chỉ Crypto / Vàng-bạc mới bán được")
    label = (payload.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Thiếu tên khoản")
    qty = payload.quantity
    if not (qty and qty > 0):
        raise HTTPException(status_code=400, detail="Số lượng bán phải > 0")

    row = _find_position(db, user, payload.asset_class, label)
    if not row:
        raise HTTPException(status_code=404, detail=f"Không có {label} trong {payload.asset_class} để bán")
    cur = float(row.quantity or 0)
    if qty > cur + 1e-6:
        raise HTTPException(status_code=400, detail=f"Chỉ còn {cur:g} để bán")

    amount = float(row.amount or 0)
    avg = amount / cur if cur > 0 else 0.0
    remaining = cur - float(qty)
    if remaining < 1e-6:
        db.delete(row)
        db.commit()
        return {"ok": True, "removed": True, "message": f"Đã bán hết {label}"}
    row.quantity = remaining
    row.amount = round(max(0.0, amount - float(qty) * avg), 2)
    row.unit_price = round(row.amount / remaining, 2)
    row.note = (payload.note or "").strip() or row.note
    db.commit()
    return {"ok": True, "removed": False, "remaining_qty": remaining, "message": f"Đã bán {qty:g} {label}"}


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
