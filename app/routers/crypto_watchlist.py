import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import CryptoWatchlist, User
from app.schemas import CryptoWatchlistCreate, CryptoWatchlistOut

router = APIRouter(prefix="/crypto-watchlist", tags=["crypto-watchlist"])


def _clean_symbol(raw: str) -> str:
    """Chuẩn hoá mã coin: in hoa, chỉ giữ A-Z0-9 (vd 'usdt ' → 'USDT')."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").strip().upper())


@router.get("", response_model=list[CryptoWatchlistOut])
def list_crypto_watchlist(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(CryptoWatchlist)
        .filter(CryptoWatchlist.user_id == user.id)
        .order_by(CryptoWatchlist.created_at.desc())
        .all()
    )
    return rows


@router.post("", response_model=CryptoWatchlistOut)
def add_crypto_watchlist(
    payload: CryptoWatchlistCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    symbol = _clean_symbol(payload.symbol)
    if not symbol:
        raise HTTPException(status_code=400, detail="Mã crypto không hợp lệ.")
    if len(symbol) > 30:
        raise HTTPException(status_code=400, detail="Mã crypto quá dài.")
    row = (
        db.query(CryptoWatchlist)
        .filter(CryptoWatchlist.user_id == user.id, CryptoWatchlist.symbol == symbol)
        .first()
    )
    if row:
        return row
    row = CryptoWatchlist(user_id=user.id, symbol=symbol)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{symbol}")
def remove_crypto_watchlist(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sym = _clean_symbol(symbol)
    row = (
        db.query(CryptoWatchlist)
        .filter(CryptoWatchlist.user_id == user.id, CryptoWatchlist.symbol == sym)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không có coin này trong danh sách theo dõi.")
    db.delete(row)
    db.commit()
    return {"message": "Đã bỏ theo dõi", "symbol": sym}
