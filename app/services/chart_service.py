"""Dịch vụ dữ liệu cho TradingView Advanced Charts (charting_library).

Chuỗi dữ liệu:
    VinaFintech (adapter KBS + Simplize trong vnstock_service.py)
        -> chart_service (timestamp / lọc / aggregate / phân trang)
        -> /api/chart/* (FastAPI router)
        -> TradingView Datafeed (frontend)
        -> TradingView Advanced Charts

Quy tắc dữ liệu:
  - Chỉ dùng dữ liệu OHLCV thực của VinaFintech (KBS / Simplize). KHÔNG tạo dữ liệu giả.
  - Không sửa dữ liệu gốc; mọi biến đổi (aggregate 1W/1M, đổi timezone) đều là
    phép chuyển đổi chuẩn trên dữ liệu thực, giữ nguyên OHLC gốc.
  - NGUỒN CHÍNH: KBS (KB Securities) — daily ~10 năm (2016→nay), weekly/monthly,
    và INTRADAY 1H (60P). Công bố các timeframe: 1D (daily), 1W (weekly),
    1M (monthly), 1H (hourly). 1m/5m/15m/30m/4H KHÔNG được hỗ trợ.
  - FALLBACK: Simplize (daily/aggregate) nếu KBS lỗi hoặc không có dữ liệu.
  - Realtime: VinaFintech không có nguồn OHLCV realtime -> chart hoạt động chế độ
    historical. Code subscribeBars ở frontend đã được thiết kế để bật realtime
    sau này (xem tv-datafeed.js).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import vnstock_service as vs

# Cấu hình chung cho thị trường Việt Nam
CHART_TIMEZONE = "Asia/Ho_Chi_Minh"
CHART_SESSION = "0900-1500"  # Giờ giao dịch VN (nghỉ trưa TradingView tự xử lý)
SUPPORTED_RESOLUTIONS = ["1D", "1W", "1M", "1H"]
DEFAULT_RESOLUTION = "1D"
MAX_HISTORY_BARS = 3000

# Khoảng cách lịch (giây) ước lượng giữa 2 bar liên tiếp — dùng để tính from_ts
# từ countback (lần tải đầu không có from) cho nguồn KBS.
_KBS_BAR_SPAN = {
    "1D": int(1.6 * 86400),   # ~1 ngày giao dịch
    "1W": int(8 * 86400),
    "1M": int(32 * 86400),
    "1H": int(1.6 * 3600),
}

# Tiền tố sàn mà TradingView có thể gửi kèm khi resolveSymbol ("HOSE:VCB")
_EXCHANGE_PREFIXES = ("HOSE:", "HNX:", "UPCOM:", "UPCOMM:", "HSX:")

# pricescale: giá VND thường chẵn 100đ -> minmov=1, pricescale=100
_MINMOV = 1
_PRICESCALE = 100


# ---------------------------------------------------------------------------
# Cache ngắn hạn cho dữ liệu thô (không cache lâu -> tránh giá sai)
# ---------------------------------------------------------------------------
def _fetch_daily_series(symbol: str, period: str) -> list[dict[str, Any]]:
    """Lấy chuỗi OHLCV daily từ Simplize (qua adapter VinaFintech) có cache TTL ngắn.

    Dùng trực tiếp _simplize_chart (không qua get_history) để tránh fallback
    dữ liệu mẫu khi Simplize lỗi — biểu đồ chỉ nhận dữ liệu thực.
    """
    key = f"chart-series:{symbol.upper()}:{period}"
    cached = vs.cache.get(key)
    if cached is not None:
        return cached

    try:
        values = vs._simplize_chart(symbol.upper(), "stock", period)
    except Exception:
        values = []

    if not values:
        return []

    # Sắp xếp tăng dần theo thời gian, loại bỏ candle lỗi (malformed)
    clean: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in values:
        try:
            ts = int(item["time"] and _parse_iso_ts(item["time"]))
            if ts <= 0:
                continue
            o = _f(item["open"])
            h = _f(item["high"])
            l = _f(item["low"])
            c = _f(item["close"])
            v = int(_f(item["volume"]))
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue
            if ts in seen:
                continue  # không chấp nhận duplicate candle
            seen.add(ts)
            clean.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
        except Exception:
            continue

    clean.sort(key=lambda b: b["time"])
    if clean:
        vs.cache.set(key, clean, ttl_seconds=120)
    return clean


def _parse_iso_ts(value: str) -> float:
    """Chuyển chuỗi ISO (UTC) thành unix epoch seconds."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.timestamp()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return number
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Config & Symbol
# ---------------------------------------------------------------------------
def get_chart_config() -> dict[str, Any]:
    """Cấu hình chart mà TradingView yêu cầu qua Datafeed.onReady()."""
    return {
        "supports_search": True,
        "supports_group_request": False,
        "supports_marks": False,
        "supports_timescale_marks": False,
        "supports_time": True,
        "exchanges": [
            {"value": "", "name": "HOSE", "desc": "Sở GDCK TP. Hồ Chí Minh"},
            {"value": "", "name": "HNX", "desc": "Sở GDCK Hà Nội"},
            {"value": "", "name": "UPCOM", "desc": "Thị trường UPCoM"},
        ],
        "symbols_types": [{"name": "Cổ phiếu", "value": "stock"}],
        "supported_resolutions": list(SUPPORTED_RESOLUTIONS),
        "resolutions": list(SUPPORTED_RESOLUTIONS),
        "timezone": CHART_TIMEZONE,
        "session": CHART_SESSION,
        "has_intraday": True,
        "locale": "vi_VN",
        "realtime": False,  # VinaFintech chưa có nguồn OHLCV realtime
    }


