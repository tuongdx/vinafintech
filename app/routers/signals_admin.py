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
from app.models import User
from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import repository, serializers
from app.services.signal_engine import engine as engine_module
from app.services.signal_engine import model_performance
from app.services.signal_engine import backtest as backtest_module

logger = logging.getLogger("tunix.signal.api")

router = APIRouter(prefix="/signals", tags=["signals"])

# Người dùng được cấp quyền "Tín hiệu" (admin cấp theo tài khoản) — kiểm tra ở server.
_ACCESS = require_signal_access


# ---------------------------------------------------------------------------
# Siêu dữ liệu
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Tổng quan dashboard tín hiệu (spec §17)."""
    stats = repository.overview_stats(db)
    stats["strategies"] = [
        {"name": name, "display_name": meta[0], "description": meta[1]}
        for name, meta in repository._STRATEGY_META.items()  # noqa: SLF001 — nguồn chuẩn của engine
    ]
    stats["model_status"] = model_performance.stats(db).get("status")
    stats["config_version"] = cfg_module.config_version()
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


# ---------------------------------------------------------------------------
# Danh sách + chi tiết (KHAI BÁO SAU các path tĩnh để không bị nuốt route)
# ---------------------------------------------------------------------------
@router.get("")
def list_signals(strategy: str = "", signal: str = "", state: str = "", symbol: str = "",
                 min_score: float | None = None, min_confidence: float | None = None,
                 date_from: str = "", date_to: str = "", active_only: bool = False,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db), _user: User = Depends(_ACCESS)) -> dict[str, Any]:
    """Bảng tín hiệu + filter (spec §17)."""
    return repository.list_signals(
        db, strategy=strategy, signal=signal, state=state, symbol=symbol,
        min_score=min_score, min_confidence=min_confidence,
        date_from=date_from, date_to=date_to, active_only=active_only,
        limit=limit, offset=offset,
    )


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
