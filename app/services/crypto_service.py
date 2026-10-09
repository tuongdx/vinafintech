"""Crypto — service lấy dữ liệu từ Binance (tách tầng khỏi giao diện).

Kiến trúc: Binance → TuniX Backend → Frontend (frontend KHÔNG gọi Binance trực tiếp).
- Danh sách: 1 request `GET /api/v3/ticker/24hr` (~3000+ ticker) → lọc ~100 mã có
  thanh khoản (quoteVolume 24h) cao nhất — KHÔNG bắn N request/coin.
- Klines (biểu đồ): `GET /api/v3/klines` theo từng cặp.
- Cache ngắn + single-flight theo luồng để polling hợp lý, không tạo tải API thừa.
- Không hiển thị vốn hóa (Binance không cung cấp market cap).
Không mua/bán, không khuyến nghị.
"""
import html
import json
import re
import threading
import time
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.db import SessionLocal
from app.models import Setting

_BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
# data-api.binance.vision là mirror market-data CHÍNH THỨC của Binance — KHÔNG geo-
# restrict, dùng được từ Mỹ / Render (api.binance.com bị geo-block ở Mỹ, trả 451).
_UA = {"User-Agent": "Mozilla/5.0 (TuniX/1.0)", "Accept": "application/json"}

_QUOTE = "USDT"
# Bybit public API — nguồn dự phòng KHÔNG geo/cloud-block (dùng được từ Render/SG khi
# Binance chặn IP datacenter trả 418). Không cần API key, chỉ dùng market-data công khai.
_BYBIT_BASE = "https://api.bybit.com"
_BYBIT_INTERVAL = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
_TICK_TTL = 6.0
_KLINES_TTL = 20.0

# ---- CoinGecko: nguồn PHỤ, CHỈ để lấy TÊN đầy đủ (Bitcoin, Ethereum…) + đoạn GIỚI THIỆU
# của coin. Binance chỉ trả MÃ (BTC) nên không thể có 2 thứ này từ nguồn chính.
# Best-effort: nguồn lỗi/chặn → coi như rỗng, KHÔNG được làm hỏng trang.
_CG_BASE = "https://api.coingecko.com/api/v3"
_CG_PAGES = (1, 2, 3)        # 3 × 250 coin theo vốn hóa → phủ phần lớn danh sách
_NAMES_KEY = "cg:names"
_NAMES_TTL = 86400.0         # 24h
_NAMES_FAIL_TTL = 900.0      # nguồn lỗi → thử lại sau 15 phút
_INTRO_TTL = 6 * 3600.0      # giới thiệu 1 coin: 6h
_INTRO_FAIL_TTL = 900.0
_names_state = {"refreshing": False}
_TAG_RE = re.compile(r"<[^>]+>")

# Bản dịch tiếng Việt (AI) của phần giới thiệu: giữ lâu (30 ngày) ở RAM + LƯU DB
# (key `crypto_intro_vi:{MÃ}`) để không phải gọi AI lại sau mỗi lần restart Render.
_INTRO_VI_TTL = 30 * 86400.0
_INTRO_PENDING_TTL = 20.0          # đang chờ dịch → cache ngắn để client hỏi lại sớm
_VI_SRC_MAX = 2500                 # ký tự nguồn tối đa gửi cho AI dịch (dài hơn → cắt tại cuối câu)
_INTRO_DB_PREFIX = "crypto_intro"
_INTRO_VI_DB_PREFIX = "crypto_intro_vi"   # khoá cũ (phiên bản trước) — đọc để nhập vào bản ghi mới
_REC_TTL = 30 * 86400.0            # bản ghi (tên + mô tả gốc + bản dịch): 30 ngày
_INTRO_TRY_COOLDOWN = 300.0        # không thử lại CoinGecko cho cùng 1 mã trong 5 phút
_CG_BLOCK_SEC = 180.0              # bị 429 → nghỉ gọi CoinGecko 3 phút (circuit-breaker)
_CG_MIN_INTERVAL = 1.2             # giãn cách tối thiểu giữa 2 request CoinGecko (điều tiết nhịp)
_cg_block = {"until": 0.0}
_cg_gate = threading.Lock()
_cg_last = {"at": 0.0}
_intro_try: dict[str, float] = {}
_translating: set[str] = set()
_translate_lock = threading.Lock()

