from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
import json
import math
import re
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


_SIMPLIZE_BASE_URL = "https://api2.simplize.vn"
_SIMPLIZE_WEB_BASE_URL = "https://simplize.vn"
_SIMPLIZE_TIMEOUT_SECONDS = 12
_VCIQ_URL = "https://iq.vietcap.com.vn/api/iq-insight-service"
_SYMBOL_DIRECTORY_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_directory.json"
_SYMBOL_SUMMARY_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_summaries.json"
_SYMBOL_SCREENER_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_screener.json"
_SYMBOL_DIVIDEND_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "symbol_dividends.json"
_EXCHANGE_CANONICAL = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM", "UPCOMM": "UPCOM"}
_EXCHANGE_CHART_SYMBOLS = {
    "HOSE": ("VNINDEX", "index"),
    "HNX": ("HNXINDEX", "index"),
    "UPCOM": ("UPCOM", "index"),
}


class SimpleTTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)


cache = SimpleTTLCache()


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
    req = Request(url, headers={"accept": "application/json", "user-agent": "TuniX/1.0"})
    with urlopen(req, timeout=_SIMPLIZE_TIMEOUT_SECONDS) as response:  # nosec B310
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        return {"status": 500, "message": "Invalid payload", "data": []}
    return data


def _http_post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{_SIMPLIZE_BASE_URL}{path}"
    req = Request(
        url,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "application/json", "user-agent": "TuniX/1.0"},
    )
    with urlopen(req, timeout=_SIMPLIZE_TIMEOUT_SECONDS) as response:  # nosec B310
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
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


def _load_symbol_directory() -> list[dict[str, str]]:
    cache_key = "symbol-directory"
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
    cache_data = _read_symbol_summary_cache_file() or {}
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
        req = Request(url, headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "user-agent": "TuniX/1.0"})
        with urlopen(req, timeout=_SIMPLIZE_TIMEOUT_SECONDS) as response:  # nosec B310
            html = response.read().decode("utf-8", errors="replace")

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


def _fetch_simplize_symbol_summary(symbol: str) -> dict[str, Any]:
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}
    page_props = _fetch_simplize_page_props(symbol_upper)
    summary = page_props.get("summary")
    if not isinstance(summary, dict):
        return {}
    summary["ticker"] = symbol_upper
    return summary


def _fetch_simplize_dividends(symbol: str) -> list[dict[str, Any]]:
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return []
    page_props = _fetch_simplize_page_props(symbol_upper)
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

    file_cache = _read_symbol_summary_cache_file() or {}
    if symbol_upper in file_cache:
        cache.set(cache_key, file_cache[symbol_upper], ttl_seconds=86400)
        return file_cache[symbol_upper]

    summary = _fetch_simplize_symbol_summary(symbol_upper)
    if summary:
        file_cache[symbol_upper] = summary
        cache.set(cache_key, summary, ttl_seconds=86400)
        _write_symbol_summary_cache_file()
    return summary


def _get_cached_symbol_summary(symbol: str) -> dict[str, Any]:
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return {}
    file_cache = _read_symbol_summary_cache_file() or {}
    return file_cache.get(symbol_upper, {})


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


def get_dividends(symbol: str) -> list[dict[str, Any]]:
    """Lịch sử trả cổ tức của cổ phiếu (Simplize dividendPayment)."""
    symbol_upper = symbol.upper().strip()
    if not symbol_upper:
        return []

    cache_key = f"dividends:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    file_cache = _read_dividend_cache_file() or {}
    if symbol_upper in file_cache:
        cache.set(cache_key, file_cache[symbol_upper], ttl_seconds=86400)
        return file_cache[symbol_upper]

    rows = _fetch_simplize_dividends(symbol_upper)
    if rows:
        file_cache[symbol_upper] = rows
        cache.set(cache_key, rows, ttl_seconds=86400)
        _write_dividend_cache_file()
    return rows


