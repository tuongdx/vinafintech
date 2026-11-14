from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
import functools
import json
import math
import re
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import settings


_SIMPLIZE_BASE_URL = "https://api2.simplize.vn"
_SIMPLIZE_WEB_BASE_URL = "https://simplize.vn"
_SIMPLIZE_TIMEOUT_SECONDS = 12
_VCIQ_URL = "https://iq.vietcap.com.vn/api/iq-insight-service"
# VCI iQ insight (KQKD/BCTC, cơ cấu cổ đông) — từ 2026-09 BẮT BUỘC có `Referer`,
# thiếu Referer trả HTTP 403 (chỉ Origin KHÔNG đủ) ⇒ mọi endpoint VCI iQ rỗng.
_VCIQ_HEADERS = {
    "accept": "application/json",
    "accept-language": "vi-VN,vi;q=0.9,en;q=0.8",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "referer": "https://iq.vietcap.com.vn/",
    "origin": "https://iq.vietcap.com.vn",
}
# VCI trading REST (price board + intraday) — dùng TRỰC TIẾP để bỏ phí import/khởi tạo thư viện vnstock.
_VCI_TRADING_URL = "https://trading.vietcap.com.vn/api"
_VCI_TRADING_TIMEOUT = 15
_VCI_TRADING_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7",
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn/",
}
_SYMBOL_DIRECTORY_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_directory.json"
_SYMBOL_SUMMARY_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_summaries.json"
_SYMBOL_SCREENER_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_screener.json"
_SYMBOL_DIVIDEND_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_dividends.json"
_SYMBOL_BUSINESS_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_business.json"
_EXCHANGE_CANONICAL = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM", "UPCOMM": "UPCOM"}


class SimpleTTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.time() > expires_at:
            # Giữ entry lại làm "stale" (xem get_stale) — phục vụ stale-while-revalidate.
            return None
        return value

    def get_stale(self, key: str) -> Any | None:
        """Trả dữ liệu cũ đã HẾT HẠN (nếu còn giữ) — dùng cho stale-while-revalidate."""
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        return value if time.time() > expires_at else None

    def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self._store if k.startswith(prefix)]:
            self._store.pop(key, None)

    def keys(self, prefix: str = "") -> list[str]:
        """Danh sách key đang giữ trong cache (kể cả entry đã hết hạn nhưng chưa bị xoá).

        Dùng để liệt kê những gì đã từng được nạp (VD scheduler 12h trưa chỉ dựng lại
        các cặp mã/danh mục báo cáo đã từng được xem).
        """
        return [k for k in list(self._store) if k.startswith(prefix)]

    def _lock_for(self, key: str) -> threading.Lock:
        """Lock riêng theo key — dùng cho single-flight (chống cache stampede)."""
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks.setdefault(key, threading.Lock())
            return lock

    def compute(self, key: str, ttl_seconds: int, fn) -> Any:
        """Single-flight: trả cache nếu còn hạn; nếu không chỉ gọi fn() ĐÚNG MỘT lần
        (các request trùng key khác chờ kết quả đó) rồi lưu lại."""
        cached = self.get(key)
        if cached is not None:
            return cached
        with self._lock_for(key):
            cached = self.get(key)
            if cached is not None:
                return cached
            value = fn()
            self.set(key, value, ttl_seconds)
            return value


cache = SimpleTTLCache()


# ---------------------------------------------------------------------------
# HTTP client dùng chung (keep-alive / connection pool)
# ---------------------------------------------------------------------------
# Trước đây mọi request dùng urllib.urlopen → MỞ KẾT NỐI MỚI mỗi lần (tốn TLS
# handshake ~50-150ms). Dùng chung 1 httpx.Client để TÁI SỬ DỤNG kết nối giữa
# các request / lần load → giảm latency rõ rệt cho trang gọi nhiều API.
# httpx đã là dependency (dùng trong ai_service).
_HTTP_DEFAULT_TIMEOUT = httpx.Timeout(_SIMPLIZE_TIMEOUT_SECONDS, connect=10.0)

_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()


def _get_http_client() -> httpx.Client:
    """Trả về client dùng chung (lazy singleton, thread-safe khi tạo)."""
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                _http_client = httpx.Client(
                    timeout=_HTTP_DEFAULT_TIMEOUT,
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                    headers={"user-agent": "VinaFintech/1.0", "accept": "application/json"},
                    follow_redirects=True,
                    trust_env=False,
                )
    return _http_client


def _singleflight(key_fn, ttl_seconds: int = 30, stale_while_revalidate: bool = True):
    """Decorator chống cache stampede + stale-while-revalidate (SWR).

    - Cache còn hạn → trả ngay.
    - Cache hết hạn nhưng có stale → trả stale NGAY + refresh nền (không chặn user).
    - Không có cache/stale → build đồng bộ (single-flight) rồi cache.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            cached = cache.get(key)
            if cached is not None:
                return cached
            stale = cache.get_stale(key)
            with cache._lock_for(key):
                cached = cache.get(key)
                if cached is not None:
                    return cached
                if stale is not None and stale_while_revalidate:
                    _start_background_refresh(key, fn, args, kwargs)
                    return stale
                value = fn(*args, **kwargs)
                if cache.get(key) is None and value:
                    cache.set(key, value, ttl_seconds)
                return value
        return wrapper
    return deco


_refreshing: set[str] = set()
_refreshing_guard = threading.Lock()


def _start_background_refresh(key: str, fn, args, kwargs) -> bool:
    """Refresh nền cho key — chỉ 1 luồng/key cùng lúc (chống thread stampede)."""
    with _refreshing_guard:
        if key in _refreshing:
            return False
        _refreshing.add(key)

    def _run() -> None:
        try:
            fn(*args, **kwargs)
        except Exception:
            pass
        finally:
            with _refreshing_guard:
                _refreshing.discard(key)

    threading.Thread(target=_run, daemon=True).start()
    return True


def _load_base_symbol_directory() -> list[dict[str, str]]:
    """Directory gốc (toàn bộ mã HOSE/HNX/UPCoM) từ file cache."""

    cache_key = "symbol-directory-base"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    file_cache = _read_symbol_directory_cache_file()
    if file_cache:
        cache.set(cache_key, file_cache, ttl_seconds=900)
        return file_cache

    fallback = [
        {"symbol": "FPT", "name": "Cong ty Co phan FPT", "exchange": "HOSE"},
        {"symbol": "VCB", "name": "Ngan hang TMCP Ngoai thuong Viet Nam", "exchange": "HOSE"},
        {"symbol": "TCB", "name": "Ngan hang TMCP Ky thuong Viet Nam", "exchange": "HOSE"},
    ]
    cache.set(cache_key, fallback, ttl_seconds=300)
    return fallback


def _load_custom_symbols() -> list[dict[str, str]]:
    """Mã cổ phiếu do quản trị viên thêm thủ công (VD: cổ phiếu IPO mới) — lưu trong DB."""

    cache_key = "custom-symbols"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows: list[dict[str, str]] = []
    try:
        from app.db import SessionLocal
        from app.models import StockSymbol

        db = SessionLocal()
        try:
            for item in db.query(StockSymbol).order_by(StockSymbol.symbol).all():
                symbol = str(item.symbol or "").strip().upper()
                if not symbol:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": str(item.name or symbol).strip(),
                        "exchange": _EXCHANGE_CANONICAL.get(
                            str(item.exchange or "").strip().upper(), ""
                        ),
                    }
                )
        finally:
            db.close()
    except Exception:
        rows = []

    cache.set(cache_key, rows, ttl_seconds=20)
    return rows


def _merge_custom_symbols(base: list[dict[str, str]]) -> list[dict[str, str]]:
    custom = _load_custom_symbols()
    if not custom:
        return base
    known = {item["symbol"] for item in base}
    return base + [item for item in custom if item["symbol"] not in known]


def _load_symbol_directory() -> list[dict[str, str]]:
    """Toàn bộ directory cổ phiếu: gốc + mã do quản trị viên thêm thủ công."""
    return _merge_custom_symbols(_load_base_symbol_directory())


def invalidate_symbol_directory_cache() -> None:
    """Xóa cache directory để mã cổ phiếu mới thêm/xóa hiện ra ngay lập tức."""
    cache.delete("symbol-directory-base")
    cache.delete("custom-symbols")
    cache.delete_prefix("search:")


def _last_quote(symbol: str) -> dict[str, Any]:
    """Bảng giá realtime của 1 mã (Simplize last-price) — cache ngắn 15s.

    Trả về dict row hoặc {} nếu mã chưa có giá. Dùng chung cho tra tên
    (lookup_symbol_name) và kiểm tra "đang giao dịch" (has_live_quote)."""
    symbol_upper = (symbol or "").strip().upper()
    if not symbol_upper:
        return {}
    key = f"last-quote:{symbol_upper}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        last_map = _simplize_last("stock", [symbol_upper])
        row = last_map.get(symbol_upper, {}) or {}
    except Exception:
        row = {}
    cache.set(key, row, ttl_seconds=15)
    return row


def get_live_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Giá hiện tại (realtime) cho NHIỀU mã cùng lúc — 1 request cho cả lô.

    Dùng cho các BẢNG có nhiều mã (vd trang Tín hiệu): nếu gọi
    ``/api/stocks/{symbol}/quote`` từng mã thì 50 dòng = 50 request; ở đây gom thành
    1-2 request (``_rank_live_quotes`` chia lô 120 mã) rồi cache 15s.

    Trả ``{MÃ: {"price", "change", "percent_change", "as_of"}}``; mã chưa có giá bị BỎ QUA
    (caller tự hiển thị "—"). Dùng CHUNG cache ``last-quote:{MÃ}`` với ``_last_quote`` nên
    không fetch trùng khi cả hai đường cùng hoạt động.
    """
    uniq = sorted({str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()})
    if not uniq:
        return {}

    rows: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for sym in uniq:
        cached_row = cache.get(f"last-quote:{sym}")
        if cached_row is None:
            missing.append(sym)
        elif cached_row:
            rows[sym] = cached_row

    if missing:
        try:
            fetched = _rank_live_quotes(missing)
        except Exception:
            fetched = {}
        for sym in missing:
            row = fetched.get(sym) or {}
            cache.set(f"last-quote:{sym}", row, ttl_seconds=15)
            if row:
                rows[sym] = row

    out: dict[str, dict[str, Any]] = {}
    for sym in uniq:
        row = rows.get(sym) or {}
        price = _to_float(row.get("price"))
        if price <= 0:
            continue
        out[sym] = {
            "price": round(price, 2),
            "change": round(_to_float(row.get("netChange")), 2),
            "percent_change": round(_to_float(row.get("pctChange")), 2),
            "as_of": _unix_to_iso(row.get("date", 0)),
        }
    return out


def lookup_symbol_name(symbol: str) -> str:
    """Tra cứu tên công ty từ Simplize cho mã cổ phiếu (trả về '' nếu không có).
    Ưu tiên tên từ bảng giá; nếu chưa có thì lấy tên từ dữ liệu screener."""
    try:
        last = _last_quote(symbol)
        name = str(last.get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass

    try:
        symbol_upper = symbol.upper()
        for row in _get_screener_dataset():
            if str(row.get("ticker") or "").upper() == symbol_upper:
                return str(row.get("companyNameVi") or "").strip()
    except Exception:
        pass
    return ""


def lookup_symbol_exchange(symbol: str) -> str:
    """Tra cứu sàn giao dịch chính thức (HOSE/HNX/UPCOM) từ dữ liệu screener
    của Simplize cho mã cổ phiếu (trả về '' nếu chưa có dữ liệu)."""
    try:
        symbol_upper = symbol.upper()
        for row in _get_screener_dataset():
            if str(row.get("ticker") or "").upper() == symbol_upper:
                exchange = str(row.get("stockExchange") or "").strip().upper()
                return _EXCHANGE_CANONICAL.get(exchange, exchange)
    except Exception:
        pass
    return ""


def is_listed_symbol(symbol: str) -> bool:
    """Mã cổ phiếu đã được niêm yết trên HOSE/HNX/UPCoM hay chưa
    (có trong dữ liệu screener của Simplize)."""
    try:
        symbol_upper = symbol.upper()
        for row in _get_screener_dataset():
            if str(row.get("ticker") or "").upper() == symbol_upper:
                return True
    except Exception:
        pass
    return False


def has_live_quote(symbol: str) -> bool:
    """Mã có dữ liệu giá realtime trên bảng giá (Simplize last-price) hay chưa.

    Dùng làm dấu hiệu "đã niêm yết & đang giao dịch" cho mã IPO rất mới:
    bảng giá thường có ngay từ phiên đầu, trong khi Simplize chỉ đưa mã vào
    screener sau đó (khi đã đủ hồ sơ công ty) — tránh chặn oan mã đã lên sàn
    (VD: LPS giao dịch trên HOSE nhưng chưa nằm trong screener).
    """
    try:
        return _to_float(_last_quote(symbol).get("price")) > 0
    except Exception:
        return False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _unix_to_iso(value: Any) -> str:
    ts = int(_to_float(value, 0))
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _percentile_scores(values: list) -> dict:
    """Chuyển list số (có thể chứa None) → điểm percentile 0..100.
    Winsorize 2%–98% để giảm ảnh hưởng outlier; 50 ≈ trung vị (dùng cho
    Growth/Valuation/Liquidity Score của bản đồ cơ hội thị trường)."""
    valid: list[tuple[int, float]] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN
            continue
        valid.append((i, f))
    if not valid:
        return {}
    arr = sorted(f for _, f in valid)
    n = len(arr)
    lo = arr[max(0, int(n * 0.02) - 1)]
    hi = arr[min(n - 1, int(n * 0.98) - 1)]
    span = hi - lo
    out: dict[int, float] = {}
    for i, f in valid:
        ff = max(lo, min(hi, f))
        out[i] = round(100.0 * (ff - lo) / span, 1) if span > 0 else 50.0
    return out


_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _normalize_exchange_names(exchanges: list[str] | None) -> list[str]:
    requested = exchanges or ["HOSE", "HNX", "UPCOM"]
    normalized: list[str] = []
    for exchange in requested:
        key = str(exchange).strip().upper()
        canonical = _EXCHANGE_CANONICAL.get(key)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized or ["HOSE", "HNX", "UPCOM"]


def _get_vn_now() -> datetime:
    return datetime.now(_VN_TZ)


def _encode_ticker_for_api(ticker: str) -> str:
    # Simplize expects caret-prefixed indexes as %255E... after URL decode.
    if ticker.startswith("^"):
        return ticker.replace("^", "%5E", 1)
    return ticker


def _http_get_json(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{_SIMPLIZE_BASE_URL}{path}{query}"
    response = _get_http_client().get(url)
    data = response.json()
    if not isinstance(data, dict):
        return {"status": 500, "message": "Invalid payload", "data": []}
    return data


def _http_post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{_SIMPLIZE_BASE_URL}{path}"
    response = _get_http_client().post(url, json=body)
    data = response.json()
    if not isinstance(data, dict):
        return {"status": 500, "message": "Invalid payload", "data": []}
    return data


def _simplize_last(type_name: str, tickers: list[str]) -> dict[str, dict[str, Any]]:
    if not tickers:
        return {}

    encoded_tickers = ",".join(_encode_ticker_for_api(ticker) for ticker in tickers)
    payload = _http_get_json("/api/historical/prices/last", {"type": type_name, "tickers": encoded_tickers})
    rows = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {}

    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker", "") or "")
        if ticker:
            mapped[ticker.upper()] = row
    return mapped


def _simplize_chart(ticker: str, type_name: str, period: str) -> list[dict[str, Any]]:
    payload = _http_get_json(
        "/api/historical/prices/chart",
        {"ticker": _encode_ticker_for_api(ticker), "period": period, "type": type_name},
    )
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []

    points: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, list) or len(item) < 6:
            continue
        points.append(
            {
                "time": _unix_to_iso(item[0]),
                "open": _to_float(item[1]),
                "high": _to_float(item[2]),
                "low": _to_float(item[3]),
                "close": _to_float(item[4]),
                "volume": int(_to_float(item[5])),
            }
        )
    return points


# ---------------------------------------------------------------------------
# KBS (KB Securities) — nguồn OHLCV lịch sử SÂU (~10 năm daily) + intraday 1H
# ---------------------------------------------------------------------------
# KBS endpoint: https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{sym}/data_{suffix}
#   "1D"→day, "1W"→week, "1M"→month, "1H"→60P ; params sdate/edate (DD-MM-YYYY).
# Đã kiểm chứng: daily ~2553 bar (2016→2026), weekly 529, monthly 124, 1H hoạt động.
_KBS_BASE_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment"
_KBS_INTERVAL_SUFFIX = {"1D": "day", "1W": "week", "1M": "month", "1H": "60P"}
_KBS_TIMEOUT_SECONDS = 15
_KBS_DATA_TTL = 60        # cache dữ liệu KBS ngắn (tránh spam khi lazy-load)
# KBS TỪ CHỐI mọi request span > ~1 năm ("Vượt quá range timeFrame") -> phải chia
# request thành từng lát ≤ giới hạn này rồi ghép để lấy được ~10 năm daily.
_KBS_MAX_RANGE_DAYS = 350
_KBS_EARLIEST_TTL = 21600  # cache mốc lịch sử xa nhất (6 giờ)


def _kbs_chart(symbol: str, interval: str, from_ts: int = 0, to_ts: int = 0) -> list[dict[str, Any]]:
    """Lấy OHLCV từ KB Securities (KBS) cho (symbol, interval) trong [from, to].

    Trả list [{time(unix), open, high, low, close, volume}] tăng dần theo time.
    - from_ts = 0 -> từ 01-01-2000.
    - to_ts   = 0 -> đến hôm nay.
    - Timestamp KBS theo giờ VN (Asia/Ho_Chi_Minh); daily 07:00 ICT = 00:00 UTC.

    LƯU Ý: KBS server CHỈ chấp nhận request span ≤ ~1 năm (trả lỗi "Vượt quá range
    timeFrame" nếu lâu hơn). Vì vậy khi yêu cầu dài hơn _KBS_MAX_RANGE_DAYS, hàm tự
    chia thành nhiều lát ≤ giới hạn rồi ghép lại (mỗi lát 1 request riêng, có cache)
    → lấy được TOÀN BỘ lịch sử (~10 năm daily) thay vì bị chặn như trước.
    """
    interval = str(interval or "").upper()
    suffix = _KBS_INTERVAL_SUFFIX.get(interval)
    if not suffix:
        return []
    symbol = str(symbol or "").upper()

    start_dt = datetime(2000, 1, 1, tzinfo=_VN_TZ) if not from_ts else datetime.fromtimestamp(int(from_ts), _VN_TZ)
    end_dt = datetime.now(_VN_TZ) if not to_ts else datetime.fromtimestamp(int(to_ts), _VN_TZ)
    if end_dt < start_dt:
        return []
    sdate = start_dt.strftime("%d-%m-%Y")
    edate = end_dt.strftime("%d-%m-%Y")

    cache_key = f"kbs-chart:{symbol}:{interval}:{sdate}:{edate}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Khoảng quá dài so với giới hạn 1 request của KBS -> chia lát rồi ghép.
    if (end_dt - start_dt).days > _KBS_MAX_RANGE_DAYS:
        merged: dict[int, dict[str, Any]] = {}
        cursor = start_dt
        while cursor < end_dt:
            chunk_end = min(end_dt, cursor + timedelta(days=_KBS_MAX_RANGE_DAYS))
            part = _kbs_chart(symbol, interval, int(cursor.timestamp()), int(chunk_end.timestamp()))
            for bar in part:
                merged[int(bar["time"])] = bar
            cursor = chunk_end
            if len(merged) > 9000:  # an toàn chống loop; ~2500 bar/năm là quá đủ
                break
        out = sorted(merged.values(), key=lambda b: b["time"])
        if out:
            cache.set(cache_key, out, ttl_seconds=_KBS_DATA_TTL)
        return out

    url = f"{_KBS_BASE_URL}/stocks/{symbol}/data_{suffix}?sdate={sdate}&edate={edate}"
    response = _get_http_client().get(
        url,
        headers={
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "referer": "https://kbbuddywts.kbsec.com.vn/",
            "accept": "application/json, text/plain, */*",
        },
        timeout=httpx.Timeout(_KBS_TIMEOUT_SECONDS, connect=10.0),
    )
    payload = response.json()

    rows = payload.get("data_" + suffix) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        t_raw = str(item.get("t", "") or "").strip()
        try:
            t = datetime.strptime(t_raw, "%Y-%m-%d %H:%M").replace(tzinfo=_VN_TZ)
        except Exception:
            continue
        o = _to_float(item.get("o"))
        h = _to_float(item.get("h"))
        l = _to_float(item.get("l"))
        c = _to_float(item.get("c"))
        v = int(_to_float(item.get("v"), 0))
        if o <= 0 or h <= 0 or l <= 0 or c <= 0 or h < l:
            continue
        out.append({"time": int(t.timestamp()), "open": o, "high": h, "low": l, "close": c, "volume": v})

    # KBS trả giảm dần (mới trước) -> sort tăng dần + dedupe theo time.
    out.sort(key=lambda b: b["time"])
    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for b in out:
        if b["time"] in seen:
            continue
        seen.add(b["time"])
        unique.append(b)

    if unique:
        cache.set(cache_key, unique, ttl_seconds=_KBS_DATA_TTL)
    return unique


def _kbs_earliest_time(symbol: str) -> int | None:
    """Mốc lịch sử xa nhất KBS có cho symbol (cache 6 giờ).

    Do KBS chỉ nhận range ≤ ~1 năm/request nên KHÔNG thể probe 2000→nay trong 1 lần
    (bị lỗi "Vượt quá range timeFrame"). Thay vào đó quét NGƯỢC từ hiện tại theo
    từng lát ~350 ngày cho tới khi gặp lát TRỐNG (trước ngày niêm yết) — bar đầu
    của lát cuối không rỗng chính là mốc sớm nhất. Dùng chung cho mọi timeframe
    (dữ liệu KBS của 1 mã bắt đầu cùng một mốc).
    """
    symbol = str(symbol or "").upper()
    key = f"kbs-earliest:{symbol}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    earliest: int | None = None
    cursor = datetime.now(_VN_TZ)
    for _ in range(40):  # 40 × 350 ngày ≈ 38 năm — đủ phủ 2000→nay
        start = cursor - timedelta(days=_KBS_MAX_RANGE_DAYS)
        rows = _kbs_chart(symbol, "1D", int(start.timestamp()), int(cursor.timestamp()))
        if not rows:
            break
        earliest = rows[0]["time"]
        if start.year <= 2000:
            break
        cursor = start

    if earliest:
        cache.set(key, earliest, ttl_seconds=_KBS_EARLIEST_TTL)
    return earliest


def _fallback_quote(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "name": symbol.upper(),
        "exchange": "",
        "price": 0,
        "change": 0,
        "percent_change": 0,
        "source": "fallback",
    }


def _read_symbol_directory_cache_file() -> list[dict[str, str]] | None:
    if not _SYMBOL_DIRECTORY_CACHE_PATH.exists():
        return None
    try:
        raw = json.loads(_SYMBOL_DIRECTORY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, list):
        return None

    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "") or "").upper().strip()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(item.get("name", symbol) or symbol).strip(),
                "exchange": _EXCHANGE_CANONICAL.get(str(item.get("exchange", "") or "").upper(), ""),
            }
        )
    return rows


_summary_file_cache: dict[str, dict[str, Any]] | None = None


def _ensure_cache_dir() -> None:
    _SYMBOL_SUMMARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _read_symbol_summary_cache_file() -> dict[str, dict[str, Any]] | None:
    global _summary_file_cache
    if _summary_file_cache is not None:
        return _summary_file_cache

    if not _SYMBOL_SUMMARY_CACHE_PATH.exists():
        _summary_file_cache = {}
        return _summary_file_cache

    try:
        raw = json.loads(_SYMBOL_SUMMARY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _summary_file_cache = {}
        return _summary_file_cache

    if not isinstance(raw, dict):
        _summary_file_cache = {}
        return _summary_file_cache

    cache_data: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        cache_data[key.upper()] = value

    _summary_file_cache = cache_data
    return _summary_file_cache


def _write_symbol_summary_cache_file() -> None:
    _ensure_cache_dir()
    cache_data = _read_symbol_summary_cache_file()
    if cache_data is None:
        cache_data = {}
    try:
        _SYMBOL_SUMMARY_CACHE_PATH.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _fetch_simplize_page_props(symbol: str) -> dict[str, Any]:
    """Fetch __NEXT_DATA__ pageProps from the Simplize stock page."""
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}

    try:
        url = f"{_SIMPLIZE_WEB_BASE_URL}/co-phieu/{quote(symbol_upper)}"
        response = _get_http_client().get(
            url,
            headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        html = response.text

        match = re.search(r"<script[^>]+id=['\"]__NEXT_DATA__['\"][^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
        if not match:
            match = re.search(r"window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;", html, re.DOTALL | re.IGNORECASE)
            if not match:
                return {}

        payload = json.loads(match.group(1))
        page_props: dict[str, Any] = {}
        if isinstance(payload, dict):
            props = payload.get("props")
            if isinstance(props, dict):
                page_props = props.get("pageProps", {}) if isinstance(props.get("pageProps"), dict) else {}
            elif isinstance(payload.get("pageProps"), dict):
                page_props = payload.get("pageProps", {})
        if not isinstance(page_props, dict):
            return {}
        return page_props
    except Exception:
        return {}


def _get_simplize_page_props_cached(symbol: str) -> dict[str, Any]:
    """Fetch __NEXT_DATA__ pageProps từ Simplize 1 lần DUY NHẤT rồi cache 24h
    (single-flight, không cache kết quả rỗng). Dividends / Tóm tắt doanh nghiệp
    / Quote fundamentals đều dùng chung bản này — khi bấm cả 2 nút popup chỉ tốn
    1 request mạng, không fetch lại toàn bộ trang HTML Simplize cho từng endpoint.
    """
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}
    key = f"simplize-page-props:{symbol_upper}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache._lock_for(key):
        cached = cache.get(key)
        if cached is not None:
            return cached
        props = _fetch_simplize_page_props(symbol_upper)
        if props:
            cache.set(key, props, ttl_seconds=86400)
        return props


def _fetch_simplize_symbol_summary(symbol: str) -> dict[str, Any]:
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}
    page_props = _get_simplize_page_props_cached(symbol_upper)
    summary = page_props.get("summary")
    if not isinstance(summary, dict):
        return {}
    summary["ticker"] = symbol_upper
    return summary


def _fetch_simplize_dividends(symbol: str) -> list[dict[str, Any]]:
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return []
    page_props = _get_simplize_page_props_cached(symbol_upper)
    rows = page_props.get("dividendPayment")
    if not isinstance(rows, list):
        return []

    result: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "symbol": symbol_upper,
                "date": str(item.get("date") or ""),
                "content": str(item.get("content") or ""),
            }
        )
    return result


def _get_symbol_summary(symbol: str) -> dict[str, Any]:
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}

    cache_key = f"symbol-summary:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    file_cache = _read_symbol_summary_cache_file()
    if file_cache is None:
        file_cache = {}
    if symbol_upper in file_cache:
        cache.set(cache_key, file_cache[symbol_upper], ttl_seconds=86400)
        return file_cache[symbol_upper]

    summary = _fetch_simplize_symbol_summary(symbol_upper)
    if summary:
        file_cache[symbol_upper] = summary
        cache.set(cache_key, summary, ttl_seconds=86400)
        _write_symbol_summary_cache_file()
    return summary


_dividend_file_cache: dict[str, list[dict[str, Any]]] | None = None