_VI_SYSTEM = (
    "Bạn là biên dịch viên tài chính. Dịch đoạn giới thiệu về tiền mã hóa (crypto) sang "
    "TIẾNG VIỆT tự nhiên, dễ hiểu với nhà đầu tư Việt Nam. Giữ nguyên tên riêng/tên coin/mã "
    "(Bitcoin, Ethereum, BTC…) và giữ nguyên số liệu. KHÔNG thêm bình luận, giải thích hay "
    "tiêu đề, KHÔNG tóm tắt — chỉ trả về bản dịch."
)


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


def _bybit_get(path: str, params: dict | None = None) -> object:
    """GET Bybit public API + kiểm tra retCode (0 = thành công)."""
    with httpx.Client(base_url=_BYBIT_BASE, headers=_UA, timeout=15.0) as client:
        r = client.get(path, params=params)
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and data.get("retCode") not in (0, None):
        raise RuntimeError(f"Bybit API error retCode={data.get('retCode')}")
    return data


def _bybit_tickers() -> dict[str, dict]:
    """Ticker spot Bybit → map về đúng dạng Binance để dùng chung logic xếp hạng."""

    def _prod() -> dict[str, dict]:
        data = _bybit_get("/v5/market/tickers", {"category": "spot"})
        out: dict[str, dict] = {}
        rows = ((data or {}).get("result") or {}).get("list") or []
        for row in rows:
            sym = str(row.get("symbol") or "")
            if not sym.endswith(_QUOTE):
                continue
            try:
                chg = float(row.get("price24hPcnt") or 0) * 100.0
            except (TypeError, ValueError):
                chg = 0.0
            out[sym] = {
                "symbol": sym,
                "priceChangePercent": chg,
                "lastPrice": row.get("lastPrice"),
                "highPrice": row.get("highPrice24h"),
                "lowPrice": row.get("lowPrice24h"),
                "quoteVolume": row.get("turnover24h"),
            }
        return out

    return _cached("tickers:bybit:24hr", _TICK_TTL, _prod)


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
    """TOÀN BỘ mã spot có cặp USDT và CÒN giao dịch, xếp theo quoteVolume 24h giảm dần.

    - Không giới hạn số lượng (trang Crypto hiện 20 mã rồi nạp thêm 10 mã mỗi lần cuộn).
    - Lọc bỏ mã ĐÃ NGỪNG giao dịch (quoteVolume = 0): /ticker/24hr vẫn trả chúng kèm
      giá tồn đọng (token đòn bẩy cũ, coin delist… ~63 mã) nên nếu không lọc thì danh
      sách có nhiều dòng “chết” bấm vào không có nến.
    - KHÔNG lọc theo tên kiểu “base chứa chữ số” như trước: luật đó loại oan coin thật
      (1INCH, 1000SATS, 1000CAT, C98, API3, KP3R…).
    """
    ranked = sorted(tickers.values(), key=lambda t: _f(t.get("quoteVolume")), reverse=True)
    out: list[dict] = []
    for t in ranked:
        sym = str(t.get("symbol") or "")
        if not sym.endswith(_QUOTE) or len(sym) <= len(_QUOTE):
            continue
        if _f(t.get("quoteVolume")) <= 0:
            continue
        base = sym[: -len(_QUOTE)]
        if not base:
            continue
        out.append({"symbol": base, "name": base})
    return out


# ===================== CoinGecko (nguồn PHỤ: tên + giới thiệu) =====================

