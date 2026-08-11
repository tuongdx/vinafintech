import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import RoleEnum, StockSymbol, User
from app.schemas import AdminStockSymbolOut, StockSymbolCreate, StockSymbolOut
from app.services.vnstock_service import (
    _load_base_symbol_directory,
    invalidate_symbol_directory_cache,
    is_listed_symbol,
    lookup_symbol_exchange,
    lookup_symbol_name,
)

router = APIRouter(prefix="/admin/stocks", tags=["admin-stocks"])

# Thêm mã: cả Quản trị viên (admin) và Super Admin được phép
_ADMIN_ROLES = (RoleEnum.super_admin, RoleEnum.admin)
# Sửa/Xóa mã: chỉ Super Admin được phép
_SUPER_ADMIN_ROLES = (RoleEnum.super_admin,)

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}$")
_EXCHANGES = {"", "HOSE", "HNX", "UPCOM"}


@router.get("", response_model=list[AdminStockSymbolOut])
def list_all_stocks(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_ADMIN_ROLES)),
):
    """Toàn bộ mã cổ phiếu hiện có (gốc HOSE/HNX/UPCoM + mã admin thêm thủ công)
    dành cho trang quản trị. ``is_custom=True`` đánh dấu mã do admin thêm."""
    base = _load_base_symbol_directory()
    custom_rows = db.query(StockSymbol).order_by(StockSymbol.symbol).all()

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in base:
        symbol = str(item.get("symbol", "") or "").strip().upper()
        if not symbol:
            continue
        seen.add(symbol)
        result.append(
            {
                "symbol": symbol,
                "name": str(item.get("name", symbol) or symbol).strip(),
                "exchange": str(item.get("exchange", "") or "").upper(),
                "is_custom": False,
                "created_at": None,
            }
        )

    for row in custom_rows:
        symbol = str(row.symbol or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(
            {
                "symbol": symbol,
                "name": (row.name or symbol).strip(),
                "exchange": str(row.exchange or "").upper(),
                "is_custom": True,
                "created_at": row.created_at,
            }
        )

    result.sort(key=lambda r: (r["is_custom"], r["symbol"]))
    return result


@router.get("/lookup")
def lookup_symbol_info(
    symbol: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_ADMIN_ROLES)),
):
    """Tra cứu nhanh mã cổ phiếu cho form thêm.

    ``status``:
    - ``exists``  → đã có trong danh sách cổ phiếu của hệ thống (không thêm được)
    - ``ok``      → mã hợp lệ, đã niêm yết trên HOSE/HNX/UPCoM (thêm được)
    - ``unknown`` → chưa niêm yết / không tồn tại (không thêm được)
    """
    code = (symbol or "").strip().upper()

    # 1) Đã có trong danh sách hệ thống (directory gốc hoặc mã đã thêm thủ công)?
    for item in _load_base_symbol_directory():
        if item.get("symbol") == code:
            return {
                "symbol": code,
                "name": item.get("name", code) or code,
                "exchange": item.get("exchange", "") or "",
                "found": True,
                "status": "exists",
            }
    if db.query(StockSymbol).filter(StockSymbol.symbol == code).first():
        return {"symbol": code, "name": code, "exchange": "", "found": True, "status": "exists"}

    # 2) Mã hợp lệ đã niêm yết (có dữ liệu thị trường)?
    name = lookup_symbol_name(code)
    exchange = lookup_symbol_exchange(code)
    if name or exchange or is_listed_symbol(code):
        return {
            "symbol": code,
            "name": name or code,
            "exchange": exchange,
            "found": True,
            "status": "ok",
        }

    # 3) Chưa niêm yết / không tồn tại
    return {"symbol": code, "name": "", "exchange": "", "found": False, "status": "unknown"}


