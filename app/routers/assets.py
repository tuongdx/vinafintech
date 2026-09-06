"""Tài sản ngoài cổ phiếu (Crypto / Tiền gửi NH / Vàng-bạc).

- GET    /api/assets?class=crypto|deposit|gold   → danh sách khoản của user
- POST   /api/assets                             → Crypto: MUA bằng USDT/USDC (amount = tiền chi ra);
                                                    Vàng-bạc: MUA theo số lượng (đơn giá VNĐ);
                                                    Tiền gửi NH: thêm 1 khoản gửi mới
- POST   /api/assets/sell                        → Bán bớt vị thế (hết thì tự xoá)
- GET    /api/assets/fx                          → tỷ giá USD/VND (cache 6h, dùng chung trang Vàng)
- PUT    /api/assets/{id}                        → cập nhật khoản
- DELETE /api/assets/{id}                        → xoá khoản

Mô hình:
- Crypto: giao dịch bằng stablecoin USDT/USDC. `amount` = số stablecoin BỎ RA (Mua) hoặc NHẬN VỀ
  (Bán); `unit_price` = giá 1 coin; `quantity` = số coin (tự suy = amount × (1−phí%) ÷ giá).
  `currency` (USDT/USDC) lưu trong `note`. Khi list gắn thêm `currency`, `vnd_amount`, `fx_rate`.
- Vàng/Bạc: giá VNĐ/lượng, nhập số lượng (lượng/chỉ) → amount = quantity × đơn giá.
- Tiền gửi NH: mỗi lần gửi = 1 khoản riêng (amount VNĐ nhập tay).
- Chung: mỗi TÊN (crypto/vàng-bạc) = 1 VỊ THẾ duy nhất gộp theo tên; `unit_price` = giá vốn BQ,
  `quantity` = tổng số lượng, `amount` = tổng tiền đầu tư.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import User, UserAsset, UserAssetClass
from app.schemas import UserAssetCreate, UserAssetOut, UserAssetSell, UserAssetUpdate
from app.services.gold_service import _get_usd_vnd

router = APIRouter(prefix="/assets", tags=["assets"])

_CLASSES = {"crypto", "deposit", "gold"}
_STABLES = {"USDT", "USDC"}


def _currency_of(row: UserAsset) -> str:
    """Stablecoin của vị thế crypto (lưu trong note), mặc định USDT."""
    note = (row.note or "").strip().upper()
    return note if note in _STABLES else "USDT"


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


def _to_out(row: UserAsset, fx: dict | None = None) -> UserAssetOut:
    out = UserAssetOut(
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
    if out.asset_class == "crypto":
        out.currency = _currency_of(row)
        rate = (fx or {}).get("usd_vnd") if fx else None
        if rate:
            out.fx_rate = float(rate)
            out.fx_as_of = (fx or {}).get("as_of")
            out.vnd_amount = round(out.amount * float(rate), 0)
    return out


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
    fx = _get_usd_vnd() if asset_class == "crypto" else None
    return [_to_out(r, fx) for r in rows]


@router.get("/fx")
def asset_fx():
    """Tỷ giá USD/VND (cache 6h, dùng chung với trang Vàng) — để quy đổi Crypto sang VND."""
    fx = _get_usd_vnd()
    if not fx:
        raise HTTPException(status_code=503, detail="Chưa lấy được tỷ giá USD/VND")
    return fx


@router.post("", response_model=UserAssetOut)
def create_asset(payload: UserAssetCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.asset_class not in _CLASSES:
        raise HTTPException(status_code=400, detail="asset_class không hợp lệ")
    label = (payload.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Thiếu tên khoản")

    # ----- Crypto: MUA bằng USDT/USDC (amount = số stablecoin bỏ ra, coin tự suy) -----
    if payload.asset_class == "crypto":
        price = payload.unit_price
        spend = payload.amount
        if not (price and price > 0):
            raise HTTPException(status_code=400, detail="Nhập Đơn giá (USDT/USDC) > 0")
        if not (spend and spend > 0):
            raise HTTPException(status_code=400, detail="Nhập số tiền USDT/USDC mua > 0")
        fee = max(0.0, min(100.0, float(payload.fee or 0)))
        cur = (payload.currency or "USDT").strip().upper()
        if cur not in _STABLES:
            cur = "USDT"
        qty_in = float(spend) * (1 - fee / 100.0) / float(price)  # coin nhận sau phí
        ex = _find_position(db, user, "crypto", label)
        if ex:
            new_qty = float(ex.quantity or 0) + qty_in
            new_amt = float(ex.amount or 0) + float(spend)
            ex.quantity = new_qty
            ex.amount = round(new_amt, 8)
            ex.unit_price = round(new_amt / new_qty, 12) if new_qty else ex.unit_price
            db.commit()
            db.refresh(ex)
            return _to_out(ex)
        row = UserAsset(
            user_id=user.id,
            asset_class=UserAssetClass("crypto"),
            label=label,
            quantity=qty_in,
            unit_price=round(float(spend) / qty_in, 12) if qty_in else float(price),
            amount=round(float(spend), 8),
            note=cur,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_out(row)

    # ----- Vàng/Bạc: MUA theo số lượng (đơn giá VNĐ) -----
    if payload.asset_class == "gold":
        qty = payload.quantity
        price = payload.unit_price
        if not (qty and qty > 0 and price and price > 0):
            raise HTTPException(status_code=400, detail="Mua cần Số lượng và Đơn giá (đ) > 0")
        amount = round(float(qty) * float(price), 2)
        ex = _find_position(db, user, "gold", label)
        if ex:
            new_qty = float(ex.quantity or 0) + float(qty)
            new_amt = float(ex.amount or 0) + amount
            ex.quantity = new_qty
            ex.amount = round(new_amt, 2)
            ex.unit_price = round(new_amt / new_qty, 2) if new_qty else ex.unit_price
            db.commit()
            db.refresh(ex)
            return _to_out(ex)
        row = UserAsset(
            user_id=user.id,
            asset_class=UserAssetClass("gold"),
            label=label,
            quantity=float(qty),
            unit_price=float(price),
            amount=amount,
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
    if payload.asset_class not in _CLASSES:
        raise HTTPException(status_code=400, detail="asset_class không hợp lệ")
    label = (payload.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Thiếu tên khoản")

    # ----- Crypto: BÁN — nhập số USDT/USDC NHẬN VỀ (sau phí), số coin bán tự suy -----
    if payload.asset_class == "crypto":
        price = payload.unit_price
        receive = payload.amount
        if not (price and price > 0):
            raise HTTPException(status_code=400, detail="Nhập Giá bán (USDT/USDC) > 0")
        if not (receive and receive > 0):
            raise HTTPException(status_code=400, detail="Nhập số tiền USDT/USDC nhận về > 0")
        fee = max(0.0, min(100.0, float(payload.fee or 0)))
        row = _find_position(db, user, "crypto", label)
        if not row:
            raise HTTPException(status_code=404, detail=f"Không có {label} trong Crypto để bán")
        cur = _currency_of(row)
        cur_qty = float(row.quantity or 0)
        # Để NHẬN VỀ receive (sau phí) → giá trị bán thô = receive/(1−fee%) → số coin cần bán
        gross = float(receive) / (1 - fee / 100.0) if fee < 100 else float(receive)
        coins_sold = gross / float(price)
        if coins_sold >= cur_qty - 1e-12:
            actual_gross = cur_qty * float(price)
            actual_receive = actual_gross * (1 - fee / 100.0)
            db.delete(row)
            db.commit()
            return {
                "ok": True, "removed": True, "coins_sold": cur_qty, "received_usdt": actual_receive,
                "message": f"Đã bán hết {label} · nhận ~{_fmt_num(actual_receive)} {cur} (sau phí {fee:g}%)",
            }
        amt = float(row.amount or 0)
        avg = amt / cur_qty if cur_qty > 0 else float(price)
        new_qty = cur_qty - coins_sold
        row.quantity = new_qty
        row.amount = round(max(0.0, amt - coins_sold * avg), 8)
        row.unit_price = round(row.amount / new_qty, 12) if new_qty else row.unit_price
        db.commit()
        return {
            "ok": True, "removed": False, "coins_sold": coins_sold, "remaining_qty": new_qty,
            "received_usdt": float(receive),
            "message": f"Đã bán {_fmt_num(coins_sold)} {label} · nhận ~{_fmt_num(float(receive))} {cur}",
        }

    # ----- Vàng/Bạc: BÁN theo số lượng (lượng/chỉ) -----
    qty = payload.quantity
    if not (qty and qty > 0):
        raise HTTPException(status_code=400, detail="Số lượng bán phải > 0")
    row = _find_position(db, user, "gold", label)
    if not row:
        raise HTTPException(status_code=404, detail=f"Không có {label} trong Vàng/Bạc để bán")
    cur_qty = float(row.quantity or 0)
    if qty > cur_qty + 1e-6:
        raise HTTPException(status_code=400, detail=f"Chỉ còn {cur_qty:g} để bán")
    amt = float(row.amount or 0)
    avg = amt / cur_qty if cur_qty > 0 else 0.0
    remaining = cur_qty - float(qty)
    if remaining < 1e-6:
        db.delete(row)
        db.commit()
        return {"ok": True, "removed": True, "message": f"Đã bán hết {label}"}
    row.quantity = remaining
    row.amount = round(max(0.0, amt - float(qty) * avg), 2)
    row.unit_price = round(row.amount / remaining, 2)
    db.commit()
    return {"ok": True, "removed": False, "remaining_qty": remaining, "message": f"Đã bán {qty:g} {label}"}


def _fmt_num(n: float) -> str:
    """Định dạng số USDT/coin trong message — bỏ số 0 thừa."""
    try:
        if n is None:
            return "0"
        s = f"{float(n):.8f}".rstrip("0").rstrip(".")
        return s if "." in s else f"{float(n):g}"
    except Exception:  # noqa: BLE001
        return f"{n:g}"


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