def _cg_get(path: str, params: dict | None = None) -> object:
    """GET CoinGecko — chỉ dùng cho TÊN/giới thiệu coin (Binance không có 2 dữ liệu này).

    - CIRCUIT-BREAKER cho 429: đang bị chặn thì NGHỈ gọi mạng trong `_CG_BLOCK_SEC` giây
      và báo lỗi ngay (tránh vừa chậm vừa bị khoá IP lâu hơn).
    - ĐIỀU TIẾT nhịp gọi: CoinGecko free chỉ cho vài request/phút nên mọi lời gọi đi qua
      1 khoá + giãn cách `_CG_MIN_INTERVAL` giây. Nguồn này là phụ (tên + giới thiệu),
      không nằm trên đường tới hạn của giá/nến nên xếp hàng chờ là chấp nhận được.
    """
    if time.time() < _cg_block["until"]:
        raise RuntimeError("CoinGecko đang tạm bị giới hạn (429) — bỏ qua lần này")
    with _cg_gate:
        gap = _CG_MIN_INTERVAL - (time.time() - _cg_last["at"])
        if gap > 0:
            time.sleep(gap)
        with httpx.Client(base_url=_CG_BASE, headers=_UA, timeout=15.0) as client:
            r = client.get(path, params=params)
        _cg_last["at"] = time.time()
    if r.status_code == 429:
        _cg_block["until"] = time.time() + _CG_BLOCK_SEC
        raise RuntimeError("CoinGecko 429 Too Many Requests")
    r.raise_for_status()
    return r.json()


def _clean_text(s: str) -> str:
    """Bỏ thẻ HTML + entity + gộp khoảng trắng (mô tả CoinGecko trả về dạng HTML)."""
    txt = _TAG_RE.sub(" ", s or "")
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _cg_names_fetch() -> dict[str, dict]:
    """Tải TÊN coin theo vốn hóa giảm dần → {MÃ: {id, name}} (giữ coin uy tín nhất cho mỗi mã)."""
    out: dict[str, dict] = {}
    for page in _CG_PAGES:
        try:
            rows = _cg_get(
                "/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250,
                 "page": page, "sparkline": "false"},
            )
        except Exception:  # noqa: BLE001 — nguồn phụ lỗi/chặn → dừng, giữ phần đã có
            break
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            if sym and sym not in out:
                out[sym] = {"id": r.get("id"), "name": str(r.get("name") or sym)}
    return out


def _refresh_names() -> None:
    try:
        out = _cg_names_fetch()
        with _lock:
            _cache[_NAMES_KEY] = (time.time() + (_NAMES_TTL if out else _NAMES_FAIL_TTL), out)
    finally:
        with _lock:
            _names_state["refreshing"] = False


def _cg_names() -> dict[str, dict]:
    """Bảng tên coin đang có trong cache — KHÔNG chờ mạng.

    Hết hạn/trống → trả bản cũ (hoặc rỗng) ngay và làm mới ở luồng nền (single-flight);
    nhờ vậy /markets không bị chậm vì nguồn phụ này.
    """
    now = time.time()
    ent: tuple[float, object] | None
    with _lock:
        ent = _cache.get(_NAMES_KEY)
        if ent and now < ent[0]:
            return ent[1]  # type: ignore[return-value]
        if not _names_state["refreshing"]:
            _names_state["refreshing"] = True
            threading.Thread(target=_refresh_names, daemon=True, name="cg-names").start()
    return ent[1] if ent else {}  # type: ignore[return-value]


def _cg_find_id(sym: str) -> tuple[str | None, str | None]:
    """Tìm id CoinGecko theo MÃ (dùng khi mã chưa có trong bảng tên)."""
    data = _cg_get("/search", {"query": sym})
    for c in ((data or {}).get("coins") or []):
        if str(c.get("symbol") or "").upper() == sym:
            return c.get("id"), str(c.get("name") or "") or None
    return None, None


def _db_raw_get(key: str) -> str:
    """Đọc 1 giá trị trong bảng `settings` (best-effort — lỗi DB coi như chưa có)."""
    try:
        db = SessionLocal()
        try:
            row = db.query(Setting).filter(Setting.key == key).first()
            return str(row.value or "") if row else ""
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return ""


def _db_raw_put(key: str, value: str) -> None:
    """Ghi 1 giá trị vào bảng `settings` (best-effort — không làm hỏng luồng chính)."""
    try:
        db = SessionLocal()
        try:
            row = db.query(Setting).filter(Setting.key == key).first()
            if row:
                row.value = value
            else:
                db.add(Setting(key=key, value=value))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass


