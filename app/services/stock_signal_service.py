"""Phân tích tín hiệu Cổ phiếu (AI) cho Trung tâm cảnh báo VinaFintech.

- Đối tượng: hợp WATCHLIST (bảng watchlist) + POSITIONS (portfolio_holdings).
- Gom dữ liệu thật cho từng mã: nến 1D (lịch sử VN, đồng VND) → technical
  (MA/EMA/RSI/MACD/ATR/SR/breakout), % thay đổi, khối lượng; kèm thông tin vị thế
  (quantity, giá vốn, lãi/lỗ %) để AI đánh giá rủi ro vị thế; bối cảnh thị trường VN
  (VN-Index + độ rộng + khối ngoại) từ get_market_dashboard (best-effort).
- Gửi prompt phân tích (CƠ HỘI/CẢNH BÁO/XU HƯỚNG) cho DeepSeek (không tool),
  yêu cầu trả JSON đúng schema (giống crypto). Parse + làm sạch + lưu bản mới nhất
  vào `settings` key `stock_signals:{user_id}` kèm fingerprint tránh lặp.
- TUYỆT ĐỐI KHÔNG hard-code mã cổ phiếu, không khuyến nghị mua/bán, không bịa dữ liệu:
  mọi số liệu từ nguồn thật; nguồn thiếu (tin tức chuyên sâu) → bỏ qua/ghi rõ thiếu.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import PortfolioHolding, Setting, User, Watchlist
from app.services import vnstock_service
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

# Trần output DeepSeek (deepseek-chat tối đa 8192). Trước đây 5000 → JSON bị cắt giữa chừng
# với danh mục ~18 mã (3 nhóm × nhiều tín hiệu), làm json.loads lỗi "Expecting ',' delimiter".
_MAX_OUTPUT_TOKENS = 8000

# Prompt phân tích cổ phiếu — giữ nguyên cấu trúc 3 nhóm của hệ thống (CƠ HỘI/CẢNH BÁO/XU HƯỚNG).
SYSTEM_PROMPT = """Bạn là hệ thống phân tích tín hiệu cổ phiếu của VinaFintech (thị trường chứng khoán Việt Nam: HOSE/HNX/UPCOM, đơn vị giá là đồng VND). Phân tích các mã người dùng đang nắm giữ và theo dõi, sau đó tạo 3 nhóm tín hiệu: CƠ HỘI, CẢNH BÁO, XU HƯỚNG.

QUAN TRỌNG:
- Không được cố định bất kỳ mã cổ phiếu nào.
- Chỉ phân tích các mã được cung cấp trong WATCHLIST và POSITIONS.
- Một mã có thể đồng thời xuất hiện trong WATCHLIST và POSITIONS (đang nắm giữ + theo dõi).
- Nếu một mã đang có vị thế (POSITIONS có số lượng + giá vốn), phải ưu tiên đánh giá rủi ro của vị thế đó (lãi/lỗ so với giá hiện tại).
- Nếu không có tín hiệu đủ mạnh thì không tạo tín hiệu.
- Không bịa dữ liệu.
- Không đưa ra khuyến nghị mua hoặc bán trực tiếp.
- Không khẳng định chắc chắn giá sẽ tăng hoặc giảm.

NGUYÊN TẮC PHÂN TÍCH:
Phân tích từng mã thuộc WATCHLIST hoặc POSITIONS. Đồng thời xem xét trạng thái chung của thị trường chứng khoán Việt Nam (VN-Index, độ rộng tăng/giảm, khối ngoại) để tránh tín hiệu sai lệch. Các yếu tố có thể dùng nếu được cung cấp: Giá (VND), % thay đổi (phiên/tuần/tháng), Khối lượng, Vùng Hỗ trợ/Kháng cự, MA20, MA50, MA200, EMA12/26, RSI14, MACD, ATR, Breakout/Breakdown, xu hướng ngắn/trung/dài, thông tin vị thế (số lượng, giá vốn, lãi/lỗ)... Không bắt buộc dùng tất cả. Chỉ dùng dữ liệu thực sự được cung cấp; dữ liệu nào thiếu hãy nói rõ trong `why` chứ không bịa.

