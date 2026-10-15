"""Danh mục đầu tư cá nhân — quản lý cổ phiếu nắm giữ (số lượng + giá vốn bình quân).

- GET    /api/portfolio            → danh sách vị thế kèm giá realtime + lãi/lỗ
- POST   /api/portfolio            → thêm vị thế (symbol, quantity, avg_price)
- PUT    /api/portfolio/{id}       → cập nhật quantity / avg_price (CỘNG/TRỪ Tiền mặt VND theo chênh lệch vốn)
- DELETE /api/portfolio/{id}       → xoá vị thế
Yêu cầu đăng nhập (JWT). Giá/lãi-lỗ lấy realtime từ vnstock_service (get_quote, cache 15s).
"""
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import PortfolioHolding, User
from app.schemas import PortfolioHoldingCreate, PortfolioHoldingOut, PortfolioHoldingUpdate, PortfolioSell
from app.services import cash_service
from app.services import dividend_adjust
from app.services import portfolio_performance as pps
from app.services import vnstock_service as vns
from app.services.alert_service import alert_service

logger = logging.getLogger("tunix.portfolio")
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


def _enrich(
    row: PortfolioHolding,
    quote: dict[str, Any] | None,
    adj: dict[str, Any] | None = None,
) -> PortfolioHoldingOut:
    """Vị thế + giá realtime + lãi/lỗ, ĐÃ ĐIỀU CHỈNH theo cổ tức/chia tách sau ngày mua.

    `quantity`/`avg_price` trả về là bản ĐÃ ĐIỀU CHỈNH (cổ tức tiền mặt làm giảm giá vốn,
    cổ tức/thưởng bằng cổ phiếu làm tăng số lượng) để lãi/lỗ không sai sau ngày chốt quyền.
    Số liệu gốc người dùng nhập nằm ở `raw_quantity`/`raw_avg_price` (dùng khi sửa/bán).
    `adj` có thể được tính sẵn (song song) — xem _enriched_holdings.
    """
    parts = quote or _quote_parts(row.symbol)
    raw_quantity = float(row.quantity or 0)
    raw_avg_price = float(row.avg_price or 0)
    if adj is None:
        # Mốc so sánh = lần cập nhật CUỐI của vị thế: nếu người dùng đã tự ghi nhận cổ tức
        # (sửa số lượng/giá vốn) thì các đợt trước đó KHÔNG bị trừ lại lần nữa.
        adj = dividend_adjust.adjust_position(row.symbol, raw_quantity, raw_avg_price, row.updated_at or row.created_at)
    quantity = float(adj.get("quantity") or 0)
    avg_price = float(adj.get("avg_price") or 0)
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
        raw_quantity=raw_quantity,
        raw_avg_price=raw_avg_price,
        adjustment=dividend_adjust.adjustment_out(adj),
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
    # Điều chỉnh cổ tức cũng SONG SONG: mỗi mã phải tra lịch cổ tức (nguồn ngoài, cache 24h)
    # → chạy tuần tự sẽ rất chậm ở lần đầu (cache lạnh) với danh mục nhiều mã.
    adjustments: dict[int, dict[str, Any]] = {}
    if rows:
        with ThreadPoolExecutor(max_workers=6) as pool:
            adj_futures = {
                pool.submit(
                    dividend_adjust.adjust_position,
                    r.symbol,
                    float(r.quantity or 0),
                    float(r.avg_price or 0),
                    r.updated_at or r.created_at,
                ): r.id
                for r in rows
            }
            for fut in adj_futures:
                try:
                    adjustments[adj_futures[fut]] = fut.result()
                except Exception:
                    pass
    return [_enrich(r, quotes.get(r.symbol), adjustments.get(r.id)) for r in rows]


