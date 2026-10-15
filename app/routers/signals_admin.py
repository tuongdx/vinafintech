"""API tính năng **Tín hiệu** (TuniX Signal Engine) cho người dùng đã được CẤP QUYỀN.

BẢO MẬT (spec §4, §33.3-§33.5):
- Mọi endpoint yêu cầu đăng nhập + ``require_signal_access``: role super-admin/admin **hoặc**
  tài khoản được admin cấp cờ ``users.signal_access``. Chưa đăng nhập ⇒ 401, chưa được cấp ⇒ 403.
- Quyền được kiểm tra Ở SERVER (không dựa vào việc frontend ẩn menu) — xem `app/core/dependencies.py`.
- Response ở đây chứa field quyết định giao dịch (signal/entry/stop_loss/take_profit/trailing_stop);
  public router (`/api/stocks/{symbol}/analysis`) dùng serializer riêng, KHÔNG trả các field này.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import require_signal_access
from app.db import get_db
from app.models import SignalPosition, User
from app.services import vnstock_service as vns
from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import lifecycle
from app.services.signal_engine import repository, serializers
from app.services.signal_engine import engine as engine_module
from app.services.signal_engine import model_performance
from app.services.signal_engine import backtest as backtest_module

logger = logging.getLogger("tunix.signal.api")

router = APIRouter(prefix="/signals", tags=["signals"])

# Người dùng được cấp quyền "Tín hiệu" (admin cấp theo tài khoản) — kiểm tra ở server.
_ACCESS = require_signal_access


def _expire_overdue(db: Session) -> None:
    """Đóng tín hiệu QUÁ HẠN nắm giữ T+ trước khi trả dữ liệu (best-effort).

    Chỉ dùng nến benchmark trong CACHE (KHÔNG gọi mạng) để không làm chậm request GET; khi
    chưa có cache thì tự quy đổi sang ngày lịch (10 phiên ≈ 14 ngày).
    """
    try:
        repository.expire_overdue(db, benchmark_bars=engine_module.cached_benchmark_bars())
    except Exception as exc:  # noqa: BLE001
        logger.warning("đóng tín hiệu quá hạn T+ lỗi: %s", exc)


def _liquidity_filter() -> tuple[float, set[str] | None]:
    """Bộ lọc THANH KHOẢN của trang Tín hiệu: chỉ lấy mã có GTGD TRUNG BÌNH 20 PHIÊN
    ≥ ``engine.min_avg_liquidity_20d_ty`` tỷ VND/phiên.

    Trả ``(ngưỡng, tập mã)``; tập mã = ``None`` nghĩa là **KHÔNG lọc** (đang tắt, hoặc nguồn
    dữ liệu lỗi — thà hiện đủ còn hơn ẩn hết tín hiệu vì lỗi nguồn).
    """
    try:
        min_ty = engine_module.liquidity_min_ty()
    except Exception:  # noqa: BLE001 — cấu hình lỗi thì coi như TẮT lọc
        min_ty = 0.0
    if min_ty <= 0:
        return 0.0, None
    liquid = engine_module.liquid_universe_symbols()
    return min_ty, (liquid or None)


def _liquidity_info(min_ty: float, symbols: set[str] | None) -> dict[str, Any]:
    """Thông tin bộ lọc thanh khoản cho UI (pill cảnh báo + số mã đang xét)."""
    return {
        "enabled": symbols is not None,
        "min_avg_20d_ty": min_ty,
        "sessions": 20,
        "symbols": len(symbols) if symbols else None,
    }


def _position_summary(db: Session, user: User) -> dict[str, Any]:
    """Thống kê VỊ THẾ của chính tài khoản đang đăng nhập (nút ON/OFF ở cột "Vị thế")."""
    try:
        return repository.position_stats(db, user.id)
    except Exception as exc:  # noqa: BLE001 — thiếu bảng/cột ⇒ không chặn trang
        logger.warning("thống kê vị thế lỗi: %s", exc)
        return {"open": 0, "closed": 0, "total": 0, "win_rate": None, "note": "chưa có dữ liệu vị thế"}


# ---------------------------------------------------------------------------
# Siêu dữ liệu
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Tổng quan dashboard tín hiệu (spec §17).

    Thống kê tính trên ĐÚNG tập mã đang hiển thị (bộ lọc thanh khoản TB20 — xem
    `_liquidity_filter`) để số liệu khớp với bảng bên dưới.
    """
    _expire_overdue(db)
    min_ty, symbols = _liquidity_filter()
    stats = repository.overview_stats(db, symbols=symbols)
    stats["liquidity_filter"] = _liquidity_info(min_ty, symbols)
    # Vị thế do NGƯỜI DÙNG tự mở (nút ON/OFF) — nguồn số liệu lãi/lỗ & tỷ lệ thắng của trang.
    stats["positions"] = _position_summary(db, _user)
    # Lịch QUÉT TỰ ĐỘNG (thread nền) + thời điểm dữ liệu tín hiệu được cập nhật gần nhất.
    try:
        from app.services.signal_engine import scheduler as signal_scheduler

        stats["scan_schedule"] = signal_scheduler.info()
    except Exception as exc:  # noqa: BLE001 — thiếu module/lỗi đọc ⇒ UI chỉ ẩn pill lịch
        logger.warning("không đọc được lịch quét tín hiệu: %s", exc)
        stats["scan_schedule"] = {"enabled": False, "times": []}
    stats["last_scan"] = repository.last_scan_at(db)
    stats["strategies"] = [
        {"name": name, "display_name": meta[0], "description": meta[1]}
        for name, meta in repository._STRATEGY_META.items()  # noqa: SLF001 — nguồn chuẩn của engine
    ]
    stats["model_status"] = model_performance.stats(db).get("status")
    stats["config_version"] = cfg_module.config_version()
    # Ngưỡng vòng đời T+ đang chạy (trang Tín hiệu hiển thị để người dùng biết tín hiệu hết
    # hiệu lực sau bao nhiêu phiên).
    stats["expiry"] = lifecycle.expiry_config(cfg_module.get_config())
    return stats


