"""Dịch vụ TIỀN MẶT — số dư + sổ ghi tiền vào/ra.

- Số dư Tiền mặt = 1 con số theo tài khoản, lưu ở bảng `settings` (key `user_cash:{id}`)
  để KHÔNG cần migration enum UserAssetClass.
- Mỗi lần thay đổi số dư (bán cổ phiếu/vàng-bạc, đáo hạn khoản gửi, hoặc nhập tay)
  đều ghi 1 dòng vào bảng `cash_transactions` (change: + vào / − ra, balance_after).

Lãi/tiền đều đơn vị VND (đ).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CashTransaction, Setting


def _key(user_id: int) -> str:
    return f"user_cash:{user_id}"


def get_cash(db: Session, user_id: int) -> float:
    """Số dư Tiền mặt hiện tại (đ)."""
    row = db.query(Setting).filter(Setting.key == _key(user_id)).first()
    if not row:
        return 0.0
    try:
        return round(float(row.value or 0), 0)
    except (TypeError, ValueError):
        return 0.0


def set_cash(db: Session, user_id: int, amount: float, kind: str = "adjust", label: str = "") -> float:
    """Đặt số dư Tiền mặt = amount (>=0) và ghi sổ tiền vào/ra cho phần CHÊNH LỆCH."""
    amount = max(0.0, round(float(amount or 0), 0))
    old = get_cash(db, user_id)
    delta = round(amount - old, 0)
    key = _key(user_id)
    row = db.query(Setting).filter(Setting.key == key).first()
    if amount <= 0:
        if row:
            db.delete(row)
    elif row:
        row.value = str(amount)
    else:
        db.add(Setting(key=key, value=str(amount)))
    if abs(delta) > 1e-9:
        db.add(
            CashTransaction(
                user_id=user_id,
                kind=kind,
                change=delta,
                balance_after=amount,
                label=(label or "").strip()[:255] or "",
            )
        )
    db.commit()
    return amount


def add_cash(db: Session, user_id: int, delta: float, kind: str = "adjust", label: str = "") -> float:
    """Cộng `delta` (>=0) vào Tiền mặt — dùng khi bán tài sản / đáo hạn khoản gửi."""
    delta = max(0.0, round(float(delta or 0), 0))
    if delta <= 0:
        return get_cash(db, user_id)
    return set_cash(db, user_id, get_cash(db, user_id) + delta, kind=kind, label=label)


def list_transactions(db: Session, user_id: int, limit: int = 200) -> list[dict]:
    """Sổ tiền vào/ra, mới nhất trước."""
    rows = (
        db.query(CashTransaction)
        .filter(CashTransaction.user_id == user_id)
        .order_by(CashTransaction.created_at.desc(), CashTransaction.id.desc())
        .limit(max(1, min(int(limit or 200), 500)))
        .all()
    )
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "change": round(float(r.change or 0), 0),
            "balance_after": round(float(r.balance_after or 0), 0),
            "label": r.label or "",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