def _strip_html(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_company_summary(symbol: str) -> dict[str, Any]:
    """Tóm tắt thông tin doanh nghiệp (Simplize summary) cho popup."""
    symbol_upper = symbol.upper().strip()
    summary = _get_symbol_summary(symbol_upper)
    if not summary:
        return {}

    return {
        "symbol": symbol_upper,
        "name_vi": str(summary.get("nameVi") or ""),
        "name_en": str(summary.get("nameEn") or ""),
        "industry": str(summary.get("industryActivity") or ""),
        "sector": str(summary.get("bcEconomicSectorName") or ""),
        "website": str(summary.get("website") or ""),
        "main_service": _strip_html(summary.get("mainService")),
        "business_line": _strip_html(summary.get("businessLine")),
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
    total = 0
    # total không nằm trong data; nếu cần có thể đọc riêng. Mặc định dùng len + lấy thêm 1 trang dự phòng.
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


def _screener_matches_industry(row: dict[str, Any], industry: str | None) -> bool:
    if not industry:
        return True
    industry = str(industry).strip().lower()
    if industry == "ngan-hang":
        # Ngân hàng = ngành con "tài chính ngân hàng" trong nhóm Tài chính.
        return str(row.get("industrySlug") or "").lower() == "tai-chinh-ngan-hang"
    return str(row.get("economicSectorSlug") or "").lower() == industry


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


def _sample_chart_points(series: list[dict[str, Any]], count: int = 13) -> list[dict[str, Any]]:
    if not series:
        return []
    points: list[dict[str, Any]] = []
    if len(series) <= count:
        for item in series:
            points.append(
                {
                    "time": str(item.get("time", "")),
                    "close": _to_float(item.get("close", 0)),
                    "volume": int(_to_float(item.get("volume", 0))),
                }
            )
        return points

    step = (len(series) - 1) / (count - 1)
    for index in range(count):
        item = series[min(round(index * step), len(series) - 1)]
        points.append(
            {
                "time": str(item.get("time", "")),
                "close": _to_float(item.get("close", 0)),
                "volume": int(_to_float(item.get("volume", 0))),
            }
        )
    return points


def _group_chart_by_date(bars: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for bar in bars:
        time = str(bar.get("time", ""))
        if not time or "T" not in time:
            continue
        date = time.split("T", 1)[0]
        grouped.setdefault(date, []).append(bar)
    return grouped


def _fetch_simplize_market_liquidity(exchange: str) -> dict[str, Any]:
    """GTGD (giá trị giao dịch) nội phiên theo sàn từ Simplize.

    Endpoint /api/historical/widget/market-liquidity là nguồn dữ liệu cho widget
    "Thanh khoản thị trường" trên trang Simplize. Mỗi bar là [timestamp_ms, GTGD_hôm_nay, GTGD_hôm_qua, ...].
    API đã tự chọn phiên giao dịch gần nhất làm "hôm nay" và phiên trước đó làm "hôm qua".
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
        today_vnd = _to_float(row[1])
        yesterday_vnd = _to_float(row[2])
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


def get_watchlist() -> list[dict[str, Any]]:
    """Danh sách cổ phiếu trong VN30: 30 mã VN30 với dữ liệu thực tế từ Simplize.

    Giá/khối lượng lấy realtime, P/E, P/B, thay đổi 7D lấy từ screener dataset,
    sparkline 30D lấy từ lịch sử giá thực tế.
    """
    dataset = _get_screener_dataset()
    screener = {str(row.get("ticker") or "").upper(): row for row in dataset}

    cache_key = "watchlist-quotes"
    live = cache.get(cache_key)
    if live is None:
        try:
            live = _simplize_last("stock", _VN30_TICKERS)
        except Exception:
            live = {}
        cache.set(cache_key, live, ttl_seconds=30)

    sparklines = _get_watchlist_sparklines()

    rows: list[dict[str, Any]] = []
    for ticker in _VN30_TICKERS:
        row = screener.get(ticker, {})
        quote = live.get(ticker, {})
        price = _to_float(quote.get("price")) or _to_float(row.get("priceCloseIntraDay")) or _to_float(row.get("priceClose"))
        change = _to_float(quote.get("netChange"))
        pct = _to_float(quote.get("pctChange")) or _to_float(row.get("pricePctChgIntraDay"))
        volume = _to_float(quote.get("volume")) or _to_float(row.get("volume"))
        rows.append(
            {
                "symbol": ticker,
                "name": str(row.get("companyNameVi") or ticker),
                "exchange": str(row.get("stockExchange") or ""),
                "price": round(price, 2),
                "change": round(change, 2),
                "percent_change": round(pct, 2),
                "change_7d": round(_to_float(row.get("pricePctChg7d")), 2),
                "volume": int(volume),
                "pe": round(_to_float(row.get("peRatio")), 2),
                "pb": round(_to_float(row.get("pbRatio")), 2),
                "sparkline": sparklines.get(ticker, []),
                "source": "simplize",
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
    result = filtered[:20]
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


def _get_quote_fundamentals(symbol: str) -> dict[str, Any]:
    """Chỉ số cơ bản của cổ phiếu: Vốn hoá, Giá trị sổ sách, P/E, P/B, EPS, ROE, ROA,
    KLGD TB 10 phiên, %1W, %1M, %YTD.

    Ưu tiên symbol summary Simplize (có đầy đủ các chỉ số); nếu chưa có thì fallback
    sang screener dataset (thiếu bookValue / EPS / ROE / ROA).
    """
    symbol_upper = symbol.upper().strip()
    out: dict[str, Any] = {}
    summary = _get_symbol_summary(symbol_upper)
    if summary:
        out["market_cap"] = _to_float(summary.get("marketCap"))
        out["book_value"] = _to_float(summary.get("bookValue"))
        out["pe"] = _to_float(summary.get("peRatio"))
        out["pb"] = _to_float(summary.get("pbRatio"))
        out["eps"] = _to_float(summary.get("epsRatio"))
        out["roe"] = _to_float(summary.get("roe"))
        out["roa"] = _to_float(summary.get("roa"))
        out["avg_volume_10d"] = _to_float(summary.get("volume10dAvg"))
        out["pct_1w"] = _to_float(summary.get("pricePctChg7d"))
        out["pct_1m"] = _to_float(summary.get("pricePctChg30d"))
        out["pct_ytd"] = _to_float(summary.get("pricePctChgYtd"))
        return out

    row = None
    for r in _get_screener_dataset():
        if str(r.get("ticker") or "").upper() == symbol_upper:
            row = r
            break
    if row:
        out["market_cap"] = _to_float(row.get("marketCapVnd"))
        out["pe"] = _to_float(row.get("peRatio"))
        out["pb"] = _to_float(row.get("pbRatio"))
        out["avg_volume_10d"] = _to_float(row.get("volume10dAvg"))
        out["pct_1w"] = _to_float(row.get("pricePctChg7d"))
        out["pct_1m"] = _to_float(row.get("pricePctChg30d"))
        out["pct_ytd"] = _to_float(row.get("pricePctChgYtd"))
    return out


def get_quote(symbol: str) -> dict[str, Any]:
    key = f"quote:{symbol.upper()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

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
        last_map = _simplize_last("stock", [symbol.upper()])
        last = last_map.get(symbol.upper(), {})
        history = get_history(symbol.upper(), period="1m")
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
        result.update(_get_quote_fundamentals(symbol.upper()))
        cache.set(key, result, ttl_seconds=15)
        return result
    except Exception:
        result = _fallback_quote(symbol)
        result["valid"] = True
        result.update(metadata)
        result.update(_get_quote_fundamentals(symbol.upper()))
        cache.set(key, result, ttl_seconds=30)
        return result


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
        indices = _simplize_last("index", ["VNINDEX", "VN30", "^DJI"])
        crypto = _simplize_last("crypto", ["BTCUSDT"])
        futures = _simplize_last("futures", ["GC=F"])
        local_gold = _simplize_last("index", ["GOLD:VN:SELL"])

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
    """
    query = f"?{urlencode(params)}" if params else ""
    url = f"{_VCIQ_URL}{path}{query}"
    req = Request(
        url,
        headers={"accept": "application/json", "user-agent": "Mozilla/5.0"},
    )
    with urlopen(req, timeout=_SIMPLIZE_TIMEOUT_SECONDS) as response:  # nosec B310
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        return {}
    return data


def get_profit_analysis(symbol: str, quarters: int = 12) -> dict[str, Any]:
    """Lợi nhuận thực tế theo quý từ báo cáo kết quả kinh doanh (VCI REST API).

    Lấy số liệu lợi nhuận sau thuế / trước thuế / doanh thu / lợi nhuận gộp của công ty
    qua từng quý trong các năm, sắp xếp tăng dần theo thời gian và trả về `quarters`
    quý gần nhất. Không phụ thuộc phiên bản vnstock (gọi thẳng VCI iQ insight).
    """
    symbol_upper = symbol.upper().strip()
    safe_quarters = max(4, min(int(quarters or 12), 60))
    empty = {"symbol": symbol_upper, "source": "empty", "unit": "vnd", "quarters": []}

    cache_key = f"profit-analysis:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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

        # 3) Gom dữ liệu, sắp tăng dần theo (năm, quý), giữ lại những quý mới nhất.
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
        quarters_data = quarters_data[-safe_quarters:]

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
# Từ khóa lọc tên tài liệu theo category.
_CAFEF_FILTER = {
    "annual": ("báo cáo thường niên", "bao cao thuong nien"),
    "agm": ("đại hội cổ đông", "dai hoi co dong", "đhđcđ"),
    "prospectus": ("bản cáo bạch", "ban cao bach"),
}


def _cafef_get_json(path: str) -> dict[str, Any]:
    req = Request(f"{_CAFEF_AJAX_URL}/{path}", headers=_CAFEF_HEADERS)
    with urlopen(req, timeout=_SIMPLIZE_TIMEOUT_SECONDS) as response:  # nosec B310
        payload = response.read().decode("utf-8", errors="replace")
    data = json.loads(payload)
    if not isinstance(data, dict):
        return {}
    return data


def _cafef_get_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "accept": "text/html,*/*",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    with urlopen(req, timeout=_SIMPLIZE_TIMEOUT_SECONDS) as response:  # nosec B310
        return response.read().decode("utf-8", errors="replace")


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
        return items[:5]
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
    return items[:5]


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


def _format_vci_time(raw: Any) -> str:
    """VCI transaction_time dạng '074500023' (HHMMSSmmm) -> '07:45:00'."""
    text = str(raw or "").strip()
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[0:2]}:{text[2:4]}:{text[4:6]}"
    return text


def get_order_book(symbol: str) -> dict[str, Any]:
    """Sổ lệnh thực tế (bên mua/bên bán, khối lượng, giá, thời gian) từ VCI price_board.

    Trả về tối đa 3 mức giá bên mua + 3 mức giá bên bán kèm khối lượng.
    """
    symbol_upper = symbol.upper().strip()
    empty = {"symbol": symbol_upper, "time": "", "bids": [], "asks": [], "source": "empty"}

    cache_key = f"order-book:{symbol_upper}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from vnstock import Trading  # lazy import

        trading = Trading(source="VCI", show_log=False)
        df = trading.price_board([symbol_upper], flatten_columns=True)
        if df is None or df.empty:
            return empty

        row = df.iloc[0]
        bids = []
        asks = []
        for i in range(1, 4):
            bid_price = _to_float(row.get(f"bid_ask_bid_{i}_price"))
            bid_volume = int(_to_float(row.get(f"bid_ask_bid_{i}_volume")))
            if bid_price > 0:
                bids.append({"price": bid_price, "volume": bid_volume})
            ask_price = _to_float(row.get(f"bid_ask_ask_{i}_price"))
            ask_volume = int(_to_float(row.get(f"bid_ask_ask_{i}_volume")))
            if ask_price > 0:
                asks.append({"price": ask_price, "volume": ask_volume})

        result = {
            "symbol": symbol_upper,
            "time": _format_vci_time(row.get("bid_ask_transaction_time")),
            "reference_price": _to_float(row.get("listing_ref_price") or row.get("match_reference_price")),
            "ceiling": _to_float(row.get("listing_ceiling") or row.get("match_ceiling_price")),
            "floor": _to_float(row.get("listing_floor") or row.get("match_floor_price")),
            "bids": bids,
            "asks": asks,
            "source": "vci-price-board",
        }
        cache.set(cache_key, result, ttl_seconds=15)
        return result
    except Exception:
        return empty


def get_recent_trades(symbol: str, limit: int = 50) -> list[dict[str, Any]]:
    """Các giao dịch khớp lệnh gần nhất của phiên (VCI intraday).

    Dữ liệu từng lệnh khớp: thời gian, giá, khối lượng, chiều mua/bán.
    """
    symbol_upper = symbol.upper().strip()
    safe_limit = max(1, min(int(limit or 50), 200))
    empty: list[dict[str, Any]] = []

    cache_key = f"recent-trades:{symbol_upper}:{safe_limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from vnstock import Quote  # lazy import

        quote = Quote(source="VCI", symbol=symbol_upper, show_log=False)
        df = quote.intraday(page_size=safe_limit)
        if df is None or df.empty:
            return empty

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
                    "time": time_label,
                    "price": price,
                    "volume": int(_to_float(row.get("volume"))),
                    "match_type": match_type,
                    "side": "B" if match_type.lower().startswith("b") else "S",
                }
            )

        # Sắp theo thời gian giảm dần (mới nhất trước), giới hạn số dòng.
        trades.sort(key=lambda t: t["time"], reverse=True)
        trades = trades[:safe_limit]
        cache.set(cache_key, trades, ttl_seconds=15)
        return trades
    except Exception:
        return empty


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
    if not current_row:
        return empty

    industry_slug = str(current_row.get("industrySlug") or "")
    industry_name = str(current_row.get("industryName") or "")
    if not industry_slug:
        return empty

    peers: list[dict[str, Any]] = []
    for row in dataset:
        if str(row.get("industrySlug") or "") != industry_slug:
            continue
        ticker = str(row.get("ticker") or "").upper()
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
    result: list[dict[str, Any]] = []

    for row in dataset:
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

        result.append(_build_screener_stock_row(row))

    result.sort(key=lambda item: item["symbol"])
    return result
