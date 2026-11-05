"""Vòng đời tín hiệu T+ — phát hiện tín hiệu HẾT HẠN và tín hiệu CŨ cần cập nhật lại.

Tín hiệu của VinaFintech là tín hiệu NGẮN HẠN (T+): chỉ có ý nghĩa trong một số phiên nắm giữ
(`signal.expiry.max_holding_sessions`, mặc định 10 phiên). Quá hạn đó tín hiệu KHÔNG còn
hiệu lực → phải tự đóng (EXIT) và/hoặc phân tích lại bằng nến mới nhất. Nếu không, một tín
hiệu từ nhiều tuần trước vẫn hiển thị "Đang hiệu lực" trên trang Tín hiệu (bug đã gặp).

Module này CHỈ chứa hàm thuần (không truy cập DB/mạng) để dễ test:
- ``expiry_config``      — đọc ngưỡng từ config tập trung.
- ``parse_time``         — parse ISO / datetime / epoch → datetime naive UTC.
- ``sessions_since``     — đếm số PHIÊN đã trôi qua (dựa trên nến benchmark VNINDEX).
- ``days_since``         — số ngày đã trôi qua.
- ``expiry_decision``    — quá hạn T+ hay chưa + số phiên/ngày.
- ``is_stale``           — tín hiệu đã cũ so với phiên mới nhất chưa (cần phân tích lại).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

DEFAULT_MAX_HOLDING_SESSIONS = 10
# Khi KHÔNG có nến benchmark để đếm phiên (nguồn lỗi / chưa warm cache): quy đổi 1 tuần
# giao dịch = 5 phiên ⇒ 10 phiên ≈ 14 ngày lịch.
_DAYS_PER_SESSION = 7 / 5

# Trạng thái "đang hiệu lực" — quá hạn T+ thì đóng (xem repository._EXPIRABLE_STATES).
ACTIVE_STATES = ("CONFIRMED", "ACTIVE", "WEAKENING")
# Trạng thái vẫn cần theo dõi → phân tích lại khi dữ liệu đã cũ.
TRACKED_STATES = ("WATCH", "SETUP_FORMING", "CONFIRMED", "ACTIVE", "WEAKENING")


def expiry_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ngưỡng hết hạn T+ (``signal.expiry``) — luôn đọc qua config tập trung."""
    from app.services.signal_engine import config as cfg_module

    cfg = cfg or cfg_module.get_config()
    raw = ((cfg.get("signal") or {}).get("expiry") or {})
    try:
        max_sessions = int(raw.get("max_holding_sessions") or DEFAULT_MAX_HOLDING_SESSIONS)
    except (TypeError, ValueError):
        max_sessions = DEFAULT_MAX_HOLDING_SESSIONS
    try:
        refresh_max = int(raw.get("refresh_max_symbols") or 80)
    except (TypeError, ValueError):
        refresh_max = 80
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_holding_sessions": max(1, max_sessions),
        "auto_refresh_on_load": bool(raw.get("auto_refresh_on_load", True)),
        "refresh_max_symbols": max(1, refresh_max),
    }


def parse_time(value: Any) -> datetime | None:
    """Parse mốc thời gian (ISO string / datetime / epoch giây hoặc ms) → datetime naive UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                number = float(text)
            except (TypeError, ValueError):
                return None
            if number > 1e11:  # ms (nến nguồn Binance/KBS)
                number /= 1000.0
            try:
                dt = datetime.fromtimestamp(number, timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def sessions_since(value: Any, benchmark_bars: list[dict] | None) -> int | None:
    """Số PHIÊN đã trôi qua kể từ mốc ``value`` (đếm nến benchmark SAU mốc đó).

    Trả ``None`` khi không parse được mốc hoặc không có nến benchmark (caller chuyển sang
    đếm theo ngày lịch — xem ``expiry_decision``).
    """
    started = parse_time(value)
    if started is None or not benchmark_bars:
        return None
    count = 0
    for bar in benchmark_bars:
        bar_time = bar.get("time") if isinstance(bar, dict) else None
        moment = parse_time(bar_time)
        if moment is not None and moment > started:
            count += 1
    return count


def days_since(value: Any, now: datetime | None = None) -> int | None:
    """Số NGÀY đã trôi qua kể từ mốc ``value`` (None nếu không parse được)."""
    started = parse_time(value)
    if started is None:
        return None
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if isinstance(now, datetime) and now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0, (now - started).days)


def latest_session_time(benchmark_bars: list[dict] | None) -> datetime | None:
    """Mốc thời gian của PHIÊN MỚI NHẤT (nến cuối của benchmark)."""
    if not benchmark_bars:
        return None
    moments = [m for m in (parse_time(b.get("time")) for b in benchmark_bars if isinstance(b, dict)) if m]
    return max(moments) if moments else None


def expiry_decision(value: Any, max_sessions: int, benchmark_bars: list[dict] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Tín hiệu (bắt đầu từ ``value``) đã QUÁ HẠN nắm giữ T+ hay chưa.

    Ưu tiên đếm theo PHIÊN; không có nến benchmark thì đếm theo ngày lịch
    (``max_sessions`` phiên ≈ ``ceil(max_sessions * 7 / 5)`` ngày).
    """
    sessions = sessions_since(value, benchmark_bars)
    days = days_since(value, now)
    if sessions is not None:
        return {
            "expired": sessions >= max_sessions,
            "sessions": sessions,
            "days": days,
            "metric": f"{sessions} phiên",
            "basis": "sessions",
        }
    limit_days = int(math.ceil(max_sessions * _DAYS_PER_SESSION))
    return {
        "expired": days is not None and days >= limit_days,
        "sessions": None,
        "days": days,
        "metric": f"{days} ngày",
        "basis": "days",
    }


def is_stale(value: Any, latest_session: Any) -> bool:
    """Tín hiệu đã CŨ so với phiên mới nhất (cần phân tích lại bằng nến mới)?

    - Không parse được mốc tín hiệu → coi là cũ (an toàn: phân tích lại).
    - Không xác định được phiên mới nhất (nguồn dữ liệu lỗi / chưa có nến) → KHÔNG coi là cũ,
      tránh phân tích lại vô ích hàng loạt khi nguồn lỗi.
    """
    moment = parse_time(value)
    latest = parse_time(latest_session)
    if moment is None:
        return True
    if latest is None:
        return False
    return moment < latest
