"""Phân tích tín hiệu Crypto cho Trung tâm cảnh báo VinaFintech.

- Đối tượng: hợp WATCHLIST (bảng crypto_watchlist) + POSITIONS (user_assets class=crypto).
- Gom dữ liệu thật cho từng mã: nến 1D Binance → technical (MA/EMA/RSI/MACD/ATR/SR/breakout),
  quote spot (giá/%), funding + open interest (Binance Futures, nếu gọi được).
- Gửi toàn bộ prompt phân tích (CƠ HỘI/CẢNH BÁO/XU HƯỚNG) cho DeepSeek (không tool),
  yêu cầu trả JSON đúng schema. Parse + làm sạch + lưu bản scan mới nhất vào `settings`
  (key `crypto_signals:{user_id}`) kèm fingerprint để tránh lặp cảnh báo trùng.
- TUYỆT ĐỐI KHÔNG hard-code mã crypto, không khuyến nghị mua/bán, không bịa dữ liệu:
  mọi số liệu đều từ nguồn thật; nguồn thiếu (tin tức, liquidation) → bỏ qua/ghi rõ thiếu.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CryptoWatchlist, Setting, User, UserAsset, UserAssetClass
from app.services import crypto_service
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

# Trần output DeepSeek (deepseek-chat tối đa 8192). Trần cũ 5000 dễ làm JSON bị cắt giữa chừng
# khi danh mục nhiều mã → json.loads lỗi "Expecting ',' delimiter".
_MAX_OUTPUT_TOKENS = 8000

# Hệ thống prompt người dùng đặc tả — giữ nguyên bản.
SYSTEM_PROMPT = """Bạn là hệ thống phân tích tín hiệu Crypto của VinaFintech. Phân tích các tài sản mà người dùng đang theo dõi và đang có vị thế, sau đó tạo ra 3 nhóm tín hiệu: CƠ HỘI, CẢNH BÁO, XU HƯỚNG.

QUAN TRỌNG:
- Không được cố định bất kỳ mã crypto nào.
- Chỉ phân tích các mã được cung cấp trong WATCHLIST và POSITIONS.
- Một mã có thể đồng thời xuất hiện trong WATCHLIST và POSITIONS.
- Nếu một mã đang có vị thế, phải ưu tiên đánh giá rủi ro của vị thế đó.
- Nếu không có tín hiệu đủ mạnh thì không tạo tín hiệu.
- Không bịa dữ liệu.
- Không đưa ra khuyến nghị mua hoặc bán trực tiếp.
- Không khẳng định chắc chắn giá sẽ tăng hoặc giảm.

NGUYÊN TẮC PHÂN TÍCH:
Phân tích từng tài sản thuộc WATCHLIST hoặc POSITIONS. Đồng thời xem xét trạng thái chung của thị trường Crypto để tránh tạo tín hiệu sai lệch. Các yếu tố có thể sử dụng nếu được cung cấp: Giá, % thay đổi, Volume, Support, Resistance, MA20, MA50, MA200, EMA, RSI, MACD, ATR, Breakout, Breakdown, Funding Rate, Open Interest, Relative Strength, Market breadth... Không bắt buộc dùng tất cả. Chỉ dùng dữ liệu thực sự được cung cấp; dữ liệu nào thiếu hãy nói rõ trong `why` chứ không bịa.

1. CƠ HỘI: setup đáng chú ý như gần hỗ trợ, retest hỗ trợ, breakout, retest breakout, momentum tăng, reversal, relative strength tăng, volume xác nhận, xu hướng mới. Không gọi là "cơ hội mua". Dùng cách diễn đạt: Đáng chú ý / Đang hình thành setup / Đang tiếp cận vùng quan trọng / Có thể theo dõi / Chờ xác nhận.

2. CẢNH BÁO: giá giảm/tăng mạnh, breakdown, phá hỗ trợ, tiến gần kháng cự, volume bất thường, RSI quá mua/quá bán, thay đổi xu hướng, funding/OI bất thường, rủi ro vị thế đang giữ... Nếu tài sản ở POSITIONS ưu tiên cảnh báo yếu tố ảnh hưởng vị thế. Mức độ: LOW/MEDIUM/HIGH/CRITICAL. KHÔNG tạo cảnh báo chỉ vì 1 indicator đơn lẻ.

3. XU HƯỚNG: đánh giá ngắn hạn/trung hạn/dài hạn (BULLISH/NEUTRAL/BEARISH) + trend strength 0-100 + momentum 0-100. Chỉ tạo khi có thay đổi/tín hiệu đủ đáng chú ý. Không tạo quá nhiều card trùng.

