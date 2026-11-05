"""Tỷ giá ngoại tệ — service VinaFintech cho tiện ích "Tỷ giá ngoại tệ".

Kiến trúc: Frontend → VinaFintech Backend → Exchange-Rate provider → Cache → Frontend.

- Provider (mặc định open.er-api.com, không cần key) có thể ĐỔI qua ENV
  (EXCHANGE_RATE_API_URL / EXCHANGE_RATE_API_KEY / EXCHANGE_CACHE_TTL) mà KHÔNG đổi frontend.
- Không hard-code tỷ giá; không bịa dữ liệu khi provider lỗi.
- Cache trong bộ nhớ (TTL theo ENV) + stale-while-revalidate:
  hết hạn nhưng còn cache cũ → trả NGAY bản cũ (kèm updated_at cũ, cờ stale)
  rồi refresh NỀN; chỉ fetch đồng bộ khi CHƯA có cache nào.
- Mọi giá trị rate quy về VND: vnd_rate[C] = VND cần để mua 1 đơn vị C.
  (Provider base USD: 1 USD = R_C đơn vị C, 1 USD = R_VND VND → 1 C = R_VND/R_C VND.)
- Quy ước cặp "base/quote" của GET /rate: rate = số đơn vị `base` cho 1 đơn vị `quote`
  (vd base=VND&quote=USD → 1 USD = 26.000 VND). Chuyển base→quote = amount / rate.
"""
from __future__ import annotations

import email.utils
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote as _urlquote

import httpx

from app.core.config import settings

logger = logging.getLogger("tunix.exchange")

# Danh sách tiền tệ mặc định (đã chốt) — thứ tự hiển thị. digits dùng cho UI, flag = mã quốc gia 2 ký tự
# (flagcdn: vn/us/jp/kr/cn/sg/eu/gb/tw/au).
SUPPORTED_CURRENCIES: list[dict[str, Any]] = [
    {"code": "USD", "name": "Đô la Mỹ", "digits": 2, "flag": "us"},
    {"code": "JPY", "name": "Yên Nhật", "digits": 0, "flag": "jp"},
    {"code": "KRW", "name": "Won Hàn Quốc", "digits": 0, "flag": "kr"},
    {"code": "CNY", "name": "Nhân dân tệ", "digits": 2, "flag": "cn"},
    {"code": "SGD", "name": "Đô la Singapore", "digits": 2, "flag": "sg"},
    {"code": "EUR", "name": "Euro", "digits": 2, "flag": "eu"},
    {"code": "GBP", "name": "Bảng Anh", "digits": 2, "flag": "gb"},
    {"code": "TWD", "name": "Đài tệ", "digits": 2, "flag": "tw"},
    {"code": "AUD", "name": "Đô la Úc", "digits": 2, "flag": "au"},
    {"code": "VND", "name": "Việt Nam Đồng", "digits": 0, "flag": "vn"},
]

CODE_TO_NAME: dict[str, str] = {c["code"]: c["name"] for c in SUPPORTED_CURRENCIES}
CODE_TO_DIGITS: dict[str, int] = {c["code"]: c["digits"] for c in SUPPORTED_CURRENCIES}
CODE_TO_FLAG: dict[str, str] = {c["code"]: c["flag"] for c in SUPPORTED_CURRENCIES}


class ExchangeRateError(RuntimeError):
    pass