@router.get("/config")
def get_thresholds(_user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Threshold/trọng số đang chạy (để Admin debug — spec §26)."""
    return cfg_module.admin_thresholds()


@router.get("/models")
def model_status(_user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Model AI nào đang thực sự hoạt động (spec §8/§35.9-35.10)."""
    from app.services.signal_engine.ai.ensemble import EnsembleModel

    ensemble = EnsembleModel(cfg_module.get_config())
    return {
        "ensemble_status": ensemble.status(),
        "models": ensemble.model_descriptions(),
        "note": "Chỉ model có checkpoint (MODEL_READY) mới tạo prediction; hiện chưa có model nào được train.",
    }


@router.get("/model-performance")
def model_perf(db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    return model_performance.stats(db)


@router.get("/history")
def history(symbol: str = "", strategy: str = "", limit: int = 100, offset: int = 0,
            db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Lịch sử signal (spec §24)."""
    return repository.signal_history(db, symbol=symbol, strategy=strategy, limit=limit, offset=offset)


@router.get("/backtest")
def list_backtests(symbol: str = "", strategy: str = "", limit: int = 20,
                   db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    return repository.list_backtests(db, symbol=symbol, strategy=strategy, limit=limit)


@router.post("/backtest")
def run_backtest(payload: dict = Body(...), db: Session = Depends(get_db),
                 _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Chạy backtest 1 mã + 1 chiến thuật rồi lưu kết quả (spec §23)."""
    symbol = _clean_symbol(payload.get("symbol"))
    strategy = str(payload.get("strategy") or "UPTREND").upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Thiếu mã cổ phiếu")
    result = backtest_module.run(symbol, strategy)
    result["id"] = repository.save_backtest(db, result) if result.get("stats") else None
    return result


# ---------------------------------------------------------------------------
# Scan / phân tích
# ---------------------------------------------------------------------------
@router.post("/scan")
def scan(payload: dict = Body(default={}), db: Session = Depends(get_db),
         _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Quét thị trường → tạo/cập nhật signal (lưu DB)."""
    symbols = payload.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    symbols = [_clean_symbol(s) for s in symbols]
    symbols = [s for s in symbols if s]
    limit = payload.get("limit")
    result = engine_module.scan(
        symbols=symbols or None,
        limit=int(limit) if limit else None,
        db=db,
        persist=True,
        use_cache=bool(payload.get("use_cache", True)),
    )
    # Quét lại cũng là "cập nhật lại": đóng tín hiệu đã quá hạn nắm giữ T+ (nến benchmark vừa
    # được nạp trong lúc quét nên có thể đếm phiên chính xác).
    _expire_overdue(db)
    rows = [serializers.admin_list_row(item) for item in result.get("items") or []]
    rows.sort(key=lambda r: (r.get("signal") is not None, r.get("score") or 0), reverse=True)
    return {
        "as_of": result.get("as_of"),
        "generated_at": result.get("generated_at"),
        "count": result.get("count"),
        "market": result.get("market"),
        "rotation": result.get("rotation"),
        "items": rows,
    }


@router.post("/analyze/{symbol}")
def analyze(symbol: str, db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Phân tích 1 mã với ĐẦY ĐỦ thông tin admin (BUY/SELL, Entry, SL, TP, Trailing)."""
    cleaned = _clean_symbol(symbol)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Mã không hợp lệ")
    previous = repository.previous_states(db, cleaned)
    snapshot = engine_module.analyze_symbol(cleaned, previous_states=previous, db=db)
    if snapshot.get("status") == "DATA_UNAVAILABLE":
        raise HTTPException(status_code=404, detail=f"Không có dữ liệu cho mã {cleaned}")
    return serializers.admin_view(snapshot)


@router.post("/refresh")
def refresh(payload: dict = Body(default={}), db: Session = Depends(get_db),
            _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Cập nhật lại tín hiệu ĐANG MỞ theo phiên mới nhất (vòng đời T+).

    Trang Tín hiệu gọi endpoint này khi mở trang (chạy nền, không chặn UI):
    1. Đóng tín hiệu đã QUÁ HẠN nắm giữ T+ (``signal.expiry.max_holding_sessions``).
    2. Phân tích lại các mã còn tín hiệu đang mở nhưng dữ liệu đã CŨ → trạng thái trong bảng
       luôn phản ánh nến mới nhất (setup hỏng ⇒ WEAKENING/EXIT/INVALIDATED, không "sống" mãi).
    """
    limit = payload.get("max_symbols") if isinstance(payload, dict) else None
    try:
        return engine_module.refresh_open_signals(db=db, max_symbols=int(limit) if limit else None)
    except Exception as exc:  # noqa: BLE001 — trả lỗi rõ ràng để UI hiển thị
        logger.warning("refresh tín hiệu lỗi: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc) or "Cập nhật tín hiệu thất bại.") from exc


# ---------------------------------------------------------------------------
# Danh sách + chi tiết (KHAI BÁO SAU các path tĩnh để không bị nuốt route)
# ---------------------------------------------------------------------------
@router.get("")
def list_signals(strategy: str = "", signal: str = "", state: str = "", symbol: str = "",
                 min_score: float | None = None, min_confidence: float | None = None,
                 date_from: str = "", date_to: str = "", active_only: bool = False,
                 sort: str = "confidence", direction: str = "",
                 one_per_symbol: bool = True,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Bảng tín hiệu + filter (spec §17).

    MỘT DÒNG CHO MỖI MÃ (``one_per_symbol``, mặc định BẬT): nếu một cổ phiếu có 2–3 chiến thuật
    cùng phát tín hiệu thì bảng chỉ hiển thị **1 dòng** — dòng đứng đầu theo đúng cột đang sắp xếp
    (đổi cột sắp xếp = đổi chiến thuật được chọn cho mã đó). ``total`` cũng đếm theo SỐ MÃ.

    Mặc định sắp theo **ĐỘ TIN CẬY giảm dần** (``sort=confidence``). Nút sort ở header bảng
    gửi ``sort`` = khoá cột (`symbol|strategy|signal|score|confidence|entry|stop_loss|
    trailing_stop|state|created|updated`) + ``direction`` = `asc|desc` (bỏ trống → hướng
    mặc định của cột, xem ``repository.sort_config``). Khoá cột lạ tự về ``confidence``.
    Cột "Giá hiện tại" là giá REALTIME (không có trong DB) — frontend tự sắp trong trang.

    Danh sách CHỈ gồm mã đạt thanh khoản TB20 (``engine.min_avg_liquidity_20d_ty``) — cùng
    quy tắc với vũ trụ quét, nên mọi tín hiệu đã tạo đều hiển thị được.
    """
    _expire_overdue(db)
    _min_ty, symbols = _liquidity_filter()
    return repository.list_signals(
        db, strategy=strategy, signal=signal, state=state, symbol=symbol,
        min_score=min_score, min_confidence=min_confidence,
        date_from=date_from, date_to=date_to, active_only=active_only,
        sort=sort, direction=direction, symbols=symbols, one_per_symbol=one_per_symbol,
        limit=limit, offset=offset,
    )


@router.get("/positions")
def list_positions(limit: int = Query(300, ge=1, le=500), db: Session = Depends(get_db),
                   _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Vị thế của TÀI KHOẢN ĐANG ĐĂNG NHẬP (đang mở + đã đóng) — kèm lãi/lỗ TẠM TÍNH theo giá hiện tại."""
    rows = repository.list_positions(db, _user.id, limit=limit)
    open_symbols = [r.symbol for r in rows if r.status == repository.POSITION_OPEN]
    quotes = vns.get_live_quotes(open_symbols) if open_symbols else {}
    items = [repository.position_out(r, (quotes.get(str(r.symbol).upper()) or {}).get("price")) for r in rows]
    return {"items": items, "summary": _position_summary(db, _user)}


@router.post("/positions")
def open_position(payload: dict = Body(...), db: Session = Depends(get_db),
                  _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """BẬT vị thế cho 1 mã: mở tại GIÁ HIỆN TẠI (realtime) — từ đây mới bắt đầu tính lãi/lỗ."""
    symbol = _clean_symbol(payload.get("symbol"))
    if not symbol:
        raise HTTPException(status_code=400, detail="Thiếu mã cổ phiếu")
    quote = (vns.get_live_quotes([symbol]) or {}).get(symbol) or {}
    price = quote.get("price")
    if not price:
        raise HTTPException(status_code=409, detail=f"Chưa có giá hiện tại cho mã {symbol} — vui lòng thử lại sau.")
    row, created = repository.open_position(
        db, _user.id, symbol, price=float(price),
        strategy=str(payload.get("strategy") or ""),
        signal_id=payload.get("signal_id"),
        note=str(payload.get("note") or ""),
    )
    return {
        "ok": True,
        "created": created,
        "message": (f"Đã mở vị thế {symbol} tại {price:,.0f} đ") if created else f"{symbol} đang có vị thế mở",
        "position": repository.position_out(row, float(price)),
    }


@router.post("/positions/{position_id}/close")
def close_position(position_id: int, payload: dict = Body(default={}), db: Session = Depends(get_db),
                   _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """TẮT vị thế: đóng tại giá hiện tại ⇒ CHỐT lãi/lỗ và ghi vào tỷ lệ thắng."""
    row = (
        db.query(SignalPosition)
        .filter(SignalPosition.id == position_id, SignalPosition.user_id == _user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế")
    if row.status == repository.POSITION_CLOSED:
        return {"ok": True, "created": False, "message": "Vị thế đã đóng trước đó",
                "position": repository.position_out(row)}
    price = payload.get("price") if isinstance(payload, dict) else None
    if not price:
        quote = (vns.get_live_quotes([row.symbol]) or {}).get(str(row.symbol).upper()) or {}
        price = quote.get("price")
    if not price:
        raise HTTPException(status_code=409, detail=f"Chưa có giá hiện tại cho mã {row.symbol} — vui lòng thử lại sau.")
    row = repository.close_position(db, row, price=float(price),
                                   reason=str((payload or {}).get("reason") or ""))
    return {
        "ok": True,
        "message": f"Đã đóng vị thế {row.symbol}: {row.result} {row.return_pct}%",
        "position": repository.position_out(row),
    }


@router.get("/quotes")
def live_quotes(symbols: str = "", _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Giá HIỆN TẠI (realtime) cho danh sách mã đang hiển thị trên bảng tín hiệu.

    Gom nhiều mã vào 1-2 request thay vì gọi ``quote`` từng mã (bảng có thể tới 200 dòng).
    Trả ``{items: {MÃ: {price, change, percent_change, as_of}}}``; mã chưa có giá thì thiếu key
    (UI hiển thị "—") — KHÔNG bịa giá.
    """
    parsed: list[str] = []
    seen: set[str] = set()
    for chunk in (symbols or "").replace(";", ",").split(","):
        code = _clean_symbol(chunk)
        if code and code not in seen:
            seen.add(code)
            parsed.append(code)
        if len(parsed) >= 80:  # trần an toàn cho 1 request
            break
    return {"items": vns.get_live_quotes(parsed) if parsed else {}}


@router.get("/{signal_id}")
def signal_detail(signal_id: int, db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Chi tiết signal + conditions + history + "Why this signal" (spec §18)."""
    data = repository.get_signal(db, signal_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy signal")
    return data


def _clean_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in (".", "-"))
    return cleaned[:12]