def _normalize_symbol(raw: str) -> str:
    """Chuẩn hoá mã: bỏ tiền tố sàn, in hoa."""
    symbol = str(raw or "").strip().upper()
    for prefix in _EXCHANGE_PREFIXES:
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):]
            break
    return symbol


def resolve_chart_symbol(raw: str) -> dict[str, Any]:
    """resolveSymbol: trả metadata symbol cho TradingView."""
    symbol = _normalize_symbol(raw)
    if not symbol:
        return {"valid": False, "message": "Thiếu mã cổ phiếu"}

    if not vs._is_known_symbol(symbol):
        return {
            "valid": False,
            "symbol": symbol,
            "message": f"Không có mã cổ phiếu {symbol} trên các sàn HOSE, HNX, UPCoM",
        }

    meta = vs._lookup_symbol_metadata(symbol)
    exchange = str(meta.get("exchange") or "HOSE").upper()

    return {
        "valid": True,
        "symbol": symbol,
        "name": symbol,
        "full_name": f"{exchange}:{symbol}",
        "ticker": symbol,
        "description": str(meta.get("name") or symbol),
        "type": "stock",
        "session": CHART_SESSION,
        "timezone": CHART_TIMEZONE,
        "exchange": exchange,
        "listed_exchange": exchange,
        "minmov": _MINMOV,
        "minmove2": 0,
        "fractional": False,
        "pricescale": _PRICESCALE,
        "has_intraday": True,
        "has_seconds": False,
        "has_daily": True,
        "has_empty_bars": False,
        "supported_resolutions": list(SUPPORTED_RESOLUTIONS),
        "volume_precision": 0,
        "data_status": "delayed",
        "currency_code": "VND",
    }


# ---------------------------------------------------------------------------
# History (getBars) — phân trang theo from/to
# ---------------------------------------------------------------------------
def _pick_period(interval: str, from_ts: int, to_ts: int, countback: int = 0) -> str:
    """Chọn cửa sổ Simplize SÂU NHẤT cho timeframe (timeframe/zoom độc lập).

    - "1D"  : LUÔN dùng "1y" (~250 bar DAILY). Không bao giờ dùng "5y" (weekly)
              khi zoom ra — nếu không sẽ biến daily thành weekly khi xem lịch sử
              xa (yêu cầu: 1D phải luôn là nến 1 ngày).
    - "1W"/"1M": LUÔN dùng "5y" (~131 bar WEEKLY) — cửa sổ weekly sâu nhất, gộp
              thành weekly/monthly. Nhờ vậy `earliestTime` (biên lịch sử) luôn
              đúng cho từng timeframe, bất kể countback/from/to.
    """
    return "1y" if interval == "1D" else "5y"