def _read_dividend_cache_file() -> dict[str, list[dict[str, Any]]] | None:
    global _dividend_file_cache
    if _dividend_file_cache is not None:
        return _dividend_file_cache

    if not _SYMBOL_DIVIDEND_CACHE_PATH.exists():
        _dividend_file_cache = {}
        return _dividend_file_cache

    try:
        raw = json.loads(_SYMBOL_DIVIDEND_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _dividend_file_cache = {}
        return _dividend_file_cache

    if not isinstance(raw, dict):
        _dividend_file_cache = {}
        return _dividend_file_cache

    cache_data: dict[str, list[dict[str, Any]]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, list):
            continue
        cache_data[key.upper()] = value
    _dividend_file_cache = cache_data
    return _dividend_file_cache


def _write_dividend_cache_file() -> None:
    _ensure_cache_dir()
    cache_data = _read_dividend_cache_file() or {}
    try:
        _SYMBOL_DIVIDEND_CACHE_PATH.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _to_iso_date(raw: str) -> str:
    """Chuẩn hóa ngày vnstock về 'YYYY-MM-DD' (nguồn VCI trả lẫn ISO và dd/mm/yyyy)."""
    text = str(raw or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%Y-%m-%d")
        except ValueError:
            return text
    return text


def _vnstock_event_date(row: Any, keys: tuple[str, ...]) -> str:
    """Lấy ngày đầu tiên có giá trị trong `keys` (vnstock events) → 'YYYY-MM-DD'."""
    for key in keys:
        value = row.get(key) if hasattr(row, "get") else None
        raw = _strip_html(value)
        if not raw or raw.lower() in ("nan", "nat", "none"):
            continue
        return _to_iso_date(raw)
    return ""


def _fetch_vnstock_dividends(symbol: str) -> list[dict[str, Any]]:
    """Fallback: lịch sử cổ tức / phát hành thêm từ vnstock (nguồn VCI events).

    LƯU Ý: `Reference().company(sym).events()` mặc định dùng source='kbs' và trả
    RỖNG với hầu hết mã Việt Nam — BẮT BUỘC truyền source='vci'.
    """
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return []
    try:
        from vnstock import Reference  # lazy import

        df = Reference().company(symbol_upper).events(source="vci")
    except Exception:
        return []
    if df is None or getattr(df, "empty", True):
        return []

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        code = _strip_html(row.get("event_code")).upper()
        category = _strip_html(row.get("category")).upper()
        if code not in ("DIV", "ISS") and category != "DIVIDEND":
            continue
        content = _strip_html(row.get("event_title_vi")) or _strip_html(row.get("event_name_vi"))
        if not content:
            continue
        date_s = _vnstock_event_date(
            row,
            ("exright_date", "record_date", "payout_date", "issue_date", "public_date", "display_date1"),
        )
        dedupe_key = (date_s, content)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append({"symbol": symbol_upper, "date": date_s, "content": content})

    # Mới nhất lên đầu; sự kiện thiếu ngày xuống cuối.
    result.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return result


def get_dividends(symbol: str) -> list[dict[str, Any]]:
    """Lịch sử trả cổ tức của cổ phiếu (Simplize dividendPayment, fallback vnstock VCI)."""
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return []

    cache_key = f"dividends:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    file_cache = _read_dividend_cache_file()
    if file_cache is None:
        file_cache = {}
    if symbol_upper in file_cache:
        cache.set(cache_key, file_cache[symbol_upper], ttl_seconds=86400)
        return file_cache[symbol_upper]

    rows = _fetch_simplize_dividends(symbol_upper)
    if rows:
        # Ghi thẳng vào global cache để _write_dividend_cache_file() thấy dữ liệu
        # (trước đây dùng `or {}` tạo dict mới → file cache không bao giờ được lưu)
        _read_dividend_cache_file()[symbol_upper] = rows
        cache.set(cache_key, rows, ttl_seconds=86400)
        _write_dividend_cache_file()
        return rows

    # Fallback: Simplize rỗng → lấy từ vnstock (VCI events). Chỉ cache RAM 24h,
    # KHÔNG ghi file cache để lần sau vẫn ưu tiên thử lại Simplize.
    rows = _fetch_vnstock_dividends(symbol_upper)
    if rows:
        cache.set(cache_key, rows, ttl_seconds=86400)
    return rows


def _strip_html(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_business_file_cache: dict[str, str] | None = None


def _read_business_cache_file() -> dict[str, str]:
    global _business_file_cache
    if _business_file_cache is not None:
        return _business_file_cache
    if not _SYMBOL_BUSINESS_CACHE_PATH.exists():
        _business_file_cache = {}
        return _business_file_cache
    try:
        raw = json.loads(_SYMBOL_BUSINESS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _business_file_cache = {}
        return _business_file_cache
    if not isinstance(raw, dict):
        _business_file_cache = {}
        return _business_file_cache
    _business_file_cache = {str(k).upper(): (v if isinstance(v, str) else "") for k, v in raw.items()}
    return _business_file_cache


def _write_business_cache_file() -> None:
    _ensure_cache_dir()
    cache_data = _read_business_cache_file() or {}
    try:
        _SYMBOL_BUSINESS_CACHE_PATH.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _fetch_vnstock_business_model(symbol: str) -> str:
    """Ngành nghề kinh doanh của doanh nghiệp từ vnstock company.info (cột business_model)."""
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return ""
    try:
        from vnstock import Reference  # lazy import

        df = Reference().company(symbol_upper).info()
        if df is not None and not df.empty:
            return _strip_html(df.iloc[0].get("business_model"))
    except Exception:
        return ""
    return ""


def get_business_line(symbol: str) -> str:
    """Ngành nghề kinh doanh (ưu tiên vnstock, có memory + file cache)."""
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return ""
    cache_key = f"business-line:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    file_cache = _read_business_cache_file()
    if symbol_upper in file_cache:
        cache.set(cache_key, file_cache[symbol_upper], ttl_seconds=86400)
        return file_cache[symbol_upper]
    business = _fetch_vnstock_business_model(symbol_upper)
    if business:
        file_cache[symbol_upper] = business
        cache.set(cache_key, business, ttl_seconds=86400)
        _write_business_cache_file()
    return business


def _safe_business_line(symbol: str, summary: dict[str, Any]) -> str:
    """Ngành nghề kinh doanh cho mọi mã: ưu tiên businessLine có sẵn trong
    summary Simplize (đã fetch cùng page_props nên KHÔNG tốn thêm request);
    chỉ gọi vnstock khi Simplize chưa có — tránh làm chậm popup tóm tắt
    doanh nghiệp (lazy import vnstock + gọi API rất lâu)."""
    simplize_line = _strip_html(summary.get("businessLine"))
    simplize_overall = _strip_html(summary.get("businessOverall"))
    if simplize_line and simplize_line != simplize_overall:
        return simplize_line
    line = get_business_line(symbol)
    if line:
        return line
    return ""


def _fmt_number(value: Any, unit: str = "") -> str:
    """12345 → '12.345 tỷ đồng' (bỏ giá trị <= 0 / không phải số)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    text = f"{n:,.0f}".replace(",", ".")
    return f"{text} {unit}".strip()


def _fetch_vnstock_company_info(symbol: str) -> dict[str, str]:
    """Hồ sơ doanh nghiệp từ vnstock company.info() — fallback khi Simplize rỗng.

    Bỏ free_float_percentage / free_float: vnstock 4.0.5 trả giá trị sai
    (lặp lại vốn hóa / mệnh giá) nên không hiển thị để tránh số vô nghĩa.
    """
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}
    try:
        from vnstock import Reference  # lazy import

        df = Reference().company(symbol_upper).info()
    except Exception:
        return {}
    if df is None or getattr(df, "empty", True):
        return {}
    row = df.iloc[0]

    def g(key: str) -> str:
        value = row.get(key) if hasattr(row, "get") else None
        text = _strip_html(value)
        return "" if text.lower() in ("nan", "nat", "none") else text

    as_of = g("as_of_date")
    return {
        "business_model": g("business_model"),
        "company_type": g("company_type"),
        "founded_date": g("founded_date"),
        "listing_date": g("listing_date"),
        "exchange": g("exchange"),
        "charter_capital": _fmt_number(row.get("charter_capital"), "tỷ đồng"),
        "number_of_employees": _fmt_number(row.get("number_of_employees"), "nhân sự"),
        "listed_volume": _fmt_number(row.get("listed_volume"), "triệu cổ phiếu"),
        "outstanding_shares": _fmt_number(row.get("outstanding_shares"), "cổ phiếu"),
        "ceo_name": g("ceo_name"),
        "tax_id": g("tax_id"),
        "auditor": g("auditor"),
        "address": g("address"),
        "phone": g("phone"),
        "email": g("email"),
        "website": g("website"),
        "as_of_date": as_of[:10] if len(as_of) >= 10 else as_of,
    }


def _vnstock_company_summary(symbol: str) -> dict[str, Any]:
    """Payload `company-summary` dựng từ vnstock khi Simplize không có dữ liệu."""
    info = _fetch_vnstock_company_info(symbol)
    if not info:
        return {}
    name = (lookup_symbol_name(symbol) or "").strip() or symbol
    overall_parts = [
        info.get("company_type", ""),
        f"Thành lập {info['founded_date']}" if info.get("founded_date") else "",
        f"Vốn điều lệ {info['charter_capital']}" if info.get("charter_capital") else "",
        info.get("number_of_employees", ""),
        (
            f"Niêm yết {info['exchange']}"
            + (f" ({info['listing_date']})" if info.get("listing_date") else "")
            if info.get("exchange")
            else ""
        ),
        f"Địa chỉ: {info['address']}" if info.get("address") else "",
    ]
    return {
        "symbol": symbol,
        "name_vi": name,
        "name_en": "",
        "industry": "",
        "sector": "",
        "website": info.get("website", ""),
        "main_service": "",
        "business_line": info.get("business_model", ""),
        "business_strategy": "",
        "business_risk": "",
        "business_overall": " · ".join(p for p in overall_parts if p),
        "exchange": info.get("exchange", ""),
        "analysis_updated": info.get("as_of_date", ""),
        "market_cap": 0.0,
        "roe": 0.0,
        "roa": 0.0,
        "dividend_yield": 0.0,
        "free_float": 0.0,
        "pe": 0.0,
        "pb": 0.0,
        "eps": 0.0,
        "book_value": 0.0,
        "image_url": "",
        # Bỏ business_model / as_of_date ở `profile` vì đã có ở business_line / analysis_updated
        # (tránh lặp dữ liệu dài trong payload tool của AI).
        "profile": {k: v for k, v in info.items() if v and k not in ("business_model", "as_of_date")},
        "source": "vnstock",
    }


def get_company_summary(symbol: str) -> dict[str, Any]:
    """Tóm tắt thông tin doanh nghiệp (Simplize summary, fallback vnstock company.info())."""
    symbol_upper = symbol.upper().strip()
    summary = _get_symbol_summary(symbol_upper)
    if not summary:
        # Fallback: Simplize rỗng → dựng hồ sơ tối thiểu từ vnstock (không ghi file
        # cache để lần sau vẫn ưu tiên thử lại Simplize).
        return _vnstock_company_summary(symbol_upper)

    return {
        "symbol": symbol_upper,
        "name_vi": str(summary.get("nameVi") or ""),
        "name_en": str(summary.get("nameEn") or ""),
        "industry": str(summary.get("industryActivity") or ""),
        "sector": str(summary.get("bcEconomicSectorName") or ""),
        "website": str(summary.get("website") or ""),
        "main_service": _strip_html(summary.get("mainService")),
        # Ngành nghề kinh doanh cho toàn bộ mã cổ phiếu (ưu tiên vnstock, không trùng Giới thiệu chung)
        "business_line": _safe_business_line(symbol_upper, summary),
        "business_strategy": _strip_html(summary.get("businessStrategy")),
        "business_risk": _strip_html(summary.get("businessRisk")),
        "business_overall": _strip_html(summary.get("businessOverall")),
        "exchange": str(summary.get("stockExchange") or ""),
        "analysis_updated": str(summary.get("analysisUpdated") or ""),
        "market_cap": _to_float(summary.get("marketCap")),
        "roe": _to_float(summary.get("roe")),
        "roa": _to_float(summary.get("roa")),
        "dividend_yield": _to_float(summary.get("dividendYieldCurrent")),
        "free_float": _to_float(summary.get("freeFloatRate")),
        "pe": _to_float(summary.get("peRatio")),
        "pb": _to_float(summary.get("pbRatio")),
        "eps": _to_float(summary.get("epsRatio")),
        "book_value": _to_float(summary.get("bookValue")),
        "image_url": str(summary.get("imageUrl") or ""),
    }


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


# ============================================================
# BỘ LỌC CỔ PHIẾU (Stock Screener)
# Data source: Simplize screener API (api2.simplize.vn/api/company/screener/filter).
# It returns the FULL universe of listed stocks with their ngành (economicSectorSlug),
# cơ bản (growth/performance/financialHealth/dividend points), định giá (valuationPoint)
# and rủi ro (overallRiskLevel) — the same data shown on the Simplize website filter.
# ============================================================

# Default rules: tất cả cổ phiếu niêm yết trên HOSE/HNX/UPCoM.
_SCREENER_RULES = json.dumps(
    [{"id": "stockExchange", "val": ["HOSE", "HNX", "UPCOM"], "op": "in"}],
    ensure_ascii=False,
)
_SCREENER_PAGE_SIZE = 200
_SCREENER_CACHE_TTL_SECONDS = 1800  # in-memory 30 phút
_SCREENER_FILE_MAX_AGE_SECONDS = 1 * 3600  # file cache 1 giờ (cập nhật theo phiên)

_screener_file_cache: list[dict[str, Any]] | None = None
_screener_file_cache_mtime: float = 0.0


# ---------------------------------------------------------------------------
# Bộ lọc "cổ phiếu giao dịch được": VỐN HOÁ LỚN + THANH KHOẢN đủ để nhà đầu tư giao
# dịch hằng ngày. Dùng CHUNG cho mọi block lấy mã từ toàn thị trường để không nơi nào
# lọt các mã siêu nhỏ/thanh khoản ảo (thường là UPCOM):
#   - Market Radar (trang chủ) · VinaFintech Market Radar (trang Thị trường)
#   - Khám phá theo chiến lược · Xếp hạng VinaFintech (trang Khám phá)
#   - "Cơ hội mới" (trang chủ) · universe quét của trang Tín hiệu
# Ngưỡng cấu hình trong settings: `stock_min_market_cap`, `stock_min_liquidity_ty`.
# ---------------------------------------------------------------------------
_TRADABLE_SYMBOLS_CACHE_KEY = "tradable-symbols"


def stock_liquidity_ty(item: dict[str, Any]) -> float:
    """Thanh khoản của mã (tỷ VND/phiên).

    Ưu tiên GTGD TRUNG BÌNH 10 phiên — vì `volume` là khối lượng phiên hiện tại (có thể
    mới chạy nửa phiên) nên suy ngược TB 10 phiên từ `volume_ratio` (sai số ~0.5% do
    `volume_ratio` được làm tròn 2 chữ số); mã quá ít giao dịch/thiếu dữ liệu thì dùng
    GTGD phiên hiện tại.
    """
    price = _to_float(item.get("price"))
    volume = _to_float(item.get("volume"))
    if price <= 0 or volume <= 0:
        return 0.0
    ratio = _to_float(item.get("volume_ratio"))
    avg_volume = volume / ratio if ratio > 0.01 else volume
    return avg_volume * price / 1e9


def is_tradable_stock(item: dict[str, Any]) -> bool:
    """Cổ phiếu VỐN HOÁ LỚN + THANH KHOẢN đủ để giao dịch hằng ngày?

    `item` là 1 dòng trong `get_stock_screener_page()["stocks"]`.
    """
    if _to_float(item.get("market_cap")) < settings.stock_min_market_cap:
        return False
    return stock_liquidity_ty(item) >= settings.stock_min_liquidity_ty


def liquid_symbols_20d(min_ty: float = 5.0) -> set[str]:
    """Tập mã có **GTGD TRUNG BÌNH 20 PHIÊN ≥ ``min_ty`` tỷ VND/phiên** (screener Simplize).

    Dùng cho bộ lọc thanh khoản của trang Tín hiệu (chỉ lấy cổ phiếu đủ thanh khoản để
    vào/ra lệnh được). Tính từ DÒNG THÔ screener (`volume20dAvg` × giá) — dataset đã được
    cache sẵn nên KHÔNG phát sinh request mới.

    - Thiếu ``volume20dAvg`` (mã mới niêm yết) → lần lượt dùng ``volume10dAvg`` rồi ``volume``.
    - ``min_ty`` ≤ 0 → trả ``set()`` (coi như TẮT lọc; caller tự bỏ qua khi tập rỗng).
    - Nguồn lỗi/rỗng → KHÔNG cache (lần sau thử lại) và trả ``set()`` để bên gọi không
      vô tình ẨN HẾT tín hiệu vì lỗi dữ liệu.
    """
    if min_ty <= 0:
        return set()
    key = f"liquid-symbols-20d:{min_ty}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    symbols: set[str] = set()
    try:
        for row in _get_screener_dataset() or []:
            symbol = str(row.get("ticker") or "").upper().strip()
            if not symbol:
                continue
            price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
            volume = (_to_float(row.get("volume20dAvg")) or _to_float(row.get("volume10dAvg"))
                      or _to_float(row.get("volume")))
            if price > 0 and volume > 0 and volume * price / 1e9 >= min_ty:
                symbols.add(symbol)
    except Exception:  # noqa: BLE001 — nguồn lỗi thì thà KHÔNG lọc còn hơn ẩn hết tín hiệu
        return set()

    if symbols:
        cache.set(key, symbols, ttl_seconds=600)
    return symbols


def _is_tradable_raw_row(row: dict[str, Any]) -> bool:
    """Như `is_tradable_stock` nhưng cho DÒNG THÔ của screener (`_get_screener_dataset`).

    Dòng thô dùng tên field khác (`marketCapVnd`, `priceClose*`, `volume10dAvg`) nên
    không dùng lại được `is_tradable_stock`.
    """
    if _to_float(row.get("marketCapVnd")) < settings.stock_min_market_cap:
        return False
    price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
    volume = _to_float(row.get("volume"))
    if price <= 0 or volume <= 0:
        return False
    volume10 = _to_float(row.get("volume10dAvg"))
    avg_volume = volume10 if volume10 > 0 else volume
    return avg_volume * price / 1e9 >= settings.stock_min_liquidity_ty


def tradable_symbols() -> set[str]:
    """Tập mã "giao dịch được" (từ screener, cache 120s) — cho nguồn dữ liệu chỉ có MÃ.

    Dùng cho bản đồ thị trường / các list radar (chỉ có symbol), tránh phải tính lại
    điều kiện vốn hoá + thanh khoản ở từng nơi.

    ⚠️ CHỈ được tính từ `_get_screener_dataset()` (dòng THÔ), TUYỆT ĐỐI không gọi
    `get_stock_screener_page()`: hàm đó gọi `get_market_dashboard()` cho `as_of`, mà
    `get_market_dashboard()` lại gọi hàm này ⇒ cùng 1 luồng xin lại lock
    "market-dashboard" ⇒ TREO TOÀN BỘ SERVER (lock thường, không re-entrant; đã gặp thật).
    """
    cached = cache.get(_TRADABLE_SYMBOLS_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        rows = _get_screener_dataset() or []
        symbols = {
            str(row.get("ticker") or "").upper()
            for row in rows
            if str(row.get("ticker") or "").strip()
            and str(row.get("stockExchange") or "").upper() in ("HOSE", "HNX", "UPCOM")
            and _is_tradable_raw_row(row)
        }
    except Exception:  # noqa: BLE001 — nguồn lỗi ⇒ không lọc được thì trả rỗng
        symbols = set()
    cache.set(_TRADABLE_SYMBOLS_CACHE_KEY, symbols, ttl_seconds=120)
    return symbols


def _screener_request(page: int, size: int) -> list[dict[str, Any]]:
    payload = _http_post_json(
        "/api/company/screener/filter",
        {
            "page": page,
            "size": size,
            "rules": _SCREENER_RULES,
            "sort": json.dumps({"id": "ticker", "direction": "ASC"}),
        },
    )
    data = payload.get("data") if isinstance(payload, dict) else []
    return [row for row in data if isinstance(row, dict)]


def _fetch_screener_dataset() -> list[dict[str, Any]]:
    """Lấy toàn bộ cổ phiếu (phân trang song song) từ Simplize screener API."""
    first_page = _screener_request(0, _SCREENER_PAGE_SIZE)
    rows = list(first_page)

    # Dựa vào số lượng thực tế để tiếp tục lấy thêm trang cho tới khi hết dữ liệu.
    more_pages = 12  # 12 * 200 = 2400 >= universe (~1622)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_screener_request, page, _SCREENER_PAGE_SIZE) for page in range(1, more_pages + 1)]
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception:
                continue

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique.append(row)
    return unique


def _read_screener_cache_file() -> list[dict[str, Any]]:
    global _screener_file_cache, _screener_file_cache_mtime
    if _screener_file_cache is not None:
        return _screener_file_cache
    if not _SYMBOL_SCREENER_CACHE_PATH.exists():
        _screener_file_cache = []
        return _screener_file_cache
    try:
        raw = json.loads(_SYMBOL_SCREENER_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _screener_file_cache = []
        return _screener_file_cache
    if not isinstance(raw, list):
        _screener_file_cache = []
        return _screener_file_cache
    _screener_file_cache = [row for row in raw if isinstance(row, dict)]
    try:
        _screener_file_cache_mtime = _SYMBOL_SCREENER_CACHE_PATH.stat().st_mtime
    except Exception:
        _screener_file_cache_mtime = 0.0
    return _screener_file_cache


def _write_screener_cache_file(rows: list[dict[str, Any]]) -> None:
    global _screener_file_cache, _screener_file_cache_mtime
    _ensure_cache_dir()
    try:
        _SYMBOL_SCREENER_CACHE_PATH.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        _screener_file_cache = rows
        _screener_file_cache_mtime = time.time()
    except Exception:
        pass


@_singleflight(lambda: "screener-dataset", _SCREENER_CACHE_TTL_SECONDS)
def _get_screener_dataset() -> list[dict[str, Any]]:
    """Trả về toàn bộ dữ liệu screener (memory cache -> file cache -> fetch từ Simplize)."""
    cached = cache.get("screener-dataset")
    if cached is not None:
        return cached

    file_rows = _read_screener_cache_file()
    file_age = (time.time() - _screener_file_cache_mtime) if _screener_file_cache_mtime else float("inf")
    if file_rows and file_age < _SCREENER_FILE_MAX_AGE_SECONDS:
        cache.set("screener-dataset", file_rows, ttl_seconds=_SCREENER_CACHE_TTL_SECONDS)
        return file_rows

    try:
        rows = _fetch_screener_dataset()
        if rows:
            _write_screener_cache_file(rows)
            cache.set("screener-dataset", rows, ttl_seconds=_SCREENER_CACHE_TTL_SECONDS)
            return rows
    except Exception:
        pass

    if file_rows:
        cache.set("screener-dataset", file_rows, ttl_seconds=_SCREENER_CACHE_TTL_SECONDS)
        return file_rows
    return []


# ---------------------------------------------------------------------------
# VŨ TRỤ CỔ PHIẾU KẾT HỢP (Simplize screener + Vietcap getAll)
# Vietcap /price/symbols/getAll nhận diện IPO mới SỚM hơn screener Simplize
# (VD: LPS đã có trên Vietcap nhưng chưa vào screener), nên dùng làm nguồn bổ
# sung cho tính năng "Quét & thêm cổ phiếu mới" ở trang quản trị.
# ---------------------------------------------------------------------------
_VCI_GETALL_URL = f"{_VCI_TRADING_URL}/price/symbols/getAll"
_VCI_UNIVERSE_TTL_SECONDS = 1800


def _fetch_vci_getall_rows() -> list[dict[str, Any]]:
    """Toàn bộ sản phẩm niêm yết từ Vietcap (stocks/bonds/CW/funds...) — raw."""
    try:
        response = _get_http_client().get(
            _VCI_GETALL_URL,
            headers=_VCI_TRADING_HEADERS,
            timeout=httpx.Timeout(30, connect=10.0),
        )
        data = response.json()
    except Exception:
        return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _get_vci_stock_universe() -> list[dict[str, str]]:
    """Cổ phiếu (STOCK) trên HOSE/HNX/UPCoM theo Vietcap — chuẩn hoá sàn/tên."""
    cache_key = "vci-stock-universe"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _fetch_vci_getall_rows():
        if str(item.get("type") or "").upper() != "STOCK":
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        exchange = _EXCHANGE_CANONICAL.get(str(item.get("board") or "").strip().upper(), "")
        if not symbol or exchange not in ("HOSE", "HNX", "UPCOM") or symbol in seen:
            continue
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": str(item.get("organName") or symbol).strip(),
                "exchange": exchange,
            }
        )
    rows.sort(key=lambda r: r["symbol"])
    cache.set(cache_key, rows, ttl_seconds=_VCI_UNIVERSE_TTL_SECONDS)
    return rows


def _get_combined_stock_universe() -> list[dict[str, str]]:
    """Vũ trụ cổ phiếu kết hợp: screener Simplize (nền, giàu hồ sơ công ty)
    + Vietcap getAll (bổ sung IPO mới chưa vào screener). Trùng mã → giữ
    dữ liệu screener (tên/sàn chuẩn hoá)."""
    screener: list[dict[str, str]] = []
    for row in _get_screener_dataset():
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("ticker") or "").strip().upper()
        if not symbol:
            continue
        exchange = _EXCHANGE_CANONICAL.get(str(row.get("stockExchange") or "").strip().upper(), "")
        screener.append(
            {
                "symbol": symbol,
                "name": str(row.get("companyNameVi") or symbol).strip(),
                "exchange": exchange,
            }
        )
    seen = {item["symbol"] for item in screener}
    merged = list(screener)
    for item in _get_vci_stock_universe():
        if item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        merged.append(item)
    merged.sort(key=lambda r: (r["exchange"], r["symbol"]))
    return merged


def _screener_matches_industry(row: dict[str, Any], industry: str | None) -> bool:
    if not industry:
        return True
    industry = str(industry).strip().lower()
    # Ngành khai báo thủ công (mã IPO mới / mã bị nguồn phân loại sai) được ưu tiên.
    override = _SECTOR_OVERRIDES.get(str(row.get("ticker") or "").upper()) or {}
    if industry == "ngan-hang":
        # Ngân hàng = ngành con "tài chính ngân hàng" trong nhóm Tài chính.
        slug = str(override.get("industry") or row.get("industrySlug") or "").lower()
        return slug == "tai-chinh-ngan-hang"
    sector = str(override.get("sector") or row.get("economicSectorSlug") or "").lower()
    return sector == industry


def _screener_quality_score(row: dict[str, Any]) -> float:
    return (
        _to_float(row.get("growthPoint"))
        + _to_float(row.get("passPerformancePoint"))
        + _to_float(row.get("financialHealthPoint"))
        + _to_float(row.get("dividendPoint"))
    )


def _screener_matches_basic(row: dict[str, Any], basic: str | None) -> bool:
    if not basic:
        return True
    score = _screener_quality_score(row)
    if basic == "rat-tot":
        return score >= 14
    if basic == "tot":
        return 10 <= score < 14
    if basic == "trung-binh":
        return 5 <= score < 10
    if basic == "chua-dat":
        return score < 5
    return True


def _screener_matches_valuation(row: dict[str, Any], valuation: str | None) -> bool:
    if not valuation:
        return True
    point = _to_float(row.get("valuationPoint"))
    if valuation == "du-bien-an-toan":
        return point >= 3
    if valuation == "chua-du-bien-an-toan":
        return point < 3
    return True


def _screener_matches_risk(row: dict[str, Any], risk: str | None) -> bool:
    if not risk:
        return True
    level = _normalize_text(row.get("overallRiskLevel"))
    if risk == "thap":
        return level == "low"
    if risk == "trung-binh":
        return level == "medium"
    if risk == "can-chu-y":
        return level in ("high", "extreme")
    return True


def _build_screener_stock_row(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("ticker") or "").upper()
    return {
        "symbol": symbol,
        "name": str(row.get("companyNameVi") or symbol),
        "exchange": str(row.get("stockExchange") or ""),
        "industry": row.get("industrySlug", ""),
        "industryName": row.get("industryName", ""),
        "sector": row.get("economicSectorSlug", ""),
        "sectorName": row.get("economicSectorName", ""),
        "overallRiskLevel": row.get("overallRiskLevel", ""),
        "valuationPoint": _to_float(row.get("valuationPoint")),
        "qualityScore": _screener_quality_score(row),
        "growthPoint": _to_float(row.get("growthPoint")),
        "performancePoint": _to_float(row.get("passPerformancePoint")),
        "financialHealthPoint": _to_float(row.get("financialHealthPoint")),
        "dividendPoint": _to_float(row.get("dividendPoint")),
        "peRatio": _to_float(row.get("peRatio")),
        "pbRatio": _to_float(row.get("pbRatio")),
        "source": "simplize-screener",
    }


# ---------------------------------------------------------------------------
# GÁN NGÀNH THỦ CÔNG CHO MÃ IPO MỚI (chưa được screener Simplize index)
# ---------------------------------------------------------------------------
# Mọi nhóm ngành trong app (trang "Cổ phiếu" › Nhóm ngành, "Phân bổ theo ngành" ở
# Quản lý tài sản, so sánh cùng ngành, AI Screener...) đều suy từ dữ liệu screener
# Simplize: economicSectorSlug/economicSectorName (nhóm ngành) và
# industrySlug/industryName (ngành con). Cổ phiếu mới IPO thường CHƯA có trong
# screener (nguồn index trễ) nên tạm thời không thuộc nhóm ngành nào.
#
# Khai báo ở _SECTOR_OVERRIDES để gán ngành NGAY cho các mã đó — không phải chờ
# nguồn cập nhật, không cần sửa DB. Giá trị khai báo được ƯU TIÊN hơn dữ liệu
# nguồn, nên cũng dùng được để sửa mã bị nguồn phân loại sai.
#
# Slug chuẩn của screener (economicSectorSlug):
#   cong-nghiep | nguyen-vat-lieu | hang-hoa-khong-thiet-yeu | hang-hoa-thiet-yeu
#   bat-dong-san | tien-ich | tai-chinh | nang-luong | cham-soc-suc-khoe | cong-nghe
# industrySlug hay dùng: chung-khoan-va-ngan-hang-dau-tu, tai-chinh-ngan-hang, ...
#
# Thêm 1 mã mới = thêm 1 dòng (mã viết HOA):
#   "XYZ": {
#       "sector": "tai-chinh", "sectorName": "Tài chính",
#       "industry": "chung-khoan-va-ngan-hang-dau-tu",
#       "industryName": "Chứng khoán và Ngân hàng đầu tư",
#   },
_SECTOR_OVERRIDES: dict[str, dict[str, str]] = {
    # --- Tài chính › Chứng khoán và Ngân hàng đầu tư ---
    "KAI": {
        "sector": "tai-chinh",
        "sectorName": "Tài chính",
        "industry": "chung-khoan-va-ngan-hang-dau-tu",
        "industryName": "Chứng khoán và Ngân hàng đầu tư",
    },
    "LPS": {
        "sector": "tai-chinh",
        "sectorName": "Tài chính",
        "industry": "chung-khoan-va-ngan-hang-dau-tu",
        "industryName": "Chứng khoán và Ngân hàng đầu tư",
    },
    # --- Tài chính › Tài chính ngân hàng ---
    "PCB": {
        "sector": "tai-chinh",
        "sectorName": "Tài chính",
        "industry": "tai-chinh-ngan-hang",
        "industryName": "Tài chính ngân hàng",
    },
    # --- Công nghiệp › Dịch vụ công nghiệp và Thương mại ---
    "V68": {
        "sector": "cong-nghiep",
        "sectorName": "Công nghiệp",
        "industry": "dich-vu-cong-nghiep-va-thuong-mai",
        "industryName": "Dịch vụ công nghiệp và Thương mại",
    },
}


def sector_override_map() -> dict[str, dict[str, str]]:
    """Toàn bộ khai báo gán ngành thủ công (mã → thông tin ngành)."""
    return {sym: dict(ov) for sym, ov in _SECTOR_OVERRIDES.items()}


def get_sector_override(symbol: str) -> dict[str, str]:
    """Thông tin ngành gán thủ công của 1 mã ({} nếu không khai báo)."""
    return dict(_SECTOR_OVERRIDES.get(str(symbol or "").strip().upper(), {}))


def _override_only_symbols(dataset: list[dict[str, Any]]) -> list[str]:
    """Các mã có khai báo ngành thủ công nhưng CHƯA xuất hiện trong screener."""
    present = {str(row.get("ticker") or "").upper() for row in dataset if isinstance(row, dict)}
    return [sym for sym in sorted(_SECTOR_OVERRIDES) if sym not in present]


def _override_sector_fields(symbol: str, row: dict[str, Any]) -> tuple[str, str, str, str]:
    """(sector, sectorName, industry, industryName) sau khi áp override lên 1 row."""
    override = _SECTOR_OVERRIDES.get(str(symbol or "").upper()) or {}
    return (
        str(override.get("sector") or row.get("economicSectorSlug") or "").lower(),
        str(override.get("sectorName") or row.get("economicSectorName") or "Khác"),
        str(override.get("industry") or row.get("industrySlug") or "").lower(),
        str(override.get("industryName") or row.get("industryName") or ""),
    )


def _override_pseudo_row(symbol: str) -> dict[str, Any]:
    """Row screener tối thiểu (mã/tên/sàn/ngành khai báo thủ công) cho mã chưa có
    trong dữ liệu nguồn — giúp mã xuất hiện đúng nhóm ngành / bộ lọc."""
    meta = _lookup_symbol_metadata(symbol)
    override = _SECTOR_OVERRIDES.get(symbol) or {}
    return {
        "ticker": symbol,
        "companyNameVi": str(meta.get("name") or symbol),
        "stockExchange": str(meta.get("exchange") or ""),
        "economicSectorSlug": str(override.get("sector") or ""),
        "economicSectorName": str(override.get("sectorName") or ""),
        "industrySlug": str(override.get("industry") or ""),
        "industryName": str(override.get("industryName") or ""),
    }


def _override_live_quote(symbol: str) -> dict[str, Any]:
    """Giá realtime best-effort cho mã gán ngành thủ công ({} nếu chưa có giá)."""
    try:
        return _last_quote(symbol) or {}
    except Exception:  # noqa: BLE001
        return {}


def _lookup_symbol_metadata(symbol: str) -> dict[str, str]:
    symbol_upper = symbol.upper()
    for item in _load_symbol_directory():
        if item.get("symbol") == symbol_upper:
            return item
    return {"symbol": symbol_upper, "name": symbol_upper, "exchange": ""}


def _is_known_symbol(symbol: str) -> bool:
    """Mã cổ phiếu có tồn tại trên HOSE/HNX/UPCoM không (kiểm tra trong symbol directory)."""
    symbol_upper = symbol.upper()
    for item in _load_symbol_directory():
        if item.get("symbol") == symbol_upper:
            return True
    return False


def _fetch_simplize_market_liquidity(exchange: str) -> dict[str, Any]:
    """GTGD (giá trị giao dịch) nội phiên theo sàn từ Simplize.

    Endpoint /api/historical/widget/market-liquidity là nguồn dữ liệu cho widget
    "Thanh khoản thị trường" trên trang Simplize. Mỗi bar là
    [timestamp_ms, GTGD_hôm_qua, GTGD_hôm_nay, ...] (cột 1 = phiên trước, cột 2 =
    phiên gần nhất). Đã kiểm chứng ngày 21/08/2026: GTGD HOSE hôm nay 18.356 tỷ VND
    nằm ở cột index 2, GTGD HNX hôm nay 1.119 tỷ VND cũng ở cột index 2.
    """
    payload = _http_get_json(
        "/api/historical/widget/market-liquidity",
        {"period": "1D", "stockExchange": exchange},
    )
    rows = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {"today": [], "yesterday": []}

    today: list[dict[str, Any]] = []
    yesterday: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        ts_ms = _to_float(row[0], 0)
        if ts_ms <= 0:
            continue
        yesterday_vnd = _to_float(row[1])
        today_vnd = _to_float(row[2])
        iso = datetime.fromtimestamp(ts_ms / 1000.0, _VN_TZ).isoformat()
        today.append({"time": iso, "close": round(today_vnd / 1e9, 2), "timestamp": ts_ms})
        yesterday.append({"time": iso, "close": round(yesterday_vnd / 1e9, 2), "timestamp": ts_ms})

    return {"today": today, "yesterday": yesterday}


def _get_simplize_market_liquidity(exchange: str) -> dict[str, Any]:
    key = f"market-liquidity:{exchange}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = _fetch_simplize_market_liquidity(exchange)
    cache.set(key, data, ttl_seconds=60)
    return data


@_singleflight(lambda exchange, period="5d": f"exchange-chart:{exchange}", 30)
def get_exchange_chart(exchange: str, period: str = "5d") -> dict[str, Any]:
    """Thanh khoản thị trường (GTGD) theo sàn HOSE/HNX/UPCOM.

    Trả về hai chuỗi nội phiên: "today" = GTGD phiên gần nhất (hoặc phiên đang
    giao dịch), "yesterday" = GTGD phiên trước đó. Đơn vị: tỷ VND.

    Quy tắc hiển thị theo thời gian:
    - 07:00–09:00 ngày giao dịch (T2–T6): chưa mở cửa -> hiển thị tại gốc (rỗng).
    - Các thời điểm khác (bao gồm cuối tuần / trước 7h T2): hiển thị phiên gần nhất
      đã kết thúc (ví dụ T6) làm "hôm nay" và phiên trước đó làm "hôm qua".
    """
    normalized_exchanges = _normalize_exchange_names([exchange])
    canonical = normalized_exchanges[0] if normalized_exchanges else "HOSE"

    data = _get_simplize_market_liquidity(canonical)
    today = data.get("today", [])
    yesterday = data.get("yesterday", [])

    now = _get_vn_now()
    if now.weekday() < 5 and 7 <= now.hour < 9:
        today = []
        yesterday = []

    # Khoảng thời gian phiên (09:00–15:00 theo giờ VN) để frontend căn trục X.
    session_start: str | None = None
    session_end: str | None = None
    reference = today[0] if today else (yesterday[0] if yesterday else None)
    if reference:
        ref_dt = datetime.fromtimestamp(_to_float(reference.get("timestamp")) / 1000.0, _VN_TZ)
        session_start = (
            datetime(ref_dt.year, ref_dt.month, ref_dt.day, 9, 0, 0, tzinfo=_VN_TZ).timestamp() * 1000
        )
        session_end = (
            datetime(ref_dt.year, ref_dt.month, ref_dt.day, 15, 0, 0, tzinfo=_VN_TZ).timestamp() * 1000
        )

    return {
        "exchange": canonical,
        "period": "1D",
        "unit": "ty_vnd",
        "today": today,
        "yesterday": yesterday,
        "session_start": session_start,
        "session_end": session_end,
        "as_of": today[-1].get("time", "") if today else "",
        "close_as_of": today[-1].get("close", 0) if today else 0,
    }


def _load_symbols_by_exchange(exchanges: list[str]) -> dict[str, list[str]]:
    rows = _load_symbol_directory()
    result = {exchange: [] for exchange in exchanges}
    for item in rows:
        symbol = item.get("symbol", "")
        exchange = item.get("exchange", "")
        if symbol and exchange in result:
            result[exchange].append(symbol)
    return result


def _build_market_item(
    *,
    key: str,
    symbol: str,
    label: str,
    value: float,
    change: float,
    percent_change: float,
    unit: str | None = None,
    as_of: str = "",
    source: str = "simplize",
) -> dict[str, Any]:
    return {
        "key": key,
        "symbol": symbol,
        "label": label,
        "value": round(value, 4),
        "change": round(change, 4),
        "percent_change": round(percent_change, 4),
        "unit": unit,
        "as_of": as_of,
        "source": source,
    }


def get_realtime_exchange_board(exchanges: list[str] | None = None) -> dict[str, Any]:
    normalized_exchanges = _normalize_exchange_names(exchanges)
    cache_key = f"realtime-board:{','.join(normalized_exchanges)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    symbols_by_exchange = _load_symbols_by_exchange(normalized_exchanges)
    rows: list[dict[str, Any]] = []

    for exchange in normalized_exchanges:
        symbols = symbols_by_exchange.get(exchange, [])
        for start in range(0, len(symbols), 120):
            chunk = symbols[start : start + 120]
            if not chunk:
                continue
            prices = _simplize_last("stock", chunk)
            for symbol in chunk:
                row = prices.get(symbol)
                if not row:
                    continue
                price = _to_float(row.get("price"))
                change = _to_float(row.get("netChange"))
                pct_change = _to_float(row.get("pctChange"))
                rows.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "price": price,
                        "matched_price": price,
                        "reference_price": price - change,
                        "open": 0.0,
                        "high": 0.0,
                        "low": 0.0,
                        "change": round(change, 4),
                        "percent_change": round(pct_change, 4),
                        "volume": 0,
                        "value": 0.0,
                        "bid_price_1": 0.0,
                        "bid_volume_1": 0,
                        "ask_price_1": 0.0,
                        "ask_volume_1": 0,
                        "trading_status": "",
                        "security_status": "",
                        "as_of": _unix_to_iso(row.get("date", 0)),
                        "source": "simplize-last",
                    }
                )

    result = {"exchanges": normalized_exchanges, "total_symbols": len(rows), "data": rows}
    cache.set(cache_key, result, ttl_seconds=10)
    return result


# Bộ 30 mã cổ phiếu trong rổ VN30 (danh sách tham chiếu chuẩn).
_VN30_TICKERS = [
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSB", "MWG", "NVL", "PLX", "PNJ", "POW", "SAB", "SHB", "SSB",
    "SSI", "STB", "TCB", "TPB", "VCB", "VHM", "VIC", "VJC", "VPB", "VNM",
]


def _get_watchlist_sparklines() -> dict[str, list[dict[str, Any]]]:
    """Chuỗi giá 30 ngày (sampling ~15 điểm) cho sparkline của từng mã VN30."""
    key = "watchlist-sparklines"
    cached = cache.get(key)
    if cached is not None:
        return cached
    sparklines: dict[str, list[dict[str, Any]]] = {}

    def load(ticker: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            bars = get_history(ticker, period="1m")
        except Exception:
            bars = []
        if not bars:
            return ticker, []
        step = max(1, len(bars) // 15)
        sampled = bars[::step][:15]
        return ticker, [{"time": str(b.get("time", "")), "close": _to_float(b.get("close", 0))} for b in sampled]

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(load, ticker): ticker for ticker in _VN30_TICKERS}
        for future in as_completed(futures):
            ticker, points = future.result()
            sparklines[ticker] = points
    cache.set(key, sparklines, ttl_seconds=3600)
    return sparklines


@_singleflight(lambda: "watchlist", 30)
def get_watchlist() -> list[dict[str, Any]]:
    """Danh sách cổ phiếu trong VN30: 30 mã VN30 với dữ liệu thực tế từ Simplize.

    DÙNG CHUNG MỘT NGUỒN với các block khác của trang "Thị trường": snapshot screener
    Simplize (bản đồ thị trường / độ rộng / radar / top tăng-giảm đều lấy từ đây) —
    `priceCloseIntraDay` (giá), `pricePctChgIntraDay` (%HN), `priceReferrance` (tham chiếu),
    `peRatio` / `pbRatio` (định giá), `pricePctChg7d`, `volume`.
    KHÔNG trộn thêm quote realtime `/prices/last` nữa: nguồn đó có TTL 30s nên cùng một mã
    sẽ lệch giá/% so với các block dùng screener (TTL 30 phút) trong cùng một phiên.
    Sparkline 30D vẫn lấy từ lịch sử giá thực tế.
    """
    dataset = _get_screener_dataset()
    screener = {str(row.get("ticker") or "").upper(): row for row in dataset}

    sparklines = _get_watchlist_sparklines()

    rows: list[dict[str, Any]] = []
    for ticker in _VN30_TICKERS:
        row = screener.get(ticker, {})
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        reference = _to_float(row.get("priceReferrance"))
        pct = _to_float(row.get("pricePctChgIntraDay"))
        # Thay đổi tuyệt đối (đồng) — suy từ chính dữ liệu screener để không lệch với %HN.
        if reference > 0 and price > 0:
            change = price - reference
        elif price > 0 and pct > -100:
            change = price - price / (1 + pct / 100.0)
        else:
            change = 0.0
        rows.append(
            {
                "symbol": ticker,
                "name": str(row.get("companyNameVi") or ticker),
                "exchange": str(row.get("stockExchange") or ""),
                "price": round(price, 2),
                "change": round(change, 2),
                "percent_change": round(pct, 2),
                "change_7d": round(_to_float(row.get("pricePctChg7d")), 2),
                "volume": int(_to_float(row.get("volume"))),
                "pe": round(_to_float(row.get("peRatio")), 2),
                "pb": round(_to_float(row.get("pbRatio")), 2),
                "sparkline": sparklines.get(ticker, []),
                "source": "simplize-screener",
            }
        )
    return rows


# Giới hạn biên độ giao dịch theo sàn (để xác định trần/sàn trên bản đồ thị trường).
_EXCHANGE_LIMIT_PCT = {"HOSE": 7.0, "HNX": 10.0, "UPCOM": 15.0}


def _get_tick_size(exchange: str, price: float) -> float:
    """Bước giá (tick) theo quy định hiện hành của từng sàn.

    - HOSE: 10đ (thị giá < 10.000), 50đ (10.000 – 49.950), 100đ (≥ 50.000).
    - HNX & UPCoM: 100đ cho mọi mức giá.
    """
    if exchange == "HOSE":
        if price < 10000:
            return 10.0
        if price < 50000:
            return 50.0
        return 100.0
    return 100.0


def _compute_ceiling_floor(exchange: str, reference: float) -> tuple[float, float]:
    """Tính giá trần/sàn thực tế theo công thức chuẩn của 3 sàn.

    - Giá trần lý thuyết = tham chiếu × (1 + biên độ) → làm tròn XUỐNG bội của bước giá.
    - Giá sàn lý thuyết  = tham chiếu × (1 − biên độ) → làm tròn LÊN bội của bước giá.
    - Nếu giá sàn ≤ 0 → mặc định bằng bước giá.
    """
    limit = _EXCHANGE_LIMIT_PCT[exchange] / 100.0
    tick = _get_tick_size(exchange, reference)
    ceiling = math.floor(reference * (1 + limit) / tick) * tick
    floor = math.ceil(reference * (1 - limit) / tick) * tick
    if floor <= 0:
        floor = tick
    return ceiling, floor


@_singleflight(lambda: "market-map", 60)
def get_market_map() -> dict[str, Any]:
    """Bản đồ thị trường (treemap): toàn bộ cổ phiếu có giao dịch phiên gần nhất.

    - Mỗi ô là một cổ phiếu; diện tích ô TỶ LỆ với KHỐI LƯỢNG giao dịch.
    - Màu theo trạng thái phiên: trần (tím) / tăng (xanh) / đứng (vàng) / giảm (đỏ) / sàn (xanh lơ).
    - Trần/sàn tính theo công thức chuẩn (làm tròn theo bước giá), không so % thô.
    - Bao phủ cả 3 sàn HOSE, HNX, UPCOM, mọi ngành nghề.
    Dữ liệu lấy từ screener Simplize (volume = khối lượng, priceReferrance = giá tham chiếu).
    """
    dataset = _get_screener_dataset()

    cached = cache.get("market-map")
    if cached is not None:
        return cached

    cells: list[dict[str, Any]] = []
    for row in dataset:
        symbol = str(row.get("ticker") or "").upper()
        exchange = str(row.get("stockExchange") or "").upper()
        if not symbol or exchange not in _EXCHANGE_LIMIT_PCT:
            continue
        volume = _to_float(row.get("volume"))
        if volume <= 0:
            continue  # chỉ cổ phiếu có giao dịch trong phiên gần nhất
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        reference = _to_float(row.get("priceReferrance"))
        pct = _to_float(row.get("pricePctChgIntraDay"))
        # Trạng thái theo công thức trần/sàn chuẩn (làm tròn theo bước giá).
        # Nếu thiếu giá tham chiếu/giá → fallback theo % biến động.
        if reference and reference > 0 and price and price > 0:
            ceiling, floor = _compute_ceiling_floor(exchange, reference)
            if price >= ceiling - 0.01:
                status = "ceiling"
            elif price <= floor + 0.01:
                status = "floor"
            elif price > reference:
                status = "up"
            elif price < reference:
                status = "down"
            else:
                status = "flat"
        else:
            limit = _EXCHANGE_LIMIT_PCT[exchange]
            if pct >= limit:
                status = "ceiling"
            elif pct <= -limit:
                status = "floor"
            elif pct > 0:
                status = "up"
            elif pct < 0:
                status = "down"
            else:
                status = "flat"
        cells.append(
            {
                "symbol": symbol,
                "name": str(row.get("companyNameVi") or symbol),
                "exchange": exchange,
                "price": price,
                "percent_change": round(pct, 2),
                "volume": int(volume),
                "status": status,
            }
        )

    cells.sort(key=lambda c: c["volume"], reverse=True)
    payload = {"count": len(cells), "cells": cells}
    cache.set("market-map", payload, ttl_seconds=60)
    return payload


# Bản đồ kỳ hạn -> field screener cho giao dịch khối ngoại.
_FOREIGN_PERIOD_FIELD = {
    "1D": "foreignBuyingValue1d",
    "1W": "foreignBuyingValue7d",
    "1M": "foreignBuyingValue30d",
    "3M": "foreignBuyingValue90d",
    "1Y": None,
    "YTD": None,
}


def get_foreign_trading(trading_type: str = "foreign", period: str = "1D") -> dict[str, Any]:
    """Giao dịch khối ngoại / tự doanh.

    - foreign: dữ liệu THỰC TẾ từ screener Simplize — toàn bộ cổ phiếu có giao dịch
      khối ngoại theo kỳ hạn (foreignBuyingValue1d/7d/30d/90d = giá trị mua/bán ròng VND),
      cập nhật theo phiên (realtime trong phiên, kết phiên là số liệu cả ngày).
      Các kỳ hạn 1Y / YTD chưa có field tương ứng trên screener.
    - proprietary: CHƯA có nguồn dữ liệu tự doanh ổn định -> trả về trạng thái không có dữ liệu.
    """
    trading_type = (trading_type or "foreign").strip().lower()
    period = (period or "1D").strip().upper()
    if trading_type != "foreign":
        return {
            "type": "proprietary",
            "period": period,
            "available": False,
            "message": "Chưa có nguồn dữ liệu tự doanh",
            "totals": {"buy": 0.0, "sell": 0.0, "net": 0.0},
            "buy_list": [],
            "sell_list": [],
            "updated_at": _get_vn_now().isoformat(),
        }

    field = _FOREIGN_PERIOD_FIELD.get(period)
    if not field:
        return {
            "type": "foreign",
            "period": period,
            "available": False,
            "message": f"Chưa có dữ liệu cho kỳ hạn {period}",
            "totals": {"buy": 0.0, "sell": 0.0, "net": 0.0},
            "buy_list": [],
            "sell_list": [],
            "updated_at": _get_vn_now().isoformat(),
        }

    key = f"foreign-trading-{period}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        payload = _http_post_json(
            "/api/company/screener/filter",
            {
                "page": 0,
                "size": 2000,
                "rules": _SCREENER_RULES,
                "sort": json.dumps({"id": field, "direction": "DESC"}),
            },
        )
        rows = payload.get("data") if isinstance(payload, dict) else []
    except Exception:
        rows = []

    buy_list: list[dict[str, Any]] = []
    sell_list: list[dict[str, Any]] = []
    total_buy = 0.0
    total_sell = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("ticker") or "").upper()
        net = _to_float(row.get(field))
        if not symbol or net == 0:
            continue
        item = {
            "symbol": symbol,
            "name": str(row.get("companyNameVi") or symbol),
            "exchange": str(row.get("stockExchange") or ""),
            "net_value": round(net, 2),
            "price": round(_to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose")), 2),
            "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
        }
        if net > 0:
            buy_list.append(item)
            total_buy += net
        else:
            sell_list.append(item)
            total_sell += net

    # API trả về theo DESC (âm nhất ở cuối) -> đảo lại để bán ròng nhiều nhất lên đầu.
    sell_list.reverse()

    result: dict[str, Any] = {
        "type": "foreign",
        "period": period,
        "available": True,
        "totals": {
            "buy": round(total_buy, 2),
            "sell": round(total_sell, 2),
            "net": round(total_buy + total_sell, 2),
        },
        "buy_list": buy_list,
        "sell_list": sell_list,
        "updated_at": _get_vn_now().isoformat(),
    }
    cache.set(key, result, ttl_seconds=90)
    return result


# Bản đồ field peak/trough theo kỳ hạn cho tab "Vượt đỉnh" / "Phá đáy".
_TOP_PERIOD_PEAK = {
    "1w": "peak1week",
    "1M": "peak4week",
    "1y": "peak1year",
    "3y": "peak3year",
    "5y": "peak5year",
    "max": "peakAllTime",
}
_TOP_PERIOD_TROUGH = {
    "1w": "trough1week",
    "1M": "trough4week",
    "1y": "trough1year",
    "3y": "trough3year",
    "5y": "trough5year",
    "max": "troughAllTime",
}


def _get_today_breakout() -> list[dict[str, Any]]:
    """Top 10 cổ phiếu đột biến HÔM NAY: volume trong ngày / volume TB 10 phiên.

    Volume lấy từ screener dataset của Simplize (phản ánh phiên hiện tại, làm mới
    định kỳ trong & sau phiên giao dịch). Cache ngắn để số liệu luôn mới.
    """
    key = "top-breakout-live"
    cached = cache.get(key)
    if cached is not None:
        return cached

    dataset = _get_screener_dataset()
    rows: list[dict[str, Any]] = []
    for row in dataset:
        symbol = str(row.get("ticker") or "").upper()
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        volume = _to_float(row.get("volume"))
        avg_volume = _to_float(row.get("volume10dAvg"))
        if not symbol or volume <= 0 or avg_volume < 100000:
            continue
        ratio = volume / avg_volume
        if ratio < 1.0:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(row.get("companyNameVi") or symbol),
                "exchange": str(row.get("stockExchange") or ""),
                "price": round(price, 2),
                "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
                "volume": int(volume),
                "avg_volume": int(avg_volume),
            }
        )

    # Sắp xếp theo khối lượng giao dịch LỚN NHẤT trong ngày.
    rows.sort(key=lambda item: item["volume"], reverse=True)
    result = rows[:30]
    cache.set(key, result, ttl_seconds=60)
    return result


def _fetch_foreign_sorted(direction: str = "DESC") -> list[dict[str, Any]]:
    """Top 10 cổ phiếu khối ngoại mua (DESC) / bán (ASC) nhiều nhất.

    Gọi trực tiếp screener API với sort theo foreignBuyingValue1d — số liệu realtime
    theo ngày (trong phiên cập nhật liên tục, sau phiên là số liệu kết phiên).
    """
    payload = _http_post_json(
        "/api/company/screener/filter",
        {
            "page": 0,
            "size": 30,
            "rules": _SCREENER_RULES,
            "sort": json.dumps({"id": "foreignBuyingValue1d", "direction": direction}),
        },
    )
    rows = payload.get("data") if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("ticker") or "").upper()
        if not symbol:
            continue
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        result.append(
            {
                "symbol": symbol,
                "name": str(row.get("companyNameVi") or symbol),
                "exchange": str(row.get("stockExchange") or ""),
                "price": round(price, 2),
                "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
                "volume": int(_to_float(row.get("volume"))),
                "avg_volume": int(_to_float(row.get("volume10dAvg"))),
                "foreign_buy_value": round(_to_float(row.get("foreignBuyingValue1d")), 2),
            }
        )
    return result


def _foreign_from_dataset(direction: str) -> list[dict[str, Any]]:
    """Top 10 cổ phiếu khối ngoại mua (DESC) / bán (ASC) từ screener dataset."""
    dataset = _get_screener_dataset()
    pool: list[dict[str, Any]] = []
    for row in dataset:
        symbol = str(row.get("ticker") or "").upper()
        fbv = _to_float(row.get("foreignBuyingValue1d"))
        if not symbol or fbv == 0:
            continue
        if direction == "DESC" and fbv <= 0:
            continue
        if direction == "ASC" and fbv >= 0:
            continue
        pool.append(
            {
                "symbol": symbol,
                "name": str(row.get("companyNameVi") or symbol),
                "exchange": str(row.get("stockExchange") or ""),
                "price": round(_to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose")), 2),
                "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
                "volume": int(_to_float(row.get("volume"))),
                "avg_volume": int(_to_float(row.get("volume10dAvg"))),
                "foreign_buy_value": round(fbv, 2),
            }
        )
    pool.sort(key=lambda item: item["foreign_buy_value"], reverse=(direction == "DESC"))
    return pool[:30]


def _get_today_foreign(direction: str = "DESC") -> list[dict[str, Any]]:
    """Top 10 cổ phiếu khối ngoại mua/bán nhiều nhất — cache ngắn 60s cập nhật liên tục.

    - Mua (DESC): screener API sort trực tiếp (realtime theo ngày, cập nhật trong phiên).
    - Bán (ASC): screener API sort ASC đang lỗi -> tính từ screener dataset (fbv < 0).
    """
    key = f"top-foreign-{direction}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    rows: list[dict[str, Any]] = []
    if direction == "DESC":
        try:
            rows = _fetch_foreign_sorted("DESC")
        except Exception:
            rows = []
    if not rows:
        rows = _foreign_from_dataset(direction)

    cache.set(key, rows, ttl_seconds=60)
    return rows


def get_top_stocks(tab: str = "breakout", period: str = "1w") -> list[dict[str, Any]]:
    """Top cổ phiếu theo tab: đột biến / khối ngoại mua / khối ngoại bán / vượt đỉnh / phá đáy.

    - Đột biến: volume realtime hôm nay so với TB 10 phiên (10 mã).
    - Khối ngoại mua/bán: gọi screener API sort theo foreignBuyingValue1d (realtime, 10 mã).
    - Vượt đỉnh/Phá đáy: tính từ screener dataset (peak*/trough*).
    """
    tab = (tab or "breakout").strip().lower()
    period = (period or "1w").strip().lower()

    if tab == "breakout":
        return _get_today_breakout()
    if tab == "foreign-buy":
        return _get_today_foreign("DESC")
    if tab == "foreign-sell":
        return _get_today_foreign("ASC")

    dataset = _get_screener_dataset()

    cache_key = f"top-{tab}-{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Vượt đỉnh / Phá đáy: chọn ứng viên từ screener dataset (peak/trough thực tế theo kỳ hạn),
    # sau đó lấy GIÁ REALTIME để kiểm tra & hiển thị chính xác.
    is_high = tab == "break-high"
    ext_field = _TOP_PERIOD_PEAK.get(period, "peak1week") if is_high else _TOP_PERIOD_TROUGH.get(period, "trough1week")

    picks: list[tuple[str, float, float, dict[str, Any]]] = []
    for row in dataset:
        symbol = str(row.get("ticker") or "").upper()
        avg_volume = _to_float(row.get("volume10dAvg"))
        ext = _to_float(row.get(ext_field))
        if not symbol or ext <= 0 or avg_volume < 50000:
            continue
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        if price <= 0:
            continue
        if is_high and price >= ext * 0.98:
            picks.append((symbol, price / ext, ext, row))
        elif not is_high and price <= ext * 1.02:
            picks.append((symbol, ext / price, ext, row))

    rows: list[dict[str, Any]] = []
    if picks:
        tickers = [item[0] for item in picks]
        try:
            live = _simplize_last("stock", tickers)
        except Exception:
            live = {}
        for symbol, score, ext, row in picks:
            quote = live.get(symbol, {})
            price = _to_float(quote.get("price")) or (_to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose")))
            if price <= 0:
                continue
            if is_high and price < ext * 0.98:
                continue
            if not is_high and price > ext * 1.02:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(row.get("companyNameVi") or symbol),
                    "exchange": str(row.get("stockExchange") or ""),
                    "price": round(price, 2),
                    "percent_change": round(_to_float(quote.get("pctChange")) or _to_float(row.get("pricePctChgIntraDay")), 2),
                    "volume": int(_to_float(row.get("volume"))),
                    "avg_volume": int(avg_volume),
                    "score": score,
                }
            )

    rows.sort(key=lambda item: item["score"], reverse=True)
    for item in rows:
        item.pop("score", None)
    result = rows[:30]
    cache.set(cache_key, result, ttl_seconds=60)
    return result


def _symbol_sector_index() -> dict[str, dict[str, str]]:
    """Bản đồ mã → {sector, sectorName, industry, industryName} (slug ngành) từ dataset
    screener — cache 30 phút. Dùng để dựng URL đẹp cấp ngành
    `/co-phieu/nganh/{sector}/{industry}/{MÃ}` NGAY từ kết quả tìm kiếm header."""
    cached = cache.get("symbol-sector-index")
    if cached is not None:
        return cached
    index: dict[str, dict[str, str]] = {}
    try:
        for row in _get_screener_dataset():
            symbol = str(row.get("ticker") or "").strip().upper()
            if not symbol or symbol in index:
                continue
            sector, sector_name, industry, industry_name = _override_sector_fields(symbol, row)
            if not (sector and industry):
                continue
            index[symbol] = {
                "sector": sector,
                "sectorName": sector_name,
                "industry": industry,
                "industryName": industry_name,
            }
    except Exception:  # noqa: BLE001 — thiếu dữ liệu ngành vẫn phải tìm kiếm được bình thường
        index = {}
    cache.set("symbol-sector-index", index, ttl_seconds=1800)
    return index


def search_stocks(query: str) -> list[dict[str, Any]]:
    key = f"search:{query.lower()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    query_upper = query.strip().upper()
    directory = _load_symbol_directory()
    filtered = [item for item in directory if query_upper in item["symbol"] or query_upper in item["name"].upper()]
    filtered.sort(
        key=lambda item: (
            0 if item["symbol"] == query_upper else 1,
            0 if item["symbol"].startswith(query_upper) else 1,
            item["symbol"],
        )
    )
    # Kèm slug ngành (nếu có) để client dựng được URL đẹp cấp ngành trực tiếp.
    # LƯU Ý: copy dict — `directory` là cache dùng chung, KHÔNG được sửa item gốc.
    sector_index = _symbol_sector_index()
    result: list[dict[str, Any]] = []
    for item in filtered[:20]:
        row = dict(item)
        info = sector_index.get(str(row.get("symbol") or "").upper())
        if info:
            row.update(info)
        result.append(row)
    cache.set(key, result, ttl_seconds=300)
    return result


def _map_period(period: str) -> str:
    mapped = {
        "1d": "1m_d",
        "5d": "1m_d",
        "1m": "1m_d",
        "3m": "3m",
        "6m": "6m",
        "1y": "1y",
        "5y": "5y",
        "max": "5y",
    }
    return mapped.get(period.lower(), "1y")


def get_history(symbol: str, interval: str = "1d", period: str = "1y") -> list[dict[str, Any]]:
    key = f"history:{symbol.upper()}:{interval}:{period}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        values = _simplize_chart(symbol.upper(), "stock", _map_period(period))
        if values:
            cache.set(key, values, ttl_seconds=180)
            return values
    except Exception:
        pass

    fallback = [
        {"time": "2026-01-01T00:00:00+00:00", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000000},
        {"time": "2026-01-02T00:00:00+00:00", "open": 102, "high": 106, "low": 101, "close": 104, "volume": 1100000},
    ]
    cache.set(key, fallback, ttl_seconds=120)
    return fallback


def render_candlestick_svg(symbol: str, period: str = "3m", limit: int = 72) -> str | None:
    """Vẽ biểu đồ NẾN đơn giản (SVG) của 1 cổ phiếu — dùng để nhúng vào câu trả lời VinaF AI.

    Trả về chuỗi SVG hoặc None nếu không đủ dữ liệu.
    """
    bars: list[dict[str, Any]] = []
    try:
        raw = get_history(symbol, "1d", period)
        bars = [b for b in (raw or []) if isinstance(b, dict)]
    except Exception:
        bars = []
    if len(bars) < 10:
        return None
    bars = bars[-limit:]
    closes = [float(b.get("close") or 0) for b in bars]
    if not any(closes):
        return None
    highs: list[float] = []
    lows: list[float] = []
    for i, bar in enumerate(bars):
        highs.append(float(bar.get("high") or closes[i]))
        lows.append(float(bar.get("low") or closes[i]))
    vols = [float(bar.get("volume") or 0) for bar in bars]
    hi, lo = max(highs), min(lows)
    span = (hi - lo) or ((hi or 1.0) * 0.01)
    hi += span * 0.07
    lo = max(0.0, lo - span * 0.07)
    vmax = max([v for v in vols if v > 0] or [1.0]) or 1.0

    W, H = 760, 360
    mL, mR, mT = 14, 14, 22
    volTop = H - 66
    volBottom = H - 20
    plotW = W - mL - mR
    plotH = volTop - mT - 6
    n = len(bars)
    step = plotW / n
    bw = max(1.5, min(14.0, step * 0.62))

    def X(i: int) -> float:
        return mL + step * (i + 0.5)

    def Y(v: float) -> float:
        return mT + (hi - v) / (hi - lo) * plotH

    up, dn = "#16a34a", "#d94b3f"
    parts: list[str] = []
    # Lưới ngang + nhãn giá
    grid_rows = 4
    for g in range(grid_rows + 1):
        val = hi - (hi - lo) * g / grid_rows
        y = Y(val)
        parts.append(f'<line x1="{mL}" y1="{y:.1f}" x2="{W - mR}" y2="{y:.1f}" stroke="#e8edf4" stroke-width="1"/>')
        parts.append(f'<text x="{W - mR - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="10" fill="#9aa8bd">{val:,.0f}</text>')
    # MA20
    ma: list[float | None] = []
    for i in range(n):
        if i < 19:
            ma.append(None)
        else:
            ma.append(sum(closes[i - 19:i + 1]) / 20.0)
    # Nến + khối lượng
    for i, b in enumerate(bars):
        c = closes[i]
        o = float(b.get("open") or c)
        h = highs[i]
        l = lows[i]
        x = X(i)
        color = up if c >= o else dn
        top, bot = min(o, c), max(o, c)
        parts.append(f'<line x1="{x:.2f}" y1="{Y(h):.2f}" x2="{x:.2f}" y2="{Y(l):.2f}" stroke="{color}" stroke-width="1"/>')
        parts.append(f'<rect x="{x - bw / 2:.2f}" y="{Y(bot):.2f}" width="{bw:.2f}" height="{max(1.0, Y(top) - Y(bot)):.2f}" fill="{color}" rx="1"/>')
        v = vols[i] or 0
        if v > 0:
            hh = (volBottom - volTop) * (v / vmax)
            parts.append(f'<rect x="{x - bw / 2:.2f}" y="{volBottom - hh:.2f}" width="{bw:.2f}" height="{hh:.2f}" fill="{color}" opacity="0.28"/>')
    # Đường MA20
    pts = [f"{X(i):.2f},{Y(v):.2f}" for i, v in enumerate(ma) if v is not None]
    if pts:
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#0f6bbd" stroke-width="1.6" opacity="0.9"/>')
    # Giá đóng cửa gần nhất
    last_close = closes[-1]
    ylast = Y(last_close)
    parts.append(f'<line x1="{mL}" y1="{ylast:.2f}" x2="{W - mR}" y2="{ylast:.2f}" stroke="#008080" stroke-width="1" stroke-dasharray="4 3" opacity="0.7"/>')
    parts.append(f'<text x="{W - mR - 4}" y="{ylast - 5:.2f}" text-anchor="end" font-size="11" font-weight="800" fill="#008080">{last_close:,.0f}</text>')
    sym = str(symbol).upper()
    title = f"{sym} · {period.upper()} · Giá {last_close:,.0f}".replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '

        f'font-family="Inter, Arial, sans-serif" role="img">'
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>'
        f'<text x="{mL}" y="16" font-size="13" font-weight="800" fill="#12203a">{title}</text>'
        f'<line x1="{mL}" y1="{volTop}" x2="{W - mR}" y2="{volTop}" stroke="#e8edf4" stroke-width="1"/>'
        + "".join(parts)
        + "</svg>"
    )


_screener_row_index: dict[str, dict[str, Any]] | None = None
_screener_row_index_src: list[dict[str, Any]] | None = None


def _screener_row(symbol: str) -> dict[str, Any]:
    """Tra 1 dòng trong snapshot screener theo mã cổ phiếu.

    Index (ticker → row) được dựng 1 LẦN cho mỗi dataset đang cache để tra nhanh,
    không phải quét 1.600+ dòng cho mỗi lần gọi quote.
    """
    global _screener_row_index, _screener_row_index_src
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}
    dataset = _get_screener_dataset()
    if _screener_row_index is None or _screener_row_index_src is not dataset:
        index: dict[str, dict[str, Any]] = {}
        for r in dataset:
            ticker = str(r.get("ticker") or "").upper().strip()
            if ticker:
                index[ticker] = r
        _screener_row_index = index
        _screener_row_index_src = dataset
    return _screener_row_index.get(symbol_upper) or {}


def _get_quote_fundamentals(symbol: str) -> dict[str, Any]:
    """Chỉ số cơ bản của cổ phiếu: Vốn hoá, Giá trị sổ sách, P/E, P/B, EPS, ROE, ROA,
    KLGD TB 10 phiên, %1W, %1M, %YTD.

    Nguồn:
    - Vốn hoá / Giá trị sổ sách / EPS / ROE / ROA / KLGD TB 10 phiên / %1W-%1M-%YTD:
      ưu tiên symbol summary Simplize (đầy đủ nhất); chưa có thì fallback screener
      dataset (thiếu bookValue / EPS / ROE / ROA).
    - **P/E & P/B: LẤY TỪ SNAPSHOT SCREENER** (fallback summary nếu screener thiếu) —
      vì đây cũng chính là nguồn của khối "Định giá" ở màn hình biểu đồ phân tích kỹ
      thuật (full screen) và bảng VN30 ở trang Thị trường. Trước đây P/E lấy từ summary
      (theo EPS LTM) nên block đầu trang chi tiết lệch với màn hình full screen
      (VD FPT: 12.34 vs 10.92).
    """
    symbol_upper = symbol.upper().strip()
    out: dict[str, Any] = {}
    summary = _get_symbol_summary(symbol_upper)
    row = _screener_row(symbol_upper)

    if summary:
        out["market_cap"] = _to_float(summary.get("marketCap"))
        out["book_value"] = _to_float(summary.get("bookValue"))
        out["eps"] = _to_float(summary.get("epsRatio"))
        out["roe"] = _to_float(summary.get("roe"))
        out["roa"] = _to_float(summary.get("roa"))
        out["avg_volume_10d"] = _to_float(summary.get("volume10dAvg"))
        out["pct_1w"] = _to_float(summary.get("pricePctChg7d"))
        out["pct_1m"] = _to_float(summary.get("pricePctChg30d"))
        out["pct_ytd"] = _to_float(summary.get("pricePctChgYtd"))
    elif row:
        out["market_cap"] = _to_float(row.get("marketCapVnd"))
        out["avg_volume_10d"] = _to_float(row.get("volume10dAvg"))
        out["pct_1w"] = _to_float(row.get("pricePctChg7d"))
        out["pct_1m"] = _to_float(row.get("pricePctChg30d"))
        out["pct_ytd"] = _to_float(row.get("pricePctChgYtd"))

    if summary or row:
        pe = _to_float(row.get("peRatio")) if row else 0.0
        pb = _to_float(row.get("pbRatio")) if row else 0.0
        if pe <= 0:
            pe = _to_float(summary.get("peRatio")) if summary else 0.0
        if pb <= 0:
            pb = _to_float(summary.get("pbRatio")) if summary else 0.0
        out["pe"] = pe
        out["pb"] = pb
    return out


# ---------------------------------------------------------------------------
# Quote block đầu trang chi tiết — nguồn vnstock (bảng giá VCI)
# ---------------------------------------------------------------------------
_VCI_PRICE_BOARD_PATH = "/price/symbols/getList"


def _build_price_board_snapshot(
    symbol: str,
    *,
    name: Any,
    exchange: Any,
    price: Any,
    reference_price: Any,
    open_price: Any,
    high: Any,
    low: Any,
    volume: Any,
    traded_value: Any,
    bid_price: Any = 0,
    bid_volume: Any = 0,
    ask_price: Any = 0,
    ask_volume: Any = 0,
    listed_shares: Any = 0,
    trading_status: Any = "",
    as_of: Any = "",
    source: str = "vnstock-vci",
) -> dict[str, Any]:
    """Chuẩn hoá 1 dòng bảng giá VCI về schema quote của VinaFintech.

    Giá / khối lượng / giá trị khớp lệnh lấy NGUYÊN từ bảng giá (nguồn vnstock),
    không suy diễn từ nến ngày. `traded_value` là VND.
    """
    price_v = _to_float(price)
    reference_v = _to_float(reference_price)
    if price_v <= 0:
        price_v = reference_v
    change_v = price_v - reference_v if price_v > 0 and reference_v > 0 else 0.0
    volume_v = int(_to_float(volume))
    value_v = _to_float(traded_value)
    if value_v <= 0 and price_v > 0 and volume_v > 0:
        value_v = price_v * volume_v
    shares_v = _to_float(listed_shares)
    exchange_v = str(exchange or "").strip().upper()

    snapshot: dict[str, Any] = {
        "symbol": symbol,
        "name": str(name or symbol),
        "exchange": _EXCHANGE_CANONICAL.get(exchange_v, exchange_v),
        "price": price_v,
        "matched_price": price_v,
        "change": round(change_v, 4),
        "percent_change": round((change_v / reference_v * 100.0) if reference_v > 0 else 0.0, 4),
        "reference_price": reference_v,
        "open": _to_float(open_price),
        "high": _to_float(high),
        "low": _to_float(low),
        "volume": volume_v,
        "value": round(value_v, 2),
        "bid_price_1": _to_float(bid_price),
        "bid_volume_1": int(_to_float(bid_volume)),
        "ask_price_1": _to_float(ask_price),
        "ask_volume_1": int(_to_float(ask_volume)),
        "trading_status": str(trading_status or ""),
        "as_of": str(as_of or ""),
        "source": source,
    }
    # Vốn hoá = giá khớp × số CP niêm yết (VCI) — luôn khớp giá đang hiển thị,
    # tránh dùng marketCap snapshot của nguồn khác (có thể lệch giá/ngày).
    if shares_v > 0 and price_v > 0:
        snapshot["market_cap"] = round(price_v * shares_v, 2)
    return snapshot


def _vci_price_board_snapshot(symbol_upper: str) -> dict[str, Any] | None:
    """Snapshot bảng giá VCI qua REST (đúng endpoint `vnstock Trading.price_board` dùng)."""
    try:
        response = _get_http_client().post(
            f"{_VCI_TRADING_URL}{_VCI_PRICE_BOARD_PATH}",
            json={"symbols": [symbol_upper]},
            headers=_VCI_TRADING_HEADERS,
            timeout=httpx.Timeout(_VCI_TRADING_TIMEOUT, connect=10.0),
        )
        rows = response.json()
    except Exception:
        return None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None

    item = rows[0]
    listing = item.get("listingInfo") or {}
    match = item.get("matchPrice") or {}
    bid_ask = item.get("bidAsk") or {}
    bids = bid_ask.get("bidPrices") or []
    asks = bid_ask.get("askPrices") or []
    bid1 = bids[0] if bids and isinstance(bids[0], dict) else {}
    ask1 = asks[0] if asks and isinstance(asks[0], dict) else {}
    # VCI trả giá trị khớp lệnh theo triệu đồng.
    traded_value = _to_float(match.get("accumulatedValue")) * 1_000_000

    return _build_price_board_snapshot(
        symbol_upper,
        name=listing.get("organName") or listing.get("enOrganName"),
        exchange=listing.get("board") or listing.get("exchange"),
        price=match.get("matchPrice"),
        reference_price=listing.get("refPrice") or match.get("referencePrice"),
        open_price=match.get("openPrice"),
        high=match.get("highest"),
        low=match.get("lowest"),
        volume=match.get("accumulatedVolume"),
        traded_value=traded_value,
        bid_price=bid1.get("price"),
        bid_volume=bid1.get("volume"),
        ask_price=ask1.get("price"),
        ask_volume=ask1.get("volume"),
        listed_shares=listing.get("listedShare"),
        trading_status=listing.get("tradingStatus"),
        as_of=match.get("receivedTime") or bid_ask.get("receivedTime") or listing.get("tradingDate"),
    )


def _vnstock_price_board_snapshot(symbol_upper: str) -> dict[str, Any] | None:
    """Fallback bảng giá qua thư viện vnstock (VCI) khi REST đổi schema/lỗi.

    Chậm hơn (~5s lần import đầu) nên chỉ dùng khi REST không trả dữ liệu.
    """
    try:
        from vnstock import Trading  # lazy import

        df = Trading(source="VCI", symbol=symbol_upper, show_log=False).price_board(
            [symbol_upper], flatten_columns=True
        )
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None

    row = df.iloc[0]

    def pick(*names: str) -> Any:
        for name in names:
            value = row.get(name)
            if value is not None and str(value).strip() not in ("", "nan", "None"):
                return value
        return None

    return _build_price_board_snapshot(
        symbol_upper,
        name=pick("listing_organ_name", "listing_en_organ_name"),
        exchange=pick("listing_exchange", "listing_board"),
        price=pick("match_match_price", "match_avg_match_price"),
        reference_price=pick("listing_ref_price", "match_reference_price"),
        open_price=pick("match_open_price"),
        high=pick("match_highest"),
        low=pick("match_lowest"),
        volume=pick("match_accumulated_volume"),
        traded_value=_to_float(pick("match_accumulated_value")) * 1_000_000,
        bid_price=pick("bid_ask_bid_1_price"),
        bid_volume=pick("bid_ask_bid_1_volume"),
        ask_price=pick("bid_ask_ask_1_price"),
        ask_volume=pick("bid_ask_ask_1_volume"),
        listed_shares=pick("listing_listed_share"),
        trading_status=pick("listing_trading_status"),
        as_of=pick("match_sending_time", "bid_ask_transaction_time"),
    )


@_singleflight(lambda symbol: f"price-board:{symbol.upper()}", 10)
def _price_board_snapshot(symbol: str) -> dict[str, Any] | None:
    """Snapshot bảng giá (nguồn vnstock) cho block "Thông tin cơ bản"."""
    symbol_upper = symbol.upper().strip()
    snapshot = _vci_price_board_snapshot(symbol_upper)
    if snapshot:
        return snapshot
    return _vnstock_price_board_snapshot(symbol_upper)


@_singleflight(lambda symbol: f"quote:{symbol.upper()}", 15)
def get_quote(symbol: str) -> dict[str, Any]:
    """Quote cho block "Thông tin cơ bản" ở trang chi tiết cổ phiếu.

    Nguồn chính: bảng giá vnstock (VCI) — giá, KL, GTGD, OHLC, giá tham chiếu,
    dư mua/bán, tên doanh nghiệp, sàn, vốn hoá (giá × số CP niêm yết).
    Fallback: Simplize khi bảng giá không khả dụng.
    """
    key = f"quote:{symbol.upper()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    # Chỉ số (VNINDEX, VN30, HNX, HNX30, UPCOM...) — trả quote như mã để frontend + chart hiển thị.
    index_key = _INDEX_SYMBOL_KEYS.get(symbol.upper())
    if index_key:
        detail = get_index_detail(index_key, "1y")
        value = _to_float(detail.get("value", 0))
        change = _to_float(detail.get("change", 0))
        result = {
            "valid": True,
            "symbol": symbol.upper(),
            "name": str(detail.get("label") or symbol.upper()),
            "exchange": "INDEX",
            "price": round(value, 4),
            "matched_price": round(value, 4),
            "change": round(change, 4),
            "percent_change": round(_to_float(detail.get("percent_change", 0)), 4),
            "reference_price": round(value - change, 4),
            "open": 0, "high": 0, "low": 0, "volume": 0, "value": 0,
            "bid_price_1": 0.0, "bid_volume_1": 0, "ask_price_1": 0.0, "ask_volume_1": 0,
            "trading_status": "",
            "as_of": str(detail.get("as_of", "")),
            "source": "index-detail",
        }
        cache.set(key, result, ttl_seconds=15)
        return result

    metadata = _lookup_symbol_metadata(symbol)

    # Mã không tồn tại trên HOSE/HNX/UPCoM → báo invalid để frontend hiển thị thông báo
    if not _is_known_symbol(symbol):
        return {
            "valid": False,
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "exchange": "",
            "price": 0,
            "change": 0,
            "percent_change": 0,
            "message": "Không có mã cổ phiếu này trên các sàn HOSE, HNX, UPCoM",
        }

    try:
        # Nạp SONG SONG bảng giá vnstock (VCI) + chỉ số cơ bản để giảm thời gian trả quote.
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_board = pool.submit(_price_board_snapshot, symbol.upper())
            f_fund = pool.submit(_get_quote_fundamentals, symbol.upper())
            board = f_board.result()
            fundamentals = f_fund.result()

        # Nguồn chính: bảng giá vnstock (VCI) — giá, KL, GTGD, OHLC, dư mua/bán, tên, sàn.
        if board:
            result = dict(board)
            board_market_cap = result.pop("market_cap", 0)
            result.update(fundamentals)
            if board_market_cap:
                result["market_cap"] = board_market_cap
            result["valid"] = True
            cache.set(key, result, ttl_seconds=15)
            return result

        # Fallback: giữ nguyên nguồn cũ khi bảng giá vnstock không khả dụng.
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_last = pool.submit(_simplize_last, "stock", [symbol.upper()])
            f_hist = pool.submit(get_history, symbol.upper(), "1d", "1m")
            last_map = f_last.result()
            history = f_hist.result()
        last = last_map.get(symbol.upper(), {})
        latest_bar = history[-1] if history else {}

        price = _to_float(last.get("price", latest_bar.get("close", 0)))
        change = _to_float(last.get("netChange", 0))
        percent_change = _to_float(last.get("pctChange", 0))
        volume = int(_to_float(latest_bar.get("volume", 0)))

        result = {
            "symbol": symbol.upper(),
            "name": str(last.get("name") or metadata.get("name") or symbol.upper()),
            "exchange": metadata.get("exchange", ""),
            "price": price,
            "matched_price": price,
            "change": round(change, 4),
            "percent_change": round(percent_change, 4),
            "reference_price": round(price - change, 4),
            "open": _to_float(latest_bar.get("open", 0)),
            "high": _to_float(latest_bar.get("high", 0)),
            "low": _to_float(latest_bar.get("low", 0)),
            "volume": volume,
            "value": round(price * volume, 2),
            "bid_price_1": 0.0,
            "bid_volume_1": 0,
            "ask_price_1": 0.0,
            "ask_volume_1": 0,
            "trading_status": "",
            "as_of": _unix_to_iso(last.get("date", 0)) or str(latest_bar.get("time", "")),
            "source": "simplize-last",
        }
        result["valid"] = True
        result.update(fundamentals)
        cache.set(key, result, ttl_seconds=15)
        return result
    except Exception:
        result = _fallback_quote(symbol)
        result["valid"] = True
        result.update(metadata)
        result.update(_get_quote_fundamentals(symbol.upper()))
        cache.set(key, result, ttl_seconds=30)
        return result


@_singleflight(lambda: "market-overview", 10)
def get_market_overview() -> list[dict[str, Any]]:
    key = "market-overview"
    cached = cache.get(key)
    if cached is not None:
        return cached

    fallback = [
        _build_market_item(key="vnindex", symbol="VNINDEX", label="VNINDEX", value=1686.1, change=-13.27, percent_change=-0.78, source="fallback"),
        _build_market_item(key="vn30", symbol="VN30", label="VN30", value=1829.3, change=-15.96, percent_change=-0.86, source="fallback"),
        _build_market_item(key="dowjones", symbol="DJI", label="Dow Jones", value=51947.2, change=235.59, percent_change=0.45, source="fallback"),
        _build_market_item(key="bitcoin", symbol="BTC", label="Bitcoin", value=64544.0, change=169.09, percent_change=0.26, unit="USD", source="fallback"),
        _build_market_item(key="gold_world", symbol="XAUUSD", label="Vang TG", value=2417.8, change=3.27, percent_change=0.14, unit="USD/oz", source="fallback"),
        _build_market_item(key="gold_vn", symbol="XAUVND", label="Vang TN", value=87.25, change=0.15, percent_change=0.17, unit="trieu/luong", source="fallback"),
    ]

    try:
        # Nạp 4 nhóm chỉ số SONG SONG (trước đây tuần tự → 4 round-trip liên tiếp).
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_indices = pool.submit(_simplize_last, "index", ["VNINDEX", "VN30", "^DJI"])
            f_crypto = pool.submit(_simplize_last, "crypto", ["BTCUSDT"])
            f_futures = pool.submit(_simplize_last, "futures", ["GC=F"])
            f_gold = pool.submit(_simplize_last, "index", ["GOLD:VN:SELL"])
            indices = f_indices.result()
            crypto = f_crypto.result()
            futures = f_futures.result()
            local_gold = f_gold.result()

        vnindex = indices.get("VNINDEX", {})
        vn30 = indices.get("VN30", {})
        dji = indices.get("^DJI", {})
        btc = crypto.get("BTCUSDT", {})
        gold_world = futures.get("GC=F", {})
        gold_vn = local_gold.get("GOLD:VN:SELL", {})

        result = [
            _build_market_item(
                key="vnindex",
                symbol="VNINDEX",
                label="VNINDEX",
                value=_to_float(vnindex.get("price", 0)),
                change=_to_float(vnindex.get("netChange", 0)),
                percent_change=_to_float(vnindex.get("pctChange", 0)),
                as_of=_unix_to_iso(vnindex.get("date", 0)),
            ),
            _build_market_item(
                key="vn30",
                symbol="VN30",
                label="VN30",
                value=_to_float(vn30.get("price", 0)),
                change=_to_float(vn30.get("netChange", 0)),
                percent_change=_to_float(vn30.get("pctChange", 0)),
                as_of=_unix_to_iso(vn30.get("date", 0)),
            ),
            _build_market_item(
                key="dowjones",
                symbol="DJI",
                label="Dow Jones",
                value=_to_float(dji.get("price", 0)),
                change=_to_float(dji.get("netChange", 0)),
                percent_change=_to_float(dji.get("pctChange", 0)),
                as_of=_unix_to_iso(dji.get("date", 0)),
            ),
            _build_market_item(
                key="bitcoin",
                symbol="BTC",
                label="Bitcoin",
                value=_to_float(btc.get("price", 0)),
                change=_to_float(btc.get("netChange", 0)),
                percent_change=_to_float(btc.get("pctChange", 0)),
                unit="USD",
                as_of=_unix_to_iso(btc.get("date", 0)),
            ),
            _build_market_item(
                key="gold_world",
                symbol="XAUUSD",
                label="Vang TG",
                value=_to_float(gold_world.get("price", 0)),
                change=_to_float(gold_world.get("netChange", 0)),
                percent_change=_to_float(gold_world.get("pctChange", 0)),
                unit="USD/oz",
                as_of=_unix_to_iso(gold_world.get("date", 0)),
            ),
            _build_market_item(
                key="gold_vn",
                symbol="XAUVND",
                label="Vang TN",
                value=round(_to_float(gold_vn.get("price", 0)) / 1000, 4),
                change=0.0,
                percent_change=0.0,
                unit="trieu/luong",
                as_of=_unix_to_iso(gold_vn.get("date", 0)),
            ),
        ]

        safe_result: list[dict[str, Any]] = []
        fallback_by_key = {item["key"]: item for item in fallback}
        for item in result:
            if item["value"] == 0:
                safe_result.append(fallback_by_key[item["key"]])
            else:
                safe_result.append(item)

        cache.set(key, safe_result, ttl_seconds=10)
        return safe_result
    except Exception:
        cache.set(key, fallback, ttl_seconds=10)
        return fallback


_YAHOO_BASE = "https://query1.finance.yahoo.com"


def _yahoo_index(symbol: str, period: str = "1y") -> dict[str, Any] | None:
    """Lấy giá + lịch sử chỉ số quốc tế từ Yahoo Finance (fallback khi Simplize không có)."""
    period_map = {"1m": "1mo", "3m": "3mo", "6m": "6mo", "1y": "1y", "5y": "5y"}
    rng = period_map.get(period, "1y")
    url = f"{_YAHOO_BASE}/v8/finance/chart/{_encode_ticker_for_api(symbol)}?range={rng}&interval=1d"
    try:
        response = _get_http_client().get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "accept": "application/json"},
            timeout=httpx.Timeout(15, connect=10.0),
        )
        data = response.json()
        result = (data.get("chart") or {}).get("result")
        if not result:
            return None
        r0 = result[0]
        meta = r0.get("meta", {})
        timestamps = r0.get("timestamp") or []
        quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        history: list[dict[str, Any]] = []
        for i in range(len(timestamps)):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            history.append(
                {
                    "time": _unix_to_iso(timestamps[i]),
                    "open": _to_float(opens[i] if i < len(opens) else c),
                    "high": _to_float(highs[i] if i < len(highs) else c),
                    "low": _to_float(lows[i] if i < len(lows) else c),
                    "close": _to_float(c),
                    "volume": int(_to_float(volumes[i] if i < len(volumes) else 0)),
                }
            )
        price = _to_float(meta.get("regularMarketPrice", 0))
        prev = _to_float(meta.get("chartPreviousClose", 0))
        if (not price or price <= 0) and history:
            price = history[-1]["close"]
        if (not prev or prev <= 0) and len(history) >= 2:
            prev = history[-2]["close"]
        change = (price - prev) if price and prev else 0.0
        pct = (change / prev * 100) if prev else 0.0
        return {
            "price": price,
            "change": change,
            "percent_change": pct,
            "as_of": history[-1]["time"] if history else "",
            "history": history,
        }
    except Exception:  # noqa: BLE001
        return None


# Kỳ hạn → cửa sổ Simplize cho biểu đồ chỉ số (luôn daily, hoặc weekly cho 5y).
# KHÔNG dùng "3m" (Simplize trả bar 2h) hay "6m" (Simplize trả ~1 năm, trùng với 1y).
_INDEX_SIMPLIZE_PERIOD = {"1m": "1m_d", "3m": "1y", "6m": "1y", "1y": "1y", "5y": "5y"}
_INDEX_CUTOFF_DAYS = {"1m": 31, "3m": 92, "6m": 184, "1y": 366, "5y": 0}


def _index_simplize_period(period: str) -> str:
    return _INDEX_SIMPLIZE_PERIOD.get(str(period).lower(), "1y")


def _filter_history_by_period(history: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    """Lọc lịch sử theo kỳ hạn (chỉ giữ các bar trong N ngày gần nhất)."""
    days = _INDEX_CUTOFF_DAYS.get(str(period).lower(), 0)
    if not days or not history:
        return history
    cutoff_ts = time.time() - days * 86400
    out: list[dict[str, Any]] = []
    for point in history:
        try:
            raw = str(point.get("time", "")).replace("Z", "+00:00")
            ts = datetime.fromisoformat(raw).timestamp()
        except Exception:
            continue
        if ts >= cutoff_ts:
            out.append(point)
    return out


_INDEX_SYMBOL_KEYS: dict[str, str] = {
    "VNINDEX": "vnindex",
    "VN30": "vn30",
    "HNX": "hnx",
    "HNXINDEX": "hnx",
    "HNX30": "hnx30",
    "UPCOM": "upcom",
    "UPINDEX": "upcom",
    "UPCOMM": "upcom",
}

_INDEX_DEFS: dict[str, dict[str, Any]] = {
    "vnindex": {"label": "VNINDEX", "unit": "", "decimals": 1, "simplize": ("VNINDEX", "index")},
    "vn30": {"label": "VN30", "unit": "", "decimals": 1, "simplize": ("VN30", "index")},
    "hnx": {"label": "HNX", "unit": "", "decimals": 1, "simplize": ("HNXINDEX", "index")},
    "hnx30": {"label": "HNX30", "unit": "", "decimals": 1, "simplize": ("HNX30", "index")},
    "upcom": {"label": "UPCOM", "unit": "", "decimals": 1, "simplize": ("UPINDEX", "index")},
    "bitcoin": {"label": "Bitcoin", "unit": "USD", "decimals": 0, "simplize": ("BTCUSDT", "crypto")},
    "gold_world": {"label": "Vàng thế giới", "unit": "USD/oz", "decimals": 2, "simplize": ("GC=F", "futures")},
    "gold_vn": {"label": "Vàng trong nước", "unit": "triệu/lượng", "decimals": 2, "simplize": ("GOLD:VN:SELL", "index"), "gold_vn_scale": True},
    "oil": {"label": "Dầu thô", "unit": "USD/thùng", "decimals": 2, "simplize": ("CL=F", "futures")},
    "dowjones": {"label": "Dow Jones", "unit": "điểm", "decimals": 1, "yahoo": "^DJI"},
    "nikkei": {"label": "Nikkei", "unit": "điểm", "decimals": 1, "yahoo": "^N225"},
    "kospi": {"label": "KOSPI", "unit": "điểm", "decimals": 1, "yahoo": "^KS11"},
    "hkex": {"label": "HKEX", "unit": "điểm", "decimals": 1, "yahoo": "^HSI"},
    "sgx": {"label": "SGX", "unit": "điểm", "decimals": 1, "yahoo": "^STI"},
    "sp500": {"label": "S&P 500", "unit": "điểm", "decimals": 1, "yahoo": "^GSPC"},
    "nasdaq": {"label": "NASDAQ", "unit": "điểm", "decimals": 1, "yahoo": "^IXIC"},
}

# 4 chỉ số cổ phiếu Việt Nam (có dữ liệu khối lượng thật → ước lượng được giá trị giao dịch)
_VN_INDEX_KEYS: set[str] = {"vnindex", "vn30", "hnx", "upcom"}


def _enrich_index_value(result: dict[str, Any], key: str) -> None:
    """Gắn ``value`` (giá trị giao dịch ước tính, VND) cho từng phiên của chỉ số VN.

    Nguồn dữ liệu chỉ số không có GTGD theo ngày → ước lượng:
    GTGD(phiên) ≈ khối lượng(phiên) × giá bình quân thị trường.
    Giá bình quân chuẩn hoá từ dữ liệu THẬT: GTGD phiên gần nhất (market-liquidity HOSE)
    ÷ khối lượng phiên gần nhất. Chỉ áp dụng cho 4 chỉ số cổ phiếu VN.
    """
    if key not in _VN_INDEX_KEYS:
        return
    history = result.get("history") or []
    if not history:
        return
    try:
        ec = get_exchange_chart("HOSE", "5d")
        today = ec.get("today") or []
        gtg_vnd = float(today[-1].get("close") or 0) * 1e9 if today else 0.0  # tỷ → VND
        vol_last = float(history[-1].get("volume") or 0)
    except Exception:
        return
    if gtg_vnd <= 0 or vol_last <= 0:
        return
    avg_price = gtg_vnd / vol_last
    for b in history:
        vol = float(b.get("volume") or 0)
        b["value"] = int(round(vol * avg_price))
    result["avg_price_estimate"] = round(avg_price, 0)


@_singleflight(lambda key, period="1y": f"index-detail:{key}:{period}", 60)
def get_index_detail(key: str, period: str = "1y") -> dict[str, Any]:
    """Chi tiết một chỉ số: giá hiện tại + lịch sử OHLCV để vẽ biểu đồ / xu hướng.

    Nguồn: Simplize (VN index/crypto/futures) → fallback Yahoo Finance (chỉ số quốc tế)
    → fallback market-overview.
    """
    key = str(key).strip().lower()
    cfg = _INDEX_DEFS.get(key)
    if not cfg:
        return {"valid": False, "key": key, "message": "Không tìm thấy chỉ số."}

    cache_key = f"index-detail:{key}:{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "valid": True,
        "key": key,
        "label": cfg["label"],
        "unit": cfg["unit"],
        "decimals": cfg["decimals"],
        "value": 0.0,
        "change": 0.0,
        "percent_change": 0.0,
        "as_of": "",
        "history": [],
    }

    # 1) Simplize
    simplize = cfg.get("simplize")
    if simplize:
        ticker, type_name = simplize
        try:
            last_map = _simplize_last(type_name, [ticker])
            last = last_map.get(ticker.upper(), {})
            value = _to_float(last.get("price", 0))
            if cfg.get("gold_vn_scale"):
                value = round(value / 1000, 4)
            result["value"] = round(value, 4)
            result["change"] = round(_to_float(last.get("netChange", 0)), 4)
            result["percent_change"] = round(_to_float(last.get("pctChange", 0)), 4)
            result["as_of"] = _unix_to_iso(last.get("date", 0))
        except Exception:  # noqa: BLE001
            pass
        try:
            history = _filter_history_by_period(
                _simplize_chart(ticker, type_name, _index_simplize_period(period)),
                period,
            )
            if cfg.get("gold_vn_scale"):
                for point in history:
                    for field in ("open", "high", "low", "close"):
                        if point.get(field) is not None:
                            point[field] = round(point[field] / 1000, 4)
            result["history"] = history
            if not result.get("as_of") and history:
                result["as_of"] = history[-1].get("time", "")
        except Exception:  # noqa: BLE001
            result["history"] = []

    # 2) Yahoo fallback cho chỉ số quốc tế (nikkei/kospi/hkex/sgx/dowjones)
    if (result["value"] == 0 or not result["history"]) and cfg.get("yahoo"):
        y = _yahoo_index(cfg["yahoo"], period)
        if y:
            result["value"] = round(y["price"], 4)
            result["change"] = round(y["change"], 4)
            result["percent_change"] = round(y["percent_change"], 4)
            result["as_of"] = y.get("as_of", "")
            result["history"] = y.get("history", [])

    # 3) Fallback market-overview (vnindex/vn30/dowjones/bitcoin/gold_*)
    if result["value"] == 0:
        for item in get_market_overview():
            if item.get("key") == key:
                result["value"] = item.get("value", 0)
                result["change"] = item.get("change", 0)
                result["percent_change"] = item.get("percent_change", 0)
                result["as_of"] = item.get("as_of", "")
                break

    # 4) Nguồn KHÔNG trả netChange/pctChange → tự suy tăng/giảm từ 2 phiên cuối của lịch sử.
    #    VD Simplize trả row cho GOLD:VN:SELL chỉ có {ticker, price} (không netChange/pctChange)
    #    → trước đây thẻ "Vàng trong nước" luôn hiển thị 0% dù giá có biến động.
    if not result["change"] and not result["percent_change"]:
        hist = result.get("history") or []
        if len(hist) >= 2:
            prev_close = _to_float(hist[-2].get("close"))
            last_close = _to_float(hist[-1].get("close"))
            if prev_close > 0 and last_close > 0:
                result["change"] = round(last_close - prev_close, 4)
                result["percent_change"] = round((last_close - prev_close) / prev_close * 100, 4)

    # Ước lượng GTGD (giá trị giao dịch) cho từng phiên của chỉ số VN → tooltip biểu đồ.
    _enrich_index_value(result, key)

    cache.set(cache_key, result, ttl_seconds=30)
    return result


def get_indices() -> list[dict[str, Any]]:
    overview = get_market_overview()
    selected = [x for x in overview if x.get("key") in {"vnindex", "vn30", "dowjones"}]
    return [
        {
            "symbol": item["symbol"],
            "value": item["value"],
            "change": item["change"],
            "percent_change": item["percent_change"],
            "source": item.get("source", "simplize"),
        }
        for item in selected
    ]


def get_financials(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "revenue": 0,
        "net_profit": 0,
        "roe": 0,
        "de_ratio": 0,
        "source": "fallback",
    }


def _vci_get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Gọi thẳng VCI iQ insight REST API (không cần handshake/cookie, kiểm chứng 2026-08-08).

    Cùng nguồn dữ liệu mà vnstock Finance dùng nhưng không phụ thuộc phiên bản vnstock,
    nên hoạt động ổn định dù server chạy bằng venv (vnstock 4.x) hay Python toàn cục (vnstock 3.x).

    PHẢI gửi `_VCIQ_HEADERS` (có `referer`): thiếu Referer ⇒ VCI trả 403 ⇒ các block
    "Phân tích lợi nhuận"/"Cơ cấu cổ đông" và dữ liệu KQKD của VinaF AI trống.
    """
    query = f"?{urlencode(params)}" if params else ""
    url = f"{_VCIQ_URL}{path}{query}"
    response = _get_http_client().get(url, headers=_VCIQ_HEADERS)
    data = response.json()
    if not isinstance(data, dict):
        return {}
    return data


_PROFIT_MAX_QUARTERS = 80


def _limit_profit_quarters(result: dict[str, Any], quarters: int) -> dict[str, Any]:
    """Cắt danh sách quý của kết quả profit-analysis. `quarters <= 0` ⇒ giữ toàn bộ lịch sử."""
    if not isinstance(result, dict):
        return result
    all_quarters = result.get("quarters") or []
    if quarters <= 0 or len(all_quarters) <= quarters:
        return result
    return {**result, "quarters": all_quarters[-quarters:]}


def get_profit_analysis(symbol: str, quarters: int | None = None) -> dict[str, Any]:
    """Lợi nhuận thực tế theo quý từ báo cáo kết quả kinh doanh (VCI REST API).

    Lấy số liệu lợi nhuận sau thuế / trước thuế / doanh thu / lợi nhuận gộp của công ty
    qua từng quý trong các năm, sắp xếp tăng dần theo thời gian. `quarters` là số quý gần
    nhất cần trả về; `None` / `0` / số âm ⇒ trả về TOÀN BỘ lịch sử VCI có (thường 8-15 năm).
    Không phụ thuộc phiên bản vnstock (gọi thẳng VCI iQ insight).
    """
    symbol_upper = symbol.upper().strip()
    try:
        wanted_quarters = int(quarters) if quarters is not None else 0
    except (TypeError, ValueError):
        wanted_quarters = 0
    wanted_quarters = max(0, min(wanted_quarters, _PROFIT_MAX_QUARTERS))
    empty = {"symbol": symbol_upper, "source": "empty", "unit": "vnd", "quarters": []}

    # Cache luôn giữ TOÀN BỘ lịch sử; mỗi lần gọi tự cắt theo `quarters` để các nơi
    # dùng khác nhau (trang chi tiết, VinaF AI...) không nhận nhầm dữ liệu đã bị cắt.
    cache_key = f"profit-analysis:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return _limit_profit_quarters(cached, wanted_quarters)

    try:
        # 1) Metadata chỉ tiêu -> ánh xạ mã trường (isa*) sang tên tiếng Anh.
        meta = _vci_get_json(f"/v1/company/{symbol_upper}/financial-statement/metrics")
        metric_rows = (meta.get("data") or {}).get("INCOME_STATEMENT", [])
        field_to_en: dict[str, str] = {}
        for row in metric_rows:
            if isinstance(row, dict):
                field_to_en[str(row.get("field") or "")] = str(row.get("titleEn") or "")

        def _find_field(needles: list[str]) -> str | None:
            for field, en in field_to_en.items():
                lower = en.lower()
                if all(needle in lower for needle in needles):
                    return field
            return None

        net_profit_field = _find_field(["net profit", "after tax"]) or "isa20"
        profit_before_tax_field = _find_field(["profit", "before tax"]) or "isa16"
        gross_profit_field = _find_field(["gross profit"]) or "isa5"
        # Doanh thu: công ty thường dùng "Net sales", ngân hàng dùng "Total operating income".
        revenue_field = (
            _find_field(["net sales"])
            or _find_field(["total operating income"])
            or "isa3"
        )

        # 2) Báo cáo kết quả kinh doanh theo quý.
        fin = _vci_get_json(
            f"/v1/company/{symbol_upper}/financial-statement",
            {"section": "INCOME_STATEMENT"},
        )
        raw_quarters = (fin.get("data") or {}).get("quarters", [])
        if not isinstance(raw_quarters, list) or not raw_quarters:
            return empty

        # 3) Gom dữ liệu và sắp tăng dần theo (năm, quý) — giữ toàn bộ lịch sử VCI trả về.
        quarters_data: list[dict[str, Any]] = []
        for q in raw_quarters:
            if not isinstance(q, dict):
                continue
            try:
                year = int(q.get("yearReport"))
                quarter = int(q.get("lengthReport"))
            except (TypeError, ValueError):
                continue
            if not (1 <= quarter <= 4):
                continue
            quarters_data.append(
                {
                    "year": year,
                    "quarter": quarter,
                    "label": f"Q{quarter}",
                    "period": f"{year}-Q{quarter}",
                    "revenue": _to_float(q.get(revenue_field)),
                    "gross_profit": _to_float(q.get(gross_profit_field)),
                    "profit_before_tax": _to_float(q.get(profit_before_tax_field)),
                    "net_profit": _to_float(q.get(net_profit_field)),
                }
            )

        quarters_data.sort(key=lambda item: (item["year"], item["quarter"]))
        if not quarters_data:
            return empty

        result = {
            "symbol": symbol_upper,
            "source": "vci-finance",
            "unit": "vnd",
            "quarters": quarters_data,
        }
        cache.set(cache_key, result, ttl_seconds=21600)
        return _limit_profit_quarters(result, wanted_quarters)
    except Exception:
        return empty


_FIN_SECTION_ALIAS = {
    "income": "INCOME_STATEMENT",
    "income_statement": "INCOME_STATEMENT",
    "kqkd": "INCOME_STATEMENT",
    "balance": "BALANCE_SHEET",
    "balance_sheet": "BALANCE_SHEET",
    "cdkt": "BALANCE_SHEET",
    "cashflow": "CASH_FLOW",
    "cash_flow": "CASH_FLOW",
    "lctt": "CASH_FLOW",
}


def get_financial_statement(symbol: str, section: str = "balance", periods: int = 6) -> dict[str, Any]:
    """Đọc một mục của BÁO CÁO TÀI CHÍNH (VCI iQ): balance (Cân đối kế toán),
    cashflow (Lưu chuyển tiền tệ). (Kết quả kinh doanh dùng get_profit_analysis.)
    Trả về các kỳ gần nhất kèm các chỉ tiêu (ánh xạ tiêu đề tiếng Anh từ metadata)."""
    symbol_upper = symbol.upper().strip()
    section_key = _FIN_SECTION_ALIAS.get(str(section).lower().strip(), "BALANCE_SHEET")
    periods = max(2, min(int(periods or 6), 12))
    cache_key = f"fin-statement:{symbol_upper}:{section_key}:{periods}"
    empty = {"symbol": symbol_upper, "section": section_key, "source": "empty", "periods": []}
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        meta = _vci_get_json(f"/v1/company/{symbol_upper}/financial-statement/metrics")
        metric_rows = (meta.get("data") or {}).get(section_key, [])
        field_to_en: dict[str, str] = {}
        for row in metric_rows:
            if isinstance(row, dict):
                en = str(row.get("titleEn") or "").strip()
                if en:
                    field_to_en[str(row.get("field") or "")] = en

        fin = _vci_get_json(
            f"/v1/company/{symbol_upper}/financial-statement",
            {"section": section_key},
        )
        raw = (fin.get("data") or {}).get("quarters", [])
        if not isinstance(raw, list) or not raw:
            return empty

        periods_data: list[dict[str, Any]] = []
        for q in raw:
            if not isinstance(q, dict):
                continue
            try:
                year = int(q.get("yearReport"))
                quarter = int(q.get("lengthReport"))
            except (TypeError, ValueError):
                continue
            items: dict[str, Any] = {}
            for field, en in field_to_en.items():
                v = q.get(field)
                if isinstance(v, (int, float)):
                    items[en] = round(float(v), 2)
            if 1 <= quarter <= 4:
                label = f"Q{quarter}/{year}"
                period_key = f"{year}-{quarter:02d}"
            else:
                label = f"{year} (kỳ {quarter})"
                period_key = f"{year}-k{quarter:02d}"
            periods_data.append({"period": period_key, "label": label, "items": items})
        periods_data.sort(key=lambda x: x["period"])
        periods_data = periods_data[-periods:]
        result = {
            "symbol": symbol_upper,
            "section": section_key,
            "source": "vci-finance",
            "periods": periods_data,
        }
        cache.set(cache_key, result, ttl_seconds=21600)
        return result
    except Exception:
        return empty


def get_financial_statement_table(
    symbol: str,
    section: str = "balance",
    periods: int = 8,
    period: str = "quarter",
) -> dict[str, Any]:
    """Bảng BÁO CÁO TÀI CHÍNH đầy đủ (mọi chỉ tiêu) để hiển thị dạng bảng.

    Khác `get_financial_statement` (trả dict tiêu đề tiếng Anh → giá trị, dùng cho AI),
    hàm này giữ ĐÚNG THỨ TỰ chỉ tiêu của báo cáo gốc + nhãn tiếng Việt + cấp phân cấp,
    nên FE render được Cân đối kế toán / KQKD / Lưu chuyển tiền tệ như báo cáo.

    - section: balance | income | cashflow (alias: cdkt, kqkd, lctt...)
    - period : quarter | year (báo cáo quý / báo cáo năm)
    - periods: số kỳ gần nhất (2..12)
    """
    symbol_upper = symbol.upper().strip()
    section_key = _FIN_SECTION_ALIAS.get(str(section).lower().strip(), "BALANCE_SHEET")
    period_kind = "year" if str(period).lower().strip() in ("year", "annual", "y", "nam") else "quarter"
    try:
        wanted = int(periods) if periods is not None else 8
    except (TypeError, ValueError):
        wanted = 8
    wanted = max(2, min(wanted, 12))
    empty = {
        "symbol": symbol_upper,
        "section": section_key,
        "period": period_kind,
        "source": "empty",
        "unit": "vnd",
        "periods": [],
        "rows": [],
    }
    cache_key = f"fin-table:{symbol_upper}:{section_key}:{period_kind}:{wanted}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        meta = _vci_get_json(f"/v1/company/{symbol_upper}/financial-statement/metrics")
        metrics = [
            r
            for r in ((meta.get("data") or {}).get(section_key) or [])
            if isinstance(r, dict) and r.get("field")
        ]
        fin = _vci_get_json(
            f"/v1/company/{symbol_upper}/financial-statement",
            {"section": section_key},
        )
        raw = (fin.get("data") or {}).get("quarters" if period_kind == "quarter" else "years") or []
        if not metrics or not isinstance(raw, list) or not raw:
            return empty

        cols: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            try:
                year = int(row.get("yearReport"))
                length = int(row.get("lengthReport"))
            except (TypeError, ValueError):
                continue
            if period_kind == "quarter":
                if not (1 <= length <= 4):
                    continue
                cols.append({"key": f"{year}-{length:02d}", "label": f"Q{length}/{year}", "row": row})
            else:
                cols.append({"key": f"{year}", "label": f"Năm {year}", "row": row})
        cols.sort(key=lambda c: c["key"])
        cols = cols[-wanted:]
        if not cols:
            return empty

        rows: list[dict[str, Any]] = []
        for m in metrics:
            field = str(m.get("field") or "")
            values: list[float | None] = []
            has_value = False
            for c in cols:
                v = c["row"].get(field)
                if isinstance(v, (int, float)):
                    values.append(round(float(v), 2))
                    has_value = True
                else:
                    values.append(None)
            # Chỉ tiêu không áp dụng cho doanh nghiệp này (VD ngân hàng vs sản xuất) → bỏ
            if not has_value:
                continue
            try:
                level = int(m.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            rows.append(
                {
                    "field": field,
                    "title_en": str(m.get("titleEn") or ""),
                    "title_vi": str(m.get("titleVi") or ""),
                    "level": level,
                    "parent": m.get("parent"),
                    "values": values,
                }
            )

        result = {
            "symbol": symbol_upper,
            "section": section_key,
            "period": period_kind,
            "source": "vci-finance",
            "unit": "vnd",
            "periods": [{"period": c["key"], "label": c["label"]} for c in cols],
            "rows": rows,
        }
        cache.set(cache_key, result, ttl_seconds=21600)
        return result
    except Exception:
        return empty


def get_income_statement_detail(symbol: str, quarters: int = 8) -> dict[str, Any]:
    """Chi tiết Bảng Kết quả kinh doanh (KQKD) theo quý — để AI giải thích NGUYÊN NHÂN
    tăng/giảm doanh thu - lợi nhuận (bridge: doanh thu, biên gộp, chi phí bán hàng/QLDN,
    chi phí tài chính/lãi vay, thu nhập khác, thuế, LNST công ty mẹ, EPS...).

    Cùng nguồn VCI iQ insight như get_profit_analysis, nhưng trả về nhiều khoản mục hơn.
    Dùng khi cần lý giải vì sao lợi nhuận/doanh thu đổi chiều hoặc biến động lớn.
    """
    symbol_upper = symbol.upper().strip()
    safe_q = max(4, min(int(quarters or 8), 20))
    empty = {"symbol": symbol_upper, "source": "empty", "unit": "vnd", "quarters": []}
    cache_key = f"income-detail:{symbol_upper}:{safe_q}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Các khoản mục muốn lấy (khóa → mã isa* theo schema VCI đã kiểm chứng FPT/PC1/HPG...).
    # Ưu tiên khớp tên tiếng Anh từ metadata; nếu không tìm thấy thì dùng mã dự phòng.
    _TARGETS: list[tuple[str, list[str], str]] = [
        ("revenue", ["net sales", "total operating income"], "isa3"),
        ("gross_profit", ["gross profit"], "isa5"),
        ("selling_expense", ["selling expense"], "isa9"),
        ("admin_expense", ["general and admin"], "isa10"),
        ("operating_profit", ["operating profit"], "isa11"),
        ("financial_income", ["financial income"], "isa6"),
        ("financial_expense", ["financial expenses"], "isa7"),
        ("interest_expense", ["interest expenses"], "isa8"),
        ("other_income_net", ["net other income"], "isa14"),
        ("profit_before_tax", ["profit before tax"], "isa16"),
        ("tax_expense", ["tax expense"], "isa19"),
        ("net_profit", ["net profit", "after tax"], "isa20"),
        ("minority_interest", ["minority interest"], "isa21"),
        ("net_profit_parent", ["attributable to parent"], "isa22"),
        ("eps", ["eps basic"], "isa23"),
    ]
    try:
        meta = _vci_get_json(f"/v1/company/{symbol_upper}/financial-statement/metrics")
        metric_rows = (meta.get("data") or {}).get("INCOME_STATEMENT", [])
        field_to_en: dict[str, str] = {}
        for row in metric_rows:
            if isinstance(row, dict):
                field_to_en[str(row.get("field") or "")] = str(row.get("titleEn") or "")

        def _pick(field_candidates: list[str]) -> str | None:
            for field, en in field_to_en.items():
                lower = en.lower()
                if all(nd in lower for nd in field_candidates):
                    return field
            return None

        # field đích cho từng khóa: ưu tiên khớp tên, fallback mã isa.
        target_field: dict[str, str] = {}
        for key, needles, fallback in _TARGETS:
            target_field[key] = _pick(needles) or fallback

        fin = _vci_get_json(
            f"/v1/company/{symbol_upper}/financial-statement",
            {"section": "INCOME_STATEMENT"},
        )
        raw_quarters = (fin.get("data") or {}).get("quarters", [])
        if not isinstance(raw_quarters, list) or not raw_quarters:
            return empty

        quarters_data: list[dict[str, Any]] = []
        for q in raw_quarters:
            if not isinstance(q, dict):
                continue
            try:
                year = int(q.get("yearReport"))
                quarter = int(q.get("lengthReport"))
            except (TypeError, ValueError):
                continue
            if not (1 <= quarter <= 4):
                continue
            row: dict[str, Any] = {
                "period": f"{year}-Q{quarter}",
                "label": f"Q{quarter}/{year}",
            }
            for key, _needles, _fb in _TARGETS:
                row[key] = _to_float(q.get(target_field.get(key, "")), default=None)
            rev = row["revenue"]
            gp = row["gross_profit"]
            row["gross_margin_pct"] = round(gp / rev * 100.0, 2) if (isinstance(gp, (int, float)) and rev) else None
            quarters_data.append(row)

        quarters_data.sort(key=lambda item: item["period"])
        quarters_data = quarters_data[-safe_q:]
        result = {
            "symbol": symbol_upper,
            "source": "vci-finance",
            "unit": "vnd",
            "quarters": quarters_data,
        }
        cache.set(cache_key, result, ttl_seconds=21600)
        return result
    except Exception:
        return empty


def get_insider_trades(symbol: str) -> list[dict[str, Any]]:
    return [{"symbol": symbol.upper(), "person": "N/A", "action": "N/A", "volume": 0, "date": "N/A"}]


# ============================================================
# CƠ CẤU CỔ ĐÔNG (Shareholder Structure)
# Data source: VCI iQ insight REST API (không phụ thuộc vnstock).
#   - /v1/company/{SYM}/shareholder-structure : cơ cấu Nhà nước / Nước ngoài / Khác
#   - /v1/company/{SYM}/shareholder           : danh sách cổ đông lớn
# ============================================================

_VN_DIACRITICS_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    r"ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]"
)

_VN_NATIONALITY_HINTS = (
    "công ty", "cong ty", "ctcp", "tnhh", "tổng công ty", "tong cong ty",
    "tập đoàn", "tap doan", "ngân hàng", "ngan hang", "việt nam", "viet nam",
    "vietnam", "scic", "cty", "cổ phần", "co phan", "doanh nghiệp", "doanh nghiep",
    "nhà nước", "nha nuoc",
)

# Gợi ý quốc tịch cho cổ đông nước ngoài (đoán từ tên). Chỉ dùng các từ khóa
# thương hiệu / địa danh mạnh để tránh trùng với tên người Việt (vd: "thai", "kim"...).
_COUNTRY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Nhật Bản", ("japan", "tokyo", "mitsubishi", "ufj", "nomura", "mizuho", "sumitomo", "daiwa", "smbc", "softbank")),
    ("Hàn Quốc", ("korea", "seoul", "hyundai", "samsung", "kb financial", " kb ", "lg electronics", "sk hynix", "sk ", "shinhan", "woori", "keb")),
    ("Trung Quốc", ("china", "chinese", "shanghai", "beijing", "shenzhen", "bank of china", "icbc")),
    ("Hồng Kông", ("hong kong", "hongkong")),
    ("Đài Loan", ("taiwan", "taipei")),
    ("Singapore", ("singapore", "temasek", "gic ") ),
    ("Đức", ("deutsche", "germany", "frankfurt", "dresdner", "commerzbank", "allianz", "siemens", "bayer", "basf", "volkswagen", "bmw")),
    ("Anh", ("london", "united kingdom", "england", "british", "schroder", "hsbc", "standard chartered", "barclays", "lloyds", "prudential", "aviva")),
    ("Mỹ", ("united states", "usa", "new york", "america", "citibank", "citigroup", "jpmorgan", "goldman", "morgan stanley", "berkshire", "fidelity", "vanguard", "wells fargo", "blackrock", "state street", "merrill lynch", "mellon")),
    ("Úc", ("australia", "macquarie", "sydney", "commonwealth bank", "westpac")),
    ("Pháp", ("france", "paris", "bnp ", "societe generale", "credit agricole", "axa ", "lvmh", "totalenergies")),
    ("Hà Lan", ("netherlands", "hà lan", "amsterdam", "robeco", "aegon", "rabobank", "ing bank", "b.v.", "n.v.")),
    ("Thụy Sĩ", ("switzerland", "zurich", "ubs ", "credit suisse", "nestle", "novartis", "roche")),
    ("Thái Lan", ("thailand", "bangkok", "siam commercial")),
    ("Malaysia", ("malaysia", "kuala lumpur", "maybank", "petronas")),
    ("Ấn Độ", ("india", "mumbai", "tata ", "reliance")),
    ("Luxembourg", ("luxembourg",)),
    ("Ireland", ("ireland", "dublin")),
    ("Cayman", ("cayman",)),
    ("Bermuda", ("bermuda",)),
    ("Canada", ("canada", "toronto", "manulife", "sun life", "td bank", "royal bank of canada")),
    ("Na Uy", ("norway", "oslo", "norges")),
    ("Thụy Điển", ("sweden", "stockholm", "volvo")),
    ("Phần Lan", ("finland", "helsinki", "nokia")),
    ("Đan Mạch", ("denmark", "copenhagen", "novo ", "maersk")),
    ("Ý", ("italy", "milan", "intesa", "unicredit", "generali")),
    ("Tây Ban Nha", ("spain", "madrid", "santander", "bbva")),
    ("Nga", ("russia", "moscow", "sberbank", "gazprom")),
    ("UAE", ("uae", "dubai", "abu dhabi")),
)

# Dấu hiệu nhận biết "quỹ đầu tư / tổ chức tài chính" trong tên cổ đông.
_FUND_HINTS = (
    " fund", " etf", "asset management", "asset mgmt", "securities", " insurance",
    " bank", "ngân hàng", "chứng khoán", "bảo hiểm", "quỹ", "quy dau tu",
    "đầu tư", "investment", "holdings",
)

# Dạng pháp lý / từ khóa doanh nghiệp tiếng Anh -> cổ đông nước ngoài
# (chỉ áp dụng khi tên KHÔNG có dấu tiếng Việt).
_FOREIGN_LEGAL_HINTS = (
    " limited", " ltd", " plc", " b.v.", " n.v.", " inc.", " inc ", " corporation",
    " corp", " llc", " llp", " fund", " etf", " holdings", " holding", " investments",
    " investment", " gmbh", " s.a.", " sarl", " berhad", " pte.", " bhd",
    " incorporated", " master fund", " international ", " group ", " co., ltd",
    " open-ended fund", " company limited",
)


def _guess_shareholder_nationality(name: str, name_en: str) -> str:
    """Ước lượng quốc tịch cổ đông từ tên (API không có trường quốc tịch).

    Thứ tự xét:
      1) Dấu hiệu thương hiệu / địa danh nước ngoài mạnh (Samsung, Tokyo Mitsubishi,
         Deutsche, Morgan Stanley...) -> trả về quốc gia đó ngay, kể cả khi tên có
         chữ tiếng Việt như "Ngân Hàng Tokyo Mitsubishi".
      2) Tên có dấu tiếng Việt -> Việt Nam (cá nhân / công ty nội địa).
      3) Dạng pháp lý / từ khóa doanh nghiệp tiếng Anh mà tên không dấu
         (Limited, Plc, Fund, B.V...) -> Nước ngoài (chưa rõ quốc gia cụ thể).
      4) Từ khóa doanh nghiệp Việt Nam dạng không dấu (ctcp, tnhh, cong ty...) -> Việt Nam.
      5) Mặc định -> Nước ngoài.
    """
    text = f"{name} {name_en}"
    low = f" {text.lower()} "

    for country, hints in _COUNTRY_HINTS:
        if any(hint in low for hint in hints):
            return country

    if _VN_DIACRITICS_RE.search(text):
        return "Việt Nam"

    for hint in _FOREIGN_LEGAL_HINTS:
        if hint in low:
            return "Nước ngoài"

    if any(hint in low for hint in _VN_NATIONALITY_HINTS):
        return "Việt Nam"

    return "Nước ngoài"


def get_shareholder_structure(symbol: str) -> dict[str, Any]:
    """Cơ cấu cổ đông (2 biểu đồ tròn + danh sách cổ đông lớn) từ VCI REST API.

    - `structure`: Nhà nước / Nước ngoài / Khác (tỷ lệ + khối lượng).
    - `investor_types`: Chiến lược / Quỹ đầu tư / Nhỏ lẻ (suy ra từ danh sách).
    - `shareholders`: danh sách cổ đông lớn (tên, quốc tịch ước lượng, số lượng,
      tỷ lệ, chức danh, ngày công bố).
    """
    symbol_upper = symbol.upper().strip()
    empty = {
        "symbol": symbol_upper,
        "source": "empty",
        "total_shares": 0.0,
        "updated": "",
        "structure": [],
        "investor_types": [],
        "shareholders": [],
    }

    cache_key = f"shareholder-structure:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        struc = _vci_get_json(f"/v1/company/{symbol_upper}/shareholder-structure")
        list_payload = _vci_get_json(f"/v1/company/{symbol_upper}/shareholder")
        struc_data = struc.get("data") if isinstance(struc, dict) else {}
        if not isinstance(struc_data, dict):
            struc_data = {}
        raw_list = list_payload.get("data") if isinstance(list_payload, dict) else None
        if not isinstance(raw_list, list):
            raw_list = []

        def _pct(key: str) -> float:
            return round(_to_float(struc_data.get(key)) * 100.0, 2)

        structure = [
            {
                "key": "state",
                "label": "Cổ đông nhà nước",
                "percentage": _pct("statePercentage"),
                "volume": int(_to_float(struc_data.get("stateVolume"))),
            },
            {
                "key": "foreign",
                "label": "Cổ đông nước ngoài",
                "percentage": _pct("foreignPercentage"),
                "volume": int(_to_float(struc_data.get("foreignerVolume"))),
            },
            {
                "key": "other",
                "label": "Cổ đông khác",
                "percentage": _pct("otherPercentage"),
                "volume": int(_to_float(struc_data.get("otherVolume"))),
            },
        ]

        shareholders: list[dict[str, Any]] = []
        for row in raw_list:
            if not isinstance(row, dict):
                continue
            name = str(row.get("ownerName") or "")
            name_en = str(row.get("ownerNameEn") or "")
            quantity = int(_to_float(row.get("quantity")))
            percentage = round(_to_float(row.get("percentage")) * 100.0, 2)
            if quantity <= 0 and percentage <= 0:
                continue
            shareholders.append(
                {
                    "name": name,
                    "name_en": name_en,
                    "nationality": _guess_shareholder_nationality(name, name_en),
                    "quantity": quantity,
                    "percentage": percentage,
                    "type": str(row.get("ownerType") or ""),
                    "position": str(row.get("positionName") or ""),
                    "updated": str(row.get("publicDate") or ""),
                }
            )
        shareholders.sort(key=lambda s: s["quantity"], reverse=True)

        # Biểu đồ 2: phân loại Chiến lược / Quỹ đầu tư / Nhỏ lẻ từ danh sách.
        fund_pct = strategic_pct = retail_pct = 0.0
        for s in shareholders:
            name = s["name"]
            name_en = s["name_en"]
            percent = s["percentage"]
            text = f" {f'{name} {name_en}'.lower()} "
            is_state = (
                "state" in text
                or "nhà nước" in name.lower()
                or "nha nuoc" in name.lower()
                or "scic" in text
            )
            if is_state:
                strategic_pct += percent
            elif any(hint in text for hint in _FUND_HINTS):
                fund_pct += percent
            elif s["position"] or (s["type"] == "CORPORATE" and percent >= 2.0):
                strategic_pct += percent
            else:
                retail_pct += percent

        listed_sum = fund_pct + strategic_pct + retail_pct
        if listed_sum > 0:
            retail_pct += max(0.0, 100.0 - listed_sum)
        investor_types = [
            {"key": "strategic", "label": "Cổ đông chiến lược", "percentage": round(strategic_pct, 2)},
            {"key": "funds", "label": "Quỹ đầu tư", "percentage": round(fund_pct, 2)},
            {"key": "retail", "label": "Cổ đông nhỏ lẻ / thông thường", "percentage": round(retail_pct, 2)},
        ]

        updated = ""
        for s in shareholders:
            if s["updated"]:
                updated = s["updated"]
                break

        result = {
            "symbol": symbol_upper,
            "source": "vci",
            "total_shares": _to_float(struc_data.get("totalShares")),
            "updated": updated,
            "structure": structure,
            "investor_types": investor_types,
            "shareholders": shareholders,
        }
        cache.set(cache_key, result, ttl_seconds=3600)
        return result
    except Exception:
        return empty


# ============================================================
# BÁO CÁO & TÀI LIỆU CÔNG TY (Company Reports & Documents)
# Data source: cafef.vn (công bố tài liệu + báo cáo phân tích).
#   - FileBCTC.ashx?Symbol={sym}&Type={t}&Year=0 : JSON danh sách tài liệu PDF
#       Type: 1=BCTC, 3=Cáo bạch & BCTN, 4=Nghị quyết, 5=Quản trị
#   - /du-lieu/phan-tich-bao-cao.chn : báo cáo phân tích (render phía server)
# ============================================================

_CAFEF_BASE_URL = "https://cafef.vn"
_CAFEF_AJAX_URL = "https://cafef.vn/du-lieu/Ajax/PageNew"
_CAFEF_HEADERS = {
    "accept": "application/json, text/html,*/*",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "x-requested-with": "XMLHttpRequest",
}

# Loại tài liệu FileBCTC theo category.
_CAFEF_FILE_TYPE = {"financial": 1, "annual": 3, "agm": 4, "prospectus": 3}
# Số báo cáo tối đa trả về cho 1 danh mục (frontend hiển thị 5, phần còn lại để cuộn).
_REPORTS_MAX_ITEMS = 20
# Từ khóa lọc tên tài liệu theo category.
_CAFEF_FILTER = {
    "annual": ("báo cáo thường niên", "bao cao thuong nien"),
    "agm": ("đại hội cổ đông", "dai hoi co dong", "đhđcđ"),
    "prospectus": ("bản cáo bạch", "ban cao bach"),
}


def _cafef_get_json(path: str) -> dict[str, Any]:
    response = _get_http_client().get(f"{_CAFEF_AJAX_URL}/{path}", headers=_CAFEF_HEADERS)
    data = response.json()
    if not isinstance(data, dict):
        return {}
    return data


def _cafef_get_text(url: str) -> str:
    response = _get_http_client().get(
        url,
        headers={
            "accept": "text/html,*/*",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    return response.text


def _normalize_report_date(value: Any) -> str:
    """Chuẩn hóa kỳ/ngày từ cafef ('Q2/2026', 'CN/2025', '04-06-2026')."""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"/0+$", text) or text.lower() in ("cn/0", "0", "n/a"):
        return ""
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", text)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    return text


def _report_period_key(item: dict[str, Any]) -> tuple[int, int]:
    """Khóa sắp xếp kỳ báo cáo: CN/2026 > Q4/2026 > Q1/2026 > CN/2025 ..."""
    text = f"{item.get('date') or ''} {item.get('title') or ''}"
    m = re.search(r"(?:Q|Quý)\s*(\d)\s*(?:/|-)\s*(\d{4})", text, re.IGNORECASE)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    m2 = re.search(r"(?:CN|Cả năm|Năm)\s*(\d{4})", text, re.IGNORECASE)
    if m2:
        return (int(m2.group(1)), 5)
    m3 = re.search(r"(?:^|[/\s-])(\d{4})(?:[/\s-]|$)", text)
    if m3:
        return (int(m3.group(1)), 0)
    return (0, 0)


def _fetch_cafef_documents(symbol: str, category: str) -> list[dict[str, Any]]:
    """Tài liệu PDF từ FileBCTC.ashx (BCTC / thường niên / nghị quyết / cáo bạch)."""
    try:
        file_type = _CAFEF_FILE_TYPE.get(category, 1)
        payload = _cafef_get_json(
            f"FileBCTC.ashx?Symbol={symbol.lower()}&Type={file_type}&Year=0"
        )
        rows = payload.get("Data")
        if not isinstance(rows, list):
            return []

        keywords = _CAFEF_FILTER.get(category)
        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip()
            if not name:
                continue
            if keywords and not any(k.lower() in name.lower() for k in keywords):
                continue
            items.append(
                {
                    "title": name,
                    "date": _normalize_report_date(row.get("Time") or row.get("Date")),
                    "link": str(row.get("Link") or ""),
                }
            )
        if category == "financial":
            items.sort(key=_report_period_key, reverse=True)
        # Trả tối đa 20 báo cáo (trang chi tiết cổ phiếu chỉ hiển thị 5, còn lại cuộn trong block).
        return items[:_REPORTS_MAX_ITEMS]
    except Exception:
        return []


def _enrich_analysis_report(item: dict[str, Any]) -> None:
    """Bổ sung tiêu đề / ngày / nguồn (công ty chứng khoán) từ JSON-LD bài báo cáo."""
    try:
        html = _cafef_get_text(item["link"])
    except Exception:
        return
    for m in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        if not isinstance(data, dict) or str(data.get("@type")) != "Report":
            continue
        if data.get("headline"):
            item["title"] = str(data["headline"]).strip()
        date_raw = str(data.get("datePublished") or data.get("dateModified") or "")
        dm = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_raw)
        if dm:
            item["date"] = f"{dm.group(3)}/{dm.group(2)}/{dm.group(1)}"
        author = data.get("author") or {}
        if isinstance(author, dict) and author.get("name"):
            item["source"] = str(author["name"]).strip()
        elif isinstance(author, list) and author and isinstance(author[0], dict):
            item["source"] = str(author[0].get("name") or "").strip()
        if not item["source"]:
            desc = str(data.get("description") or "")
            sm = re.search(r"Nguồn báo cáo\s*:?\s*([^.,;]+)", desc)
            if sm:
                item["source"] = sm.group(1).strip()
        break


def _fetch_analysis_reports(symbol: str) -> list[dict[str, Any]]:
    """Báo cáo phân tích của công ty chứng khoán (cafef) cho 1 mã cổ phiếu."""
    sym_lower = symbol.lower()
    pages = [
        f"{_CAFEF_BASE_URL}/du-lieu/phan-tich-bao-cao.chn",
        f"{_CAFEF_BASE_URL}/du-lieu/phan-tich-bao-cao/cap-nhat-doanh-nghiep-khuyen-nghi.chn",
    ]
    found: dict[str, dict[str, Any]] = {}
    for url in pages:
        try:
            html = _cafef_get_text(url)
        except Exception:
            continue
        # Mỗi báo cáo: <a href=".../du-lieu/report/{symbol}-{slug}-{id}.chn">...</a>
        for m in re.finditer(
            r'<a[^>]+href="([^"]*/du-lieu/report/([a-z0-9]+)[^"]*)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL | re.IGNORECASE,
        ):
            href = m.group(1)
            slug_sym = m.group(2).lower()
            title = re.sub(r"<[^>]+>", " ", m.group(3))
            title = re.sub(r"\s+", " ", title).strip()
            if not title:
                continue
            if slug_sym != sym_lower and sym_lower not in title.lower():
                continue
            full_href = href if href.startswith("http") else f"{_CAFEF_BASE_URL}{href}"
            found.setdefault(full_href, {"title": title, "date": "", "source": "", "link": full_href})

    items = list(found.values())
    for item in items:
        _enrich_analysis_report(item)
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:_REPORTS_MAX_ITEMS]


def get_company_reports(symbol: str, category: str = "financial") -> dict[str, Any]:
    """Danh sách báo cáo / tài liệu mới nhất của công ty (nguồn cafef.vn).

    - category='analysis'   : báo cáo phân tích của công ty chứng khoán (MUA/BÁN/NẮM GIỮ)
    - category='financial'  : báo cáo tài chính (hợp nhất / công ty mẹ / công ty con)
    - category='annual'     : báo cáo thường niên (5 năm gần nhất)
    - category='agm'        : nghị quyết ĐHĐCĐ (thường niên / bất thường)
    - category='prospectus' : bản cáo bạch
    """
    symbol_upper = symbol.upper().strip()
    cat = (category or "financial").lower()
    empty = {"symbol": symbol_upper, "category": cat, "source": "empty", "items": []}

    cache_key = f"company-reports:{symbol_upper}:{cat}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if cat == "analysis":
        items = _fetch_analysis_reports(symbol_upper)
    else:
        items = _fetch_cafef_documents(symbol_upper, cat)

    result = {"symbol": symbol_upper, "category": cat, "source": "cafef", "items": items}
    cache.set(cache_key, result, ttl_seconds=3600)
    return result


# ---------------------------------------------------------------------------
# LÀM MỚI BLOCK "BÁO CÁO" VÀO 12H TRƯA (giờ VN) HẰNG NGÀY
# ---------------------------------------------------------------------------
# Báo cáo/tài liệu (cafef) được nạp theo yêu cầu (lazy, cache 1 giờ) nên tự làm mới khi
# hết hạn. Scheduler dưới đây (cùng pattern với "Lịch sự kiện thị trường" — asset_events
# và snapshot "Cơ hội mới" — home_opportunity_service) XOÁ toàn bộ cache báo cáo rồi
# DỰNG LẠI các cặp (mã, danh mục) đã từng được xem, đúng mốc 12h trưa giờ VN mỗi ngày.
_REPORTS_REFRESH_LOCK = threading.Lock()
_REPORTS_REFRESH_STARTED = False
_REPORTS_WARM_LIMIT = 40       # số cặp (mã, danh mục) dựng lại mỗi lần chạy
_REPORTS_WARM_SLEEP = 0.25     # giãn nhịp giữa các request tới cafef khi dựng lại


def _reports_cached_pairs() -> list[tuple[str, str]]:
    """[(mã, danh mục)] đang giữ trong cache báo cáo (tức đã từng được xem)."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key in cache.keys("company-reports:"):
        rest = key[len("company-reports:") :]
        if ":" not in rest:
            continue
        symbol, category = rest.split(":", 1)
        pair = (symbol.strip().upper(), category.strip().lower())
        if pair[0] and pair[1] and pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def refresh_company_reports() -> int:
    """Làm mới block "Báo cáo": xoá cache cũ + dựng lại cho các mã đã từng được xem.

    Trả số cặp dựng lại thành công. Gọi bởi scheduler 12h trưa (giờ VN).
    """
    pairs = _reports_cached_pairs()
    # Xoá TRƯỚC để chắc chắn không phục vụ bản cũ hơn mốc 12h cho BẤT KỲ mã nào
    # (mã không nằm trong danh sách dựng lại sẽ tự nạp mới ở lần xem kế tiếp).
    cache.delete_prefix("company-reports:")
    warmed = 0
    for symbol, category in pairs[:_REPORTS_WARM_LIMIT]:
        cache_key = f"company-reports:{symbol}:{category}"
        try:
            fresh = get_company_reports(symbol, category)
            if fresh.get("items"):
                warmed += 1
            else:
                # Nguồn lỗi/rỗng → KHÔNG giữ cache rỗng, để lần xem sau thử lại.
                cache.delete(cache_key)
        except Exception:  # noqa: BLE001 — 1 mã lỗi không làm hỏng cả lượt làm mới
            cache.delete(cache_key)
        time.sleep(_REPORTS_WARM_SLEEP)
    return warmed


def _seconds_until_reports_refresh() -> float:
    """Số giây tới mốc làm mới báo cáo kế tiếp (12h trưa giờ VN)."""
    now = _get_vn_now()
    target = now.replace(hour=settings.report_refresh_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _reports_refresh_loop() -> None:
    """Thread nền: ngủ tới 12h trưa rồi làm mới cache báo cáo, lặp lại mỗi ngày."""
    while True:
        try:
            time.sleep(_seconds_until_reports_refresh())
            refresh_company_reports()
        except Exception:  # noqa: BLE001 — không để thread chết vì lỗi nguồn
            time.sleep(300)


def start_daily_reports_refresh() -> None:
    """Bật làm mới block "Báo cáo" vào 12h trưa (giờ VN) mỗi ngày (idempotent)."""
    global _REPORTS_REFRESH_STARTED
    with _REPORTS_REFRESH_LOCK:
        if _REPORTS_REFRESH_STARTED:
            return
        _REPORTS_REFRESH_STARTED = True
    threading.Thread(target=_reports_refresh_loop, daemon=True, name="company-reports-daily").start()


def _format_vci_time(raw: Any) -> str:
    """VCI transaction_time dạng '074500023' (HHMMSSmmm) -> '07:45:00'."""
    text = str(raw or "").strip()
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[0:2]}:{text[2:4]}:{text[4:6]}"
    return text


def get_order_book(symbol: str) -> dict[str, Any]:
    """Sổ lệnh thực tế (bên mua/bên bán, khối lượng, giá, thời gian) từ VCI.

    Trả về tối đa 3 mức giá bên mua + 3 mức giá bên bán kèm khối lượng.
    Gọi TRỰC TIẾP REST API VCI (~0.5s) thay vì thư viện vnstock (~5s do import
    + khởi tạo object); fallback về vnstock nếu direct API lỗi.
    """
    symbol_upper = symbol.upper().strip()
    empty = {"symbol": symbol_upper, "time": "", "bids": [], "asks": [], "source": "empty"}

    cache_key = f"order-book:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = _get_http_client().post(
            f"{_VCI_TRADING_URL}/price/symbols/getList",
            json={"symbols": [symbol_upper]},
            headers=_VCI_TRADING_HEADERS,
            timeout=httpx.Timeout(_VCI_TRADING_TIMEOUT, connect=10.0),
        )
        data = response.json()
        rows = data if isinstance(data, list) else []
        if rows and isinstance(rows[0], dict):
            item = rows[0]
            bid_ask = item.get("bidAsk") or {}
            listing = item.get("listingInfo") or {}
            match = item.get("matchPrice") or {}
            bids: list[dict[str, Any]] = []
            asks: list[dict[str, Any]] = []
            for bp in (bid_ask.get("bidPrices") or [])[:3]:
                price = _to_float(bp.get("price"))
                if price > 0:
                    bids.append({"price": price, "volume": int(_to_float(bp.get("volume")))})
            for ap in (bid_ask.get("askPrices") or [])[:3]:
                price = _to_float(ap.get("price"))
                if price > 0:
                    asks.append({"price": price, "volume": int(_to_float(ap.get("volume")))})
            result = {
                "symbol": symbol_upper,
                "time": _format_vci_time(bid_ask.get("transactionTime")),
                "reference_price": _to_float(listing.get("refPrice") or match.get("referencePrice")),
                "ceiling": _to_float(listing.get("ceiling") or match.get("ceilingPrice")),
                "floor": _to_float(listing.get("floor") or match.get("floorPrice")),
                "bids": bids,
                "asks": asks,
                "source": "vci-price-board",
            }
            cache.set(cache_key, result, ttl_seconds=15)
            return result
    except Exception:
        pass

    fallback = _order_book_vnstock(symbol_upper)
    if fallback:
        cache.set(cache_key, fallback, ttl_seconds=15)
        return fallback
    return empty


def _order_book_vnstock(symbol_upper: str) -> dict[str, Any] | None:
    """Fallback Sổ lệnh qua thư viện vnstock (VCI) — chậm nhưng an toàn nếu direct API lỗi."""
    try:
        from vnstock import Trading  # lazy import

        trading = Trading(source="VCI", show_log=False)
        df = trading.price_board([symbol_upper], flatten_columns=True)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        bids: list[dict[str, Any]] = []
        asks: list[dict[str, Any]] = []
        for i in range(1, 4):
            bid_price = _to_float(row.get(f"bid_ask_bid_{i}_price"))
            bid_volume = int(_to_float(row.get(f"bid_ask_bid_{i}_volume")))
            if bid_price > 0:
                bids.append({"price": bid_price, "volume": bid_volume})
            ask_price = _to_float(row.get(f"bid_ask_ask_{i}_price"))
            ask_volume = int(_to_float(row.get(f"bid_ask_ask_{i}_volume")))
            if ask_price > 0:
                asks.append({"price": ask_price, "volume": ask_volume})
        return {
            "symbol": symbol_upper,
            "time": _format_vci_time(row.get("bid_ask_transaction_time")),
            "reference_price": _to_float(row.get("listing_ref_price") or row.get("match_reference_price")),
            "ceiling": _to_float(row.get("listing_ceiling") or row.get("match_ceiling_price")),
            "floor": _to_float(row.get("listing_floor") or row.get("match_floor_price")),
            "bids": bids,
            "asks": asks,
            "source": "vci-price-board",
        }
    except Exception:
        return None


def get_recent_trades(
    symbol: str, limit: int = 50, before: int | None = None
) -> list[dict[str, Any]]:
    """Các giao dịch khớp lệnh gần nhất của phiên (VCI intraday).

    `before` (epoch giây) dùng để phân trang lấy TOÀN BỘ giao dịch của phiên:
    trả về các lệnh có `ts <= before` (bao gồm cả giây đó). Client truyền `ts` của
    lệnh cũ nhất đang có rồi tự loại trùng theo `id` (VCI chỉ trả tối đa 100 lệnh
    mỗi request nên không lấy được cả phiên trong 1 lần gọi).

    Dữ liệu từng lệnh khớp: thời gian, giá, khối lượng, chiều mua/bán.
    Gọi TRỰC TIẾP REST API VCI (~0.1s) thay vì thư viện vnstock; fallback nếu lỗi.
    """
    symbol_upper = symbol.upper().strip()
    safe_limit = max(1, min(int(limit or 50), 200))
    try:
        cursor = int(before) if before is not None else None
    except (TypeError, ValueError):
        cursor = None
    # VCI giới hạn 100 lệnh/request; muốn nhiều hơn phải phân trang bằng `before`.
    vci_limit = min(safe_limit, 100)
    empty: list[dict[str, Any]] = []

    # Chỉ cache trang đầu: mỗi cursor là một key riêng, cache sẽ phình nếu lưu tất cả.
    cache_key = f"recent-trades:{symbol_upper}:{vci_limit}" if cursor is None else None
    if cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        response = _get_http_client().post(
            f"{_VCI_TRADING_URL}/market-watch/LEData/getAll",
            json={
                "symbol": symbol_upper,
                "limit": vci_limit,
                # VCI trả các lệnh có truncTime < mốc này ⇒ +1 để bao gồm cả giây `before`.
                "truncTime": cursor + 1 if cursor is not None else None,
            },
            headers=_VCI_TRADING_HEADERS,
            timeout=httpx.Timeout(_VCI_TRADING_TIMEOUT, connect=10.0),
        )
        data = response.json()
        rows = data if isinstance(data, list) else []
        trades = _parse_vci_intraday(symbol_upper, rows)
        if trades:
            if cache_key:
                cache.set(cache_key, trades, ttl_seconds=15)
            return trades
    except Exception:
        pass

    if cursor is not None:
        # Fallback vnstock không hỗ trợ cursor ⇒ không dùng cho các trang sau.
        return empty
    fallback = _recent_trades_vnstock(symbol_upper, safe_limit)
    if fallback:
        cache.set(cache_key, fallback, ttl_seconds=15)
        return fallback
    return empty


def _parse_vci_intraday(symbol_upper: str, rows: list[Any]) -> list[dict[str, Any]]:
    """Parse rows VCI LEData/getAll -> [{symbol,id,ts,time,price,volume,match_type,side}]."""
    trades: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _to_float(row.get("matchPrice"))
        if price <= 0:
            continue
        try:
            ts = int(row.get("truncTime") or 0)
        except (TypeError, ValueError):
            ts = 0
        match_type = str(row.get("matchType") or "")
        trades.append(
            {
                "symbol": symbol_upper,
                "id": str(row.get("id") or ""),
                "ts": ts,
                "time": (
                    datetime.fromtimestamp(ts, _VN_TZ).strftime("%H:%M:%S")
                    if ts
                    else str(row.get("truncTime") or "")
                ),
                "price": price,
                "volume": int(_to_float(row.get("matchVol"))),
                "match_type": match_type,
                "side": "B" if match_type.lower().startswith("b") else "S",
            }
        )
    trades.sort(key=lambda t: t["ts"], reverse=True)
    return trades


def _recent_trades_vnstock(symbol_upper: str, safe_limit: int) -> list[dict[str, Any]]:
    """Fallback Giao dịch gần nhất qua thư viện vnstock (VCI)."""
    try:
        from vnstock import Quote  # lazy import

        quote = Quote(source="VCI", symbol=symbol_upper, show_log=False)
        df = quote.intraday(page_size=safe_limit)
        if df is None or df.empty:
            return []
        trades: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            # VCI price từ intraday_to_df đã chia 1000 -> nhân lại về VND.
            price = _to_float(row.get("price")) * 1000
            if price <= 0:
                continue
            time_raw = row.get("time")
            time_label = ""
            if time_raw is not None:
                try:
                    time_label = time_raw.strftime("%H:%M:%S")
                except Exception:
                    time_label = str(time_raw)
            match_type = str(row.get("match_type") or "")
            trades.append(
                {
                    "symbol": symbol_upper,
                    "id": "",
                    "ts": 0,
                    "time": time_label,
                    "price": price,
                    "volume": int(_to_float(row.get("volume"))),
                    "match_type": match_type,
                    "side": "B" if match_type.lower().startswith("b") else "S",
                }
            )
        trades.sort(key=lambda t: t["time"], reverse=True)
        trades = trades[:safe_limit]
        # Fallback không có id từ VCI → tạo id nội bộ để client loại trùng.
        for idx, trade in enumerate(trades):
            trade["id"] = f"fb-{trade['time']}-{trade['price']}-{trade['volume']}-{idx}"
        return trades
    except Exception:
        return []


_MONEY_FLOW_DEFAULT_SESSIONS = 10
_MONEY_FLOW_MAX_SESSIONS = 60


def _vn_session_date(value: Any) -> str:
    """ISO time → 'YYYY-MM-DD' theo giờ VN (bar daily của Simplize là 00:00 UTC)."""
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_VN_TZ).strftime("%Y-%m-%d")


def _money_flow_row(bar: dict[str, Any], prev_close: float) -> dict[str, Any]:
    """Một phiên dòng tiền: tách giá trị giao dịch thành tiền VÀO / RA theo CLV."""
    high = _to_float(bar.get("high"))
    low = _to_float(bar.get("low"))
    close = _to_float(bar.get("close"))
    volume = max(0.0, _to_float(bar.get("volume")))
    value = (high + low + close) / 3.0 * volume if volume > 0 else 0.0
    if high > low:
        clv = ((close - low) - (high - close)) / (high - low)
    else:
        clv = 0.0
    clv = max(-1.0, min(1.0, clv))
    inflow = value * (1.0 + clv) / 2.0
    outflow = value * (1.0 - clv) / 2.0
    change_pct = ((close - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
    return {
        "date": _vn_session_date(bar.get("time")),
        "time": bar.get("time") or "",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(volume),
        "value": round(value),
        "clv": round(clv, 4),
        "inflow": round(inflow),
        "outflow": round(outflow),
        "net": round(inflow - outflow),
    }


def get_money_flow(symbol: str, sessions: int = _MONEY_FLOW_DEFAULT_SESSIONS) -> dict[str, Any]:
    """Dòng tiền ròng N phiên gần nhất — TÍNH từ OHLCV.

    VCI/SSI chỉ cung cấp giá trị mua/bán chủ động của phiên đang diễn ra (không có
    lịch sử theo phiên), nên dòng tiền được suy ra từ biến động giá trong phiên:
    - Giá trị giao dịch `value` = giá TB (H+L+C)/3 × KL.
    - Hệ số dòng tiền CLV = ((C−L)−(H−C))/(H−L) ∈ [−1, 1]: đóng cửa càng sát đỉnh
      ⇒ càng nhiều tiền VÀO, càng sát đáy ⇒ càng nhiều tiền RA.
    - Vào = value×(1+CLV)/2, Ra = value×(1−CLV)/2, Ròng = Vào − Ra.
    """
    symbol_upper = str(symbol or "").upper().strip()
    try:
        count = int(sessions)
    except (TypeError, ValueError):
        count = _MONEY_FLOW_DEFAULT_SESSIONS
    count = max(1, min(_MONEY_FLOW_MAX_SESSIONS, count))

    cache_key = f"money-flow:{symbol_upper}:{count}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    bars: list[dict[str, Any]] = []
    try:
        # "1m" → bar NGÀY (1m_d, ~22 phiên); "3m" của Simplize là bar 2 giờ, không dùng được.
        raw = get_history(symbol_upper, "1d", "1m")
        bars = [b for b in (raw or []) if isinstance(b, dict) and _to_float(b.get("close")) > 0]
    except Exception:
        bars = []

    rows: list[dict[str, Any]] = []
    prev_close = 0.0
    for bar in bars:
        rows.append(_money_flow_row(bar, prev_close))
        prev_close = _to_float(bar.get("close")) or prev_close
    items = rows[-count:]

    inflow = sum(item["inflow"] for item in items)
    outflow = sum(item["outflow"] for item in items)
    net = inflow - outflow
    total_value = inflow + outflow
    result = {
        "symbol": symbol_upper,
        "sessions": count,
        "source": "ohlcv-derived" if items else "empty",
        "items": items,
        "summary": {
            "sessions": len(items),
            "inflow": round(inflow),
            "outflow": round(outflow),
            "net": round(net),
            "ratio": round(net / total_value, 4) if total_value > 0 else 0.0,
            "inflow_sessions": sum(1 for item in items if item["net"] > 0),
            "outflow_sessions": sum(1 for item in items if item["net"] < 0),
            "avg_value": round(total_value / len(items)) if items else 0,
        },
    }
    if items:
        cache.set(cache_key, result, ttl_seconds=180)
    return result


def get_peer_comparison(symbol: str, limit: int = 10) -> dict[str, Any]:
    """Các cổ phiếu cùng ngành để so sánh Vốn hoá / Doanh thu / Lợi nhuận.

    Dữ liệu lấy từ screener dataset (đã cache): marketCapVnd (Vốn hoá),
    peRatio + profitMarginLtm để suy ra Lợi nhuận = Vốn hoá / P/E và
    Doanh thu = Lợi nhuận / biên lợi nhuận (xấp xỉ tham khảo).
    """
    symbol_upper = symbol.upper().strip()
    empty = {"symbol": symbol_upper, "industry": "", "industry_name": "", "peers": []}

    cache_key = f"peer-comparison:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    dataset = _get_screener_dataset()
    current_row = None
    for row in dataset:
        if str(row.get("ticker") or "").upper() == symbol_upper:
            current_row = row
            break
    # Mã chưa có trong screener nhưng có khai báo ngành thủ công → vẫn so sánh được.
    override = _SECTOR_OVERRIDES.get(symbol_upper) or {}
    if current_row is None and not override:
        return empty

    industry_slug = str(override.get("industry") or (current_row or {}).get("industrySlug") or "").lower()
    industry_name = str(override.get("industryName") or (current_row or {}).get("industryName") or "")
    if not industry_slug:
        return empty

    peers: list[dict[str, Any]] = []
    for row in dataset:
        ticker = str(row.get("ticker") or "").upper()
        if _override_sector_fields(ticker, row)[2] != industry_slug:
            continue
        market_cap = _to_float(row.get("marketCapVnd"))
        pe = _to_float(row.get("peRatio"))
        margin = _to_float(row.get("profitMarginLtm"))
        if market_cap <= 0:
            continue
        net_profit = (market_cap / pe) if pe > 0 else 0.0
        revenue = (net_profit / (margin / 100.0)) if margin > 0 else 0.0
        peers.append(
            {
                "symbol": ticker,
                "name": str(row.get("companyNameVi") or ticker),
                "market_cap": market_cap,
                "revenue": revenue,
                "net_profit": net_profit,
                "pe": pe,
                "is_current": ticker == symbol_upper,
            }
        )

    peers.sort(key=lambda p: (not p["is_current"], -p["market_cap"]))
    peers = peers[: int(max(1, min(int(limit or 10), 30)))]

    result = {
        "symbol": symbol_upper,
        "industry": industry_slug,
        "industry_name": industry_name,
        "peers": peers,
        "source": "simplize-screener",
    }
    cache.set(cache_key, result, ttl_seconds=1800)
    return result


def filter_stocks(
    industry: str | None = None,
    basic: str | None = None,
    valuation: str | None = None,
    risk: str | None = None,
) -> list[dict[str, Any]]:
    """Lọc cổ phiếu theo Ngành / Cơ bản / Định giá / Rủi ro.

    Dữ liệu của từng cổ phiếu được lấy từ Simplize screener API cho TOÀN BỘ
    cổ phiếu hiện có (HOSE/HNX/UPCoM), sau đó lọc theo các tiêu chí dropdown.
    """
    dataset = _get_screener_dataset()
    known_symbols = {item["symbol"] for item in _load_symbol_directory()}
    # Mã IPO mới có khai báo ngành thủ công nhưng chưa vào screener → thêm row tối thiểu.
    override_only = [sym for sym in _override_only_symbols(dataset) if sym in known_symbols]
    rows = list(dataset) + [_override_pseudo_row(sym) for sym in override_only]
    result: list[dict[str, Any]] = []

    for row in rows:
        symbol = str(row.get("ticker") or "").upper()
        if symbol not in known_symbols:
            continue
        if not _screener_matches_industry(row, industry):
            continue
        if not _screener_matches_basic(row, basic):
            continue
        if not _screener_matches_valuation(row, valuation):
            continue
        if not _screener_matches_risk(row, risk):
            continue

        built = _build_screener_stock_row(row)
        if symbol in override_only:
            built["source"] = "sector-override"
        result.append(built)

    result.sort(key=lambda item: item["symbol"])
    return result


def get_stock_fundamentals(symbol: str) -> dict[str, Any]:
    """Cơ bản / Định giá / Rủi ro của MỘT cổ phiếu (từ screener dataset đã cache).

    Trả về các điểm 0-5: growthPoint / performancePoint / financialHealthPoint /
    dividendPoint, qualityScore (0-20), valuationPoint, overallRiskLevel + pe/pb.
    """
    symbol_upper = symbol.upper().strip()
    empty = {"symbol": symbol_upper, "valid": False, "source": "simplize-screener"}
    cache_key = f"fundamentals:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    dataset = _get_screener_dataset()
    for row in dataset:
        if str(row.get("ticker") or "").upper() == symbol_upper:
            result = _build_screener_stock_row(row)
            result["valid"] = True
            cache.set(cache_key, result, ttl_seconds=1800)
            return result

    cache.set(cache_key, empty, ttl_seconds=300)
    return empty


# ---------------------------------------------------------------------------
# Danh sách cổ phiếu cho sidebar fullscreen (VN30 / HNX30 / HOSE / HNX / UPCOM / Ngành)
# ---------------------------------------------------------------------------
def get_stock_lists() -> dict[str, Any]:
    """Trả về toàn bộ vũ trụ cổ phiếu (HOSE/HNX/UPCoM) kèm nhóm sẵn cho sidebar:
    - stocks : mọi mã (symbol, name, exchange, price, percent_change, sector, sectorName, market_cap)
    - vn30   : ticker trong _VN30_TICKERS
    - hnx30  : TOP 30 vốn hóa sàn HNX (proxy HNX30 — screener có marketCapVnd)
    - hose/hnx/upcom : theo sàn
    - sectors: [{slug, name, stocks[]}] (nhóm ngành economicSector)

    KHÔNG hard-code danh sách ngoài _VN30_TICKERS đã có — HNX30 dùng top-30 vốn hóa
    thay vì danh sách thành viên chính thức (nguồn hiện không cung cấp index membership).
    """
    key = "stock-lists"
    cached = cache.get(key)
    if cached is not None:
        return cached

    dataset = _get_screener_dataset()
    known_symbols = {item["symbol"] for item in _load_symbol_directory()}
    vn30_set = set(_VN30_TICKERS)

    stocks: list[dict[str, Any]] = []
    hnx_by_cap: list[tuple[float, dict[str, Any]]] = []
    sectors_map: dict[str, dict[str, Any]] = {}

    for row in dataset:
        symbol = str(row.get("ticker") or "").upper()
        if symbol not in known_symbols:
            continue
        exchange = str(row.get("stockExchange") or "").upper()
        if exchange not in ("HOSE", "HNX", "UPCOM"):
            continue
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        pct = _to_float(row.get("pricePctChgIntraDay"))
        cap = _to_float(row.get("marketCapVnd"))
        sector, sector_name, _industry, _industry_name = _override_sector_fields(symbol, row)
        item = {
            "symbol": symbol,
            "name": str(row.get("companyNameVi") or symbol),
            "exchange": exchange,
            "price": round(price, 2),
            "percent_change": round(pct, 2),
            "sector": sector,
            "sectorName": sector_name,
            "market_cap": cap,
        }
        if symbol in _SECTOR_OVERRIDES:
            item["sector_override"] = True
        stocks.append(item)
        if exchange == "HNX":
            hnx_by_cap.append((cap, item))
        if sector not in sectors_map:
            sectors_map[sector] = {"slug": sector, "name": sector_name, "stocks": []}
        sectors_map[sector]["stocks"].append(symbol)

    # Mã IPO mới khai báo ngành thủ công nhưng CHƯA có trong screener → bổ sung vào danh sách.
    for symbol in _override_only_symbols(dataset):
        if symbol not in known_symbols:
            continue
        meta = _lookup_symbol_metadata(symbol)
        exchange = _EXCHANGE_CANONICAL.get(str(meta.get("exchange") or "").upper(), "")
        if exchange not in ("HOSE", "HNX", "UPCOM"):
            continue
        last = _override_live_quote(symbol)
        override = _SECTOR_OVERRIDES.get(symbol) or {}
        sector = str(override.get("sector") or "").lower()
        sector_name = str(override.get("sectorName") or "Khác")
        item = {
            "symbol": symbol,
            "name": str(last.get("name") or meta.get("name") or symbol),
            "exchange": exchange,
            "price": round(_to_float(last.get("price")), 2),
            "percent_change": round(_to_float(last.get("pctChange")), 2),
            "sector": sector,
            "sectorName": sector_name,
            "market_cap": 0.0,
            "sector_override": True,
        }
        stocks.append(item)
        if sector not in sectors_map:
            sectors_map[sector] = {"slug": sector, "name": sector_name, "stocks": []}
        sectors_map[sector]["stocks"].append(symbol)

    stocks.sort(key=lambda s: s["symbol"])
    hnx_by_cap.sort(key=lambda x: x[0] or 0, reverse=True)
    hnx30 = [item["symbol"] for _, item in hnx_by_cap[:30]]
    vn30 = [s["symbol"] for s in stocks if s["symbol"] in vn30_set]

    sectors = sorted(sectors_map.values(), key=lambda x: x["name"])
    for sec in sectors:
        if not sec["name"]:
            sec["name"] = "Khác"

    payload = {
        "stocks": stocks,
        "vn30": vn30,
        "hnx30": hnx30,
        "hose": [s["symbol"] for s in stocks if s["exchange"] == "HOSE"],
        "hnx": [s["symbol"] for s in stocks if s["exchange"] == "HNX"],
        "upcom": [s["symbol"] for s in stocks if s["exchange"] == "UPCOM"],
        "sectors": sectors,
    }
    cache.set(key, payload, ttl_seconds=60)
    return payload


_INDEX_CARD_KEYS: list[tuple[str, str]] = [
    ("vnindex", "VN-INDEX"),
    ("vn30", "VN30"),
    ("hnx", "HNX-INDEX"),
    ("upcom", "UPCOM"),
]


@_singleflight(lambda: "market-dashboard", ttl_seconds=60, stale_while_revalidate=True)
def get_market_dashboard() -> dict[str, Any]:
    """Toàn cảnh thị trường cho trang "Thị trường" (dashboard).

    Gom: 4 chỉ số chính (VNINDEX/VN30/HNX/UPCOM + history để vẽ mini chart),
    độ rộng thị trường (tăng/giảm/tham chiếu + trần/sàn), dòng tiền (GTGD, khối ngoại,
    thanh khoản), hiệu suất ngành, radar (khối ngoại bán mạnh / khối ngoại mua mạnh /
    breakout / volume lạ / nhóm suy yếu) và top cổ phiếu ảnh hưởng VN-INDEX.
    Cache 60s + stale-while-revalidate (hết hạn trả bản cũ ngay, refresh nền).
    """
    # 1) Bốn chỉ số chính
    indices: list[dict[str, Any]] = []
    for k, label in _INDEX_CARD_KEYS:
        try:
            d = get_index_detail(k, "1y")
            hist = d.get("history") or []
            indices.append(
                {
                    "key": k,
                    "label": label,
                    "value": d.get("value"),
                    "change": d.get("change"),
                    "percent_change": d.get("percent_change"),
                    "as_of": d.get("as_of", ""),
                    "history": [
                        {"time": b.get("time"), "close": b.get("close")} for b in hist[-60:]
                    ],
                }
            )
        except Exception:
            indices.append({"key": k, "label": label, "value": 0, "change": 0, "percent_change": 0, "history": []})

    # 2) Độ rộng + trần/sàn + top tăng/giảm/volume từ market-map
    breadth: dict[str, Any] = {"up": 0, "down": 0, "flat": 0, "ceiling": 0, "floor": 0, "total": 0}
    cells: list[dict[str, Any]] = []
    try:
        cells = (get_market_map() or {}).get("cells") or []
    except Exception:
        cells = []
    for c in cells:
        st = c.get("status")
        if st == "ceiling":
            breadth["ceiling"] += 1
            breadth["up"] += 1
        elif st == "floor":
            breadth["floor"] += 1
            breadth["down"] += 1
        elif st == "up":
            breadth["up"] += 1
        elif st == "down":
            breadth["down"] += 1
        else:
            breadth["flat"] += 1
    breadth["total"] = len(cells)

    # Block "Market Radar" (trang chủ) & "VinaFintech Market Radar" (trang Thị trường) CHỈ lấy mã
    # VỐN HOÁ LỚN + THANH KHOẢN đủ để giao dịch hằng ngày (bộ lọc dùng chung).
    tradable = tradable_symbols()
    radar_cells = [c for c in cells if str(c.get("symbol") or "").upper() in tradable]
    gainers = [c for c in radar_cells if c.get("percent_change", 0) > 0]
    losers = [c for c in radar_cells if c.get("percent_change", 0) < 0]
    breakout = sorted(gainers, key=lambda c: c["percent_change"], reverse=True)[:8]
    weak = sorted(losers, key=lambda c: c["percent_change"])[:8]
    volume_spike = sorted(radar_cells, key=lambda c: c.get("volume", 0), reverse=True)[:8]

    # 3) Dòng tiền
    flow: dict[str, Any] = {
        "gtgd_today": None,
        "gtgd_yesterday": None,
        "foreign_net": None,
        "foreign_buy_top": [],
        "foreign_sell_top": [],
        "liquidity": {},
        "ceiling_count": breadth["ceiling"],
        "floor_count": breadth["floor"],
        "self_trading": None,  # nguồn tự doanh chưa có
        "trend": {},
    }
    try:
        ec = get_exchange_chart("HOSE", "5d")
        t = ec.get("today") or []
        y = ec.get("yesterday") or []
        if t:
            flow["gtgd_today"] = t[-1].get("close")
        if y:
            flow["gtgd_yesterday"] = y[-1].get("close")
    except Exception:
        pass
    try:
        ft = get_foreign_trading("foreign", "1D")
        flow["foreign_net"] = (ft.get("totals") or {}).get("net")
        # Danh sách mã hiển thị trong block Market Radar: CHỈ giữ mã VỐN HOÁ LỚN +
        # THANH KHOẢN đủ để giao dịch hằng ngày (bộ lọc dùng chung).
        # Mỗi chiều giữ 8 mã mạnh nhất: khối ngoại mua mạnh / khối ngoại bán mạnh.
        flow["foreign_buy_top"] = [
            it for it in (ft.get("buy_list") or []) if str(it.get("symbol") or "").upper() in tradable
        ][:8]
        flow["foreign_sell_top"] = [
            it for it in (ft.get("sell_list") or []) if str(it.get("symbol") or "").upper() in tradable
        ][:8]
    except Exception:
        pass
    try:
        d = get_index_detail("vnindex", "1y")
        hist = d.get("history") or []
        closes = [float(b.get("close")) for b in hist if b.get("close") is not None]
        vols = [float(b.get("volume") or 0) for b in hist if b.get("volume") is not None]

        def _chg(back: int) -> float:
            base = closes[-(back + 1)] if len(closes) > back else closes[0]
            return ((closes[-1] / base) - 1.0) * 100.0 if base else 0.0

        if len(closes) >= 2:
            c1m, c3m = _chg(21), _chg(63)
            flow["trend"] = {
                "1m": round(c1m, 2),
                "3m": round(c3m, 2),
                "6m": round(_chg(126), 2),
                "label": "tăng" if (c1m > 0 and c3m > 0) else ("giảm" if (c1m < 0 and c3m < 0) else "đi ngang"),
            }
        if len(vols) >= 2:
            window = vols[-21:] or vols
            avg = sum(window) / len(window)
            flow["liquidity"] = {
                "last_volume": int(vols[-1]),       # thanh khoản phiên hôm nay (khối lượng)
                "prev_volume": int(vols[-2]),       # thanh khoản phiên hôm qua (khối lượng)
                "avg_volume_1m": int(round(avg)),
                "volume_ratio": round(vols[-1] / avg, 2) if avg else 0,
            }
    except Exception:
        pass

    # 4) Hiệu suất ngành + top ảnh hưởng VN-INDEX từ stock-lists
    sectors: list[dict[str, Any]] = []
    impact: list[dict[str, Any]] = []
    try:
        sl = get_stock_lists()
        stocks = sl.get("stocks") or []
        sec_map: dict[str, dict[str, Any]] = {}
        for s in stocks:
            slug = str(s.get("sector") or "khac")
            pct = float(s.get("percent_change") or 0)
            cap = float(s.get("market_cap") or 0)
            sec = sec_map.setdefault(
                slug,
                {"slug": slug, "name": s.get("sectorName") or "Khác", "sum": 0.0, "n": 0, "up": 0, "down": 0, "top_gainer": None, "top_pct": -999.0,
                 "top_tradable": None, "top_tradable_pct": -999.0},
            )
            sec["sum"] += pct
            sec["n"] += 1
            if pct > 0:
                sec["up"] += 1
            elif pct < 0:
                sec["down"] += 1
            if pct > sec["top_pct"]:
                sec["top_pct"] = pct
                sec["top_gainer"] = s.get("symbol")
            # Mã "đại diện" của ngành hiển thị trong block Market Radar: ưu tiên mã VỐN HOÁ
            # LỚN + THANH KHOẢN đủ (vẫn giữ mã dẫn dắt nếu ngành không có mã nào đủ điều kiện).
            if str(s.get("symbol") or "").upper() in tradable and pct > sec["top_tradable_pct"]:
                sec["top_tradable_pct"] = pct
                sec["top_tradable"] = s.get("symbol")
            impact.append(
                {"symbol": s.get("symbol"), "name": s.get("name"), "percent_change": pct, "market_cap": cap, "impact": abs(pct * cap)}
            )
        for slug, sec in sec_map.items():
            sectors.append(
                {
                    "slug": slug,
                    "name": sec["name"],
                    "avg_pct": round(sec["sum"] / sec["n"], 2) if sec["n"] else 0.0,
                    "up_count": sec["up"],
                    "down_count": sec["down"],
                    "total": sec["n"],
                    "top_gainer": sec["top_tradable"] or sec["top_gainer"],
                }
            )
        sectors.sort(key=lambda x: x["avg_pct"], reverse=True)
        impact.sort(key=lambda x: x["impact"], reverse=True)
        impact = [
            {**it, "impact": round(it["impact"] / 1e9, 2)} for it in impact[:15]
        ]
    except Exception:
        pass

    # 4b) Tín hiệu kỹ thuật toàn thị trường cho block "Market Radar":
    #     - breakout_tech     : giá sát đỉnh 4 tuần (>=97%) + đang tăng + volume >= 1.2x TB 10 phiên
    #     - risk_signal       : giá dưới đỉnh 4 tuần >=10% + xu hướng ngắn hạn âm
    #     - volume_spike_ratio: volume >= 1.5x TB 10 phiên ("bất thường" thật, không phải KL tuyệt đối)
    #     Lọc sàn HOSE/HNX/UPCOM + mã VỐN HOÁ LỚN/THANH KHOẢN đủ để giao dịch hằng ngày.
    #     Dữ liệu lấy từ screener Simplize đã cache (không thêm request mạng).
    breakout_tech: list[dict[str, Any]] = []
    risk_signal: list[dict[str, Any]] = []
    volume_spike_ratio: list[dict[str, Any]] = []
    try:
        _min_volume = 100_000  # 100.000 cp/phiên
        tech: list[dict[str, Any]] = []
        for row in _get_screener_dataset():
            symbol = str(row.get("ticker") or "").upper()
            if str(row.get("stockExchange") or "").upper() not in ("HOSE", "HNX", "UPCOM"):
                continue
            if symbol not in tradable:
                continue
            price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
            peak4w = _to_float(row.get("peak4week"))
            volume = _to_float(row.get("volume"))
            volume10 = _to_float(row.get("volume10dAvg"))
            if price <= 0 or peak4w <= 0:
                continue
            tech.append(
                {
                    "symbol": symbol,
                    "name": str(row.get("companyNameVi") or ""),
                    "price": round(price, 2),
                    "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
                    "pct_short_term": round(_to_float(row.get("pctChgShortTerm")), 2),
                    "volume": int(volume),
                    "volume_ratio": round(volume / volume10, 2) if volume10 else 0.0,
                    "pct_of_peak4w": round(price / peak4w * 100, 1),
                }
            )

        # Mỗi nhóm giữ BẢN COPY riêng — một mã có thể nằm ở nhiều nhóm (VD vừa breakout
        # vừa volume đột biến) và note của nhóm này sẽ ghi đè note của nhóm kia nếu dùng chung dict.
        breakout_tech = [
            dict(t)
            for t in sorted(
                [t for t in tech if t["pct_of_peak4w"] >= 97.0 and t["percent_change"] > 0 and t["volume_ratio"] >= 1.2],
                key=lambda t: t["volume_ratio"],
                reverse=True,
            )[:5]
        ]
        for t in breakout_tech:
            t["note"] = f"Sát đỉnh 4 tuần ({t['pct_of_peak4w']:.0f}%) · volume {t['volume_ratio']:.1f}× TB"

        risk_signal = [
            dict(t)
            for t in sorted(
                [t for t in tech if t["pct_of_peak4w"] <= 90.0 and t["pct_short_term"] < 0],
                key=lambda t: t["pct_short_term"],
            )[:5]
        ]
        for t in risk_signal:
            t["note"] = f"Dưới đỉnh 4 tuần {100 - t['pct_of_peak4w']:.0f}% · ngắn hạn {t['pct_short_term']:+.1f}%"

        volume_spike_ratio = [
            dict(t)
            for t in sorted(
                [t for t in tech if t["volume"] >= _min_volume and t["volume_ratio"] >= 1.5],
                key=lambda t: t["volume_ratio"],
                reverse=True,
            )[:5]
        ]
        for t in volume_spike_ratio:
            t["note"] = f"Volume {t['volume_ratio']:.1f}× TB 10 phiên"
    except Exception:
        pass

    hot_sector = None
    for sec in sectors:
        if sec["total"] >= 5:
            hot_sector = sec
            break

    payload: dict[str, Any] = {
        "as_of": indices[0].get("as_of", "") if indices else "",
        "indices": indices,
        "breadth": breadth,
        "flow": flow,
        "sectors": sectors,
        "radar": {
            "hot_sector": hot_sector,
            "breakout": breakout,
            "volume_spike": volume_spike,
            "weak": weak,
            "foreign_buy": flow.get("foreign_buy_top", []),
            "foreign_sell": flow.get("foreign_sell_top", []),
            "breakout_tech": breakout_tech,
            "risk_signal": risk_signal,
            "volume_spike_ratio": volume_spike_ratio,
        },
        "impact": impact,
    }
    return payload


# Trọng số điểm cho VinaFintech Score (0-5 mỗi nhóm, tổng tối đa 25 → 0-100).
_SCORE_RISK_ADJUST = {"low": 0, "medium": -3, "high": -6, "extreme": -9}


def _compute_vinafintech_score(row: dict[str, Any]) -> int:
    growth = _to_float(row.get("growthPoint"))
    perf = _to_float(row.get("passPerformancePoint"))
    fh = _to_float(row.get("financialHealthPoint"))
    div = _to_float(row.get("dividendPoint"))
    val = _to_float(row.get("valuationPoint"))
    base = (growth + perf + fh + div + val) / 25.0 * 100.0
    risk_adj = _SCORE_RISK_ADJUST.get(str(row.get("overallRiskLevel") or "").lower(), 0)
    return int(max(0, min(100, round(base + risk_adj))))


def get_stock_screener_page() -> dict[str, Any]:
    """Dữ liệu trang "Cổ phiếu": toàn bộ screener (định giá/tăng trưởng/điểm VinaFintech)
    + các nhóm "Cổ phiếu nổi bật" (tăng trưởng / định giá hấp dẫn / dòng tiền mạnh /
    breakout / VinaFintech Score cao). Không cache toàn trang (dataset đã cache riêng) để
    ROE nạp nền hiển thị ngay.
    """
    dataset = _get_screener_dataset()
    stocks: list[dict[str, Any]] = []
    # ROE bổ sung từ symbol summary (cache file) — sẽ được nạp nền phía dưới cho top score
    summary_map = _read_symbol_summary_cache_file()
    if summary_map is None:
        summary_map = {}
    for row in dataset:
        symbol = str(row.get("ticker") or "").upper()
        exchange = str(row.get("stockExchange") or "").upper()
        if exchange not in ("HOSE", "HNX", "UPCOM"):
            continue
        vol = _to_float(row.get("volume"))
        vol10 = _to_float(row.get("volume10dAvg"))
        sector, sector_name, industry, industry_name = _override_sector_fields(symbol, row)
        item = {
            "symbol": symbol,
            "name": str(row.get("companyNameVi") or symbol),
            "exchange": exchange,
            "sector": sector,
            "sectorName": sector_name,
            "industry": industry,
            "industryName": industry_name,
            "price": round(_to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose")), 2),
            "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
            "change_7d": round(_to_float(row.get("pricePctChg7d")), 2),
            "change_1y": round(_to_float(row.get("pricePctChg1y")), 2),
            "roe": round(_to_float((summary_map.get(symbol) or {}).get("roe")), 2),
            "market_cap": _to_float(row.get("marketCapVnd")),
            "pe_ratio": _to_float(row.get("peRatio")),
            "pb_ratio": _to_float(row.get("pbRatio")),
            "ev_ebitda": _to_float(row.get("evEbitdaRatio")),
            "dividend_yield": _to_float(row.get("dividendYieldCurrent")),
            "revenue_growth": _to_float(row.get("revenue5yGrowth")),
            "net_profit_growth": _to_float(row.get("netIncome5yGrowth")),
            "eps_growth": _to_float(row.get("netIncomeEstGrowth")),
            "volume": int(vol),
            "volume_ratio": round(vol / vol10, 2) if vol10 else 0,
            "risk": str(row.get("overallRiskLevel") or "").lower(),
            "points": {
                "growth": _to_float(row.get("growthPoint")),
                "performance": _to_float(row.get("passPerformancePoint")),
                "financial_health": _to_float(row.get("financialHealthPoint")),
                "dividend": _to_float(row.get("dividendPoint")),
                "valuation": _to_float(row.get("valuationPoint")),
                "quality": round(
                    _to_float(row.get("growthPoint"))
                    + _to_float(row.get("passPerformancePoint"))
                    + _to_float(row.get("financialHealthPoint"))
                    + _to_float(row.get("dividendPoint"))
                    + _to_float(row.get("valuationPoint")),
                    1,
                ),
            },
        }
        item["score"] = _compute_vinafintech_score(row)
        if symbol in _SECTOR_OVERRIDES:
            item["sector_override"] = True
        stocks.append(item)

    # Mã IPO mới khai báo ngành thủ công nhưng CHƯA có trong screener → bổ sung item
    # tối thiểu (tên/sàn/ngành + giá realtime nếu có) để mã xuất hiện đúng nhóm ngành.
    # LƯU Ý: tên/sàn lấy từ symbol directory → nên chạy "Quét & thêm cổ phiếu mới" trước.
    for symbol in _override_only_symbols(dataset):
        override = _SECTOR_OVERRIDES.get(symbol) or {}
        meta = _lookup_symbol_metadata(symbol)
        last = _override_live_quote(symbol)
        stocks.append(
            {
                "symbol": symbol,
                "name": str(last.get("name") or meta.get("name") or symbol),
                "exchange": _EXCHANGE_CANONICAL.get(str(meta.get("exchange") or "").upper(), ""),
                "sector": str(override.get("sector") or "").lower(),
                "sectorName": str(override.get("sectorName") or ""),
                "industry": str(override.get("industry") or "").lower(),
                "industryName": str(override.get("industryName") or ""),
                "price": round(_to_float(last.get("price")), 2),
                "percent_change": round(_to_float(last.get("pctChange")), 2),
                "change_7d": 0,
                "change_1y": 0,
                "roe": 0.0,
                "market_cap": 0.0,
                "pe_ratio": 0.0,
                "pb_ratio": 0.0,
                "ev_ebitda": 0.0,
                "dividend_yield": 0.0,
                "revenue_growth": 0.0,
                "net_profit_growth": 0.0,
                "eps_growth": 0.0,
                "volume": 0,
                "volume_ratio": 0,
                "risk": "",
                "points": {
                    "growth": 0.0,
                    "performance": 0.0,
                    "financial_health": 0.0,
                    "dividend": 0.0,
                    "valuation": 0.0,
                    "quality": 0.0,
                },
                "score": 0,
                "sector_override": True,
                "source": "sector-override",
            }
        )

    stocks.sort(key=lambda s: s["score"], reverse=True)

    # Nạp nền ROE (symbol summary) cho top 80 theo VinaFintech Score — bảng mặc định hiện top score
    try:
        if len(summary_map) < 100 and cache.get("screener-summary-enrich") is None:
            cache.set("screener-summary-enrich", 1, ttl_seconds=600)
            top_syms = [s["symbol"] for s in stocks[:80]]

            def _fill_summaries() -> None:
                def _load(tk: str) -> None:
                    try:
                        _get_symbol_summary(tk)
                    except Exception:
                        pass

                with ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(_load, top_syms))

            threading.Thread(target=_fill_summaries, daemon=True).start()
    except Exception:
        pass

    def _pick(key: str, reverse: bool = True, n: int = 8, cond=None) -> list[dict[str, Any]]:
        arr = stocks if cond is None else [s for s in stocks if cond(s)]
        return sorted(arr, key=lambda s: s.get(key) if isinstance(s.get(key), (int, float)) else -999999.0, reverse=reverse)[:n]

    categories: dict[str, list[dict[str, Any]]] = {
        "growth": _pick("net_profit_growth", cond=lambda s: s["net_profit_growth"] > 0),
        "value": sorted(
            [s for s in stocks if s["pe_ratio"] > 0 and s["pb_ratio"] > 0],
            key=lambda s: (s["pe_ratio"] + s["pb_ratio"]) + (s["ev_ebitda"] if s["ev_ebitda"] > 0 else 10),
        )[:8],
        "cash_flow": sorted(
            stocks, key=lambda s: s["points"]["financial_health"] * 2 + s["points"]["dividend"] + (1 if s["dividend_yield"] > 0 else 0), reverse=True
        )[:8],
        "breakout": _pick("percent_change", cond=lambda s: s["percent_change"] > 0),
        "score": stocks[:8],
    }

    # Mốc thời gian dữ liệu (phiên realtime) — lấy từ market dashboard (VNINDEX).
    try:
        as_of = get_market_dashboard().get("as_of", "")
    except Exception:  # noqa: BLE001
        as_of = ""

    payload: dict[str, Any] = {
        "count": len(stocks),
        "as_of": as_of,
        "stocks": stocks,
        "categories": categories,
        "examples": ["ACB", "VCI", "FPT", "HPG", "MBB"],
    }
    return payload


def get_top_stocks_by_sector(sector: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return stocks in a sector ordered by market capitalization."""
    sector_slug = str(sector or "").strip().lower()
    if not sector_slug:
        return []

    summary_map = _read_symbol_summary_cache_file() or {}
    stocks: list[dict[str, Any]] = []
    for row in _get_screener_dataset():
        symbol = str(row.get("ticker") or "").strip().upper()
        exchange = str(row.get("stockExchange") or "").strip().upper()
        if not symbol or exchange not in ("HOSE", "HNX", "UPCOM"):
            continue

        sector_key, sector_name, industry_key, industry_name = _override_sector_fields(symbol, row)
        market_cap = _to_float(row.get("marketCapVnd"))
        price = _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        volume = int(_to_float(row.get("volume")))
        summary = summary_map.get(symbol) or {}
        matches_sector = (
            industry_key == "tai-chinh-ngan-hang"
            if sector_slug == "ngan-hang"
            else industry_key == "chung-khoan-va-ngan-hang-dau-tu"
            if sector_slug == "chung-khoan"
            else sector_key == sector_slug
        )
        if not matches_sector or market_cap <= 0:
            continue

        stocks.append({
            "symbol": symbol,
            "name": str(row.get("companyNameVi") or symbol),
            "exchange": exchange,
            "sector": sector_slug,
            "sectorName": "Ngân hàng" if sector_slug == "ngan-hang" else sector_name,
            "industry": industry_key,
            "industryName": industry_name,
            "price": round(price, 2),
            "percent_change": round(_to_float(row.get("pricePctChgIntraDay")), 2),
            "market_cap": market_cap,
            "volume": volume,
            "liquidity": volume * price,
            "pe_ratio": _to_float(row.get("peRatio")),
            "pb_ratio": _to_float(row.get("pbRatio")),
            "roe": _to_float(summary.get("roe")),
            "roa": _to_float(summary.get("roa")),
        })

    stocks.sort(key=lambda stock: stock["market_cap"], reverse=True)
    return stocks[:max(1, min(int(limit), 20))]


# ---------------------------------------------------------------------------
# XẾP HẠNG VinaFintech (block "Xếp hạng VinaFintech" — trang "Khám phá")
# ---------------------------------------------------------------------------
# 5 bảng × 8 dòng. Thứ hạng + chỉ số cơ bản (tăng trưởng LN, P/E, cổ tức, VinaFintech
# Score, EPS growth) lấy từ dataset screener (cập nhật theo phiên). Riêng
# GIÁ / % THAY ĐỔI được làm mới từ bảng giá realtime (Simplize last-price) ⇒
# bảng xếp hạng ĐỘNG, tự cập nhật trong phiên giao dịch.
_RANK_RENDER_ROWS = 8
# Số mã đầu mỗi bảng được lấy giá realtime rồi xếp lại (đủ rộng để thứ hạng đúng
# khi giá biến động trong phiên nhưng vẫn nhẹ: ≤ 5 × 30 mã/lần làm mới).
_RANK_REALTIME_WINDOW = 30
# TTL bản xếp hạng realtime: ngắn để giá bám sát bảng giá, đủ dài để không spam nguồn.
_RANK_LIVE_CACHE_TTL_SECONDS = 20
_RANK_SCREENER_STOCKS_TTL_SECONDS = 120
# Bảng "Top định giá" xếp theo P/E realtime (giá đổi trong phiên ⇒ P/E đổi).
_RANK_TAB_META: list[tuple[str, str, str, str]] = [
    ("growth", "Top tăng trưởng", "Tăng trưởng LN", "net_profit_growth"),
    ("valuation", "Top định giá", "P/E", "pe_ratio"),
    ("cash_flow", "Top dòng tiền", "Sức khỏe TC", "dividend_yield"),
    ("score", "Top Score", "VinaFintech Score", "score"),
    ("improve", "Top cải thiện", "Tăng trưởng dự kiến", "eps_growth"),
]


def _rank_candidate_pools(stocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """5 nhóm ứng viên cho 5 bảng xếp hạng (đã sắp xếp theo chỉ số của chính bảng)."""
    return {
        "growth": sorted(
            [s for s in stocks if s["net_profit_growth"] and s["net_profit_growth"] > 0],
            key=lambda s: s["net_profit_growth"],
            reverse=True,
        ),
        "valuation": sorted(
            [s for s in stocks if s["pe_ratio"] and s["pe_ratio"] > 0 and s["pb_ratio"] and s["pb_ratio"] > 0],
            key=lambda s: (s["pe_ratio"] + s["pb_ratio"]) + (s["ev_ebitda"] if s["ev_ebitda"] and s["ev_ebitda"] > 0 else 10),
        ),
        "cash_flow": sorted(
            stocks,
            key=lambda s: s["points"]["financial_health"] * 2 + s["points"]["dividend"] + (1 if s["dividend_yield"] and s["dividend_yield"] > 0 else 0),
            reverse=True,
        ),
        "score": sorted(stocks, key=lambda s: s["score"], reverse=True),
        "improve": sorted(
            [s for s in stocks if s["eps_growth"] and s["eps_growth"] > 0],
            key=lambda s: s["eps_growth"],
            reverse=True,
        ),
    }


# Mốc thời gian (ISO) của lần lấy bảng giá realtime gần nhất — dùng cho as_of.
_rank_quotes_as_of: str = ""


def _rank_live_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Bảng giá realtime (Simplize last-price) cho danh sách mã — chia lô 120 mã/request."""
    global _rank_quotes_as_of
    uniq = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    if not uniq:
        return {}
    out: dict[str, dict[str, Any]] = {}
    chunks = [uniq[i : i + 120] for i in range(0, len(uniq), 120)]
    with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as pool:
        futures = [pool.submit(_simplize_last, "stock", chunk) for chunk in chunks]
        for future in as_completed(futures):
            try:
                out.update(future.result() or {})
            except Exception:
                continue
    if out:
        latest = max((_to_float(row.get("date"), 0) for row in out.values()), default=0)
        if latest > 0:
            _rank_quotes_as_of = _unix_to_iso(latest)
    return out


def _build_vinafintech_rankings(stocks: list[dict[str, Any]], quote_loader=None) -> list[dict[str, Any]]:
    """Dựng 5 bảng xếp hạng VinaFintech từ danh sách cổ phiếu (screener).

    `quote_loader`: hàm nhận list mã → dict bảng giá realtime. Khi truyền vào, giá và
    % thay đổi của mọi dòng được làm mới theo bảng giá, đồng thời bảng "Top định giá"
    được xếp lại theo P/E realtime (P/E = giá realtime / EPS, EPS suy từ snapshot
    screener) ⇒ xếp hạng đổi theo thị trường ngay trong phiên.
    Không truyền `quote_loader` → hành vi cũ (dùng snapshot screener).
    """
    # "Xếp hạng VinaFintech" CHỈ lấy mã VỐN HOÁ LỚN + THANH KHOẢN đủ để giao dịch hằng ngày
    # (bộ lọc dùng chung với Market Radar / Khám phá theo chiến lược).
    stocks = [s for s in stocks if is_tradable_stock(s)]
    pools = _rank_candidate_pools(stocks)

    quotes: dict[str, dict[str, Any]] = {}
    if quote_loader is not None:
        candidates: list[str] = []
        for arr in pools.values():
            candidates.extend(s["symbol"] for s in arr[:_RANK_REALTIME_WINDOW])
        try:
            quotes = quote_loader(candidates) or {}
        except Exception:
            quotes = {}

    def _live_quote(s: dict[str, Any]) -> tuple[float, float, bool]:
        """(giá, % thay đổi, là dữ liệu realtime?) — fallback về snapshot screener."""
        q = quotes.get(s["symbol"]) or {}
        price = _to_float(q.get("price")) if q else 0.0
        if price > 0:
            pct_raw = q.get("pctChange")
            pct = _to_float(pct_raw) if pct_raw is not None else _to_float(s["percent_change"])
            return price, pct, True
        return _to_float(s["price"]), _to_float(s["percent_change"]), False

    def _live_pe(s: dict[str, Any]) -> float:
        """P/E realtime = giá realtime / EPS; EPS suy từ (giá snapshot / P/E snapshot)."""
        pe_snap = _to_float(s.get("pe_ratio"))
        price_snap = _to_float(s.get("price"))
        price_live, _, is_live = _live_quote(s)
        if is_live and pe_snap > 0 and price_snap > 0 and price_live > 0:
            eps = price_snap / pe_snap
            if eps > 0:
                return round(price_live / eps, 2)
        return pe_snap

    def _rows(key: str, pool: list[dict[str, Any]], value_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for s in pool[:_RANK_RENDER_ROWS]:
            price, pct, is_live = _live_quote(s)
            out.append(
                {
                    "symbol": s["symbol"],
                    "name": s["name"],
                    "price": round(price, 2),
                    "percent_change": round(pct, 2),
                    "score": s["score"],
                    "value": _live_pe(s) if key == "valuation" else s.get(value_key),
                    "realtime": is_live,
                }
            )
        return out

    rankings: list[dict[str, Any]] = []
    for key, title, metric, value_key in _RANK_TAB_META:
        pool = pools[key]
        if key == "valuation" and quotes:
            # Giá biến động trong phiên ⇒ P/E biến động ⇒ xếp lại top theo P/E realtime.
            pool = sorted(pool[:_RANK_REALTIME_WINDOW], key=_live_pe)
        rankings.append({"key": key, "title": title, "metric": metric, "rows": _rows(key, pool, value_key)})
    return rankings


def _get_rank_screener_stocks() -> list[dict[str, Any]]:
    """Phần `stocks` của trang screener (đã có VinaFintech Score) — cache riêng 120s.

    Bảng xếp hạng realtime poll ~20s/lần nên chỉ cần dựng lại phần giá; danh sách
    mã + chỉ số cơ bản tái sử dụng cache này để không tính lại toàn bộ page.
    """
    cached = cache.get("rank-screener-stocks")
    if cached is not None:
        return cached
    stocks = (get_stock_screener_page() or {}).get("stocks") or []
    cache.set("rank-screener-stocks", stocks, ttl_seconds=_RANK_SCREENER_STOCKS_TTL_SECONDS)
    return stocks


@_singleflight(lambda: "stock-rankings-live", _RANK_LIVE_CACHE_TTL_SECONDS, stale_while_revalidate=False)
def get_stock_rankings_live() -> dict[str, Any]:
    """Bảng xếp hạng VinaFintech cập nhật REALTIME — dùng cho vòng polling block
    "Xếp hạng VinaFintech" ở trang "Khám phá" (nhẹ hơn nhiều so với stock-discover:
    không dựng featured / chiến lược / bản đồ cơ hội)."""
    stocks = _get_rank_screener_stocks()
    rankings = _build_vinafintech_rankings(stocks, quote_loader=_rank_live_quotes)
    rows = [row for rk in rankings for row in rk["rows"]]
    live_rows = sum(1 for row in rows if row.get("realtime"))
    updated_at = datetime.now(_VN_TZ).isoformat()
    return {
        # as_of = mốc dữ liệu THỊ TRƯỜNG (nếu nguồn bảng giá trả về); rỗng thì client
        # hiển thị thời điểm máy chủ lấy dữ liệu (updated_at).
        "as_of": _rank_quotes_as_of,
        "updated_at": updated_at,
        "server_time": updated_at,
        "source": "simplize-last",
        "realtime": True,
        "live_rows": live_rows,
        "total_rows": len(rows),
        "universe": len(stocks),
        "rankings": rankings,
    }


def build_discover_strategies(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """6 nhóm "Khám phá theo chiến lược" (trang Khám phá) × 5 mã/nhóm.

    Dùng chung cho `/api/stocks/stock-discover` (trang Khám phá) và block "Cơ hội mới" ở
    trang chủ (xem `app/services/home_opportunity_service.py`) ⇒ tiêu chí chọn mã KHÔNG
    bị lệch giữa 2 nơi.

    Tiêu chí (KHÔNG đổi so với bản trước đây):
      value     = P/E>0 & P/B>0, sort (P/E + P/B + EV/EBITDA) tăng dần
      growth    = top `net_profit_growth` giảm dần, chỉ mã > 0
      cash_flow = điểm sức khỏe tài chính ×2 + cổ tức (+1 nếu có cổ tức) — KHÔNG dùng volume
      breakout / defensive / hidden: giữ nguyên.
    """

    def _pick(sort_key: str, reverse: bool = True, n: int = 5, cond=None) -> list[dict[str, Any]]:
        arr = stocks if cond is None else [s for s in stocks if cond(s)]
        return sorted(
            arr,
            key=lambda s: s.get(sort_key) if isinstance(s.get(sort_key), (int, float)) else -999999.0,
            reverse=reverse,
        )[:n]

    value = sorted(
        [s for s in stocks if s["pe_ratio"] and s["pe_ratio"] > 0 and s["pb_ratio"] and s["pb_ratio"] > 0],
        key=lambda s: (s["pe_ratio"] + s["pb_ratio"]) + (s["ev_ebitda"] if s["ev_ebitda"] and s["ev_ebitda"] > 0 else 10),
    )[:5]
    growth = _pick("net_profit_growth", cond=lambda s: s["net_profit_growth"] and s["net_profit_growth"] > 0)
    cash_flow = sorted(
        stocks,
        key=lambda s: s["points"]["financial_health"] * 2 + s["points"]["dividend"] + (1 if s["dividend_yield"] and s["dividend_yield"] > 0 else 0),
        reverse=True,
    )[:5]
    breakout = _pick("percent_change", cond=lambda s: s["percent_change"] > 0)
    defensive = sorted(
        [s for s in stocks if s["dividend_yield"] and s["dividend_yield"] > 0],
        key=lambda s: s["dividend_yield"] + s["points"]["financial_health"] * 0.5,
        reverse=True,
    )[:5]
    hidden = sorted(
        [s for s in stocks if s["market_cap"] and s["market_cap"] < 3e12 and s["score"] >= 40],
        key=lambda s: s["score"],
        reverse=True,
    )[:5]
    if len(hidden) < 5:
        hidden = sorted(
            [s for s in stocks if s["market_cap"] and s["market_cap"] < 5e12],
            key=lambda s: s["score"],
            reverse=True,
        )[:5]

    return [
        {"key": "value", "icon": "💎", "title": "Cổ phiếu giá trị", "desc": "P/E thấp, ROE cao", "stocks": value},
        {"key": "growth", "icon": "🚀", "title": "Tăng trưởng", "desc": "Lợi nhuận tăng trưởng mạnh", "stocks": growth},
        {"key": "cash_flow", "icon": "🔥", "title": "Dòng tiền mạnh", "desc": "Thanh khoản và volume tăng", "stocks": cash_flow},
        {"key": "breakout", "icon": "📈", "title": "Breakout", "desc": "Vượt vùng kháng cự", "stocks": breakout},
        {"key": "defensive", "icon": "🛡️", "title": "Phòng thủ", "desc": "Ổn định, cổ tức cao", "stocks": defensive},
        {"key": "hidden", "icon": "⭐", "title": "Hidden Gems", "desc": "Doanh nghiệp chưa được thị trường chú ý", "stocks": hidden},
    ]


@_singleflight(lambda: "stock-discover-page", ttl_seconds=120, stale_while_revalidate=True)
def get_stock_discover_page() -> dict[str, Any]:
    """Dữ liệu trang "Khám phá": cơ hội nổi bật hôm nay, chiến lược, bản đồ cơ hội
    thị trường (bubble chart) và các bảng xếp hạng VinaFintech. Tái sử dụng dữ liệu
    screener (đã cache). Cache 120s + stale-while-revalidate.
    """
    screener = get_stock_screener_page()
    stocks = screener["stocks"]
    by_sym = {s["symbol"]: s for s in stocks}

    # ---- 1. Cơ hội nổi bật hôm nay (4 mã) ----
    featured_syms = ["VCI", "ACB", "HPG", "MBB"]
    featured: list[dict[str, Any]] = []
    for sym in featured_syms:
        s = by_sym.get(sym)
        if not s:
            continue
        notes: list[str] = []
        if s["volume_ratio"] and s["volume_ratio"] >= 1.2:
            notes.append("Volume tăng mạnh")
        if s["percent_change"] > 0:
            notes.append("giá đang tăng")
        if s["points"]["growth"] >= 3:
            notes.append("lợi nhuận cải thiện")
        if s["points"]["valuation"] >= 3:
            notes.append("định giá hấp dẫn")
        if s["points"]["performance"] >= 3:
            notes.append("xu hướng kỹ thuật tốt")
        if not notes:
            notes.append("chỉ số cơ bản ổn định")
        featured.append(
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "price": s["price"],
                "percent_change": s["percent_change"],
                "market_cap": s["market_cap"],
                "score": s["score"],
                "growth_score": s["points"]["growth"],
                "valuation_score": s["points"]["valuation"],
                "technical_score": s["points"]["performance"],
                "highlight": "Điểm nổi bật: " + ", ".join(notes[:3]).lower() + ".",
            }
        )

    # ---- 2. Khám phá theo chiến lược (6 nhóm × 5 mã) ----
    # Dùng CHUNG hàm với block "Cơ hội mới" ở trang chủ (snapshot 12h trưa hằng ngày) để
    # tiêu chí chọn mã của 2 nơi luôn khớp nhau; cả 2 chỉ lấy mã VỐN HOÁ LỚN + THANH KHOẢN
    # đủ để giao dịch hằng ngày (bộ lọc dùng chung — xem `is_tradable_stock`).
    strategies = build_discover_strategies([s for s in stocks if is_tradable_stock(s)])

    # ---- 3. Bản đồ cơ hội thị trường (Quadrant Bubble Chart) ----
    # Lọc cổ phiếu đủ điều kiện → tính Growth/Valuation/Liquidity/Opportunity Score
    # (percentile 0..100) → xếp hạng & chọn theo từng quadrant (không hard-code mã).
    def _blend_growth(s):
        parts, w = [], []
        for key, wt in (("net_profit_growth", 0.45), ("revenue_growth", 0.30), ("eps_growth", 0.25)):
            v = s.get(key)
            if v is not None:
                parts.append(v)
                w.append(wt)
        return (sum(p * wi for p, wi in zip(parts, w)) / sum(w)) if parts else None

    def _blend_valuation(s):
        parts, w = [], []
        pe = s.get("pe_ratio")
        pb = s.get("pb_ratio")
        if pe and pe > 0:
            parts.append(math.log(pe))
            w.append(0.6)
        if pb and pb > 0:
            parts.append(math.log(pb))
            w.append(0.4)
        return (sum(p * wi for p, wi in zip(parts, w)) / sum(w)) if parts else None

    # Ngưỡng thanh khoản tối thiểu (tỷ VND/phiên) — cấu hình được.
    _MAP_MIN_LIQUIDITY_TY = 5.0
    vn30_set = set(_VN30_TICKERS)
    eligible: list[dict[str, Any]] = []
    for s in stocks:
        growth_raw = _blend_growth(s)
        val_raw = _blend_valuation(s)
        if growth_raw is None or val_raw is None:
            continue
        if not (s.get("market_cap") and s["market_cap"] > 0):
            continue
        if not (s.get("score") and s["score"] > 0):
            continue
        liquidity = (s.get("volume") or 0) * (s.get("price") or 0) / 1e9
        if liquidity < _MAP_MIN_LIQUIDITY_TY:
            continue
        eligible.append(
            {
                "symbol": s["symbol"],
                "vn30": s["symbol"] in vn30_set,
                "name": s["name"],
                "exchange": s["exchange"],
                "sector": s.get("sector") or "khac",
                "sectorName": s.get("sectorName") or "Khác",
                "industry": s.get("industry") or "khac",
                "industryName": s.get("industryName") or "Khác",
                "price": s["price"],
                "percent_change": s["percent_change"],
                "pe": round(s["pe_ratio"], 1) if s.get("pe_ratio") and s["pe_ratio"] > 0 else None,
                "pb": round(s["pb_ratio"], 2) if s.get("pb_ratio") and s["pb_ratio"] > 0 else None,
                "market_cap": s["market_cap"],
                "liquidity": round(liquidity, 1),
                "tuni_score": s["score"],
                "_growth_raw": growth_raw,
                "_val_raw": val_raw,
            }
        )

    g_pct = _percentile_scores([e["_growth_raw"] for e in eligible])
    v_pct = _percentile_scores([e["_val_raw"] for e in eligible])
    l_pct = _percentile_scores([e["liquidity"] for e in eligible])

    for i, e in enumerate(eligible):
        e["growth_score"] = g_pct.get(i, 50.0)
        e["valuation_score"] = v_pct.get(i, 50.0)
        e["liquidity_score"] = l_pct.get(i, 50.0)
        e["opportunity_score"] = round(
            0.40 * e["growth_score"]
            + 0.35 * (100 - e["valuation_score"])
            + 0.15 * e["tuni_score"]
            + 0.10 * e["liquidity_score"],
            1,
        )
        e["quadrant"] = (
            "opportunity" if e["growth_score"] >= 50 and e["valuation_score"] < 50
            else "growth" if e["growth_score"] >= 50
            else "value" if e["valuation_score"] < 50
            else "caution"
        )
        e.pop("_growth_raw", None)
        e.pop("_val_raw", None)

    # Chọn Top theo từng quadrant (Opportunity Score giảm dần) — mặc định 50/35/35/20
    _MAP_QUOTA = {"opportunity": 50, "growth": 35, "value": 35, "caution": 20}
    by_q: dict[str, list] = {}
    for e in eligible:
        by_q.setdefault(e["quadrant"], []).append(e)
    for q in by_q:
        by_q[q].sort(key=lambda x: x["opportunity_score"], reverse=True)
    _selected: list[dict[str, Any]] = []
    for q, n in _MAP_QUOTA.items():
        _selected.extend(by_q.get(q, [])[:n])

    opportunity_map: dict[str, Any] = {
        "eligible_total": len(eligible),
        "selected_total": len(_selected),
        "min_liquidity_ty": _MAP_MIN_LIQUIDITY_TY,
        "quadrant_quota": _MAP_QUOTA,
        "sectors": sorted({e["sectorName"] for e in eligible}),
        "stocks": eligible,
    }

    # ---- 4. Xếp hạng VinaFintech (5 bảng × 8 dòng) ----
    # Lần load đầu lấy theo snapshot screener (nhanh, đã cache); block này sau đó tự
    # làm mới REALTIME qua /api/stocks/stock-rankings (giá + % thay đổi theo bảng giá).
    rankings = _build_vinafintech_rankings(stocks)

    # Thời điểm cập nhật dữ liệu: lấy từ market dashboard (VNINDEX) — screener không có as_of.
    try:
        as_of = get_market_dashboard().get("as_of", "")
    except Exception:
        as_of = ""

    payload: dict[str, Any] = {
        "as_of": as_of,
        "featured": featured,
        "strategies": strategies,
        "opportunity": opportunity_map,
        "rankings": rankings,
        "ai_prompts": [
            "Tìm cổ phiếu ngân hàng định giá thấp",
            "Tìm doanh nghiệp lợi nhuận tăng mạnh",
        ],
    }
    return payload
