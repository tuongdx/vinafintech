from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import RoleEnum, StockSymbol, User
from app.schemas import AdminStockSymbolOut
from app.services.vnstock_service import (
    _get_combined_stock_universe,
    _load_base_symbol_directory,
    classify_symbol_sector,
    invalidate_sector_cache,
    invalidate_symbol_directory_cache,
    refresh_sector_classification,
)

router = APIRouter(prefix="/admin/stocks", tags=["admin-stocks"])

# Quét/liệt kê: cả Quản trị viên (admin) và Super Admin được phép
_ADMIN_ROLES = (RoleEnum.super_admin, RoleEnum.admin)
# Xóa mã: chỉ Super Admin được phép
_SUPER_ADMIN_ROLES = (RoleEnum.super_admin,)


@router.get("", response_model=list[AdminStockSymbolOut])
def list_all_stocks(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_ADMIN_ROLES)),
):
    """Toàn bộ mã cổ phiếu hiện có (gốc HOSE/HNX/UPCoM + mã được bổ sung bằng
    tính năng quét tự động). ``is_custom=True`` đánh dấu mã do hệ thống thêm."""
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
                **classify_symbol_sector(symbol),
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
                **classify_symbol_sector(symbol),
            }
        )

    result.sort(key=lambda r: (r["is_custom"], r["symbol"]))
    return result


@router.post("/scan")
def scan_and_add_new_symbols(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
):
    """Quét toàn bộ cổ phiếu đang niêm yết (HOSE/HNX/UPCoM) và tự thêm các mã
    MỚI chưa có trong danh sách hệ thống.

    Nguồn dữ liệu KẾT HỢP đa nguồn:
    - Simplize screener: nền chính, giàu hồ sơ công ty (tên, sàn, ngành).
    - Vietcap /price/symbols/getAll: bổ sung, nhận diện IPO rất mới sớm hơn
      screener (VD: LPS giao dịch trên HOSE nhưng Simplize chưa index).

    Mỗi lần bấm nút, hệ thống so sánh toàn bộ thị trường với danh sách đang
    biết và chỉ bổ sung phần chênh lệch (delta). Không sửa/xóa mã đã có nên
    luôn an toàn.
    """
    # Mã hệ thống đã biết = directory gốc (snapshot) + mã đã lưu trong DB.
    base_known = {
        str(item.get("symbol", "") or "").strip().upper()
        for item in _load_base_symbol_directory()
        if (item.get("symbol") or "").strip()
    }
    db_known = {
        str(row.symbol or "").strip().upper()
        for row in db.query(StockSymbol).all()
        if (row.symbol or "").strip()
    }
    known = base_known | db_known

    universe = _get_combined_stock_universe()
    added: list[dict[str, str]] = []
    skipped = 0
    for row in universe:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if symbol in known:
            skipped += 1
            continue
        known.add(symbol)
        name = str(row.get("name") or "").strip() or symbol
        exchange = str(row.get("exchange") or "").strip().upper()
        db.add(StockSymbol(symbol=symbol, name=name, exchange=exchange, added_by_id=user.id))
        added.append({"symbol": symbol, "name": name, "exchange": exchange})

    db.commit()
    if added:
        invalidate_symbol_directory_cache()
        # Phân loại nhóm ngành tự động cho các mã vừa thêm (ICB mới nhất + xoá cache
        # ngành) để mã xuất hiện ngay trong cây nhóm ngành và bộ lọc.
        refresh_sector_classification()
        for item in added:
            item.update(classify_symbol_sector(item["symbol"]))
    classified = sum(1 for item in added if item.get("sector"))

    return {
        "scanned": len(universe),
        "added_count": len(added),
        "already_known": skipped,
        "classified_count": classified,
        "added": added,
    }


@router.delete("/{symbol}")
def delete_stock_symbol(
    symbol: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_SUPER_ADMIN_ROLES)),
):
    """Xóa mã cổ phiếu đã được hệ thống thêm (chỉ Super Admin)."""
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
    invalidate_sector_cache()
    return {"message": f"Đã xóa mã {row.symbol} khỏi danh sách cổ phiếu."}
