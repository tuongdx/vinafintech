"""TuniX Signal Engine — cấu hình tập trung (KHÔNG hard-code threshold rải rác).

Thứ tự merge (sau ghi đè trước):
  1. ``DEFAULTS`` — giá trị gốc trong file này.
  2. File JSON (mặc định ``app/services/signal_engine/signal_config.json``, đổi bằng
     ENV ``SIGNAL_CONFIG_PATH``) — dùng khi muốn version-control bộ tham số riêng.
  3. Bản ghi DB ``settings`` với key ``signal_engine_config`` (JSON) — cho phép
     đổi runtime không cần deploy.

Toàn bộ module khác PHẢI lấy ngưỡng qua ``get_config()`` / ``cfg('strategies.uptrend.rsi_min')``.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "signal_config.json"
_DB_KEY = "signal_engine_config"
_TTL_SECONDS = 60

# ---------------------------------------------------------------------------
# DEFAULTS — mọi ngưỡng của hệ thống nằm ở đây (spec §26)
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "version": "1.0.0",
    "engine": {
        # Số phiên tối thiểu để tính đủ feature (52 tuần ~ 260 phiên; cần >= 200 để có MA50 + RS)
        "min_bars": 200,
        "bars_for_52w": 260,
        "history_period": "2y",
        "interval": "1d",
        "benchmark": "VNINDEX",
        "universe_limit": 120,          # số mã mỗi lần scan (hàng trăm mã: chạy batch nhiều lượt)
        "universe_min_volume_ratio": 0.5,
        "scan_workers": 8,              # ThreadPool: tính song song feature cho nhiều mã
        "cache_ttl_seconds": 900,       # cache snapshot phân tích mỗi mã
        "snapshot_ttl_seconds": 300,    # cache snapshot thị trường (regime)
    },
    "features": {
        "ma_periods": [10, 20, 50],
        "momentum_periods": {"short": 10, "medium": 21, "long": 63},
        "rsi_period": 14,
        "rsi_overbought": 70.0,
        "rsi_oversold": 30.0,
        "rsi_acceleration_low": 60.0,
        "rsi_acceleration_high": 80.0,
        "volume_short": 5,
        "volume_long": 40,
        "volume_surge_ratio": 1.5,      # volume TB5 / TB40
        "volume_exhaustion_ratio": 0.6,  # volume cạn (áp lực bán yếu dần)
        "cmf_period": 20,
        "cmf_short_period": 10,
        "cmf_positive": 0.05,
        "cmf_strong": 0.10,
        "cmf_negative": -0.05,
        "consolidation_min_bars": 15,   # nền tích luỹ tối thiểu
        "consolidation_max_range_pct": 12.0,  # biên độ nền / giá <= 12% coi là nền chặt
        "breakout_lookback": 20,
        "relative_strength_periods": {"short": 21, "medium": 63},
        "wavelet_levels": 4,
    },
    "ai": {
        # KHÔNG bật model chưa train. Danh sách này chỉ khai báo adapter nào được nạp.
        "models": ["wavelet_transformer", "cmdmamba"],
        "ensemble_min_agreement": 0.5,   # 0.5 = 2 model đồng thuận; chưa có model -> agreement = None
        "allow_untrained_boost": False,  # không cho model chưa train ảnh hưởng điểm
        "sequence_length": 60,
    },
    "signal": {
        "weights": {
            "ai": 0.15,
            "trend": 0.18,
            "momentum": 0.14,
            "ma": 0.12,
            "rsi": 0.10,
            "volume": 0.10,
            "money_flow": 0.08,
            "relative_strength": 0.08,
            "price_structure": 0.05,
        },
        # Ngưỡng xếp hạng tổng điểm
        "score_weak": 40,
        "score_moderate": 55,
        "score_strong": 70,
        "score_very_strong": 85,
        # Cổng chất lượng: chỉ phát signal BUY khi Setup Strength đạt tối thiểu mức này
        # ("" = tắt cổng). KHÔNG áp dụng cho SELL — lệnh thoát luôn phải chạy được.
        "require_min_setup_strength": "MODERATE",
        "setup_strength": {
            "very_strong": {"score": 85, "confirmations": 8, "technical_agreement": 0.85},
            "strong": {"score": 70, "confirmations": 6, "technical_agreement": 0.7},
            "moderate": {"score": 55, "confirmations": 4, "technical_agreement": 0.5},
        },
        # Vòng đời T+: tín hiệu của TuniX là tín hiệu NGẮN HẠN (T+), chỉ có ý nghĩa trong
        # một số phiên nắm giữ. Quá hạn ⇒ KHÔNG còn hiệu lực (tự đóng EXIT) và phải phân
        # tích lại bằng nến mới nhất khi mở trang Tín hiệu.
        "expiry": {
            "enabled": True,
            "max_holding_sessions": 10,     # T+10: quá 10 phiên kể từ khi tín hiệu mở ⇒ hết hạn
            "auto_refresh_on_load": True,   # mở trang Tín hiệu → tự cập nhật lại tín hiệu đang mở
            "refresh_max_symbols": 80,      # trần số mã phân tích lại mỗi lần (chạy nền, không chặn UI)
        },
    },
    "risk": {
        "atr_stop_multiple": 2.0,        # stop loss = entry - 2*ATR
        "atr_target_multiple": 4.0,      # take profit = entry + 4*ATR (R:R = 2)
        "trailing_atr_multiple": 3.0,    # trailing stop = max(entry-2ATR, close - 3ATR)
        "min_atr_pct": 0.5,              # ATR% quá thấp -> thiếu biến động để giao dịch
        "max_atr_pct": 9.0,              # ATR% quá cao -> rủi ro
        "risk_low_max": 35,
        "risk_medium_max": 60,
    },
    "regime": {
        "benchmark_ma_fast": 20,
        "benchmark_ma_slow": 50,
        "breadth_strong_up": 0.65,       # tỉ lệ mã tăng / tổng
        "breadth_weak": 0.35,
        "momentum_strong_up": 3.0,       # % thay đổi chỉ số 1 tháng
        "momentum_up": 1.0,
        "momentum_down": -3.0,
        "momentum_strong_down": -7.0,
    },
    "strategies": {
        "uptrend": {
            "enabled": True,
            "rsi_min": 55.0,
            "rsi_max": 82.0,
            "require_ai": False,             # spec §9.1-9.2: AI là điều kiện khi CÓ model
            "require_volume_surge": True,
            "require_ma_alignment": True,
            "require_breakout": True,
            "require_relative_strength": True,
            "min_composite_momentum": 2.0,
            "min_consolidation_bars": 15,
            "max_distance_from_high_pct": 6.0,   # trong vùng 6% dưới đỉnh 52 tuần
            "bad_signal_exit_threshold": 4,
            "preferred_regimes": ["STRONG_UPTREND", "UPTREND"],
        },
        "bottom_fishing": {
            "enabled": True,
            "minimum_confirmations": 7,
            "total_confirmations": 8,
            "require_ai": False,
            "max_distance_from_high_pct": 25.0,   # giảm sâu >= 25% từ đỉnh 52 tuần
            "rsi_max": 35.0,
            "momentum_max": -3.0,
            "ma_alignment_max": 30.0,
            "max_distance_from_low_pct": 12.0,    # đã hồi từ đáy <= 12% (không bắt dao rơi)
            "bad_signal_exit_threshold": 3,
            "preferred_regimes": ["WEAK_MARKET", "DOWNTREND", "SIDEWAYS"],
        },
        "swing": {
            "enabled": True,
            "cmf_entry": 0.10,
            "cmf_exit": 0.05,
            "require_ai": False,
            "min_relative_strength": 0.0,
            "portfolio_size": 5,
            "require_above_ma20": True,
            "bad_signal_exit_threshold": 3,
            "preferred_regimes": ["SIDEWAYS", "UPTREND", "STRONG_UPTREND"],
        },
    },
    "state_machine": {
        "watch_confirm_ratio": 0.4,
        "forming_confirm_ratio": 0.7,
        "weakening_bad_signals": 2,
        "exit_bad_signals": 4,
    },
    "backtest": {
        "max_trades": 40,
        "min_trades_for_stats": 5,
        "fee_pct": 0.15,                 # phí mua+bán mỗi chiều (%)
        "allow_shorts": False,
    },
    "permissions": {
        "admin_roles": ["super-admin", "admin"],
    },
}

_lock = threading.Lock()
_cached: dict[str, Any] | None = None
_cached_at: float = 0.0
_config_version: str = ""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_json_file() -> dict[str, Any]:
    path = Path(os.environ.get("SIGNAL_CONFIG_PATH") or _DEFAULT_CONFIG_PATH)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — file lỗi thì dùng default, không làm chết API
        return {}
    return {}


def _read_db_override() -> dict[str, Any]:
    """Đọc override từ bảng `settings` (key signal_engine_config). Lỗi DB -> bỏ qua."""
    try:
        from app.db import SessionLocal
        from app.models import Setting

        db = SessionLocal()
        try:
            row = db.query(Setting).filter(Setting.key == _DB_KEY).first()
            if not row or not row.value:
                return {}
            data = json.loads(row.value)
            return data if isinstance(data, dict) else {}
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return {}


def get_config(force: bool = False) -> dict[str, Any]:
    """Trả config đã merge (cache 60s)."""
    global _cached, _cached_at, _config_version
    now = time.time()
    if _cached is not None and not force and (now - _cached_at) < _TTL_SECONDS:
        return _cached
    with _lock:
        if _cached is not None and not force and (time.time() - _cached_at) < _TTL_SECONDS:
            return _cached
        merged = _deep_merge(DEFAULTS, _read_json_file())
        merged = _deep_merge(merged, _read_db_override())
        _cached = merged
        _cached_at = time.time()
        _config_version = str(merged.get("version") or "1.0.0")
    return _cached


def get(path: str, default: Any = None) -> Any:
    """Đọc 1 giá trị theo đường dẫn ``a.b.c``."""
    node: Any = get_config()
    for part in str(path).split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def config_version() -> str:
    get_config()
    return _config_version or "1.0.0"


def save_config_override(override: dict[str, Any]) -> None:
    """Lưu override vào DB (dùng cho script/CLI, không expose qua API)."""
    from app.db import SessionLocal
    from app.models import Setting

    db = SessionLocal()
    try:
        row = db.query(Setting).filter(Setting.key == _DB_KEY).first()
        payload = json.dumps(override or {}, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            db.add(Setting(key=_DB_KEY, value=payload))
        db.commit()
    finally:
        db.close()
    get_config(force=True)


def admin_thresholds() -> dict[str, Any]:
    """Bản config rút gọn cho UI Admin (xem/debug threshold đang chạy)."""
    cfg = get_config()
    return {
        "version": cfg.get("version"),
        "features": cfg.get("features"),
        "signal": cfg.get("signal"),
        "risk": cfg.get("risk"),
        "strategies": cfg.get("strategies"),
        "state_machine": cfg.get("state_machine"),
        "backtest": cfg.get("backtest"),
        "engine": cfg.get("engine"),
    }
