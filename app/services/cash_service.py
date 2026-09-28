"""Dịch vụ TIỀN MẶT — 2 quỹ VND + USDT/USDC (gộp 1:1) + sổ ghi tiền vào/ra.

Kiến trúc:
- Mỗi quỹ = 1 con số theo tài khoản, lưu ở bảng `settings`:
    * VND : key `user_cash:{user_id}`  (giữ nguyên key cũ → số dư VND hiện có được bảo toàn)
    * USDT: key `user_cash_usdt:{user_id}`  (USDT & USDC gộp chung 1 quỹ, tỷ lệ 1:1)
- Mỗi lần đổi số dư đều ghi 1 dòng `cash_transactions` (có `currency` = quỹ bị tác động,
  change: + vào / − ra, balance_after = số dư quỹ sau giao dịch).
- `credit(...)` cộng vào (bán tài sản / đáo hạn / nạp).
- `debit(...)` trừ ra (mua tài sản / rút). Nếu KHÔNG ĐỦ số dư → trả None (không ghi gì),
  router sẽ báo lỗi yêu cầu Nạp tiền trước.

Làm tròn: VND = 0 chữ số thập phân (đ); USDT = 6 chữ số.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CashTransaction, Setting

CUR_VND = "VND"
CUR_USDT = "USDT"
_DEC = {CUR_VND: 0, CUR_USDT: 6}


def _key(user_id: int, currency: str) -> str:
    base = f"user_cash:{user_id}"
    if currency == CUR_USDT:
        return base + "_usdt"
    return base


def _round(v: float, currency: str) -> float:
    dec = _DEC.get(currency, 0)
    return round(float(v or 0), dec)


def get_balance(db: Session, user_id: int, currency: str) -> float:
    """Số dư hiện tại của 1 quỹ (VND hoặc USDT)."""
    row = db.query(Setting).filter(Setting.key == _key(user_id, currency)).first()
    if not row:
        return 0.0
    try:
        return _round(float(row.value or 0), currency)
    except (TypeError, ValueError):
        return 0.0


def get_balances(db: Session, user_id: int) -> dict[str, float]:
    """Số dư cả 2 quỹ: {'VND': …, 'USDT': …}."""
    return {CUR_VND: get_balance(db, user_id, CUR_VND), CUR_USDT: get_balance(db, user_id, CUR_USDT)}


def _write(db: Session, user_id: int, currency: str, amount: float) -> float:
    amount = max(0.0, _round(amount, currency))
    key = _key(user_id, currency)
    row = db.query(Setting).filter(Setting.key == key).first()
    if amount <= 0:
        if row:
            db.delete(row)
    elif row:
        row.value = str(amount)
    else:
        db.add(Setting(key=key, value=str(amount)))
    return amount


def _log(
    db: Session,
    user_id: int,
    currency: str,
    change: float,
    balance_after: float,
    kind: str,
    label: str,
    ref_key: str = "",
) -> None:
    db.add(
        CashTransaction(
            user_id=user_id,
            kind=kind,
            currency=currency,
            change=_round(change, currency),
            balance_after=_round(balance_after, currency),
            label=(label or "").strip()[:255] or "",
            ref_key=(ref_key or "").strip()[:120],
        )
    )
    db.commit()


def has_ref(db: Session, user_id: int, ref_key: str) -> bool:
    """Đã có bút toán TỰ ĐỘNG với khoá `ref_key` chưa (chống cộng trùng khi mở trang nhiều lần)."""
    key = (ref_key or "").strip()[:120]
    if not key:
        return False
    return (
        db.query(CashTransaction.id)
        .filter(CashTransaction.user_id == user_id, CashTransaction.ref_key == key)
        .first()
        is not None
    )


def credit(
    db: Session,
    user_id: int,
    currency: str,
    delta: float,
    kind: str = "adjust",
    label: str = "",
    ref_key: str = "",
) -> float:
    """Cộng `delta` (>=0) vào quỹ `currency` — bán tài sản / đáo hạn / nạp tiền. Trả số dư mới."""
    cur = currency if currency in _DEC else CUR_VND
    delta = _round(delta, cur)
    if delta <= 0:
        return get_balance(db, user_id, cur)
    old = get_balance(db, user_id, cur)
    new = _write(db, user_id, cur, old + delta)
    _log(db, user_id, cur, delta, new, kind, label, ref_key)
    return new


def debit(db: Session, user_id: int, currency: str, delta: float, kind: str = "adjust", label: str = "") -> float | None:
    """Trừ `delta` (>=0) khỏi quỹ `currency` — mua tài sản / rút tiền.

    KHÔNG đủ số dư → trả None và KHÔNG ghi gì (router sẽ trả 400 yêu cầu Nạp tiền trước).
    """
    cur = currency if currency in _DEC else CUR_VND
    delta = _round(delta, cur)
    if delta <= 0:
        return get_balance(db, user_id, cur)
    old = get_balance(db, user_id, cur)
    if delta > old + 1e-9:
        return None
    new = _write(db, user_id, cur, old - delta)
    _log(db, user_id, cur, -delta, new, kind, label)
    return new


def list_transactions(db: Session, user_id: int, limit: int = 200) -> list[dict]:
    """Sổ tiền vào/ra (mọi quỹ), mới nhất trước."""
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
            "currency": (r.currency or CUR_VND),
            "change": _round(float(r.change or 0), r.currency or CUR_VND),
            "balance_after": _round(float(r.balance_after or 0), r.currency or CUR_VND),
            "label": r.label or "",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
