"""Snapshot "Cơ hội mới" (trang chủ) — tự cập nhật theo thị trường vào 12h trưa hằng ngày.

Block "Cơ hội mới" ở trang chủ (`#ovOpps` trong `frontend/Home.html`) hiển thị 3 nhóm
cổ phiếu:

  - growth    : tăng trưởng lợi nhuận ròng mạnh
  - value     : P/E, P/B thấp so với nền tảng
  - cash_flow : sức khỏe tài chính cao, cổ tức đều

Dữ liệu nguồn là screener thị trường (giá / thanh khoản / chỉ số cơ bản của phiên gần
nhất — `vnstock_service.get_stock_screener_page`, đã cache nên KHÔNG thêm request mạng).
Tiêu chí chọn mã dùng CHUNG `vnstock_service.build_discover_strategies` với trang Khám phá
⇒ 2 nơi không bị lệch tiêu chí.

CHỈ lấy cổ phiếu **vốn hoá lớn + thanh khoản đủ để giao dịch hằng ngày** — dùng CHUNG bộ
lọc `vnstock_service.is_tradable_stock` với Market Radar, Khám phá theo chiến lược, Xếp hạng
VinaFintech và universe của trang Tín hiệu (ngưỡng ở `settings.stock_min_market_cap` /
`stock_min_liquidity_ty`), nên không còn các mã UPCOM siêu nhỏ, ít thanh khoản.

Snapshot được dựng lại vào **12h trưa (giờ VN) hằng ngày**: trước 12h dùng bản 12h HÔM QUA,
từ 12h trở đi là dữ liệu mới (`_snapshot_day()` + thread nền `start_daily_refresh`) — cùng
cơ chế với "Lịch sự kiện thị trường" ở `app/routers/asset_events.py`.

Trang Khám phá KHÔNG bị ảnh hưởng: trang đó vẫn dùng `/api/stocks/stock-discover` (dữ liệu tươi).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings
from app.services import news_time
from app.services import vnstock_service as vns

logger = logging.getLogger("vinafintech.home_opportunities")

# 3 nhóm hiển thị ở block "Cơ hội mới" — khớp `renderOpps()` trong Home.html.
HOME_STRATEGY_KEYS = ("growth", "value", "cash_flow")

_SNAPSHOT_PREFIX = "home-opportunities"
# Nguồn lỗi ⇒ CHỈ giữ bản fallback ngắn (20s) để request kế tiếp còn thử dựng lại.
# (Trước đây 300s: lần load ĐẦU sau khi khởi động gặp screener chưa sẵn sàng sẽ bị
#  "dính" block trống tới 5 phút, phải sang trang khác rồi quay lại mới có dữ liệu.)
_FALLBACK_TTL = 20
_REFRESH_LOCK = threading.Lock()
_REFRESH_STARTED = False
_LAST_GOOD: dict[str, Any] | None = None


def _snapshot_day(now: datetime | None = None) -> str:
    """Mốc snapshot "Cơ hội mới": 12h trưa hôm nay — trước 12h vẫn tính mốc 12h HÔM QUA."""
    now = now or news_time.now_vn()
    day = now.date() if now.hour >= settings.home_opportunity_refresh_hour else now.date() - timedelta(days=1)
    return day.isoformat()


def _cache_key(day: str | None = None) -> str:
    return f"{_SNAPSHOT_PREFIX}:{day or _snapshot_day()}"


def _liquidity_ty(s: dict[str, Any]) -> float:
    """Thanh khoản (tỷ VND/phiên) — dùng chung helper của `vnstock_service`."""
    return vns.stock_liquidity_ty(s)


def _is_tradable(s: dict[str, Any]) -> bool:
    """Vốn hoá lớn + thanh khoản đủ để giao dịch hằng ngày — dùng chung helper.

    Ngưỡng nằm ở `settings.stock_min_market_cap` / `settings.stock_min_liquidity_ty`
    (`vnstock_service.is_tradable_stock`) ⇒ CÙNG bộ lọc với Market Radar, Khám phá
    theo chiến lược, Xếp hạng VinaFintech và universe của trang Tín hiệu.
    """
    return vns.is_tradable_stock(s)


def _build() -> dict[str, Any]:
    """Dựng snapshot từ dữ liệu thị trường HIỆN TẠI (screener đã cache)."""
    global _LAST_GOOD
    screener = vns.get_stock_screener_page()
    stocks = (screener or {}).get("stocks") or []
    if not stocks:
        raise RuntimeError("Screener thị trường chưa có dữ liệu")
    # Chỉ giữ mã vốn hoá lớn + thanh khoản đủ mạnh, rồi mới chọn nhóm theo tiêu chí.
    eligible = [s for s in stocks if _is_tradable(s)]
    if not eligible:
        raise RuntimeError("Không có mã nào đạt ngưỡng vốn hoá/thanh khoản")
    by_key = {s["key"]: s for s in vns.build_discover_strategies(eligible)}
    strategies = [by_key[k] for k in HOME_STRATEGY_KEYS if k in by_key]
    if not strategies:
        raise RuntimeError("Không dựng được nhóm cổ phiếu nào")
    # as_of = mốc dữ liệu phiên (VN-Index) — giống các block khác của trang chủ.
    try:
        as_of = vns.get_market_dashboard().get("as_of", "")
    except Exception:  # noqa: BLE001 — thiếu mốc phiên không làm hỏng block
        as_of = ""
    payload: dict[str, Any] = {
        "ok": True,
        "snapshot_day": _snapshot_day(),
        "refresh_hour": settings.home_opportunity_refresh_hour,
        "as_of": as_of,
        "updated_at": news_time.now_vn().isoformat(),
        "filters": {
            "min_market_cap": settings.stock_min_market_cap,
            "min_liquidity_ty": settings.stock_min_liquidity_ty,
            "universe": len(stocks),
            "eligible": len(eligible),
        },
        "strategies": strategies,
    }
    _LAST_GOOD = payload
    return payload


def get_home_opportunities() -> dict[str, Any]:
    """Snapshot "Cơ hội mới" đang hiệu lực (cache theo mốc 12h trưa, TTL theo config)."""
    key = _cache_key()
    cached = vns.cache.get(key)
    if cached is not None:
        return cached
    with vns.cache._lock_for(key):
        cached = vns.cache.get(key)
        if cached is not None:
            return cached
        try:
            payload = _build()
        except Exception as exc:  # noqa: BLE001 — nguồn lỗi KHÔNG được làm sập trang chủ
            logger.warning("home-opportunities: dựng snapshot lỗi (%s)", exc)
            if _LAST_GOOD is not None:
                return _LAST_GOOD
            fallback: dict[str, Any] = {
                "ok": False,
                "snapshot_day": _snapshot_day(),
                "as_of": "",
                "strategies": [],
                "message": "Chưa lấy được dữ liệu thị trường.",
            }
            vns.cache.set(key, fallback, ttl_seconds=_FALLBACK_TTL)
            return fallback
        vns.cache.set(key, payload, ttl_seconds=settings.home_opportunity_ttl)
        return payload


def refresh_home_opportunities() -> int:
    """Dựng lại snapshot NGAY (bỏ qua cache) — dùng cho scheduler 12h trưa + warm startup.

    Trả số nhóm dựng được; 0 = nguồn lỗi (KHÔNG ghi đè snapshot cũ để block vẫn có dữ liệu).
    """
    try:
        payload = _build()
    except Exception as exc:  # noqa: BLE001
        logger.warning("home-opportunities: làm mới lỗi (%s)", exc)
        return 0
    vns.cache.set(_cache_key(), payload, ttl_seconds=settings.home_opportunity_ttl)
    return len(payload["strategies"])


def _seconds_until_refresh() -> float:
    """Số giây tới mốc làm mới kế tiếp (12h trưa giờ VN)."""
    now = news_time.now_vn()
    target = now.replace(hour=settings.home_opportunity_refresh_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _refresh_loop() -> None:
    """Thread nền: ngủ tới 12h trưa rồi dựng lại snapshot, lặp lại mỗi ngày."""
    while True:
        try:
            time.sleep(_seconds_until_refresh())
            refresh_home_opportunities()
        except Exception:  # noqa: BLE001 — không để thread chết vì lỗi nguồn
            time.sleep(300)


def start_daily_refresh() -> None:
    """Bật làm mới "Cơ hội mới" vào 12h trưa (giờ VN) mỗi ngày (idempotent)."""
    global _REFRESH_STARTED
    with _REFRESH_LOCK:
        if _REFRESH_STARTED:
            return
        _REFRESH_STARTED = True
    threading.Thread(target=_refresh_loop, daemon=True, name="home-opportunities-daily").start()