def _rec_get(sym: str) -> dict:
    """Bản ghi giới thiệu của 1 mã: {name, src, vi, vi_src, vi_error} — RAM trước, rồi DB.

    `src` = mô tả GỐC, `vi` = bản tiếng Việt (AI dịch hoặc nguồn có sẵn).
    Lưu 30 ngày ở cả RAM lẫn DB ⇒ mỗi coin chỉ tốn 1 lần CoinGecko + 1 lần gọi AI.
    """
    k = f"cg:rec:{sym}"
    now = time.time()
    with _lock:
        ent = _cache.get(k)
        if ent and now < ent[0]:
            return ent[1]  # type: ignore[return-value]
    rec: dict = {}
    raw = _db_raw_get(f"{_INTRO_DB_PREFIX}:{sym}")
    if raw:
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                rec = obj
        except Exception:  # noqa: BLE001
            rec = {}
    if not rec.get("vi"):     # bản dịch lưu theo khoá của phiên bản trước → nhập vào bản ghi
        legacy = _db_raw_get(f"{_INTRO_VI_DB_PREFIX}:{sym}")
        if legacy:
            rec["vi"] = legacy
            rec["vi_src"] = rec.get("vi_src") or "ai"
    with _lock:
        _cache[k] = (now + _REC_TTL, rec)
    return rec


def _rec_put(sym: str, rec: dict) -> None:
    """Lưu bản ghi vào RAM (30 ngày) + DB (sống qua restart/deploy)."""
    with _lock:
        _cache[f"cg:rec:{sym}"] = (time.time() + _REC_TTL, rec)
    _db_raw_put(f"{_INTRO_DB_PREFIX}:{sym}", json.dumps(rec, ensure_ascii=False))


def _cg_fetch_intro(sym: str) -> dict:
    """Lấy TÊN + mô tả gốc từ CoinGecko (1 lần cho mỗi mã). Lỗi/429 → trả phần đã biết."""
    info = _cg_names().get(sym) or {}
    cg_id = info.get("id")
    name = str(info.get("name") or sym)
    try:
        if not cg_id:
            cg_id, nm = _cg_find_id(sym)
            if nm:
                name = nm
        if not cg_id:
            return {"name": name}
        data = _cg_get(
            f"/coins/{cg_id}",
            {"localization": "true", "tickers": "false", "market_data": "false",
             "community_data": "false", "developer_data": "false", "sparkline": "false"},
        )
        name = str((data or {}).get("name") or name)
        dm = (data or {}).get("description") or {}
        vi_native = _clean_text(dm.get("vi") or "")
        src = _clean_text(dm.get("en") or "")
        if not src:   # mô tả ở ngôn ngữ khác (không phải vi/en) → lấy bản dài nhất
            others = [_clean_text(v) for k, v in dm.items() if k not in ("vi", "en") and v]
            src = max(others, key=len) if others else ""
        out: dict = {"name": name, "src": src}
        if vi_native:                       # nguồn đã có tiếng Việt → khỏi tốn lượt AI
            out["vi"] = vi_native
            out["vi_src"] = "coingecko"
        return out
    except Exception:  # noqa: BLE001 — nguồn phụ lỗi → không có mô tả
        return {"name": name}


def _trim_src(s: str, limit: int) -> str:
    """Cắt bớt mô tả quá dài TẠI CUỐI CÂU (tránh bản dịch bị đứt giữa câu)."""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    p = cut.rfind(". ")
    return (cut[:p + 1] if p > limit * 0.5 else cut).rstrip() + "…"


def _translate_vi(text: str) -> str:
    """Dịch sang tiếng Việt bằng AI đang dùng cho TuniAI (DeepSeek)."""
    src = (text or "").strip()
    if not src:
        return ""
    if not settings.deepseek_api_key:
        raise RuntimeError("chưa cấu hình DEEPSEEK_API_KEY")
    base = (settings.deepseek_base_url or "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _VI_SYSTEM},
            {"role": "user", "content": _trim_src(src, _VI_SRC_MAX)},
        ],
        "temperature": 0.3,
        "max_tokens": 1600,
        "stream": False,
    }
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=12.0)) as client:
        r = client.post(
            base + "/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer " + settings.deepseek_api_key,
                     "Content-Type": "application/json"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"DeepSeek trả lỗi {r.status_code}: {r.text[:160]}")
    choice = ((r.json().get("choices") or [{}])[0].get("message") or {})
    return _clean_text(str(choice.get("content") or ""))


