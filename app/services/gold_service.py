"""Giá Vàng/Bạc — service tách tầng.

Kiến trúc:
  Vàng trong nước (giavang.org — nhiều thương hiệu)   → TuniX Backend → Frontend
  Vàng/Bạc thế giới (Yahoo Finance, GC=F / SI=F)      → TuniX Backend → Frontend

Nguyên tắc:
  - KHÔNG hard-code giá. Mọi giá trị đều lấy live từ nguồn.
  - Cache ngắn + single-flight (dict + lock) để không spam nguồn khi nhiều người xem.
  - Nguồn lỗi → giữ cache cũ (nếu có) hoặc trả payload lỗi có trường `error` —
    frontend hiển thị trạng thái, KHÔNG hiển thị dữ liệu giả.
  - Vàng trong nước: mỗi thương hiệu = 1 trang (Miếng + Nhẫn, có logo, có mốc cập nhật).
    Đồng thời lưu "snapshot" (Setting DB) để tính ▲/▼ thay đổi so với lần cập nhật trước.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from app.db import SessionLocal
from app.models import Setting

# ---------------------------------------------------------------- chung
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()

_VN_BASE = "https://giavang.org"
_Y_BASE = "https://query1.finance.yahoo.com"
_Y_BASE2 = "https://query2.finance.yahoo.com"

_VN_TTL = 120.0      # vàng trong nước
_WORLD_TTL = 30.0    # giá thế giới (poll frontend ~30s)
_CHART_TTL = {       # biểu đồ — theo độ ổn định dữ liệu
    "1D": 20.0,
    "1W": 60.0,
    "1M": 120.0,
    "3M": 300.0,
    "1Y": 300.0,
    "5Y": 600.0,
}
_SNAP_KEY = "gold_vn_snapshot"
_FX_TTL = 6 * 3600.0  # tỷ giá USD/VND đổi chậm → cache 6h
_FX_URL = "https://open.er-api.com/v6/latest/USD"
_FX_SRC = "open.er-api.com"
_GOLDAPI_BASE = "https://api.gold-api.com"  # fallback spot XAU/XAG (không cần key)
_GOLDAPI_SYM = {"xau": "XAU", "xag": "XAG"}

# Các thương hiệu vàng trong nước trên giavang.org (slug → tên hiển thị + logo)
VN_BRANDS: list[dict] = [
    {"slug": "sjc", "name": "SJC"},
    {"slug": "doji", "name": "DOJI"},
    {"slug": "pnj", "name": "PNJ"},
    {"slug": "bao-tin-minh-chau", "name": "Bảo Tín Minh Châu"},
    {"slug": "bao-tin-manh-hai", "name": "Bảo Tín Mạnh Hải"},
    {"slug": "phu-quy", "name": "Phú Quý"},
    {"slug": "mi-hong", "name": "Mi Hồng"},
    {"slug": "ngoc-tham", "name": "Ngọc Thẩm"},
]
_BRAND_URL = _VN_BASE + "/trong-nuoc/{slug}/"

# Thế giới: symbol hợp đồng tương lai COMEX (GC=F vàng, SI=F bạc) — USD/ounce
WORLD_ITEMS: list[dict] = [
    {"key": "xau", "name": "Vàng thế giới", "symbol": "XAU/USD", "yahoo": "GC=F"},
    {"key": "xag", "name": "Bạc thế giới", "symbol": "XAG/USD", "yahoo": "SI=F"},
]

PERIODS_CHART: dict[str, tuple[str, str]] = {
    # period -> (range, interval) của Yahoo chart API
    "1D": ("1d", "5m"),
    "1W": ("5d", "1h"),
    "1M": ("1mo", "1d"),
    "3M": ("3mo", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1d"),
}
_VN_TODAY_LABEL = "Cập nhật lúc "
_SRC_VN = "giavang.org"
_SRC_VN_URL = _VN_BASE + "/"
_SRC_WORLD = "Yahoo Finance (COMEX futures)"
_SRC_WORLD_URL = "https://finance.yahoo.com/quote/GC=F/"


def _now() -> float:
    return time.time()


_key_locks: dict[str, threading.Lock] = {}


def _key_lock(key: str) -> threading.Lock:
    with _lock:
        return _key_locks.setdefault(key, threading.Lock())


def _cached(key: str, ttl: float, producer):
    """Cache + single-flight THEO KEY. Chỉ khoá đúng key đang fetch (không khoá global)
    → domestic/world/chart/fx fetch song song; cùng 1 key không gọi mạng trùng lặp."""
    now = _now()
    with _lock:
        ent = _cache.get(key)
        if ent and now < ent[0]:
            return ent[1]
    kl = _key_lock(key)
    if kl.acquire(timeout=min(max(ttl, 5.0), 120.0) + 10.0):
        try:
            with _lock:
                ent = _cache.get(key)
                if ent and _now() < ent[0]:
                    return ent[1]
            value = producer()
            with _lock:
                ent = _cache.get(key)
                if not (ent and ent[0] > _now()):
                    _cache[key] = (_now() + ttl, value)
                    if len(_cache) > 512:
                        for k in [k for k, v in _cache.items() if v[0] < _now()]:
                            _cache.pop(k, None)
                return value
        finally:
            kl.release()
    # không giành được lock (thread khác đang fetch lâu) → chờ nó xong rồi đọc cache
    with _lock:
        ent = _cache.get(key)
        if ent:
            return ent[1]
    return producer()


def _http_get(url: str, timeout: float = 20.0) -> str:
    with httpx.Client(headers=_UA, timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def _num(text: str | None) -> float | None:
    """Trích số từ chuỗi — dấu '.' là phân tách nghìn kiểu VN, dấu ',' là thập phân.
    VD '144.600' → 144600 ; '1.234,56' → 1234.56. None nếu rỗng."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    if "," in s:
        # có thập phân: bỏ '.' (phân tách nghìn) rồi ',' → '.'
        return float(s.replace(".", "").replace(",", "."))
    # không thập phân: toàn bộ '.' là phân tách nghìn
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    return float(digits)