@router.post("", response_model=StockSymbolOut, status_code=201)
def add_stock_symbol(
    payload: StockSymbolCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
):
    """Thêm mã cổ phiếu mới (VD: cổ phiếu IPO) vào danh sách để API nhận dạng."""
    symbol = (payload.symbol or "").strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(
            status_code=400,
            detail="Mã cổ phiếu không hợp lệ (chỉ chữ A-Z và số 0-9, tối đa 10 ký tự).",
        )

    # Ưu tiên sàn chính thức từ thị trường (screener) nếu có; sàn admin chọn chỉ dùng
    # khi thị trường chưa có dữ liệu cho mã (VD: IPO rất mới chưa được index).
    exchange = lookup_symbol_exchange(symbol) or (payload.exchange or "").strip().upper()
    if exchange not in _EXCHANGES:
        raise HTTPException(status_code=400, detail="Sàn giao dịch không hợp lệ (HOSE/HNX/UPCOM).")

    # Mã đã có sẵn trong directory gốc (toàn bộ HOSE/HNX/UPCoM) → không cần thêm
    if symbol in {item["symbol"] for item in _load_base_symbol_directory()}:
        raise HTTPException(
            status_code=400,
            detail=f"Mã {symbol} đã có trong danh sách cổ phiếu của hệ thống.",
        )

    existing = db.query(StockSymbol).filter(StockSymbol.symbol == symbol).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Mã {symbol} đã có trong danh sách cổ phiếu của hệ thống.")

    # Mã chưa được niêm yết (IPO) lên HOSE/HNX/UPCoM → không cho thêm
    if not is_listed_symbol(symbol) and not lookup_symbol_name(symbol):
        raise HTTPException(
            status_code=400,
            detail=(
                "Cổ phiếu chưa được niêm yết (IPO) lên sàn chứng khoán Việt Nam. "
                "Hãy kiểm tra lại mã chứng khoán bạn đang nhập."
            ),
        )

    # Ưu tiên tên chính thức từ thị trường (Simplize) nếu có; tên admin nhập chỉ dùng
    # khi thị trường chưa có dữ liệu cho mã (VD: IPO rất mới chưa được index).
    market_name = lookup_symbol_name(symbol)
    name = market_name or (payload.name or "").strip() or symbol

    row = StockSymbol(symbol=symbol, name=name, exchange=exchange, added_by_id=user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_symbol_directory_cache()
    return row


@router.put("/{symbol}", response_model=StockSymbolOut)
def update_stock_symbol(
    symbol: str,
    payload: StockSymbolCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_SUPER_ADMIN_ROLES)),
):
    """Sửa mã cổ phiếu do admin thêm thủ công (chỉ Super Admin).
    Chỉ sửa được Tên công ty & Sàn giao dịch; mã (symbol) không đổi."""
    row = (
        db.query(StockSymbol)
        .filter(StockSymbol.symbol == symbol.strip().upper())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy mã cổ phiếu do bạn thêm.")

    # Ưu tiên sàn chính thức từ thị trường (screener) nếu có; sàn admin chọn chỉ dùng khi chưa có dữ liệu.
    exchange = lookup_symbol_exchange(row.symbol) or (payload.exchange or "").strip().upper()
    if exchange not in _EXCHANGES:
        raise HTTPException(status_code=400, detail="Sàn giao dịch không hợp lệ (HOSE/HNX/UPCOM).")

    # Tương tự POST: tên chính thức từ thị trường được ưu tiên khi có dữ liệu.
    market_name = lookup_symbol_name(row.symbol)
    row.name = market_name or (payload.name or "").strip() or row.symbol
    row.exchange = exchange
    db.commit()
    db.refresh(row)
    invalidate_symbol_directory_cache()
    return row


@router.delete("/{symbol}")
def delete_stock_symbol(
    symbol: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_SUPER_ADMIN_ROLES)),
):
    """Xóa mã cổ phiếu do admin thêm thủ công (chỉ Super Admin)."""
    row = (
        db.query(StockSymbol)
        .filter(StockSymbol.symbol == symbol.strip().upper())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy mã cổ phiếu do bạn thêm.")
    db.delete(row)
    db.commit()
    invalidate_symbol_directory_cache()
    return {"message": f"Đã xóa mã {row.symbol} khỏi danh sách cổ phiếu."}