def _start_translate(sym: str, src: str) -> None:
    """Dịch ở LUỒNG NỀN (1 lần cho mỗi mã) — request KHÔNG phải chờ AI 5-20 giây.

    Thành công → lưu `vi` + `vi_src='ai'` vào bản ghi; thất bại → đánh dấu `vi_error`
    để không gọi lại AI liên tục (chờ 1 giờ mới thử lại).
    """
    with _translate_lock:
        if sym in _translating or not src:
            return
        _translating.add(sym)

    def _run() -> None:
        try:
            vi = _translate_vi(src)
        except Exception:  # noqa: BLE001 — thiếu key/lỗi mạng → coi như chưa dịch
            vi = ""
        rec = _rec_get(sym)
        rec["src"] = rec.get("src") or src
        if vi:
            rec["vi"] = vi
            rec["vi_src"] = "ai"
            rec.pop("vi_error", None)
        else:
            rec["vi_error"] = time.time()
        _rec_put(sym, rec)
        with _translate_lock:
            _translating.discard(sym)

    threading.Thread(target=_run, daemon=True, name=f"cg-vi-{sym}").start()


def get_coin_intro(symbol: str) -> dict:
    """Tên đầy đủ + GIỚI THIỆU TIẾNG VIỆT của 1 coin (cache phản hồi 6h).

    Nội dung theo thứ tự: (1) bản tiếng Việt đã lưu (AI dịch / nguồn sẵn có) →
    (2) mô tả gốc + KÍCH HOẠT DỊCH AI Ở LUỒNG NỀN (trả `translating=True` để client hỏi lại) →
    (3) chưa có gì: gọi CoinGecko 1 lần/mã (có cooldown 10 phút khi lỗi) rồi lưu tên + mô tả gốc.
    Mọi nhánh đều best-effort (thiếu key AI, mạng lỗi, 429) → không bao giờ 500.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"symbol": "", "name": "", "description": "", "original": "", "source": "", "translating": False}

    key = f"cg:intro:{sym}"
    now = time.time()
    with _lock:
        ent = _cache.get(key)
        if ent and now < ent[0]:
            return ent[1]  # type: ignore[return-value]

    rec = _rec_get(sym)
    if not rec.get("src"):                     # chưa từng có mô tả → hỏi CoinGecko (có cooldown)
        with _lock:
            ask = (now - _intro_try.get(sym, 0.0)) >= _INTRO_TRY_COOLDOWN
            if ask:
                _intro_try[sym] = now
        if ask:
            fetched = _cg_fetch_intro(sym)
            if fetched.get("name"):
                rec["name"] = fetched["name"]
            if fetched.get("src"):
                rec["src"] = fetched["src"]
            if fetched.get("vi"):
                rec["vi"] = fetched["vi"]                       # nguồn đã có tiếng Việt
                rec["vi_src"] = fetched.get("vi_src") or "coingecko"
            if fetched:
                _rec_put(sym, rec)

    name = str(rec.get("name") or (_cg_names().get(sym) or {}).get("name") or sym)
    src = str(rec.get("src") or "")
    desc = str(rec.get("vi") or "")
    failed_at = float(rec.get("vi_error") or 0)
    if desc:
        source = str(rec.get("vi_src") or "ai")
    elif src and settings.deepseek_api_key and (not failed_at or now - failed_at > 3600):
        _start_translate(sym, src)             # AI dịch ở luồng nền (1 lần cho mỗi mã)
        source = "pending"
    elif src:
        desc = src                             # chưa cấu hình AI / AI lỗi → tạm hiển thị bản gốc
        source = "original"
    else:
        source = ""

    out = {"symbol": sym, "name": name, "description": desc,
           "original": src, "source": source, "translating": source == "pending"}
    ttl = _INTRO_TTL if desc else (_INTRO_PENDING_TTL if source == "pending" else _INTRO_FAIL_TTL)
    with _lock:
        _cache[key] = (time.time() + ttl, out)
    return out


def get_markets() -> dict:
    """TOÀN BỘ mã spot có cặp USDT trên Binance — xếp theo Volume 24h (thanh khoản).

    - Giá/Cao-Thấp/Volume 24h: Binance (host vision → api, cache 6s).
    - Thứ hạng theo thanh khoản Binance — không hiển thị vốn hóa.
    - Không phân trang ở backend: danh sách ~698 mã chỉ ~22 KB (gzip) nên trả 1 lần,
      frontend tự hiện dần 20 mã rồi nạp thêm 10 mã mỗi lần người dùng cuộn tới đáy.
    """
    source = "binance"
    try:
        tickers = _all_tickers()
    except Exception:
        # Binance bị geo/cloud-block (VD: Render region singapore trả 418) → thử Bybit.
        try:
            tickers = _bybit_tickers()
            source = "bybit"
        except Exception as e:  # noqa: BLE001 — cả Binance & Bybit đều lỗi
            return {
                "quote": _QUOTE,
                "source": "binance",
                "count": 0,
                "total": 100,
                "error": str(e),
                "message": "Không lấy được dữ liệu thị trường. Vui lòng thử lại sau.",
                "items": [],
            }

    top = _volume_ranking(tickers)
    names = _cg_names()   # tên đầy đủ (nguồn PHỤ CoinGecko) — chưa có thì dùng chính mã
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
                "name": names.get(base, {}).get("name") or entry.get("name") or base,
                "price": _f(t.get("lastPrice")),
                "change_pct": round(change, 2),
                "high": _f(t.get("highPrice")),
                "low": _f(t.get("lowPrice")),
                "volume": _f(t.get("quoteVolume")),  # Volume 24h quy đổi (USDT)
            }
        )
    return {
        "quote": _QUOTE,
        "source": source,
        "count": len(items),
        "total": len(items),
        "as_of": _now_utc().isoformat(),
        "items": items,
    }


def get_klines(symbol: str, interval: str = "1d", limit: int = 90,
               start_time: int | None = None, end_time: int | None = None) -> dict:
    """Nến giá Binance cho biểu đồ.

    ``start_time`` / ``end_time`` (mili-giây, tuỳ chọn) → lấy nến trong khoảng thời gian đó,
    dùng để TẢI THÊM lịch sử cũ khi người dùng kéo biểu đồ về quá khứ (Binance nhận
    startTime/endTime; Bybit nhận start/end). Không truyền thì lấy ``limit`` nến mới nhất.
    """
    pair = str(symbol or "").upper()
    if not pair.endswith(_QUOTE):
        pair = pair + _QUOTE
    t0 = int(start_time) if start_time else 0
    t1 = int(end_time) if end_time else 0

    def _prod() -> dict:
        out: list[dict] = []
        # 1) Binance; 2) Bybit (khi Binance bị cloud-block như Render/SG trả 418).
        params: dict[str, object] = {"symbol": pair, "interval": interval, "limit": int(limit)}
        if t0:
            params["startTime"] = t0
        if t1:
            params["endTime"] = t1
        try:
            rows = _get("/api/v3/klines", params)
        except Exception:  # noqa: BLE001 — Binance cả 2 host lỗi → thử Bybit
            rows = None
        if rows is None:
            try:
                by_int = _BYBIT_INTERVAL.get(interval, "D")
                by_params: dict[str, object] = {
                    "category": "spot", "symbol": pair, "interval": by_int, "limit": int(limit),
                }
                if t0:
                    by_params["start"] = t0
                if t1:
                    by_params["end"] = t1
                data = _bybit_get("/v5/market/kline", by_params)
                raw = ((data or {}).get("result") or {}).get("list") or []
                # Bybit trả list NGƯỢC (mới → cũ) → đảo để tăng dần theo thời gian.
                for row in reversed(raw):
                    if not isinstance(row, (list, tuple)) or len(row) < 6:
                        continue
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
            except Exception as e:  # noqa: BLE001 — Binance & Bybit đều lỗi → không làm 500
                return {
                    "symbol": pair,
                    "interval": interval,
                    "items": [],
                    "error": "Không lấy được dữ liệu biểu đồ từ Binance/Bybit: " + str(e),
                }
        else:
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

    return _cached(f"klines:{pair}:{interval}:{int(limit)}:{t0}:{t1}", _KLINES_TTL, _prod)