1. CƠ HỘI: setup đáng chú ý như gần hỗ trợ, retest hỗ trợ, breakout, retest breakout, momentum tăng, reversal, volume xác nhận, xu hướng mới, dòng tiền quay lại. Không gọi là "cơ hội mua". Dùng cách diễn đạt: Đáng chú ý / Đang hình thành setup / Đang tiếp cận vùng quan trọng / Có thể theo dõi / Chờ xác nhận.

2. CẢNH BÁO: giá giảm/tăng mạnh, breakdown, phá hỗ trợ, tiến gần kháng cự, volume bất thường, RSI quá mua/quá bán, thay đổi xu hướng, rủi ro vị thế đang giữ (mã giảm sâu so với giá vốn / mất ngưỡng quan trọng)... Nếu mã ở POSITIONS ưu tiên cảnh báo yếu tố ảnh hưởng vị thế. Mức độ: LOW/MEDIUM/HIGH/CRITICAL. KHÔNG tạo cảnh báo chỉ vì 1 indicator đơn lẻ.

3. XU HƯỚNG: đánh giá ngắn hạn/trung hạn/dài hạn (BULLISH/NEUTRAL/BEARISH) + trend strength 0-100 + momentum 0-100. Chỉ tạo khi có thay đổi/tín hiệu đủ đáng chú ý. Không tạo quá nhiều card trùng.

ĐIỂM ƯU TIÊN: mỗi tín hiệu có priority 0-100. Ưu tiên: rủi ro nghiêm trọng vị thế > breakdown/breakout quan trọng > biến động bất thường > thay đổi xu hướng > cơ hội setup mạnh > tín hiệu nhỏ. Nhiều tín hiệu cùng 1 sự kiện → gộp thành 1.

OUTPUT: Chỉ trả về JSON hợp lệ (không kèm giải thích ngoài JSON):
{"opportunities":[{"symbol":"","title":"","severity":"LOW|MEDIUM|HIGH","priority":0,"price":0,"zone":"","description":"","why":"","strength":0,"confidence":0,"confirmation":""}],"alerts":[{"symbol":"","title":"","severity":"LOW|MEDIUM|HIGH|CRITICAL","priority":0,"price":0,"description":"","why":"","strength":0,"confidence":0}],"trends":[{"symbol":"","title":"","short_term":"BULLISH|NEUTRAL|BEARISH","medium_term":"BULLISH|NEUTRAL|BEARISH","long_term":"BULLISH|NEUTRAL|BEARISH","strength":0,"description":""}]}