def _week_start(ts: int) -> int:
    """Đầu tuần (thứ 2 00:00 UTC) của timestamp."""
    dt = datetime.fromtimestamp(ts, UTC)
    start = dt - timedelta(days=dt.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _month_start(ts: int) -> int:
    """Ngày 1 của tháng (00:00 UTC)."""
    dt = datetime.fromtimestamp(ts, UTC)
    return int(datetime(dt.year, dt.month, 1, tzinfo=UTC).timestamp())


def _aggregate_bars(daily: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    """Gộp bar daily thành weekly/monthly (OHLC chuẩn: open đầu, high max,
    low min, close cuối, volume cộng dồn). Không đổi dữ liệu gốc."""
    if interval not in ("1W", "1M"):
        return list(daily)

    bucket_key = _week_start if interval == "1W" else _month_start
    buckets: dict[int, dict[str, Any]] = {}
    for bar in daily:
        key = bucket_key(bar["time"])
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = {
                "time": key,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            }
        else:
            existing["high"] = max(existing["high"], bar["high"])
            existing["low"] = min(existing["low"], bar["low"])
            existing["close"] = bar["close"]
            existing["volume"] += bar["volume"]

    result = sorted(buckets.values(), key=lambda b: b["time"])
    return result


def _kbs_history(symbol: str, interval: str, from_ts: int, to_ts: int, countback: int, limit: int) -> tuple[list[dict[str, Any]] | None, int | None]:
    """Lấy OHLCV từ KBS (KB Securities).

    Trả (bars, earliestTime) — (None, None) nếu KBS không có dữ liệu.
    earliestTime = mốc lịch sử xa nhất KBS có cho symbol (probe 1D, cache 1h).
    """
    now = int(time.time())
    if from_ts:
        rows = vs._kbs_chart(symbol, interval, from_ts, to_ts or now)
    elif countback and countback > 0:
        span = _KBS_BAR_SPAN.get(interval, int(1.6 * 86400))
        rows = vs._kbs_chart(symbol, interval, now - countback * span, now)
    else:
        rows = vs._kbs_chart(symbol, interval, 0, now)
    if not rows:
        return None, None
    if limit and limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return rows, vs._kbs_earliest_time(symbol)


# Chỉ số (VNINDEX, VN30, HNX, HNX30, UPCOM...) — hiển thị trên chart như mã cổ phiếu.
_INDEX_CHART_MAP = {
    "VNINDEX": "vnindex",
    "VN30": "vn30",
    "HNX": "hnx",
    "HNXINDEX": "hnx",
    "HNX30": "hnx30",
    "UPCOM": "upcom",
    "UPINDEX": "upcom",
    "UPCOMM": "upcom",
}


def _index_history(index_key: str, symbol: str, interval: str, from_ts: int, to_ts: int, countback: int, limit: int) -> dict[str, Any]:
    """Lịch sử OHLCV của CHỈ SỐ (từ Simplize) — dùng chung định dạng chart với cổ phiếu."""
    if interval == "1H":
        return {
            "valid": True, "symbol": symbol, "interval": interval,
            "bars": [], "noData": True, "nextTime": None, "earliestTime": None,
            "message": "Chỉ số chưa hỗ trợ timeframe 1 giờ (1H).",
        }
    detail = vs.get_index_detail(index_key, "1y")
    history = detail.get("history", []) if isinstance(detail, dict) else []
    daily: list[dict[str, Any]] = []
    seen: set[int] = set()
    for h in history:
        ts = _parse_iso_ts(h.get("time", ""))
        if not ts:
            continue
        t = int(ts)
        if t in seen:
            continue
        seen.add(t)
        daily.append({
            "time": t,
            "open": _f(h.get("open")),
            "high": _f(h.get("high")),
            "low": _f(h.get("low")),
            "close": _f(h.get("close")),
            "volume": int(_f(h.get("volume"))),
        })
    daily.sort(key=lambda b: b["time"])
    if not daily:
        return {
            "valid": True, "symbol": symbol, "interval": interval,
            "bars": [], "noData": True, "nextTime": None, "earliestTime": None,
            "message": "Không có dữ liệu biểu đồ cho chỉ số này.",
        }
    bars = _aggregate_bars(daily, interval)
    if from_ts:
        bars = [b for b in bars if b["time"] >= from_ts]
    if to_ts:
        bars = [b for b in bars if b["time"] <= to_ts]
    if countback and countback > 0 and len(bars) > countback:
        bars = bars[-countback:]
    if limit and limit > 0 and len(bars) > limit:
        bars = bars[-limit:]
    earliest_time = bars[0]["time"] if bars else None
    older_exists = bool(earliest_time) and earliest_time > daily[0]["time"]
    return {
        "valid": True,
        "symbol": symbol,
        "interval": interval,
        "bars": bars,
        "noData": not bool(bars),
        "nextTime": bars[0]["time"] if older_exists else None,
        "earliestTime": earliest_time,
        "message": "",
    }


def get_chart_history(symbol: str, interval: str, from_ts: int = 0, to_ts: int = 0, limit: int = 0, countback: int = 0) -> dict[str, Any]:
    """getBars: trả OHLCV trong [from, to], sắp xếp tăng dần, dedupe, phân trang.

    - NGUỒN CHÍNH: KBS (KB Securities) — daily ~10 năm, weekly/monthly, intraday 1H.
      earliestTime = mốc lịch sử xa nhất KBS có (probe 1D, cache 1h).
    - FALLBACK: Simplize (daily + aggregate 1W/1M) khi KBS lỗi/không có dữ liệu.
    - countback > 0 & không có from: yêu cầu lần đầu -> trả `countback` bar gần nhất.
    - from/to cụ thể: trả bar trong khoảng [from, to] (load more history).
    """
    symbol = _normalize_symbol(symbol)
    interval = str(interval or DEFAULT_RESOLUTION).upper()
    if interval not in SUPPORTED_RESOLUTIONS:
        return {
            "valid": False,
            "message": f"Timeframe {interval} không được hỗ trợ. Chỉ hỗ trợ: {', '.join(SUPPORTED_RESOLUTIONS)}",
            "bars": [],
            "noData": True,
        }

    # Chỉ số (VNINDEX, VN30, HNX, HNX30, UPCOM...) — render trên chart như mã cổ phiếu.
    index_key = _INDEX_CHART_MAP.get(symbol)
    if index_key:
        to_ts = int(to_ts or 0)
        from_ts = int(from_ts or 0)
        countback = int(countback or 0)
        return _index_history(index_key, symbol, interval, from_ts, to_ts, countback, limit)

    if not vs._is_known_symbol(symbol):
        return {
            "valid": False,
            "message": f"Không có mã cổ phiếu {symbol} trên các sàn HOSE, HNX, UPCoM",
            "bars": [],
            "noData": True,
        }

    to_ts = int(to_ts or 0)
    from_ts = int(from_ts or 0)
    countback = int(countback or 0)
    first_request = countback > 0 and not from_ts
    if to_ts and from_ts and to_ts <= from_ts:
        return {"valid": True, "bars": [], "noData": True}

    # ---------- Nguồn chính: KBS ----------
    try:
        bars, earliest_time = _kbs_history(symbol, interval, from_ts, to_ts, countback, limit)
        if bars is not None:
            if not bars:
                return {
                    "valid": True,
                    "symbol": symbol,
                    "interval": interval,
                    "bars": [],
                    "noData": True,
                    "nextTime": None,
                    "earliestTime": earliest_time,
                    "message": "Không có dữ liệu biểu đồ cho khoảng thời gian này.",
                }
            older_exists = bool(earliest_time) and bars[0]["time"] > earliest_time
            return {
                "valid": True,
                "symbol": symbol,
                "interval": interval,
                "bars": bars,
                "noData": False,
                "nextTime": bars[0]["time"] if older_exists else None,
                "earliestTime": earliest_time,
                "message": "",
            }
    except Exception:
        pass  # rơi xuống fallback Simplize

    # ---------- Fallback: Simplize ----------
    if interval == "1H":
        # Simplize không có intraday -> không thể fallback cho 1H
        return {
            "valid": True,
            "symbol": symbol,
            "interval": interval,
            "bars": [],
            "noData": True,
            "nextTime": None,
            "earliestTime": None,
            "message": "Không có dữ liệu biểu đồ cho khoảng thời gian này.",
        }

    period = _pick_period(interval, from_ts, to_ts, countback)
    daily = _fetch_daily_series(symbol, period)
    if not daily:
        return {
            "valid": True,
            "symbol": symbol,
            "bars": [],
            "noData": True,
            "message": "Không có dữ liệu biểu đồ cho khoảng thời gian này.",
        }

    bars = _aggregate_bars(daily, interval)
    # earliestTime = mốc lịch sử xa nhất có sẵn (bar cũ nhất của cửa sổ Simplize).
    # Frontend dùng để biết khi nào đã tải đủ tới biên -> ngừng request (tránh lặp).
    earliest_time = bars[0]["time"] if bars else None

    # Lọc theo [from, to]
    if from_ts:
        bars = [b for b in bars if b["time"] >= from_ts]
    if to_ts:
        bars = [b for b in bars if b["time"] <= to_ts]

    # Yêu cầu lần đầu: trả `countback` bar gần nhất
    if first_request and countback and len(bars) > countback:
        bars = bars[-countback:]

    # Dedupe + sort tăng dần (đảm bảo trước khi trả về)
    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for bar in bars:
        if bar["time"] in seen:
            continue
        seen.add(bar["time"])
        unique.append(
            {
                "time": bar["time"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            }
        )
    unique.sort(key=lambda b: b["time"])

    # Phân trang / load more history: nếu vượt quá giới hạn, giữ phần mới nhất
    if limit and limit > 0 and len(unique) > limit:
        unique = unique[-limit:]

    # nextTime: nếu trong cửa sổ Simplize đã lấy còn bar CŨ HƠN bar cũ nhất
    # vừa trả -> cho TradingView biết để tải tiếp lịch sử (from = nextTime).
    # daily đã sort tăng dần nên daily[0] là bar cũ nhất của cửa sổ.
    no_data = not unique
    older_exists = bool(unique) and bool(daily) and daily[0]["time"] < unique[0]["time"]

    if no_data:
        return {
            "valid": True,
            "symbol": symbol,
            "interval": interval,
            "bars": [],
            "noData": True,
            "nextTime": None,
            "earliestTime": earliest_time,
            "message": "Không có dữ liệu biểu đồ cho khoảng thời gian này.",
        }

    meta: dict[str, Any] = {"noData": not older_exists}
    if older_exists:
        meta["nextTime"] = unique[0]["time"]

    return {
        "valid": True,
        "symbol": symbol,
        "interval": interval,
        "bars": unique,
        "noData": meta["noData"],
        "nextTime": meta.get("nextTime"),
        "earliestTime": earliest_time,
        "message": "",
    }


# ---------------------------------------------------------------------------
# Search & Realtime quote
# ---------------------------------------------------------------------------
def search_chart_symbols(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """searchSymbols: tìm mã cổ phiếu (dùng chung search_stocks của VinaFintech)."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        rows = vs.search_stocks(query)
    except Exception:
        rows = []
    limit = max(1, min(int(limit or 20), 50))
    result: list[dict[str, Any]] = []
    for item in rows[:limit]:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        exchange = str(item.get("exchange") or "HOSE").upper()
        result.append(
            {
                "symbol": symbol,
                "full_name": f"{exchange}:{symbol}",
                "description": str(item.get("name") or symbol),
                "exchange": exchange,
                "ticker": symbol,
                "type": "stock",
            }
        )
    return result


def get_chart_quote(symbol: str) -> dict[str, Any]:
    """Quote cho cập nhật candle hiện tại (subscribeBars khi bật realtime)."""
    symbol = _normalize_symbol(symbol)
    try:
        quote = vs.get_quote(symbol)
    except Exception:
        return {"valid": False, "symbol": symbol}

    if not quote or quote.get("valid") is False:
        return {"valid": False, "symbol": symbol}

    return {
        "valid": True,
        "symbol": symbol,
        "price": _f(quote.get("price")),
        "open": _f(quote.get("open")),
        "high": _f(quote.get("high")),
        "low": _f(quote.get("low")),
        "close": _f(quote.get("price")),
        "volume": int(_f(quote.get("volume"))),
        "change": _f(quote.get("change")),
        "percent_change": _f(quote.get("percent_change")),
        "as_of": quote.get("as_of") or "",
    }


def get_server_time() -> int:
    """getServerTime: thời gian máy chủ (unix epoch seconds)."""
    return int(time.time())


def get_chart_library_status() -> dict[str, Any]:
    """Kiểm tra thư viện TradingView Advanced Charts (charting_library) đã được
    đặt vào project chưa. Trả về true khi file charting_library.min.js tồn tại
    tại frontend/assets/tradingview/charting_library/."""
    from pathlib import Path

    lib = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "assets"
        / "tradingview"
        / "charting_library"
        / "charting_library.min.js"
    )
    return {"available": lib.exists()}
