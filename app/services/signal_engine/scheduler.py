"""Quét TỰ ĐỘNG toàn thị trường theo LỊCH cho trang Tín hiệu.

Lịch mặc định (giờ VN, cấu hình ``schedule.scan_times``): **11:00 · 13:30 · 15:00** mỗi ngày
giao dịch (Thứ 2 – Thứ 6). Mỗi mốc, hệ thống quét **toàn bộ mã đủ điều kiện** (thanh khoản
GTGD TB20 ≥ ``engine.min_avg_liquidity_20d_ty``) và ghi tín hiệu vào DB ⇒ bảng Tín hiệu luôn
có dữ liệu mới mà người dùng không phải bấm gì.

Nguyên tắc:
- Chạy trong **THREAD NỀN (daemon)** như các job 12h trưa khác của TuniX ⇒ KHÔNG chặn API.
- Quét bỏ cache snapshot (``schedule.use_cache = False``) nên phân tích bằng **dữ liệu mới nhất**
  (nến ngày mới nhất + giá intraday của phiên đang chạy).
- Cột "Giá hiện tại" trên bảng vẫn là **giá realtime** (API quotes, cache 15s, UI tự làm mới mỗi
  phút) — độc lập với lịch quét này.
- Mỗi mốc giờ chỉ chạy 1 lần (idempotent); kết quả lần quét gần nhất giữ trong cache tiến trình
  để hiển thị ở pill đầu trang Tín hiệu (``info()``).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from app.services.news_time import now_vn
from app.services.signal_engine import config as cfg_module

logger = logging.getLogger("tunix.signal.scheduler")

# Khoá cache giữ tóm tắt lần quét gần nhất (cache tiến trình của vnstock_service).
_LAST_RUN_KEY = "signal-scan-last-run"
_LAST_RUN_TTL = 7 * 86400
# Mốc giờ mặc định khi `schedule.scan_times` để trống/sai định dạng.
_DEFAULT_SLOTS: list[tuple[int, int]] = [(11, 0), (13, 30), (15, 0)]

_STARTED = False
_RUNNING = False
_LOCK = threading.Lock()
_THREAD_NAME = "signals-scheduler"


# ---------------------------------------------------------------------------
# Cấu hình / tính mốc giờ
# ---------------------------------------------------------------------------
def parse_times(values: Any) -> list[tuple[int, int]]:
    """``["11:00", "13:30", "15:00"]`` ⇒ ``[(11, 0), (13, 30), (15, 0)]`` (đã sắp xếp, bỏ trùng).

    Chấp nhận cả dạng ``"11h"`` / ``"11.00"``; giá trị sai bị bỏ qua. Rỗng ⇒ dùng mốc mặc định.
    """
    slots: list[tuple[int, int]] = []
    for raw in (values if isinstance(values, (list, tuple)) else []):
        text = str(raw or "").strip().lower().replace("h", ":").replace(".", ":")
        parts = [p for p in text.split(":") if p.strip() != ""]
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            slots.append((hour, minute))
    return sorted(set(slots)) or list(_DEFAULT_SLOTS)


def schedule_config(cfg: dict | None = None) -> dict[str, Any]:
    """Cấu hình lịch quét đã chuẩn hoá (đọc từ ``schedule`` trong config engine)."""
    conf = ((cfg or cfg_module.get_config()).get("schedule") or {})
    try:
        universe_limit = int(conf.get("universe_limit") or 0)
    except (TypeError, ValueError):
        universe_limit = 0
    return {
        "enabled": bool(conf.get("enabled", True)),
        "times": parse_times(conf.get("scan_times")),
        "weekdays_only": bool(conf.get("weekdays_only", True)),
        "universe_limit": universe_limit,
        "use_cache": bool(conf.get("use_cache", False)),
        "scan_workers": max(0, int(conf.get("scan_workers") or 0)),
        "refresh_open_signals": bool(conf.get("refresh_open_signals", True)),
    }


def next_run(now: datetime | None = None, cfg: dict | None = None) -> datetime:
    """Mốc quét KẾ TIẾP (giờ VN, naive) — cùng với ``info()`` để UI hiển thị "lần quét tới"."""
    conf = schedule_config(cfg)
    current = now_vn() if now is None else now
    for _ in range(8):  # tìm trong tối đa 8 ngày tới (đủ để bỏ qua T7/CN)
        for hour, minute in conf["times"]:
            candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= current:
                continue
            if conf["weekdays_only"] and candidate.weekday() >= 5:
                continue
            return candidate
        current = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return current


def info() -> dict[str, Any]:
    """Tóm tắt lịch quét cho UI (mốc giờ + lần quét gần nhất/kế tiếp)."""
    from app.services import vnstock_service

    conf = schedule_config()
    last = None
    try:
        last = vnstock_service.cache.get(_LAST_RUN_KEY) or None
    except Exception:  # noqa: BLE001
        last = None
    return {
        "enabled": conf["enabled"],
        "times": [f"{h:02d}:{m:02d}" for h, m in conf["times"]],
        "weekdays_only": conf["weekdays_only"],
        "universe_limit": conf["universe_limit"],
        "next_run_at": next_run().isoformat(),
        "last_run_at": (last or {}).get("finished_at"),
        "last_run": last,
        # Thread quét có đang sống trong TIẾN TRÌNH này không (chẩn đoán nhanh ở UI).
        "worker_alive": any(t.name == _THREAD_NAME for t in threading.enumerate()),
    }


# ---------------------------------------------------------------------------
# Chạy quét
# ---------------------------------------------------------------------------
def run_scan(*, limit: int | None = None, reason: str = "schedule") -> dict[str, Any]:
    """QUÉT TOÀN THỊ TRƯỜNG (mã đủ điều kiện) và ghi tín hiệu vào DB.

    ``limit`` = 0/None ⇒ dùng ``schedule.universe_limit`` (mặc định 0 = không giới hạn).
    Trả tóm tắt (số mã đã quét, thời gian, lỗi nếu có) để log và hiển thị ở pill đầu trang.
    """
    from app.db import SessionLocal
    from app.services import vnstock_service
    from app.services.signal_engine import engine as engine_module

    conf = schedule_config()
    started = now_vn()
    summary: dict[str, Any] = {"reason": reason, "started_at": started.isoformat()}
    cap = conf["universe_limit"] if limit in (None, 0) else int(limit)
    db = SessionLocal()
    try:
        cfg = cfg_module.get_config()
        # Job NỀN: hạ số luồng so với quét tương tác (Render free 512MB rất dễ OOM nếu quét song song nhiều).
        if conf["scan_workers"] > 0:
            base = dict(cfg)
            engine_cfg = dict(base.get("engine") or {})
            engine_cfg["scan_workers"] = min(int(engine_cfg.get("scan_workers", 8)), conf["scan_workers"])
            base["engine"] = engine_cfg
            cfg = base
        # 0 = KHÔNG giới hạn ⇒ quét hết mọi mã đủ điều kiện (thanh khoản TB20 + tradable).
        universe = engine_module.default_universe(0, cfg)
        if cap > 0:
            universe = universe[:cap]
        summary["universe"] = len(universe)
        if not universe:
            summary["error"] = "không lấy được vũ trụ mã đủ điều kiện"
        else:
            result = engine_module.scan(
                symbols=universe, limit=len(universe), cfg=cfg,
                db=db, persist=True, use_cache=conf["use_cache"],
            )
            summary["scanned"] = int(result.get("count") or 0)
            summary["as_of"] = result.get("as_of")
            if conf["refresh_open_signals"]:
                # Sau khi quét: đóng tín hiệu QUÁ HẠN T+ và cập nhật các mã có tín hiệu nhưng
                # nằm ngoài vũ trụ vừa quét (những mã khác đã mới nên bước này gần như không tốn gì).
                # force=True: bỏ qua cooldown 5 phút của /signals/refresh (lịch hệ thống luôn được chạy).
                refresh = engine_module.refresh_open_signals(db=db, cfg=cfg, force=True)
                summary["expired"] = int(refresh.get("expired") or 0)
                summary["refreshed"] = int(refresh.get("refreshed") or 0)
    except Exception as exc:  # noqa: BLE001 — thread nền không được chết
        logger.warning("quét theo lịch lỗi: %s", exc)
        summary["error"] = str(exc)
    finally:
        db.close()

    summary["finished_at"] = now_vn().isoformat()
    summary["seconds"] = round(max(0.0, (now_vn() - started).total_seconds()), 1)
    try:
        vnstock_service.cache.set(_LAST_RUN_KEY, summary, ttl_seconds=_LAST_RUN_TTL)
    except Exception:  # noqa: BLE001
        pass
    logger.info("quét tín hiệu theo lịch xong: %s", summary)
    return summary


def _seconds_until_next(cfg: dict | None = None) -> float:
    now = now_vn()
    return max(1.0, (next_run(now, cfg) - now).total_seconds())


def _loop() -> None:
    """Thread nền: ngủ tới mốc giờ kế tiếp → quét → lặp lại (không bao giờ thoát)."""
    global _RUNNING
    while True:
        try:
            wait = _seconds_until_next()
            logger.info("quét tín hiệu tự động: lần kế tiếp sau %.0f phút", wait / 60)
            time.sleep(wait)
            if not schedule_config()["enabled"]:
                time.sleep(60)
                continue
            if _RUNNING:  # lần quét trước chưa xong ⇒ bỏ mốc này, chờ mốc sau
                logger.warning("lần quét trước chưa xong — bỏ qua mốc này")
                continue
            _RUNNING = True
            try:
                run_scan(reason="schedule")
            finally:
                _RUNNING = False
        except Exception as exc:  # noqa: BLE001 — không để thread chết vì lỗi nguồn
            logger.warning("vòng lặp quét theo lịch lỗi: %s", exc)
            time.sleep(300)


def start_scheduler() -> None:
    """Bật quét theo lịch (idempotent) — gọi 1 lần lúc startup app."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    conf = schedule_config()
    if not conf["enabled"]:
        logger.info("quét tín hiệu tự động đang TẮT (schedule.enabled = false)")
        return
    threading.Thread(target=_loop, daemon=True, name=_THREAD_NAME).start()
    logger.info(
        "đã bật quét tín hiệu tự động: %s giờ VN mỗi ngày%s (tới %s)",
        ", ".join(f"{h:02d}:{m:02d}" for h, m in conf["times"]),
        " giao dịch (T2–T6)" if conf["weekdays_only"] else "",
        next_run().strftime("%d/%m/%Y %H:%M"),
    )