# ---------------------------------------------------------------- Vàng trong nước
def _product_kind(title: str) -> str:
    t = title.lower()
    if "miếng" in t or "mieng" in t:
        return "Vàng miếng"
    if "nhẫn" in t or "nhan" in t:
        return "Vàng nhẫn"
    # "Giá vàng X" → giữ phần còn lại
    rest = re.sub(r"^giá\s+vàng\s*", "", title, flags=re.I).strip()
    return rest or "Vàng"


def _parse_updated(text: str) -> float | None:
    """'Cập nhật lúc 20:25:22 06/09/2026' (giờ VN) → epoch (UTC)."""
    if not text:
        return None
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})\s+(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    hh, mm, ss, d, mo, y = (int(m.group(i)) for i in range(1, 7))
    try:
        local = datetime(y, mo, d, hh, mm, ss)  # giờ VN (UTC+7)
        epoch = (local - timedelta(hours=7) - datetime(1970, 1, 1)).total_seconds()
        return epoch
    except ValueError:
        return None


def _fetch_vn_brand(slug: str, name: str) -> dict | None:
    """Fetch + parse 1 thương hiệu → {slug,name,logo,updated_at,products:[...]}. Lỗi → None."""
    try:
        html = _http_get(_BRAND_URL.format(slug=slug), timeout=25.0)
    except Exception:  # noqa: BLE001 — 1 thương hiệu lỗi không làm hỏng toàn bộ
        return None
    soup = BeautifulSoup(html, "html.parser")
    updated: float | None = None
    h1 = soup.select_one("h1 small") or soup.select_one("h1")
    if h1:
        updated = _parse_updated(h1.get_text(" ", strip=True))
    products: list[dict] = []
    for box in soup.select(".gold-price-box"):
        for h2 in box.select("h2"):
            title = " ".join(h2.get_text(" ", strip=True).split())
            kind = _product_kind(title)
            row = h2.find_next_sibling("div", class_="row")
            if row is None:
                continue
            buy_el = row.select_one(".box-cgre .gold-price")
            sell_el = row.select_one(".box-cred .gold-price")
            if buy_el is None or sell_el is None:
                continue
            buy = _price_from_el(buy_el)
            sell = _price_from_el(sell_el)
            if buy is None or sell is None:
                continue
            products.append(
                {
                    "id": slug + ":" + kind.lower(),
                    "name": f"{kind} {name}",
                    "kind": kind,
                    "buy": buy,
                    "sell": sell,
                }
            )
    if not products:
        return None
    return {
        "slug": slug,
        "name": name,
        "logo": f"{_VN_BASE}/assets/images/gia-vang/logo-{slug}.png",
        "updated_at": int(updated) if updated else None,
        "products": products,
    }


def _price_from_el(el) -> float | None:
    """.gold-price có text trực tiếp '144.600 ' + small unit 'x1000đ/lượng' → VND thực."""
    direct = "".join(el.find_all(string=True, recursive=False))
    unit_el = el.select_one(".gold-unit")
    unit = unit_el.get_text(strip=True) if unit_el else ""
    mult = 1.0
    m = re.search(r"x\s*([\d.,]+)", unit)
    if m:
        mult = float(_num(m.group(1)) or 1.0)
    val = _num(direct)
    if val is None:
        return None
    return round(val * mult)


# ------------------------------------------------- snapshot cho ▲/▼ (đổi theo phiên cập nhật)
def _snapshot_get() -> dict:
    db = SessionLocal()
    try:
        row = db.query(Setting).filter(Setting.key == _SNAP_KEY).first()
        if row and row.value:
            return json.loads(row.value)
    except Exception:  # noqa: BLE001
        return {}
    finally:
        db.close()
    return {}


def _snapshot_set(value: dict) -> None:
    try:
        db = SessionLocal()
        try:
            row = db.query(Setting).filter(Setting.key == _SNAP_KEY).first()
            if row:
                row.value = json.dumps(value, ensure_ascii=False)
            else:
                db.add(Setting(key=_SNAP_KEY, value=json.dumps(value, ensure_ascii=False)))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — snapshot lỗi không làm hỏng dữ liệu chính
        pass


def _apply_deltas(brands: list[dict], today_iso: str) -> list[dict]:
    """Gắn buy_change/sell_change cho từng sản phẩm = chênh so với snapshot cũ nhất có được."""
    snap = _snapshot_get()
    cur_prices: dict[str, dict] = {}
    for br in brands:
        for p in br["products"]:
            cur_prices[p["id"]] = {"buy": p["buy"], "sell": p["sell"]}
    prev: dict[str, dict] | None = None
    dates = [k for k in snap.keys() if k != "today"]
    if dates:
        # ưu tiên snapshot có ngày khác hôm nay (mốc phiên trước)
        other = [d for d in dates if d != today_iso]
        pick = sorted(other or dates)[-1]
        prev = snap.get(pick)
    for br in brands:
        for p in br["products"]:
            old = (prev or {}).get(p["id"])
            p["buy_change"] = round(p["buy"] - old["buy"], 0) if old and old.get("buy") is not None else None
            p["sell_change"] = round(p["sell"] - old["sell"], 0) if old and old.get("sell") is not None else None
    # lưu snapshot hôm nay (dùng cho lần fetch sau)
    snap["today"] = today_iso
    snap[today_iso] = cur_prices
    # giữ tối đa vài ngày để không phình
    keys = sorted([k for k in snap.keys() if k != "today"])
    for k in keys[:-3]:
        snap.pop(k, None)
    _snapshot_set(snap)
    return brands


def get_domestic() -> dict:
    """Bảng giá vàng trong nước theo thương hiệu (live giavang.org, cache ~2 phút)."""
    def _prod() -> dict:
        def one(b):
            return _fetch_vn_brand(b["slug"], b["name"])

        brands: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(8, len(VN_BRANDS))) as ex:
            results = list(ex.map(one, VN_BRANDS))
        for b in results:
            if b is not None:
                brands.append(b)
        if not brands:
            raise RuntimeError("Khong lay duoc du lieu vang trong nuoc")
        updated = max([b["updated_at"] for b in brands if b.get("updated_at")] or [int(_now())])
        brands = _apply_deltas(brands, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        # sắp xếp thứ tự hiển thị theo thứ tự VN_BRANDS
        order = {b["slug"]: i for i, b in enumerate(VN_BRANDS)}
        brands.sort(key=lambda x: order.get(x["slug"], 999))
        return {
            "source": _SRC_VN,
            "source_url": _SRC_VN_URL,
            "as_of": updated,
            "brands": brands,
            "note": "Dữ liệu do giavang.org tổng hợp từ các thương hiệu; đơn vị: VND/lượng.",
        }

    try:
        return _cached("gold:vn", _VN_TTL, _prod)  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001 — nguồn lỗi → giữ cache cũ nếu có
        with _lock:
            ent = _cache.get("gold:vn")
        if ent:
            return ent[1]
        return {"error": str(e), "message": "Không lấy được giá vàng trong nước. Vui lòng thử lại sau."}


# ---------------------------------------------------------------- Thế giới
# Circuit-breaker đơn giản cho Yahoo: nếu bị 429/khóa (rate-limit) thì "ngậm miệng"
# một khoảng thời gian → request tiếp theo dùng thẳng nguồn dự phòng, không chờ timeout.
_YAHOO_BLOCK_UNTIL = 0.0
_YAHOO_BLOCK_429 = 300.0   # 429 rate-limit → chờ 5 phút
_YAHOO_BLOCK_ERR = 90.0    # lỗi mạng/timeout → chờ 90s
_YAHOO_TIMEOUT = 5.0


def _yahoo(symbol: str, rng: str, interval: str) -> dict | None:
    """Gọi Yahoo chart API → {'price', 'time', 'prev_close', 'ts':[], 'closes':[]}.
    Nếu Yahoo đang bị khóa (429) hoặc vừa lỗi liên tiếp → trả None ngay để dùng nguồn phụ."""
    global _YAHOO_BLOCK_UNTIL
    if time.time() < _YAHOO_BLOCK_UNTIL:
        return None
    blocked = False
    for base in (_Y_BASE, _Y_BASE2):
        url = f"{base}/v8/finance/chart/{symbol}?range={rng}&interval={interval}"
        try:
            txt = _http_get(url, timeout=_YAHOO_TIMEOUT)
            data = json.loads(txt)
            res = data["chart"]["result"][0]
            meta = res.get("meta", {})
            ts = res.get("timestamp") or []
            quote = (res.get("indicators", {}).get("quote") or [{}])[0]
            closes_all = quote.get("close") or []
            closes: list[float] = []
            for i, c in enumerate(closes_all):
                if c is None or i >= len(ts):
                    continue
                closes.append(float(c))
            if not closes:
                continue
            return {
                "price": float(meta.get("regularMarketPrice") or closes[-1]),
                "time": int(meta.get("regularMarketTime") or ts[-1]),
                "prev_close": float(meta.get("chartPreviousClose")) if meta.get("chartPreviousClose") else None,
                "ts": [int(t) for t in ts[: len(closes)]],
                "closes": closes,
            }
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 429:
                blocked = True  # rate-limit
            continue
        except Exception:  # noqa: BLE001 — timeout/network
            continue
    if blocked:
        _YAHOO_BLOCK_UNTIL = time.time() + _YAHOO_BLOCK_429
    else:
        # cả 2 host đều lỗi (timeout/network) → chặn ngắn để không chờ timeout lặp lại
        _YAHOO_BLOCK_UNTIL = time.time() + _YAHOO_BLOCK_ERR
    return None


def _change_at(ts: list[int], closes: list[float], last_ts: int, seconds: int) -> tuple[float, float] | None:
    """(giá cách đây `seconds`, thay đổi) dựa trên bar cuối có ts <= last_ts - seconds."""
    if len(closes) < 2:
        return None
    target = last_ts - seconds
    anchor = None
    for i, t in enumerate(ts):
        if t <= target:
            anchor = i
        else:
            break
    if anchor is None:
        anchor = 0
    cur = closes[-1]
    ref = closes[anchor]
    if ref == 0:
        return None
    return (cur - ref, ((cur - ref) / abs(ref)) * 100.0)


def _spot_alt(metal: str) -> dict | None:
    """Giá spot thật dự phòng (gold-api.com) khi Yahoo lỗi/tạm khóa — chỉ giá hiện tại + mốc thời gian."""
    sym = _GOLDAPI_SYM.get(metal, "XAU")
    try:
        data = json.loads(_http_get(f"{_GOLDAPI_BASE}/price/{sym}", timeout=8.0))
        price = float(data.get("price") or 0)
        if price <= 0:
            return None
        t: int | None = None
        s = data.get("updatedAt")
        if s:
            try:
                t = int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                t = None
        return {"price": price, "time": t or int(_now())}
    except Exception:  # noqa: BLE001 — nguồn phụ lỗi → trả None
        return None


def _world_item(key: str, name: str, symbol: str, ysymbol: str) -> dict:
    d = _yahoo(ysymbol, "1y", "1d")
    if d is None:
        # Fallback: Yahoo lỗi/429 (rate-limit) → lấy giá spot hiện tại từ gold-api.
        alt = _spot_alt(key)
        if alt is None:
            return {"key": key, "name": name, "symbol": symbol, "error": True}
        return {
            "key": key,
            "name": name,
            "symbol": symbol,
            "price": alt["price"],
            "unit": "USD/ounce",
            "time": alt["time"],
            "fallback": True,
            "change_24h": None,
            "change_24h_pct": None,
            "change_1w": None,
            "change_1m": None,
            "change_1y": None,
            "change_1y_pct": None,
        }
    ts, closes, last_ts = d["ts"], d["closes"], int(d["ts"][-1] if d["ts"] else d["time"])
    ch24 = _change_at(ts, closes, last_ts, 24 * 3600)
    ch1w = _change_at(ts, closes, last_ts, 7 * 24 * 3600)
    ch1m = _change_at(ts, closes, last_ts, 30 * 24 * 3600)
    # 1 năm: từ bar đầu (~365 ngày trước)
    ch1y = None
    if len(closes) >= 2 and closes[0]:
        ch1y = (closes[-1] - closes[0], ((closes[-1] - closes[0]) / abs(closes[0])) * 100.0)
    return {
        "key": key,
        "name": name,
        "symbol": symbol,
        "price": d["price"],
        "unit": "USD/ounce",
        "time": d["time"],
        "change_24h": ch24[0] if ch24 else None,
        "change_24h_pct": ch24[1] if ch24 else None,
        "change_1w": ch1w[0] if ch1w else None,
        "change_1m": ch1m[0] if ch1m else None,
        "change_1y": ch1y[0] if ch1y else None,
        "change_1y_pct": ch1y[1] if ch1y else None,
    }


def _get_usd_vnd() -> dict | None:
    """Tỷ giá USD/VND thật (open.er-api.com, không cần key, cache 6h). Lỗi → None."""
    def _prod() -> dict | None:
        try:
            data = json.loads(_http_get(_FX_URL, timeout=12.0))
            rates = data.get("rates") or {}
            vnd = rates.get("VND")
            if not vnd:
                return None
            ts = data.get("time_last_update_unix")
            return {"usd_vnd": float(vnd), "as_of": int(ts) if ts else int(_now())}
        except Exception:  # noqa: BLE001 — nguồn phụ lỗi không làm hỏng giá chính
            return None

    try:
        return _cached("gold:fx", _FX_TTL, _prod)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return None


def get_world() -> dict:
    """Giá thế giới XAU/XAG — live Yahoo (GC=F/SI=F), cache 30s. Kèm tỷ giá USD/VND để quy đổi."""
    def _prod() -> dict:
        items = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(_world_item, it["key"], it["name"], it["symbol"], it["yahoo"]): it for it in WORLD_ITEMS}
            for f in futs:
                items.append(f.result())
        times = [it.get("time") for it in items if it.get("time")]
        fx = _get_usd_vnd()
        return {
            "source": _SRC_WORLD,
            "source_url": _SRC_WORLD_URL,
            "as_of": max(times) if times else int(_now()),
            "items": items,
            "fx": fx,
        }

    try:
        return _cached("gold:world", _WORLD_TTL, _prod)  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        with _lock:
            ent = _cache.get("gold:world")
        if ent:
            return ent[1]
        return {"error": str(e), "message": "Không lấy được giá vàng/bạc thế giới. Vui lòng thử lại sau."}


