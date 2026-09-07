"""Crypto — service lấy dữ liệu từ Binance (tách tầng khỏi giao diện).

Kiến trúc: Binance → TuniX Backend → Frontend (frontend KHÔNG gọi Binance trực tiếp).
- Danh sách: 1 request `GET /api/v3/ticker/24hr` (~3000+ ticker) → lọc ~100 mã có
  thanh khoản (quoteVolume 24h) cao nhất — KHÔNG bắn N request/coin.
- Klines (biểu đồ): `GET /api/v3/klines` theo từng cặp.
- Cache ngắn + single-flight theo luồng để polling hợp lý, không tạo tải API thừa.
- Không hiển thị vốn hóa (Binance không cung cấp market cap).
Không mua/bán, không khuyến nghị.
"""
import threading
import time
from datetime import datetime, timezone

import httpx

_BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
# data-api.binance.vision là mirror market-data CHÍNH THỨC của Binance — KHÔNG geo-
# restrict, dùng được từ Mỹ / Render (api.binance.com bị geo-block ở Mỹ, trả 451).
_UA = {"User-Agent": "Mozilla/5.0 (TuniX/1.0)", "Accept": "application/json"}

_QUOTE = "USDT"
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
_TICK_TTL = 6.0
_KLINES_TTL = 20.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cached(key: str, ttl: float, producer) -> object:
    """Cache + single-flight đơn giản (tính trong lock để không trùng khi đồng thời)."""
    now = time.time()
    with _lock:
        ent = _cache.get(key)
        if ent and now < ent[0]:
            return ent[1]
        value = producer()
        _cache[key] = (now + ttl, value)
        # dọn cache cũ đơn giản (tránh rò rỉ)
        if len(_cache) > 512:
            for k in [k for k, v in _cache.items() if v[0] < now]:
                _cache.pop(k, None)
        return value


def _get(path: str, params: dict | None = None) -> object:
    """GET Binance với fail-over giữa các host (vision → api).

    Cùng 1 endpoint/path trên cả 2 host; host trước lỗi (geo-block/timeout/…) thì
    tự thử host kế tiếp — trang không phụ thuộc vào 1 IP/region của máy chủ.
    """
    last: Exception | None = None
    for base in _BINANCE_HOSTS:
        try:
            with httpx.Client(base_url=base, headers=_UA, timeout=15.0) as client:
                r = client.get(path, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:  # noqa: BLE001 — host lỗi → thử host kế tiếp
            last = e
    if last is not None:
        raise last
    return {}


def _f(v: str | None):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _all_tickers() -> dict[str, dict]:
    def _prod() -> dict[str, dict]:
        rows = _get("/api/v3/ticker/24hr")
        out: dict[str, dict] = {}
        if isinstance(rows, list):
            for row in rows:
                sym = str(row.get("symbol") or "")
                out[sym] = row
        return out

    return _cached("tickers:24hr", _TICK_TTL, _prod)


def _volume_ranking(tickers: dict[str, dict]) -> list[dict]:
    """~250 mã có thanh khoản cao nhất (quoteVolume 24h giảm dần) — nguồn xếp hạng chính.
    Chỉ lấy base không chứa chữ số (loại token nhân như 1000SATS/…UP/…DOWN)."""
    ranked = sorted(tickers.values(), key=lambda t: _f(t.get("quoteVolume")), reverse=True)
    out: list[dict] = []
    for t in ranked:
        sym = str(t.get("symbol") or "")
        if not sym.endswith(_QUOTE) or len(sym) <= len(_QUOTE):
            continue
        base = sym[: -len(_QUOTE)]
        if not base or any(ch.isdigit() for ch in base):
            continue
        out.append({"symbol": base, "name": base})
        if len(out) >= 250:
            break
    return out


def get_markets() -> dict:
    """~100 mã thanh khoản cao nhất có USDT trên Binance — xếp theo Volume 24h.

    - Giá/Cao-Thấp/Volume 24h: Binance (host vision → api, cache 6s).
    - Thứ hạng theo thanh khoản Binance — không hiển thị vốn hóa.
    """
    try:
        tickers = _all_tickers()
    except Exception as e:  # noqa: BLE001 — Binance cả 2 host đều lỗi
        return {
            "quote": _QUOTE,
            "source": "binance",
            "count": 0,
            "total": 100,
            "error": str(e),
            "message": "Không lấy được dữ liệu từ Binance. Vui lòng thử lại sau.",
            "items": [],
        }

    top = _volume_ranking(tickers)
    items: list[dict] = []
    seen: set[str] = set()
    for entry in top:
        base = str(entry.get("symbol") or "").upper()
        pair = base + _QUOTE
        if not base or pair in seen or pair not in tickers:
            continue
        seen.add(pair)
        t = tickers[pair]
        change = _f(t.get("priceChangePercent"))
        items.append(
            {
                "rank": len(items) + 1,
                "symbol": base,
                "pair": pair,
                "name": entry.get("name") or base,
                "price": _f(t.get("lastPrice")),
                "change_pct": round(change, 2),
                "high": _f(t.get("highPrice")),
                "low": _f(t.get("lowPrice")),
                "volume": _f(t.get("quoteVolume")),  # Volume 24h quy đổi (USDT)
            }
        )
        if len(items) >= 100:
            break
    return {
        "quote": _QUOTE,
        "source": "binance",
        "count": len(items),
        "total": 100,
        "as_of": _now_utc().isoformat(),
        "items": items,
    }


def get_klines(symbol: str, interval: str = "1d", limit: int = 90) -> dict:
    """Nến giá Binance cho biểu đồ."""
    pair = str(symbol or "").upper()
    if not pair.endswith(_QUOTE):
        pair = pair + _QUOTE

    def _prod() -> dict:
        try:
            rows = _get("/api/v3/klines", {"symbol": pair, "interval": interval, "limit": int(limit)})
        except Exception:  # noqa: BLE001 — Binance cả 2 host lỗi → không làm 500
            return {"symbol": pair, "interval": interval, "items": [], "error": "Không lấy được dữ liệu biểu đồ từ Binance."}
        out: list[dict] = []
        if isinstance(rows, list):
            for row in rows:
                try:
                    out.append(
                        {
                            "time": int(row[0]),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        }
                    )
                except (TypeError, ValueError, IndexError):
                    continue
        return {"symbol": pair, "interval": interval, "items": out}

    return _cached(f"klines:{pair}:{interval}:{int(limit)}", _KLINES_TTL, _prod)