ĐIỂM ƯU TIÊN: mỗi tín hiệu có priority 0-100. Ưu tiên: rủi ro nghiêm trọng vị thế > breakdown/breakout quan trọng > biến động bất thường > thay đổi xu hướng > cơ hội setup mạnh > tín hiệu nhỏ. Nhiều tín hiệu cùng 1 sự kiện → gộp thành 1.

OUTPUT: Chỉ trả về JSON hợp lệ (không kèm giải thích ngoài JSON):
{"opportunities":[{"symbol":"","title":"","severity":"LOW|MEDIUM|HIGH","priority":0,"price":0,"zone":"","description":"","why":"","strength":0,"confidence":0,"confirmation":""}],"alerts":[{"symbol":"","title":"","severity":"LOW|MEDIUM|HIGH|CRITICAL","priority":0,"price":0,"description":"","why":"","strength":0,"confidence":0}],"trends":[{"symbol":"","title":"","short_term":"BULLISH|NEUTRAL|BEARISH","medium_term":"BULLISH|NEUTRAL|BEARISH","long_term":"BULLISH|NEUTRAL|BEARISH","strength":0,"description":""}]}

QUY TẮC CUỐI: Không bắt buộc đủ 3 nhóm — ít nhưng chất lượng cao. Mỗi nhóm TỐI ĐA 8 tín hiệu; `description` và `why` viết ngắn gọn (mỗi trường ≤ 240 ký tự) để câu trả lời không bị cắt. Trả về đúng các mã thuộc danh sách được cấp. Giá trị price ghi số thật từ dữ liệu (không bịa). strength/confidence trong 0-100."""

_SETTING_PREFIX = "crypto_signals:"
_FUTURES_BASES = ("https://fapi.binance.com", "https://fapi.binance.com")


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(len(closes) - period, len(closes)):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _macd(closes: list[float]) -> dict | None:
    if len(closes) < 35:
        return None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = _ema_series(macd, 9)
    hist = (macd[-1] - sig[-1]) if macd and sig else None
    prev = (macd[-2] - sig[-2]) if len(macd) > 1 and len(sig) > 1 else None
    return {"macd": round(macd[-1], 8), "signal": round(sig[-1], 8), "histogram": round(hist, 8) if hist is not None else None, "bullish_cross": bool(prev is not None and hist is not None and prev <= 0 < hist)}


def _atr(bars: list[dict], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _nearest_sr(bars: list[dict], price: float, span: int = 60):
    """Đỉnh/đáy fractal + gom cụm 0.4% → support/resistance gần nhất (đơn giản)."""
    if not bars or not (price > 0):
        return None, None
    use = bars[-span:]
    highs = [b["high"] for b in use]
    lows = [b["low"] for b in use]
    pivots = []
    for i in range(2, len(use) - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
            pivots.append(highs[i])
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
            pivots.append(lows[i])
    if not pivots:
        return None, None
    pivots.sort()
    zones = []
    for v in pivots:
        z = next((z for z in zones if abs(v - z["c"]) / z["c"] <= 0.004), None)
        if z:
            z["min"] = min(z["min"], v)
            z["max"] = max(z["max"], v)
            z["c"] = (z["c"] * z["n"] + v) / (z["n"] + 1)
            z["n"] += 1
        else:
            zones.append({"min": v, "max": v, "c": v, "n": 1})
    support = next((z for z in sorted(zones, key=lambda x: -x["c"]) if z["max"] < price), None)
    resistance = next((z for z in sorted(zones, key=lambda x: x["c"]) if z["min"] > price), None)
    return (support, resistance)


def _clip(v, lo=0, hi=100):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, round(n, 2)))


def _severity_valid(v, allowed):
    s = str(v or "").strip().upper()
    return s if s in allowed else allowed[-1]


async def _fetch_futures(symbol: str) -> dict:
    """Funding rate + Open Interest từ Binance Futures (best-effort, lỗi → {})."""
    out: dict = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0)) as client:
        for base in _FUTURES_BASES:
            try:
                r = await client.get(f"{base}/fapi/v1/fundingRate", params={"symbol": symbol + "USDT", "limit": 1})
                if r.status_code == 200:
                    arr = r.json()
                    if isinstance(arr, list) and arr:
                        out["funding_rate"] = float(arr[0].get("fundingRate") or 0)
                r2 = await client.get(f"{base}/fapi/v1/openInterest", params={"symbol": symbol + "USDT"})
                if r2.status_code == 200:
                    d = r2.json()
                    oi = float(d.get("openInterest") or 0)
                    oiv = float(d.get("openInterestValue") or 0)
                    out["open_interest"] = oi
                    out["open_interest_usd"] = oiv
                if out:
                    return out
            except Exception:  # noqa: BLE001
                continue
    return out


def _gather_symbols(db: Session, user: User) -> list[str]:
    syms: list[str] = []
    for (s,) in db.query(CryptoWatchlist.symbol).filter(CryptoWatchlist.user_id == user.id).all():
        s = (s or "").strip().upper()
        if s and s not in syms:
            syms.append(s)
    for row in db.query(UserAsset).filter(
        UserAsset.user_id == user.id, UserAsset.asset_class == UserAssetClass("crypto")
    ).all():
        s = (row.label or "").strip().upper()
        if s and s not in syms:
            syms.append(s)
    return syms


def _positions_set(db: Session, user: User) -> set[str]:
    return {
        (row.label or "").strip().upper()
        for row in db.query(UserAsset).filter(
            UserAsset.user_id == user.id, UserAsset.asset_class == UserAssetClass("crypto")
        ).all()
    }


def _indicator_block(sym: str, bars: list[dict], price: float) -> dict:
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    ema12 = _ema_series(closes, 12)[-1] if len(closes) >= 12 else None
    ema26 = _ema_series(closes, 26)[-1] if len(closes) >= 26 else None
    last = closes[-1]
    vol_last = sum(b["volume"] for b in bars[-5:]) / 5 if len(bars) >= 5 else None
    vol_avg = sum(b["volume"] for b in bars[-40:]) / 40 if len(bars) >= 40 else None
    support, resistance = _nearest_sr(bars, price)
    roll = {
        "symbol": sym,
        "price": round(price, 8),
        "change_1d_pct": round((last / closes[-2] - 1) * 100, 2) if len(closes) > 1 and closes[-2] else None,
        "change_7d_pct": round((last / closes[-7] - 1) * 100, 2) if len(closes) > 7 and closes[-7] else None,
        "change_30d_pct": round((last / closes[-30] - 1) * 100, 2) if len(closes) > 30 and closes[-30] else None,
        "high_260d": round(max(highs), 8) if highs else None,
        "low_260d": round(min(lows), 8) if lows else None,
        "volume_ratio_5_40": round(vol_last / vol_avg, 2) if vol_last and vol_avg else None,
        "ma20": round(ma20, 8) if ma20 else None,
        "ma50": round(ma50, 8) if ma50 else None,
        "ma200": round(ma200, 8) if ma200 else None,
        "ema12": round(ema12, 8) if ema12 else None,
        "ema26": round(ema26, 8) if ema26 else None,
        "rsi14": round(rsi, 2) if (rsi := _rsi(closes)) is not None else None,
        "macd": _macd(closes),
        "atr14": round(atr, 4) if (atr := _atr(bars)) is not None else None,
        "support": {"low": round(support["min"], 8), "high": round(support["max"], 8), "touches": support["n"]} if support else None,
        "resistance": {"low": round(resistance["min"], 8), "high": round(resistance["max"], 8), "touches": resistance["n"]} if resistance else None,
        "above_ma20": bool(ma20 and price >= ma20),
        "above_ma50": bool(ma50 and price >= ma50),
        "above_ma200": bool(ma200 and price >= ma200),
        "breakout_20d_high": bool(price >= max(highs[-20:]) and len(highs) >= 20),
        "breakdown_20d_low": bool(price <= min(lows[-20:]) and len(lows) >= 20),
        "last_close": round(last, 8),
    }
    return roll


def _build_prompt(symbols: list[str], watch: set[str], positions: set[str], inds: list[dict], breadth: dict, deriv: dict) -> str:
    watch_txt = ", ".join(sorted(watch)) if watch else "(trống)"
    pos_txt = ", ".join(sorted(positions)) if positions else "(trống)"
    ind_txt = json.dumps(inds, ensure_ascii=False, default=str)
    br_txt = json.dumps(breadth, ensure_ascii=False, default=str)
    de_txt = json.dumps(deriv, ensure_ascii=False, default=str)
    return (
        "DỮ LIỆU NGƯỜI DÙNG:\nWATCHLIST: " + watch_txt + "\nPOSITIONS: " + pos_txt +
        "\n\nDỮ LIỆU THỊ TRƯỜNG (technical + quote, nến 1D Binance):\n" + ind_txt +
        "\n\nMARKET BREADTH / TỔNG QUAN (tính trên chính các mã được cấp):\n" + br_txt +
        "\n\nDERIVATIVES (Funding Rate + Open Interest Binance Futures; rỗng = nguồn không lấy được):\n" + de_txt +
        "\n\nNEWS DATA: (hiện VinaFintech chưa có nguồn tin crypto — bỏ qua, đừng bịa).\n" +
        "\nHãy phân tích CHỈ các mã trong WATCHLIST/POSITIONS và trả về JSON theo schema đã quy định."
    )


def _load_stored(db: Session, user: User) -> dict | None:
    row = db.query(Setting).filter(Setting.key == _SETTING_PREFIX + str(user.id)).first()
    if not row:
        return None
    try:
        return json.loads(row.value)
    except Exception:  # noqa: BLE001
        return None


def _store(db: Session, user: User, data: dict) -> None:
    key = _SETTING_PREFIX + str(user.id)
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = json.dumps(data, ensure_ascii=False, default=str)
    else:
        db.add(Setting(key=key, value=json.dumps(data, ensure_ascii=False, default=str)))
    db.commit()


async def _llm_json(system: str, user_prompt: str) -> dict:
    """Gọi DeepSeek ở chế độ JSON, trả `{content, finish_reason, usage}`.

    `finish_reason == "length"` nghĩa là model bị cắt vì chạm `max_tokens` → JSON có thể cụt
    (vẫn cứu được phần đã sinh xong nhờ `parse_llm_json`).
    """
    if not settings.deepseek_api_key:
        raise RuntimeError("VinaF AI chưa được cấu hình API key DeepSeek (DEEPSEEK_API_KEY).")
    base = (settings.deepseek_base_url or "https://api.deepseek.com").rstrip("/")
    timeout = httpx.Timeout(180.0, connect=12.0)
    payload = {
        "model": settings.deepseek_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
        "temperature": 0.2,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(base + "/chat/completions", json=payload, headers={
            "Authorization": "Bearer " + settings.deepseek_api_key,
            "Content-Type": "application/json",
        })
    if resp.status_code == 401:
        raise RuntimeError("API key DeepSeek không hợp lệ.")
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek trả lỗi {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    return {
        "content": choice.get("message", {}).get("content") or "",
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage") or {},
    }


def _normalize(items: list, kind: str, allowed_symbols: set[str]) -> list[dict]:
    out: list[dict] = []
    allowed = {"LOW", "MEDIUM", "HIGH", "CRITICAL"} if kind == "alerts" else {"LOW", "MEDIUM", "HIGH"}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym or sym not in allowed_symbols:
            continue
        try:
            price = float(it.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        row = {
            "symbol": sym,
            "title": str(it.get("title") or "").strip(),
            "severity": _severity_valid(it.get("severity"), allowed),
            "priority": int(_clip(it.get("priority"), 0, 100)),
            "price": round(price, 8),
            "description": str(it.get("description") or "").strip(),
            "why": str(it.get("why") or "").strip(),
            "strength": int(_clip(it.get("strength"))),
            "confidence": int(_clip(it.get("confidence"))),
        }
        if kind == "opportunities":
            row["zone"] = str(it.get("zone") or "").strip()
            row["confirmation"] = str(it.get("confirmation") or "").strip()
        if not row["title"]:
            continue
        out.append(row)
    out.sort(key=lambda x: -x.get("priority", 0))
    return out


def _normalize_trends(items: list, allowed_symbols: set[str]) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym or sym not in allowed_symbols:
            continue
        def tr(k):
            v = str(it.get(k) or "NEUTRAL").strip().upper()
            return v if v in ("BULLISH", "NEUTRAL", "BEARISH") else "NEUTRAL"
        out.append({
            "symbol": sym,
            "title": str(it.get("title") or "").strip(),
            "short_term": tr("short_term"),
            "medium_term": tr("medium_term"),
            "long_term": tr("long_term"),
            "strength": int(_clip(it.get("strength"))),
            "description": str(it.get("description") or "").strip(),
        })
    out.sort(key=lambda x: -x.get("strength", 0))
    return out


def _fingerprint(payload: dict) -> str:
    """Dấu vân tay nội dung — so sánh giữa các lần quét để tránh lặp cảnh báo."""
    parts = []
    for group in ("opportunities", "alerts", "trends"):
        for it in payload.get(group, []):
            p = str(it.get("price") or "")
            z = str(it.get("zone") or "")
            sig = "|".join([str(it.get(k) or "") for k in ("symbol", "title", "severity")]) + f"|{p}|{z}|{it.get('confidence','')}|{it.get('strength','')}"
            parts.append(sig)
    return "||".join(sorted(parts))


async def scan_user(db: Session, user: User) -> dict:
    symbols = _gather_symbols(db, user)
    if not symbols:
        return {"ok": False, "message": "Chưa có coin nào trong WATCHLIST/POSITIONS để phân tích."}
    positions = _positions_set(db, user)
    watch_all = {(r or "").strip().upper() for (r,) in db.query(CryptoWatchlist.symbol).filter(CryptoWatchlist.user_id == user.id).all()}
    watch = {s for s in symbols if s in watch_all}
    # giới hạn 24 mã để prompt vừa phải
    symbols = symbols[:24]

    inds: list[dict] = []
    deriv: dict = {}

    async def one(sym: str) -> None:
        d = await asyncio.to_thread(crypto_service.get_klines, sym, "1d", 260)
        items = (d or {}).get("items") or []
        if not items:
            return
        bars = [{"high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"]), "volume": float(b.get("volume") or 0)} for b in items if b.get("close")]
        if not bars:
            return
        price = float(bars[-1]["close"])
        inds.append(_indicator_block(sym, bars, price))

    # thêm funding/OI cho tối đa 12 mã để không quá tải
    deriv_targets = symbols[:12]

    async def deriv_one(sym: str) -> None:
        fx = await _fetch_futures(sym)
        if fx:
            deriv[sym] = fx

    await asyncio.gather(*[one(s) for s in symbols], *[deriv_one(s) for s in deriv_targets])

    ups = sum(1 for i in inds if (i.get("change_1d_pct") or 0) > 0)
    downs = sum(1 for i in inds if (i.get("change_1d_pct") or 0) < 0)
    flat = len(inds) - ups - downs
    total_usd = sum(i.get("price") or 0 for i in inds)
    btc = next((i for i in inds if i["symbol"] == "BTC"), None)
    breadth = {
        "symbols_analyzed": len(inds),
        "up_24h": ups, "down_24h": downs, "flat_24h": flat,
        "advancers_pct": round(ups / len(inds) * 100, 1) if inds else 0,
        "total_value_usd": round(total_usd, 2),
        "btc_change_24h_pct": btc.get("change_1d_pct") if btc else None,
    }

    user_prompt = _build_prompt(symbols, watch, positions, inds, breadth, deriv)
    llm = await _llm_json(SYSTEM_PROMPT, user_prompt)
    truncated = llm.get("finish_reason") == "length"
    try:
        parsed = parse_llm_json(llm.get("content") or "")
    except ValueError as exc:
        logger.warning(
            "crypto-signals user=%s: JSON không dùng được (finish_reason=%s, usage=%s, len=%s) — cuối phản hồi: %s",
            user.id, llm.get("finish_reason"), llm.get("usage"), len(llm.get("content") or ""),
            (llm.get("content") or "")[-400:],
        )
        if truncated:
            raise RuntimeError(
                "AI trả lời quá dài nên bị cắt và không khôi phục được dữ liệu. Vui lòng bấm \u201cQuét tín hiệu\u201d để thử lại."
            ) from exc
        raise RuntimeError("AI trả về dữ liệu không đúng định dạng. Vui lòng bấm \u201cQuét tín hiệu\u201d để thử lại.") from exc
    if truncated:
        logger.warning("crypto-signals user=%s: phản hồi AI bị cắt (max_tokens) — đã khôi phục phần hoàn chỉnh.", user.id)

    allowed = set(symbols)
    payload = {
        "ok": True,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "in_position": sorted(positions),
        "truncated": truncated,
        "opportunities": _normalize(parsed.get("opportunities"), "opportunities", allowed),
        "alerts": _normalize(parsed.get("alerts"), "alerts", allowed),
        "trends": _normalize_trends(parsed.get("trends"), allowed),
    }
    prev = _load_stored(db, user)
    fp = _fingerprint(payload)
    # Tránh lặp: nếu content giống bản trước thì chỉ cập nhật scanned_at, không làm mới giờ tạo từng tín hiệu.
    payload["fingerprint"] = fp
    if prev and prev.get("fingerprint") == fp and not (prev.get("ok")):
        payload["updated_same"] = True
    _store(db, user, payload)
    return payload


def latest(db: Session, user: User) -> dict:
    data = _load_stored(db, user)
    if not data:
        return {"ok": True, "empty": True, "message": "Chưa quét tín hiệu Crypto lần nào."}
    return data
