"""Crypto — service lấy dữ liệu từ Binance (tách tầng khỏi giao diện).

Kiến trúc: Binance → TuniX Backend → Frontend (frontend KHÔNG gọi Binance trực tiếp).
- Danh sách: 1 request `GET /api/v3/ticker/24hr` (trả ~3000+ ticker) rồi lọc theo bộ
  Top coin đã chọn — KHÔNG bắn N request/coin.
- Klines (biểu đồ): `GET /api/v3/klines` theo từng cặp.
- Cache ngắn + single-flight theo luồng để polling hợp lý, không tạo tải API thừa.
- Binance KHÔNG cung cấp Market Cap → trường market_cap để null, frontend hiển thị '—'.
Chưa tích hợp CMC/CoinGecko, không mua/bán, không khuyến nghị.
"""
import threading
import time
from datetime import datetime, timezone

import httpx

_BASE = "https://api.binance.com"
_UA = {"User-Agent": "Mozilla/5.0 (TuniX/1.0)"}

# Bộ Top coin (thứ hạng theo vốn hóa tham khảo) — CHỈ là danh sách lựa chọn/cặp giao dịch,
# KHÔNG phải dữ liệu giá hard-code (giá luôn lấy live từ Binance).
TOP_COINS: list[tuple[str, str]] = [
    ("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("BNB", "BNB"), ("XRP", "XRP"), ("SOL", "Solana"),
    ("USDC", "USD Coin"), ("ADA", "Cardano"), ("DOGE", "Dogecoin"), ("TRX", "TRON"),
    ("TON", "Toncoin"), ("AVAX", "Avalanche"), ("LINK", "Chainlink"), ("XLM", "Stellar"),
    ("SHIB", "Shiba Inu"), ("DOT", "Polkadot"), ("LTC", "Litecoin"), ("BCH", "Bitcoin Cash"),
    ("NEAR", "NEAR Protocol"), ("UNI", "Uniswap"), ("PEPE", "Pepe"), ("SUI", "Sui"),
    ("ICP", "Internet Computer"), ("APT", "Aptos"), ("XMR", "Monero"), ("HBAR", "Hedera"),
    ("ETC", "Ethereum Classic"), ("ATOM", "Cosmos"), ("OP", "Optimism"), ("ARB", "Arbitrum"),
    ("FIL", "Filecoin"), ("INJ", "Injective"), ("IMX", "Immutable"), ("VET", "VeChain"),
    ("TAO", "Bittensor"), ("STX", "Stacks"), ("RENDER", "Render"), ("SEI", "Sei"),
    ("ALGO", "Algorand"), ("TIA", "Celestia"), ("FLOW", "Flow"), ("GRT", "The Graph"),
    ("AAVE", "Aave"), ("RUNE", "THORChain"), ("ENA", "Ethena"), ("JUP", "Jupiter"),
    ("WIF", "dogwifhat"), ("BONK", "Bonk"), ("CRV", "Curve DAO"),
    ("LDO", "Lido DAO"), ("PYTH", "Pyth Network"),
]
_TOP_SET = {base for base, _ in TOP_COINS}

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
    with httpx.Client(base_url=_BASE, headers=_UA, timeout=15.0) as client:
        r = client.get(path, params=params)
        r.raise_for_status()
        return r.json()


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


def get_markets() -> dict:
    """Top coin đã chọn với giá live Binance (1 request tổng, cache 6s)."""
    tickers = _all_tickers()
    items: list[dict] = []
    seen: set[str] = set()
    for rank, (base, name) in enumerate(TOP_COINS, start=1):
        pair = base + _QUOTE
        if pair in seen:
            continue
        seen.add(pair)
        t = tickers.get(pair)
        if not t:
            continue
        change = _f(t.get("priceChangePercent"))
        items.append(
            {
                "rank": rank,
                "symbol": base,
                "pair": pair,
                "name": name,
                "price": _f(t.get("lastPrice")),
                "change_pct": round(change, 2),
                "high": _f(t.get("highPrice")),
                "low": _f(t.get("lowPrice")),
                "volume": _f(t.get("quoteVolume")),  # Volume 24h quy đổi (USDT)
                "market_cap": None,  # Binance không cung cấp
            }
        )
    return {
        "quote": _QUOTE,
        "source": "binance",
        "count": len(items),
        "as_of": _now_utc().isoformat(),
        "items": items,
    }


def get_klines(symbol: str, interval: str = "1d", limit: int = 90) -> dict:
    """Nến giá Binance cho biểu đồ."""
    pair = str(symbol or "").upper()
    if not pair.endswith(_QUOTE):
        pair = pair + _QUOTE

    def _prod() -> dict:
        rows = _get("/api/v3/klines", {"symbol": pair, "interval": interval, "limit": int(limit)})
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
