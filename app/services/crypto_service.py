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
_CAP_TTL = 3600.0  # market cap đổi chậm → cache 1h, ít tải nguồn phụ

# Market cap: Binance KHÔNG cung cấp → lấy từ CoinGecko (chỉ để hiển thị vốn hóa;
# giá vẫn luôn từ Binance). CoinGecko id theo symbol của TOP_COINS.
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_CG_IDS: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "XRP": "ripple",
    "SOL": "solana", "USDC": "usd-coin", "ADA": "cardano", "DOGE": "dogecoin",
    "TRX": "tron", "TON": "the-open-network", "AVAX": "avalanche-2", "LINK": "chainlink",
    "XLM": "stellar", "SHIB": "shiba-inu", "DOT": "polkadot", "LTC": "litecoin",
    "BCH": "bitcoin-cash", "NEAR": "near", "UNI": "uniswap", "PEPE": "pepe",
    "SUI": "sui", "ICP": "internet-computer", "APT": "aptos", "XMR": "monero",
    "HBAR": "hedera-hashgraph", "ETC": "ethereum-classic", "ATOM": "cosmos", "OP": "optimism",
    "ARB": "arbitrum", "FIL": "filecoin", "INJ": "injective-protocol", "IMX": "immutable-x",
    "VET": "vechain", "TAO": "bittensor", "STX": "stacks", "RENDER": "render-token",
    "SEI": "sei-network", "ALGO": "algorand", "TIA": "celestia", "FLOW": "flow",
    "GRT": "the-graph", "AAVE": "aave", "RUNE": "thorchain", "ENA": "ethena",
    "JUP": "jupiter-exchange-solana", "WIF": "dogwifcoin", "BONK": "bonk",
    "CRV": "curve-dao-token", "LDO": "lido-dao", "PYTH": "pyth-network",
}


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


def get_market_caps() -> dict[str, float]:
    """Vốn hóa (USD) theo CoinGecko — 1 request cho toàn bộ danh sách, cache 1h.
    Lỗi/hết id → trả {} để vẫn còn dữ liệu Binance (hiển thị '—')."""
    def _prod() -> dict[str, float]:
        ids = [cid for sym, cid in _CG_IDS.items() if sym in _TOP_SET]
        if not ids:
            return {}
        try:
            with httpx.Client(base_url=_COINGECKO_BASE, headers=_UA, timeout=15.0) as client:
                r = client.get("/coins/markets", params={"vs_currency": "usd", "ids": ",".join(ids), "per_page": 250})
                r.raise_for_status()
            by_id: dict[str, float] = {}
            for row in r.json():
                cap = row.get("market_cap")
                if isinstance(cap, (int, float)) and cap > 0:
                    by_id[str(row.get("id") or "")] = float(cap)
            # map symbol theo id
            id_to_sym = {cid: sym for sym, cid in _CG_IDS.items()}
            return {id_to_sym[k]: v for k, v in by_id.items() if k in id_to_sym}
        except Exception:  # noqa: BLE001 — nguồn phụ lỗi không được phá trang
            return {}

    return _cached("coingecko:caps", _CAP_TTL, _prod)


def get_coingecko_top(per_page: int = 250) -> list[dict]:
    """Top coin theo vốn hóa (CoinGecko, market_cap desc) — dùng để chọn ~100 mã có USDT trên Binance.
    Lỗi → [] (frontend giữ trạng thái lỗi, không vỡ)."""
    def _prod() -> list[dict]:
        try:
            with httpx.Client(base_url=_COINGECKO_BASE, headers=_UA, timeout=20.0) as client:
                r = client.get(
                    "/coins/markets",
                    params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": min(250, per_page), "page": 1, "sparkline": "false"},
                )
                r.raise_for_status()
            out: list[dict] = []
            for x in r.json():
                sym = str(x.get("symbol") or "").upper()
                if not sym:
                    continue
                cap = x.get("market_cap")
                out.append(
                    {
                        "symbol": sym,
                        "name": str(x.get("name") or sym),
                        "cap": float(cap) if isinstance(cap, (int, float)) and cap > 0 else None,
                    }
                )
            return out
        except Exception:  # noqa: BLE001 — nguồn phụ lỗi không phá trang
            return []

    return _cached("coingecko:top", _CAP_TTL, _prod)


def get_markets() -> dict:
    """~100 mã TOP theo vốn hóa (thứ hạng từ CoinGecko) có USDT trên Binance — giá/volume Binance."""
    tickers = _all_tickers()
    top = get_coingecko_top()
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
                "market_cap": entry.get("cap"),  # USD — CoinGecko (Binance không cung cấp)
            }
        )
        if len(items) >= 100:
            break
    return {
        "quote": _QUOTE,
        "source": "binance",
        "cap_source": "coingecko",
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
