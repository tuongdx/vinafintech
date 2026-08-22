"""Danh mục đầu tư cá nhân — quản lý cổ phiếu nắm giữ (số lượng + giá vốn bình quân).

- GET    /api/portfolio            → danh sách vị thế kèm giá realtime + lãi/lỗ
- POST   /api/portfolio            → thêm vị thế (symbol, quantity, avg_price)
- PUT    /api/portfolio/{id}       → cập nhật quantity / avg_price
- DELETE /api/portfolio/{id}       → xoá vị thế
Yêu cầu đăng nhập (JWT). Giá/lãi-lỗ lấy realtime từ vnstock_service (get_quote, cache 15s).
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import PortfolioHolding, User
from app.schemas import PortfolioHoldingCreate, PortfolioHoldingOut, PortfolioHoldingUpdate
from app.services import portfolio_performance as pps
from app.services import vnstock_service as vns

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _quote_parts(symbol: str) -> dict[str, Any]:
    """Quote an toàn cho 1 mã — trả {name, exchange, price, change, percent_change}."""
    try:
        q = vns.get_quote(symbol)
        if q and q.get("valid"):
            return {
                "name": str(q.get("name") or symbol),
                "exchange": str(q.get("exchange") or ""),
                "price": float(q.get("price") or 0),
                "change": float(q.get("change") or 0),
                "percent_change": float(q.get("percent_change") or 0),
            }
    except Exception:
        pass
    return {"name": symbol, "exchange": "", "price": 0.0, "change": 0.0, "percent_change": 0.0}


def _enrich(row: PortfolioHolding, quote: dict[str, Any] | None) -> PortfolioHoldingOut:
    parts = quote or _quote_parts(row.symbol)
    quantity = float(row.quantity or 0)
    avg_price = float(row.avg_price or 0)
    price = float(parts.get("price") or 0)
    market_value = round(quantity * price, 2)
    cost = round(quantity * avg_price, 2)
    pnl = round(market_value - cost, 2)
    pnl_pct = round((pnl / cost * 100.0), 2) if cost else 0.0
    return PortfolioHoldingOut(
        id=row.id,
        symbol=row.symbol,
        name=parts.get("name") or row.symbol,
        exchange=parts.get("exchange") or "",
        quantity=quantity,
        avg_price=avg_price,
        price=price,
        change=float(parts.get("change") or 0),
        percent_change=float(parts.get("percent_change") or 0),
        market_value=market_value,
        cost=cost,
        pnl=pnl,
        pnl_pct=pnl_pct,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _enriched_holdings(db: Session, user: User) -> list[PortfolioHoldingOut]:
    """Nạp toàn bộ vị thế của user kèm giá realtime (cache 15s) + lãi/lỗ."""
    rows = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.user_id == user.id)
        .order_by(PortfolioHolding.created_at.desc())
        .all()
    )
    # Lấy giá realtime song song (get_quote có cache 15s nên rẻ, kể cả khi lặp lại lần sau).
    quotes: dict[str, dict[str, Any]] = {}
    symbols = [r.symbol for r in rows]
    if symbols:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_quote_parts, s): s for s in symbols}
            for fut in futures:
                try:
                    quotes[futures[fut]] = fut.result()
                except Exception:
                    pass
    return [_enrich(r, quotes.get(r.symbol)) for r in rows]


@router.get("", response_model=list[PortfolioHoldingOut])
def list_portfolio(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _enriched_holdings(db, user)


@router.get("/performance")
def portfolio_performance(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Tổng lãi/lỗ danh mục — tính bởi PortfolioPerformanceService (dễ mở rộng sổ lệnh, XIRR...)."""
    enriched = _enriched_holdings(db, user)
    total = len(enriched)
    ok = sum(1 for h in enriched if float(h.price or 0) > 0)
    return pps.build_summary(
        [h.model_dump() for h in enriched],
        quotes_ok=ok,
        total_quotes=total,
    )


@router.post("", response_model=PortfolioHoldingOut)
def add_holding(
    payload: PortfolioHoldingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    symbol = (payload.symbol or "").strip().upper()
    if not symbol or len(symbol) > 20:
        raise HTTPException(status_code=400, detail="Mã cổ phiếu không hợp lệ.")
    if float(payload.quantity or 0) <= 0:
        raise HTTPException(status_code=400, detail="Số lượng phải lớn hơn 0.")
    if float(payload.avg_price or 0) < 0:
        raise HTTPException(status_code=400, detail="Giá vốn không hợp lệ.")

    quote = _quote_parts(symbol)
    if quote["price"] <= 0 and quote["name"] == symbol:
        raise HTTPException(
            status_code=400,
            detail=f"Không có mã cổ phiếu {symbol} trên các sàn HOSE, HNX, UPCoM.",
        )

    existing = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.user_id == user.id, PortfolioHolding.symbol == symbol)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Cổ phiếu {symbol} đã có trong danh mục. Hãy cập nhật số lượng / giá vốn.",
        )

    row = PortfolioHolding(
        user_id=user.id,
        symbol=symbol,
        quantity=float(payload.quantity or 0),
        avg_price=float(payload.avg_price or 0),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _enrich(row, quote)


@router.put("/{holding_id}", response_model=PortfolioHoldingOut)
def update_holding(
    holding_id: int,
    payload: PortfolioHoldingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.id == holding_id, PortfolioHolding.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế này trong danh mục.")
    if payload.quantity is not None:
        if float(payload.quantity) < 0:
            raise HTTPException(status_code=400, detail="Số lượng không hợp lệ.")
        row.quantity = float(payload.quantity)
    if payload.avg_price is not None:
        if float(payload.avg_price) < 0:
            raise HTTPException(status_code=400, detail="Giá vốn không hợp lệ.")
        row.avg_price = float(payload.avg_price)
    db.commit()
    db.refresh(row)
    return _enrich(row, None)


@router.delete("/{holding_id}")
def delete_holding(
    holding_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.id == holding_id, PortfolioHolding.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế này trong danh mục.")
    symbol = row.symbol
    db.delete(row)
    db.commit()
    return {"message": "Đã xoá khỏi danh mục", "symbol": symbol}