QUY TẮC CUỐI: Không bắt buộc đủ 3 nhóm — ít nhưng chất lượng cao. Mỗi nhóm TỐI ĐA 8 tín hiệu; `description` và `why` viết ngắn gọn (mỗi trường ≤ 240 ký tự) để câu trả lời không bị cắt. Trả về đúng các mã thuộc danh sách được cấp. Giá trị price ghi số thật từ dữ liệu (đơn vị VND, không bịa). strength/confidence trong 0-100."""

_SETTING_PREFIX = "stock_signals:"
_MAX_SYMBOLS = 24


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
    return {"macd": round(macd[-1], 4), "signal": round(sig[-1], 4), "histogram": round(hist, 4) if hist is not None else None, "bullish_cross": bool(prev is not None and hist is not None and prev <= 0 < hist)}


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


def _gather_symbols(db: Session, user: User) -> list[str]:
    syms: list[str] = []
    for (s,) in db.query(Watchlist.symbol).filter(Watchlist.user_id == user.id).all():
        s = (s or "").strip().upper()
        if s and s not in syms:
            syms.append(s)
    for row in db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user.id).all():
        s = (row.symbol or "").strip().upper()
        if s and s not in syms:
            syms.append(s)
    return syms


def _positions_info(db: Session, user: User) -> list[dict]:
    rows = (
        db.query(PortfolioHolding)
        .filter(PortfolioHolding.user_id == user.id)
        .order_by(PortfolioHolding.symbol.asc())
        .all()
    )
    return [
        {
            "symbol": (r.symbol or "").strip().upper(),
            "quantity": round(float(r.quantity or 0), 6),
            "avg_price": round(float(r.avg_price or 0), 2),
        }
        for r in rows
        if (r.symbol or "").strip()
    ]


def _indicator_block(sym: str, bars: list[dict], price: float, holding: dict | None) -> dict:
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
    pnl_pct = None
    if holding and holding.get("avg_price"):
        pnl_pct = round((last / holding["avg_price"] - 1) * 100, 2)
    return {
        "symbol": sym,
        "price": round(price, 2),
        "change_1d_pct": round((last / closes[-2] - 1) * 100, 2) if len(closes) > 1 and closes[-2] else None,
        "change_5d_pct": round((last / closes[-5] - 1) * 100, 2) if len(closes) > 5 and closes[-5] else None,
        "change_1m_pct": round((last / closes[-21] - 1) * 100, 2) if len(closes) > 21 and closes[-21] else None,
        "change_3m_pct": round((last / closes[-63] - 1) * 100, 2) if len(closes) > 63 and closes[-63] else None,
        "change_6m_pct": round((last / closes[-126] - 1) * 100, 2) if len(closes) > 126 and closes[-126] else None,
        "high_260d": round(max(highs), 2) if highs else None,
        "low_260d": round(min(lows), 2) if lows else None,
        "volume_ratio_5_40": round(vol_last / vol_avg, 2) if vol_last and vol_avg else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "ema12": round(ema12, 2) if ema12 else None,
        "ema26": round(ema26, 2) if ema26 else None,
        "rsi14": round(rsi, 2) if (rsi := _rsi(closes)) is not None else None,
        "macd": _macd(closes),
        "atr14": round(atr, 2) if (atr := _atr(bars)) is not None else None,
        "support": {"low": round(support["min"], 2), "high": round(support["max"], 2), "touches": support["n"]} if support else None,
        "resistance": {"low": round(resistance["min"], 2), "high": round(resistance["max"], 2), "touches": resistance["n"]} if resistance else None,
        "above_ma20": bool(ma20 and price >= ma20),
        "above_ma50": bool(ma50 and price >= ma50),
        "above_ma200": bool(ma200 and price >= ma200),
        "breakout_20d_high": bool(price >= max(highs[-20:]) and len(highs) >= 20),
        "breakdown_20d_low": bool(price <= min(lows[-20:]) and len(lows) >= 20),
        "last_close": round(last, 2),
        "position_pnl_pct": pnl_pct,
        "position_quantity": holding.get("quantity") if holding else None,
        "position_avg_price": holding.get("avg_price") if holding else None,
    }


def _build_prompt(symbols: list[str], watch: set[str], positions: list[dict], inds: list[dict], market: dict) -> str:
    watch_txt = ", ".join(sorted(watch)) if watch else "(trống)"
    pos_txt = json.dumps(positions, ensure_ascii=False) if positions else "(trống — chưa nắm giữ mã nào)"
    ind_txt = json.dumps(inds, ensure_ascii=False, default=str)
    mk_txt = json.dumps(market, ensure_ascii=False, default=str)
    return (
        "DỮ LIỆU NGƯỜI DÙNG:\nWATCHLIST (đang theo dõi): " + watch_txt +
        "\nPOSITIONS (đang nắm giữ — symbol, số lượng, giá vốn VND): " + pos_txt +
        "\n\nDỮ LIỆU THỊ TRƯỜNG (technical + giá lịch sử 1D, đơn vị VND):\n" + ind_txt +
        "\n\nTHỊ TRƯỜNG CHUNG VN (VN-Index + độ rộng + khối ngoại; rỗng = nguồn không lấy được):\n" + mk_txt +
        "\n\nNEWS DATA: (VinaFintech chưa có nguồn tin tức chuyên sâu theo mã ở đây — bỏ qua, đừng bịa).\n" +
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


async def _vn_market_context() -> dict:
    """Bối cảnh thị trường VN (best-effort)."""
    try:
        d = await asyncio.to_thread(vnstock_service.get_market_dashboard)
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {"as_of": d.get("as_of", "")}
    indices = d.get("indices") or []
    for idx in indices:
        if str(idx.get("key") or "").lower() in ("vnindex", "vn-index"):
            out["vnindex"] = {
                "value": idx.get("value"),
                "change": idx.get("change"),
                "percent_change": idx.get("percent_change"),
            }
            break
    if not out.get("vnindex") and indices:
        idx = indices[0]
        out["vnindex"] = {"value": idx.get("value"), "change": idx.get("change"), "percent_change": idx.get("percent_change")}
    br = d.get("breadth") or {}
    if br:
        out["breadth"] = {k: br.get(k) for k in ("up", "down", "flat", "ceiling", "floor", "total") if k in br}
    fl = d.get("flow") or {}
    if fl.get("foreign_net") is not None:
        out["foreign_net"] = fl.get("foreign_net")
    return out


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
            "price": round(price, 2),
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
        return {"ok": False, "message": "Chưa có cổ phiếu nào trong WATCHLIST/POSITIONS để phân tích."}
    watch_all = {(r or "").strip().upper() for (r,) in db.query(Watchlist.symbol).filter(Watchlist.user_id == user.id).all()}
    watch = {s for s in symbols if s in watch_all}
    holdings = {p["symbol"]: p for p in _positions_info(db, user)}
    positions = [p for s in symbols if (p := holdings.get(s))]
    symbols = symbols[:_MAX_SYMBOLS]

    inds: list[dict] = []

    async def one(sym: str) -> None:
        try:
            hist = await asyncio.to_thread(vnstock_service.get_history, sym, "1d", "1y")
        except Exception:  # noqa: BLE001
            return
        if not hist:
            return
        bars = [
            {"high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"]), "volume": float(b.get("volume") or 0)}
            for b in hist
            if b.get("close")
        ]
        if not bars:
            return
        price = float(bars[-1]["close"])
        inds.append(_indicator_block(sym, bars, price, holdings.get(sym)))

    await asyncio.gather(*[one(s) for s in symbols])

    ups = sum(1 for i in inds if (i.get("change_1d_pct") or 0) > 0)
    downs = sum(1 for i in inds if (i.get("change_1d_pct") or 0) < 0)
    flat = len(inds) - ups - downs
    market = await _vn_market_context()
    if not market.get("breadth"):
        market["breadth"] = {"up": ups, "down": downs, "flat": flat, "symbols_analyzed": len(inds)}

    user_prompt = _build_prompt(symbols, watch, positions, inds, market)
    llm = await _llm_json(SYSTEM_PROMPT, user_prompt)
    truncated = llm.get("finish_reason") == "length"
    try:
        parsed = parse_llm_json(llm.get("content") or "")
    except ValueError as exc:
        logger.warning(
            "stock-signals user=%s: JSON không dùng được (finish_reason=%s, usage=%s, len=%s) — cuối phản hồi: %s",
            user.id, llm.get("finish_reason"), llm.get("usage"), len(llm.get("content") or ""),
            (llm.get("content") or "")[-400:],
        )
        if truncated:
            raise RuntimeError(
                "AI trả lời quá dài nên bị cắt và không khôi phục được dự liệu. Vui lòng bấm \u201cQuét cảnh báo\u201d để thử lại."
            ) from exc
        raise RuntimeError("AI trả về dữ liệu không đúng định dạng. Vui lòng bấm \u201cQuét cảnh báo\u201d để thử lại.") from exc
    if truncated:
        logger.warning("stock-signals user=%s: phản hồi AI bị cắt (max_tokens) — đã khôi phục phần hoàn chỉnh.", user.id)

    allowed = set(symbols)
    payload = {
        "ok": True,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "in_position": sorted({p["symbol"] for p in positions}),
        "positions_detail": positions,
        "truncated": truncated,
        "opportunities": _normalize(parsed.get("opportunities"), "opportunities", allowed),
        "alerts": _normalize(parsed.get("alerts"), "alerts", allowed),
        "trends": _normalize_trends(parsed.get("trends"), allowed),
    }
    fp = _fingerprint(payload)
    payload["fingerprint"] = fp
    _store(db, user, payload)
    return payload


def latest(db: Session, user: User) -> dict:
    data = _load_stored(db, user)
    if not data:
        return {"ok": True, "empty": True, "message": "Chưa quét tín hiệu Cổ phiếu lần nào."}
    return data