class _Cache:
    """Cache nội bộ (đơn luồng API — đủ cho MVP; có thể thay redis sau)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None  # {'payload','ok_at','fetching'}
        self._refreshing = False

    @property
    def fresh(self) -> bool:
        if not self._data:
            return False
        ttl = int(settings.exchange_cache_ttl or 3600)
        return (time.time() - self._data["ok_at"]) < ttl

    def get(self) -> dict[str, Any] | None:
        with self._lock:
            return self._data["payload"] if self._data else None

    def set(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._data = {"payload": payload, "ok_at": time.time()}

    def start_refresh(self) -> bool:
        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True
            return True

    def finish_refresh(self) -> None:
        with self._lock:
            self._refreshing = False


_cache = _Cache()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_fetch() -> dict[str, Any]:
    """Gọi provider (mặc định open.er-api.com) → chuẩn hoá payload."""
    url = settings.exchange_rate_api_url or "https://open.er-api.com/v6/latest/USD"
    if settings.exchange_rate_api_key:
        sep = "&" if "?" in url else "?"
        url = url + sep + "apikey=" + _urlquote(settings.exchange_rate_api_key)
    timeout = httpx.Timeout(15.0, connect=8.0)
    try:
        resp = httpx.get(url, timeout=timeout, headers={"User-Agent": "VinaFintech/1.0", "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        raise ExchangeRateError(f"Không kết nối được nhà cung cấp tỷ giá: {exc}") from exc
    if resp.status_code != 200:
        raise ExchangeRateError(f"Nhà cung cấp tỷ giá trả lỗi {resp.status_code}.")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ExchangeRateError("Dữ liệu tỷ giá không hợp lệ.") from exc
    rates_raw = data.get("rates") or {}
    if not isinstance(rates_raw, dict) or not rates_raw:
        raise ExchangeRateError("Nhà cung cấp không trả về tỷ giá nào.")
    rates: dict[str, float] = {}
    for code, val in rates_raw.items():
        try:
            rates[str(code).upper()] = float(val)
        except (TypeError, ValueError):
            continue
    # Thời gian cập nhật của provider
    raw_upd = (
        data.get("time_last_update_utc")
        or data.get("time_last_update")
        or data.get("time_last_update_iso")
        or data.get("date")
        or ""
    )
    updated_at = ""
    if raw_upd:
        try:
            dt = email.utils.parsedate_to_datetime(str(raw_upd))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            updated_at = dt.isoformat()
        except Exception:  # noqa: BLE001
            updated_at = str(raw_upd)
    if not updated_at:
        updated_at = _iso_now()
    source = str(data.get("source") or "") or "open.er-api.com"
    base = str(data.get("base_code") or data.get("base") or "USD").upper()
    if "VND" not in rates:
        raise ExchangeRateError("Nhà cung cấp hiện tại không cung cấp tỷ giá VND.")
    return {
        "source": source,
        "base": base,
        "rates": rates,
        "updated_at": updated_at,
        "fetched_at": _iso_now(),
    }


def _payload(p: dict[str, Any]) -> dict[str, Any]:
    """Payload VinaFintech chuẩn cho /rates (mọi loại tiền quy về VND)."""
    rates = p["rates"]
    vnd_per_usd = rates.get("VND")
    currencies: list[dict[str, Any]] = []
    missing: list[str] = []
    for c in SUPPORTED_CURRENCIES:
        code = c["code"]
        if code == "VND":
            currencies.append(
                {"code": code, "name": c["name"], "digits": c["digits"], "flag": c["flag"], "per_usd": 1.0, "vnd_rate": 1.0}
            )
            continue
        per_usd = rates.get(code)
        if not per_usd:
            missing.append(code)
            continue
        currencies.append(
            {
                "code": code,
                "name": c["name"],
                "digits": c["digits"],
                "flag": c["flag"],
                "per_usd": round(per_usd, 8),
                "vnd_rate": round(vnd_per_usd / per_usd, 8),  # 1 code = ? VND
            }
        )
    currencies.sort(key=lambda x: (x["code"] == "VND", 0 if x["code"] == "VND" else 1, x["code"]))
    return {
        "ok": True,
        "base": "VND",
        "provider_base": p["base"],
        "source": p["source"],
        "updated_at": p["updated_at"],
        "fetched_at": p["fetched_at"],
        "stale": p.get("stale", False),
        "vnd_per_usd": round(vnd_per_usd, 2) if vnd_per_usd else None,
        "currencies": currencies,
        "missing": missing,
    }


def _refresh() -> dict[str, Any]:
    try:
        p = _provider_fetch()
        p["stale"] = False
        _cache.set(p)
        return p
    except ExchangeRateError:
        # provider lỗi: giữ cache cũ nếu có
        old = _cache.get()
        if old:
            old["stale"] = True
            return old
        raise
    except Exception as exc:  # noqa: BLE001
        old = _cache.get()
        if old:
            old["stale"] = True
            return old
        raise ExchangeRateError(str(exc)) from exc


def load_payload(force: bool = False) -> dict[str, Any]:
    """Lấy payload mới nhất (cache), có SWR. Ném ExchangeRateError khi chưa có cache & fetch lỗi."""
    if not force and _cache.fresh and _cache.get():
        return _payload(_cache.get())
    stale = _cache.get()
    if stale is None:
        return _payload(_refresh())  # lần đầu: fetch đồng bộ
    # Có cache cũ nhưng hết hạn → trả ngay bản cũ + refresh nền (không chặn user)
    if _cache.start_refresh():
        def _bg() -> None:
            try:
                _refresh()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Exchange refresh nền thất bại: %s", exc)
            finally:
                _cache.finish_refresh()

        threading.Thread(target=_bg, name="exchange-swr", daemon=True).start()
    stale = dict(stale)
    stale["stale"] = True
    return _payload(stale)


def refresh_now() -> dict[str, Any]:
    """Bấm nút làm mới thủ công: fetch mới; nếu lỗi trả cache cũ (stale)."""
    try:
        return _payload(_refresh())
    except ExchangeRateError as exc:
        old = _cache.get()
        if old:
            old["stale"] = True
            return _payload(old)
        raise exc


# ---------------------------------------------------------------------------
# Các hàm tra cứu / chuyển đổi
# ---------------------------------------------------------------------------
def _vnd_map() -> dict[str, float]:
    """vnd_rate[C] = VND cho 1 đơn vị C (VND=1). Ném lỗi nếu không có cache khả dụng."""
    payload = load_payload()
    out: dict[str, float] = {}
    for c in payload["currencies"]:
        out[c["code"]] = float(c["vnd_rate"])
    return out


def factor(from_code: str, to_code: str) -> float:
    """Số đơn vị `to_code` cho 1 đơn vị `from_code` (qua VND)."""
    vd = _vnd_map()
    return vd[from_code] / vd[to_code]


def convert(from_code: str, to_code: str, amount: float, fee_percent: float = 0.0) -> dict[str, Any]:
    """Chuyển đổi: fee trừ trên số tiền nguồn, sau đó mới quy đổi.

    fee = amount × fee% ; net = amount − fee ; converted = net × factor.
    Trả về đủ: fee, net, factor + rate hiển thị theo quy ước "1 quote = rate base".
    """
    vd = _vnd_map()
    if from_code not in vd or to_code not in vd:
        raise ExchangeRateError("Loại tiền không được hỗ trợ hoặc đang thiếu dữ liệu.")
    fee = amount * fee_percent / 100.0
    net = amount - fee
    f = vd[from_code] / vd[to_code]  # 1 from = f to
    converted = net * f
    # rate hiển thị: "1 {quote} = rate {base}" — ưu tiên cặp có VND để số to dễ đọc
    vnd_per_usd_rate = vd.get("USD")
    return {
        "ok": True,
        "from": from_code,
        "to": to_code,
        "from_name": CODE_TO_NAME.get(from_code, from_code),
        "to_name": CODE_TO_NAME.get(to_code, to_code),
        "amount": round(amount, 8),
        "fee_percent": round(fee_percent, 4),
        "fee": round(fee, 8),
        "net_amount": round(net, 8),
        "rate": round(f, 10),  # 1 from = rate to
        "converted_amount": round(converted, 10),
        "updated_at": _cache.get()["updated_at"] if _cache.get() else None,
        "source": (_cache.get() or {}).get("source"),
        "display": {
            "from": from_code,
            "to": to_code,
            # 1 to = ? VND
            "vnd_per_from": round(vd.get(from_code, 1), 8) if from_code != "VND" else 1,
            "vnd_per_to": round(vd.get(to_code, 1), 8) if to_code != "VND" else 1,
            "vnd_per_usd": vnd_per_usd_rate,
        },
    }


def pair_rate(base: str, quote: str) -> dict[str, Any]:
    """Quy ước: rate = số đơn vị `base` cho 1 đơn vị `quote`.
    VD base=VND&quote=USD → 1 USD = rate VND (26.000). Chuyển base→quote = amount/rate.
    """
    vd = _vnd_map()
    if base not in vd or quote not in vd:
        raise ExchangeRateError("Loại tiền không được hỗ trợ hoặc đang thiếu dữ liệu.")
    rate = vd[quote] / vd[base]  # đơn vị base cho 1 quote
    return {
        "ok": True,
        "base": base,
        "quote": quote,
        "rate": round(rate, 10),
        "updated_at": (_cache.get() or {}).get("updated_at"),
        "source": (_cache.get() or {}).get("source"),
        "stale": bool((_cache.get() or {}).get("stale")),
    }