@router.get("", response_model=list[PortfolioHoldingOut])
def list_portfolio(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Đến NGÀY CHIA cổ tức tiền → tự cộng vào quỹ Tiền mặt VND (idempotent, chống cộng trùng
    # bằng cash_transactions.ref_key). Lỗi nguồn ngoài KHÔNG được làm hỏng trang danh mục.
    try:
        dividend_adjust.credit_dividend_cash(db, user.id)
    except Exception:  # noqa: BLE001
        logger.debug("credit_dividend_cash failed", exc_info=True)
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


@router.get("/position/{symbol}")
def position_for_symbol(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Vị thế của user ở MỘT mã (đã điều chỉnh cổ tức/chia tách) — dùng để vẽ "vạch vị thế"
    (đường giá vốn) trên biểu đồ ở trang chi tiết cổ phiếu + chế độ toàn màn hình.

    Không có vị thế → trả `has_position: false` (KHÔNG 404) để trang biểu đồ xử lý đơn giản.
    Endpoint cố tình KHÔNG lấy quote realtime (biểu đồ đã có giá) và KHÔNG lấy toàn bộ danh mục
    ⇒ nhanh, không phụ thuộc nguồn giá ngoài.
    """
    sym = (symbol or "").strip().upper()
    if not sym or len(sym) > 20:
        raise HTTPException(status_code=400, detail="Mã cổ phiếu không hợp lệ.")
    row = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.user_id == user.id, PortfolioHolding.symbol == sym)
        .first()
    )
    if not row:
        return {"symbol": sym, "has_position": False}
    raw_qty = float(row.quantity or 0)
    raw_avg = float(row.avg_price or 0)
    try:
        adj = dividend_adjust.adjust_position(sym, raw_qty, raw_avg, row.updated_at or row.created_at)
    except Exception:  # noqa: BLE001 — lỗi nguồn ngoài không được làm hỏng biểu đồ
        logger.debug("adjust_position failed for %s", sym, exc_info=True)
        adj = {}
    qty = float(adj.get("quantity") or raw_qty)
    avg = float(adj.get("avg_price") or raw_avg)
    return {
        "symbol": sym,
        "has_position": bool(qty > 0 and avg > 0),
        "quantity": qty,
        "avg_price": avg,
        "raw_quantity": raw_qty,
        "raw_avg_price": raw_avg,
        "cost": round(qty * avg, 2),
        # Cổ tức đã chốt quyền nhưng chưa tới ngày chia (hiển thị thêm trong badge nếu có).
        "pending_shares": float(adj.get("pending_shares") or 0),
        "pending_cash": float(adj.get("pending_cash") or 0),
    }


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

    qty = float(payload.quantity or 0)
    buy_price = float(payload.avg_price or 0)
    fee_pct = max(0.0, float(payload.fee or 0)) / 100.0
    # Giá mua hiệu dụng đã gồm phí giao dịch → giá vốn bình quân phản ánh chi phí thực.
    effective_price = buy_price * (1 + fee_pct) if buy_price > 0 else 0.0
    # Tiền thực chi = số lượng × giá hiệu dụng (gồm phí) — trừ vào quỹ tiền mặt VND.
    cost = round(qty * effective_price, 0)
    if cost > 0:
        have_vnd = cash_service.get_balance(db, user.id, cash_service.CUR_VND)
        new_vnd = cash_service.debit(
            db, user.id, cash_service.CUR_VND, cost,
            kind="buy_stock", label=f"Mua cổ phiếu {symbol} ({qty:g} cp)",
        )
        if new_vnd is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Quỹ VND không đủ {cost:,.0f} đ để mua {qty:g} {symbol} "
                    f"(đang có {have_vnd:,.0f} đ). Hãy Nạp tiền mặt trước."
                ),
            )

    existing = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.user_id == user.id, PortfolioHolding.symbol == symbol)
        .first()
    )
    if existing:
        # Mua thêm cùng mã → cộng dồn số lượng, tính lại giá vốn bình quân gia quyền (gồm phí).
        old_qty = float(existing.quantity or 0)
        old_avg = float(existing.avg_price or 0)
        new_qty = old_qty + qty
        existing.avg_price = (old_qty * old_avg + qty * effective_price) / new_qty if new_qty else 0.0
        existing.quantity = new_qty
        db.commit()
        db.refresh(existing)
        return _enrich(existing, quote)

    row = PortfolioHolding(
        user_id=user.id,
        symbol=symbol,
        quantity=qty,
        avg_price=effective_price,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _enrich(row, quote)


@router.put("/{holding_id}")
def update_holding(
    holding_id: int,
    payload: PortfolioHoldingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sửa vị thế (giá vốn / số lượng / phí) và ĐIỀU CHỈNH TIỀN MẶT VND theo chênh lệch TỔNG VỐN.

    - Tính lại ĐÚNG NHƯ KHI MUA: `giá vốn mới = giá × (1 + phí%/100)` (phí mặc định 0 nếu không gửi).
    - Chênh lệch = (số lượng mới × giá vốn mới) − (số lượng cũ × giá vốn cũ).
    - Tăng vốn (tăng số lượng / tăng giá / thêm phí) ⇒ **trừ** Tiền mặt VND; không đủ số dư ⇒ 400
      yêu cầu Nạp tiền (KHÔNG lưu thay đổi).
    - Giảm vốn (giảm số lượng / hạ giá) ⇒ **hoàn lại** vào Tiền mặt VND.
    - Mọi lần điều chỉnh đều ghi 1 dòng vào sổ tiền vào/ra (`cash_transactions`, kind `edit_stock`).

    Trả về vị thế sau cập nhật + `cash_delta` / `cash_vnd` / `message` cho UI hiện thông báo.
    """
    row = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.id == holding_id, PortfolioHolding.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế này trong danh mục.")
    old_qty = float(row.quantity or 0)
    old_avg = float(row.avg_price or 0)
    new_qty = old_qty
    new_avg = old_avg
    if payload.quantity is not None:
        if float(payload.quantity) < 0:
            raise HTTPException(status_code=400, detail="Số lượng không hợp lệ.")
        new_qty = float(payload.quantity)
    if payload.avg_price is not None:
        if float(payload.avg_price) < 0:
            raise HTTPException(status_code=400, detail="Giá vốn không hợp lệ.")
        new_avg = float(payload.avg_price)
    fee_pct = 0.0
    if payload.fee is not None:
        if float(payload.fee) < 0:
            raise HTTPException(status_code=400, detail="Phí giao dịch không hợp lệ.")
        fee_pct = float(payload.fee) / 100.0
    # Tính lại ĐÚNG NHƯ KHI MUA: giá vốn đã bao gồm phí giao dịch.
    effective_avg = new_avg * (1 + fee_pct) if new_avg > 0 else 0.0

    # Tiền mặt đi theo TỔNG VỐN ĐẦU TƯ (số lượng × giá vốn) — sửa giá vốn, số lượng hoặc phí
    # đều làm thay đổi số tiền đã bỏ ra, nên quỹ VND phải được cập nhật tương ứng.
    delta = round(new_qty * effective_avg, 0) - round(old_qty * old_avg, 0)
    cash_delta = 0.0
    if delta > 0:
        have = cash_service.get_balance(db, user.id, cash_service.CUR_VND)
        after = cash_service.debit(
            db, user.id, cash_service.CUR_VND, delta, kind="edit_stock",
            label=f"Điều chỉnh vị thế {row.symbol} (tăng vốn {delta:,.0f} đ)",
        )
        if after is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Quỹ VND không đủ {delta:,.0f} đ để tăng vốn cho {row.symbol} "
                    f"(đang có {have:,.0f} đ). Hãy Nạp tiền mặt trước rồi lưu lại."
                ),
            )
        cash_delta = -delta
    elif delta < 0:
        cash_service.credit(
            db, user.id, cash_service.CUR_VND, -delta, kind="edit_stock",
            label=f"Điều chỉnh vị thế {row.symbol} (giảm vốn {(-delta):,.0f} đ)",
        )
        cash_delta = -delta

    row.quantity = new_qty
    row.avg_price = effective_avg
    db.commit()
    db.refresh(row)
    out = _enrich(row, None).model_dump()
    out["cash_delta"] = cash_delta          # > 0: cộng vào quỹ VND · < 0: trừ khỏi quỹ VND
    out["cash_vnd"] = cash_service.get_balance(db, user.id, cash_service.CUR_VND)
    out["message"] = (
        f"Đã cập nhật {row.symbol} · trừ {abs(cash_delta):,.0f} đ Tiền mặt VND" if cash_delta < 0
        else f"Đã cập nhật {row.symbol} · hoàn {cash_delta:,.0f} đ vào Tiền mặt VND" if cash_delta > 0
        else f"Đã cập nhật {row.symbol} (tổng vốn không đổi → Tiền mặt giữ nguyên)"
    )
    return out


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
    # Dọn cảnh báo mồ côi của mã vừa gỡ khỏi danh sách.
    alert_service.prune_untracked(db, user.id)
    return {"message": "Đã xoá khỏi danh mục", "symbol": symbol}


@router.post("/{holding_id}/sell")
def sell_holding(
    holding_id: int,
    payload: PortfolioSell,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bán cổ phiếu: trừ khối lượng, giữ nguyên giá vốn. Về 0 → đóng vị thế.

    Tiền nhận về (qty × giá thị trường, trừ phí bán) được CỘNG VÀO Tiền mặt + ghi lịch sử.
    """
    row = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.id == holding_id, PortfolioHolding.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế này trong danh mục.")
    qty = float(payload.quantity or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Số lượng bán phải lớn hơn 0.")
    # Phí bán là chi phí realized — không làm đổi vị thế còn lại (giá vốn giữ nguyên).
    if float(payload.fee or 0) < 0:
        raise HTTPException(status_code=400, detail="Phí giao dịch không hợp lệ.")
    symbol = row.symbol
    new_qty = float(row.quantity or 0) - qty
    if new_qty < 0:
        raise HTTPException(status_code=400, detail=f"Không đủ {symbol} để bán (đang giữ {row.quantity:g}).")

    # Tiền nhận về = qty × giá bán (giá thị trường) × (1 − phí).
    price = float(_quote_parts(symbol).get("price") or 0)
    if not (price > 0):
        price = float(row.avg_price or 0)
    fee_pct = max(0.0, float(payload.fee or 0)) / 100.0
    proceeds = round(qty * price * (1 - fee_pct), 0)

    if new_qty == 0:
        db.delete(row)
        db.commit()
        cash_service.credit(db, user.id, cash_service.CUR_VND, proceeds, kind="sell_stock", label=f"Bán hết cổ phiếu {symbol} ({qty:g} cp)")
        # Đã bán hết → dọn cảnh báo nếu mã không còn được theo dõi.
        alert_service.prune_untracked(db, user.id)
        return {
            "message": f"Đã bán hết {symbol}. Vị thế đã đóng · nhận ~{proceeds:,.0f} đ vào Tiền mặt VND",
            "symbol": symbol, "quantity": 0, "deleted": True, "received": proceeds,
        }
    row.quantity = new_qty
    db.commit()
    cash_service.credit(db, user.id, cash_service.CUR_VND, proceeds, kind="sell_stock", label=f"Bán cổ phiếu {symbol} ({qty:g} cp)")
    return {
        "message": f"Đã bán {qty:g} {symbol}. Còn lại {new_qty:g} cổ phiếu · nhận ~{proceeds:,.0f} đ vào Tiền mặt VND",
        "symbol": symbol, "quantity": new_qty, "deleted": False, "received": proceeds,
    }