# ---------------------------------------------------------------- Biểu đồ vàng thế giới
def get_chart(period: str = "1W", metal: str = "xau") -> dict:
    """Chuỗi giá vàng thế giới (GC=F) cho biểu đồ. period: 1D/1W/1M/3M/1Y/5Y."""
    period = str(period or "1W").upper()
    if period not in PERIODS_CHART:
        period = "1W"
    ysymbol = "GC=F"
    if metal == "xag":
        ysymbol = "SI=F"

    def _prod() -> dict:
        rng, iv = PERIODS_CHART[period]
        d = _yahoo(ysymbol, rng, iv)
        points: list[dict] = []
        src: dict | None = d
        if d:
            _ts = d["ts"]
            points = [{"t": _ts[i] * 1000, "v": d["closes"][i]} for i in range(len(d["closes"]))]
        # 1D khi thị trường nghỉ (cuối tuần) → Yahoo 1d/5m rỗng → fallback 24 bar giờ gần nhất
        if len(points) < 2 and period == "1D":
            d2 = _yahoo(ysymbol, "5d", "1h")
            if d2:
                src = d2
                points = [{"t": d2["ts"][i] * 1000, "v": d2["closes"][i]} for i in range(len(d2["closes"]))][-24:]
        if not points:
            raise RuntimeError("Khong co diem du lieu")
        return {
            "symbol": "XAU/USD" if ysymbol == "GC=F" else "XAG/USD",
            "metal": metal,
            "period": period,
            "unit": "USD/ounce",
            "as_of": int(src["time"]),
            "current": src["price"],
            "points": points,
        }

    ttl = _CHART_TTL.get(period, 300.0)
    try:
        return _cached(f"gold:chart:{metal}:{period}", ttl, _prod)  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        with _lock:
            ent = _cache.get(f"gold:chart:{metal}:{period}")
        if ent:
            return ent[1]
        return {"error": str(e), "message": "Không lấy được dữ liệu biểu đồ. Vui lòng thử lại sau."}
