"""Engine "Cơ hội nổi bật hôm nay".

Tự động phát hiện 3–5 cổ phiếu có tín hiệu thị trường đáng chú ý trong phiên, dựa trên
xu hướng giá, dòng tiền/thanh khoản, động lượng (RSI/MACD), vị trí giá, yếu tố cơ bản
và sức mạnh ngành — KHÔNG phải khuyến nghị mua/bán.

Nguyên tắc:
- Chỉ dùng dữ liệu thật (screener + history OHLCV). Không tạo dữ liệu giả.
- Mọi "lý do hiển thị" được sinh từ dữ liệu thật (không dùng AI).
- Candidate pool ~50 mã (thanh khoản trước) rồi mới gọi history tính MA/RSI/MACD.
- Cache kết quả ~10 phút + refresh nền (serve stale) để không làm chậm homepage.
- Data validation: thiếu/null/không hợp lệ → loại mã đó.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.services import vnstock_service as vns

logger = logging.getLogger("tunix.opportunity")

# ---------------- Cấu hình ----------------
_CACHE_TTL_SECONDS = 600          # kết quả tươi trong 10 phút
_POOL_SIZE = 50                   # candidate pool tối đa trước khi tính OHLCV
_MIN_VALUE_VND = 30_000_000_000   # giá trị giao dịch >= 30 tỷ/phiên
_MIN_VOL_RATIO = 0.80             # khối lượng >= 80% trung bình
_MIN_BARS = 60                    # cần >= 60 phiên để tính MA/RSI đủ tin cậy
_MIN_SCORE = 65                   # dưới ngưỡng không đưa vào block chính
_MAX_WORKERS = 8

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}       # {"ts":..., "data":..., "computing":bool}
_AUDIT: list[dict[str, Any]] = [] # log chi tiết phiên tính gần nhất (cho admin/debug)


# ---------------- Indicator helpers (bản nhỏ, thuần Python) ----------------
def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out: list[float] = []
    prev = values[0]
    out.append(prev)
    for v in values[1:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _ma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return round(100 - 100 / (1 + rs), 2)


def _macd(closes: list[float]) -> dict[str, Any] | None:
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd, 9)
    hist = [m - s for m, s in zip(macd, signal)]
    if len(macd) < 2:
        return None
    return {
        "macd": round(macd[-1], 4),
        "signal": round(signal[-1], 4),
        "hist": round(hist[-1], 4),
        "hist_prev": round(hist[-2], 4),
        "cross_up": hist[-2] <= 0 < hist[-1] or (len(hist) >= 3 and hist[-3] <= 0 < hist[-1] and hist[-2] > hist[-3] and hist[-1] > 0),
    }


def _closes(bars: list[dict[str, Any]]) -> list[float]:
    return [float(b["close"]) for b in bars if b.get("close") is not None]


def _volumes(bars: list[dict[str, Any]]) -> list[float]:
    return [float(b["volume"]) for b in bars if b.get("volume") is not None]


# ---------------- Helpers đọc dữ liệu ----------------
def _numbers(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _screener_stocks() -> list[dict[str, Any]]:
    try:
        payload = vns.get_stock_screener_page()
        return list(payload.get("stocks") or [])
    except Exception as exc:  # pragma: no cover
        logger.warning("opportunity: không lấy được screener: %s", exc)
        return []


def _index_pct() -> float:
    """% thay đổi hôm nay của VN-Index (fallback 0)."""
    try:
        for it in vns.get_market_overview():
            key = str(it.get("key") or it.get("symbol") or "").upper()
            if key in ("VNINDEX", "VN-INDEX"):
                return _numbers(it.get("percent_change"))
    except Exception as exc:  # pragma: no cover
        logger.warning("opportunity: không lấy được VN-Index: %s", exc)
    return 0.0


def _market_breadth(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    adv = dec = flat = 0
    total_value = 0.0
    for s in stocks:
        pct = _numbers(s.get("percent_change"))
        value = _numbers(s.get("price")) * _numbers(s.get("volume"))
        total_value += value
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            flat += 1
    n = max(1, adv + dec + flat)
    return {"adv": adv, "dec": dec, "flat": flat, "adv_ratio": round(adv / n, 3), "total_value": total_value}


def _sector_strengths(stocks: list[dict[str, Any]], vn_pct: float) -> dict[str, dict[str, Any]]:
    """Sức mạnh tương đối của từng ngành so với VN-Index (dựa screener intraday)."""
    agg: dict[str, list[float]] = {}
    for s in stocks:
        sec = str(s.get("sectorName") or "").strip()
        if not sec:
            continue
        pct = _numbers(s.get("percent_change"))
        value = _numbers(s.get("price")) * _numbers(s.get("volume"))
        bucket = agg.setdefault(sec, [0.0, 0.0, 0])
        bucket[0] += pct * value
        bucket[1] += value
        bucket[2] += 1
    out: dict[str, dict[str, Any]] = {}
    rows = []
    for sec, (wsum, vsum, cnt) in agg.items():
        if cnt < 5 or vsum <= 0:
            continue
        avg = wsum / vsum
        diff = avg - vn_pct
        rows.append({"name": sec, "avg": avg, "diff": diff, "cnt": cnt})
    rows.sort(key=lambda r: r["avg"], reverse=True)
    for i, r in enumerate(rows):
        avg, diff = r["avg"], r["diff"]
        if diff >= 0.6 and avg > 0 and r["cnt"] >= 8:
            score, label = 10, "nhóm mạnh"
        elif avg > 0:
            score, label = 7, "ngành tăng"
        elif diff >= -0.4:
            score, label = 3, "trung tính"
        elif diff >= -1.2:
            score, label = 0, "ngành yếu"
        else:
            score, label = -5, "bị bán mạnh"
        out[r["name"]] = {"score": score, "label": label, "avg": round(avg, 2), "diff": round(diff, 2)}
    return out


def _market_state(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    vn_pct = _index_pct()
    breadth = _market_breadth(stocks)
    ratio = breadth["adv_ratio"]
    if vn_pct > 0.2 and ratio >= 0.55:
        status, label = "positive", "Thị trường tích cực"
    elif vn_pct <= -0.8 and ratio <= 0.38:
        status, label = "negative", "Thị trường rủi ro cao"
    else:
        status, label = "neutral", "Thị trường trung lập"
    return {
        "status": status,
        "label": label,
        "vn_pct": round(vn_pct, 2),
        "adv_ratio": breadth["adv_ratio"],
        "adv": breadth["adv"],
        "dec": breadth["dec"],
        "max_items": 5 if status == "positive" else (3 if status == "neutral" else 0),
        "message": "Thị trường đang có mức độ biến động/rủi ro cao. Chưa ghi nhận đủ tín hiệu nổi bật."
        if status == "negative" else "",
    }


# ---------------- Tính điểm cho 1 mã ----------------
def _higher_high_lower_low(closes: list[float]) -> bool:
    if len(closes) < 21:
        return False
    seg = closes[-20:]
    return seg[-1] > max(seg[:-1]) and min(seg[1:]) > min(seg[:-1])


def _flow_recent_positive(price_close: float, volumes: list[float]) -> bool:
    """Dòng tiền 5 phiên gần nhất (giá×KL) so 5 phiên trước có cải thiện."""
    if len(volumes) < 10:
        return False
    recent = sum(v for v in volumes[-5:])
    prev = sum(v for v in volumes[-10:-5])
    return prev > 0 and recent > prev * 1.05


def _score_one(
    row: dict[str, Any],
    bars: list[dict[str, Any]],
    sector_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    price = _numbers(row.get("price"))
    if price <= 0:
        return None
    closes = _closes(bars)
    vols = _volumes(bars)
    if len(closes) < _MIN_BARS or len(closes) != len(vols) or len(vols) < _MIN_BARS:
        return None
    if not all(math.isfinite(c) and c > 0 for c in closes[-_MIN_BARS:]):
        return None

    # --- dữ liệu cơ bản ---
    symbol = str(row.get("symbol") or "")
    name = str(row.get("name") or symbol)
    exchange = str(row.get("exchange") or "")
    pct_day = _numbers(row.get("percent_change"))
    volume = _numbers(row.get("volume"))
    vol_ma20 = sum(vols[-21:-1]) / max(1, len(vols[-21:-1]))
    vr20 = round(volume / vol_ma20, 2) if vol_ma20 > 0 else 0.0
    sector_name = str(row.get("sectorName") or "").strip()

    ma20 = _ma(closes, 20)
    ma50 = _ma(closes, 50)
    ma200 = _ma(closes, 200)
    rsi = _rsi(closes, 14)
    macd = _macd(closes)

    # ---- A. Xu hướng giá (tối đa 25) ----
    trend = 0
    t_reasons: list[str] = []
    if ma20 is not None and price > ma20:
        trend += 5
        t_reasons.append("giá trên MA20")
    if ma20 is not None and ma50 is not None and ma20 > ma50:
        trend += 5
        t_reasons.append("MA20 trên MA50")
    if ma50 is not None and price > ma50:
        trend += 5
        t_reasons.append("giá trên MA50")
    if ma50 is not None and ma200 is not None and ma50 > ma200:
        trend += 5
    if _higher_high_lower_low(closes):
        trend += 5
        t_reasons.append("higher high / higher low")
    trend = min(25, trend)

    # ---- B. Dòng tiền & thanh khoản (tối đa 25) ----
    flow = 0
    f_reasons: list[str] = []
    breakout_flag = False
    if vr20 > 1.2:
        flow += 5
        f_reasons.append("khối lượng vượt trung bình")
    if vr20 > 1.5:
        flow += 5
        f_reasons.append("volume bùng nổ")
    if pct_day > 0 and vr20 > 1.05:
        flow += 5
        f_reasons.append("giá tăng cùng volume")
    if pct_day > 0 and _flow_recent_positive(price, vols):
        flow += 5
        f_reasons.append("dòng tiền 5 phiên cải thiện")
    if breakout_flag and vr20 >= 1.2:  # breakout kèm volume xác nhận
        flow += 5
    flow = min(25, flow)

    # ---- C. Động lượng (tối đa 20) ----
    momentum = 0
    m_reasons: list[str] = []
    if rsi is not None:
        if 50 <= rsi <= 65:
            momentum += 8
            m_reasons.append(f"RSI {rsi:.0f} tích cực")
        elif 65 < rsi <= 70:
            momentum += 5
        elif 40 <= rsi < 50:
            momentum += 2
        elif rsi > 75:
            momentum -= 5
    if macd:
        if macd["macd"] > macd["signal"]:
            momentum += 4
            m_reasons.append("MACD trên signal")
        if macd["hist"] > macd["hist_prev"]:
            momentum += 4
        if macd["cross_up"]:
            momentum += 4
            m_reasons.append("MACD vừa cắt lên")
    momentum = max(0, min(20, momentum))

    # ---- D. Vị trí giá (tối đa 15) ----
    # Kháng cự gần = đỉnh 60 phiên (loại phiên hiện tại).
    resist_60 = max(closes[-61:-1]) if len(closes) >= 62 else (max(closes[:-1]) if len(closes) >= 2 else price)
    near_res = 0 < resist_60 and price >= resist_60 * 0.98 and price < resist_60 * 1.005
    is_breakout = resist_60 > 0 and price > resist_60 * 1.005
    hot_day = pct_day > 5.5
    position = 0
    p_reasons: list[str] = []
    if is_breakout and vr20 >= 1.5:
        position = 15
        breakout_flag = True
        p_reasons.append("breakout vùng tích lũy kèm volume")
    elif is_breakout:
        position = 12
        breakout_flag = True
        p_reasons.append("breakout vùng kháng cự")
    elif near_res:
        # Sát kháng cự mạnh → trừ điểm, không đếm là cơ hội breakout.
        position = 2
        p_reasons.append("đang sát kháng cự mạnh")
        if hot_day or (rsi is not None and rsi > 75):
            position -= 3
    elif ma20 is not None and ma50 is not None and price > ma50:
        # Pullback về MA20 rồi hồi phục / xu hướng tăng
        low_band = ma20 * 0.985
        if low_band <= price <= ma20 * 1.015:
            position = 9 if vr20 >= 1.05 else 8
            p_reasons.append("pullback về MA20 có hồi phục")
        elif ma200 is not None and price > ma200:
            position = 8
            p_reasons.append("xu hướng tăng bền")
        else:
            position = 6
            p_reasons.append("xu hướng tăng")
    elif ma20 is not None and price > ma20:
        position = 5
    else:
        position = 3
    if hot_day:
        position -= 3
    if rsi is not None and rsi > 75:
        position -= 3
    position = max(0, min(15, position))

    # ---- E. Cơ bản (tối đa 15) ----
    roe = _numbers(row.get("roe"))
    profit_growth = _numbers(row.get("net_profit_growth"))
    revenue_growth = _numbers(row.get("revenue_growth"))
    eps_growth = _numbers(row.get("eps_growth"))
    pe = _numbers(row.get("pe_ratio"))
    risk = str(row.get("risk") or "").lower()
    fundamental = 0
    fnd_reasons: list[str] = []
    if roe > 0:
        fundamental += 4 if roe >= 20 else 3 if roe >= 12 else 2 if roe >= 5 else 1
        if roe >= 12:
            fnd_reasons.append(f"ROE {roe:.0f}%")
    if profit_growth > 0:
        fundamental += 4 if profit_growth >= 30 else 3 if profit_growth >= 15 else 2 if profit_growth >= 5 else 1
    if revenue_growth > 0:
        fundamental += 2
    if eps_growth and eps_growth > 0:
        fundamental = min(15, fundamental + 2)
    if pe <= 0 or pe > 60:
        fundamental -= 3
    if risk == "high":
        fundamental -= 4
    elif risk == "medium":
        fundamental -= 2
    fundamental = max(0, min(15, fundamental))

    # ---- F. Sức mạnh ngành (tối đa 10) ----
    sec_info = sector_map.get(sector_name)
    sector = sec_info["score"] if sec_info else 3
    sector_label = sec_info["label"] if sec_info else "trung tính"

    # ---- Tổng & chuẩn hóa về 0–100 ----
    raw = trend + flow + momentum + position + fundamental + sector
    total_cap = 110.0
    score = round(min(100.0, raw / total_cap * 100.0))

    # ---- Lý do (1–3, từ dữ liệu thật) ----
    reasons: list[str] = []

    def _add(flag: bool, txt: str) -> None:
        if flag and len(reasons) < 3:
            reasons.append(txt)

    if breakout_flag:
        _add(True, "Vượt vùng kháng cự nền tích lũy" + (f" · volume gấp {vr20:.1f} lần TB20" if vr20 >= 1.2 else ""))
    elif "pullback" in "".join(p_reasons):
        _add(True, "Pullback về MA20/MA50 có dấu hiệu hồi phục")
    elif near_res:
        _add(True, "Đang sát vùng kháng cự — chờ xác nhận")
    elif ma20 is not None and price > ma20 and (ma50 is None or price > ma50):
        _add(True, "Giá duy trì trên MA20 và MA50")
    # Khối lượng / dòng tiền
    if vr20 >= 1.5:
        _add(True, f"Khối lượng gấp {vr20:.1f} lần trung bình 20 phiên")
    elif vr20 >= 1.2:
        _add(True, f"Khối lượng cao hơn TB20 {vr20:.1f} lần")
    if _flow_recent_positive(price, vols):
        _add(True, "Dòng tiền 5 phiên gần nhất cải thiện")
    if rsi is not None and 50 <= rsi <= 70:
        _add(True, f"RSI {rsi:.0f} — động lượng tích cực")
    if "higher high" in "".join(t_reasons):
        _add(True, "Đà tăng nối tiếp higher high/higher low")
    if sector >= 7 and sec_info:
        _add(True, f"Ngành {sector_name} thuộc {sector_label}")
    if not reasons and fnd_reasons:
        _add(True, "Nền tảng cơ bản " + fnd_reasons[0].lower())
    if not reasons:
        reasons.append("Tín hiệu kỹ thuật tích cực, đáng theo dõi")

    return {
        "symbol": symbol,
        "name": name,
        "exchange": exchange,
        "price": round(price, 2),
        "percent_change": round(pct_day, 2),
        "market_cap": _numbers(row.get("market_cap")),
        "volume": int(volume),
        "volume_ratio": vr20,
        "rsi": round(rsi, 1) if rsi is not None else None,
        "trend_status": ("Tăng" if price > (ma20 or 0) else "Đi ngang") if ma20 else "N/A",
        "sector_name": sector_name,
        "opportunity_score": score,
        "score": score,
        "reasons": reasons[:3],
        "breakdown": {
            "trend": trend, "flow": flow, "momentum": momentum,
            "position": position, "fundamental": fundamental, "sector": max(0, sector),
            "total_raw": raw,
        },
        "audit": {
            "vn_pct_note": "",
        },
    }


# ---------------- Pipeline chính ----------------
def _build_pool(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lọc thanh khoản → chọn ~50 ứng viên (ưu tiên giá trị giao dịch + volume ratio)."""
    cands: list[dict[str, Any]] = []
    for s in stocks:
        price = _numbers(s.get("price"))
        volume = _numbers(s.get("volume"))
        vr = _numbers(s.get("volume_ratio"))
        if price <= 0 or volume <= 0:
            continue
        value = price * volume
        if value < _MIN_VALUE_VND:
            continue
        if vr < _MIN_VOL_RATIO:
            continue
        if vr > 25:  # volume bất thường → loại
            continue
        if abs(_numbers(s.get("percent_change"))) > 20:  # dữ liệu bất thường
            continue
        cands.append(s)
    cands.sort(key=lambda s: _numbers(s.get("price")) * _numbers(s.get("volume")) * (0.4 + min(_numbers(s.get("volume_ratio")), 3.0)), reverse=True)
    return cands[:_POOL_SIZE]


def _fetch_enrich(cands: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    def _load(cand: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        sym = str(cand.get("symbol") or "")
        try:
            bars = vns.get_history(sym, "1d", "1y") or []
            return cand, bars
        except Exception:
            return cand, []

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        return list(pool.map(_load, cands))


def _compute_impl() -> dict[str, Any]:
    t0 = time.time()
    stocks = _screener_stocks()
    if not stocks:
        return {"ok": False, "market": None, "items": [], "computed_at": None, "error": "Không có dữ liệu screener"}

    market = _market_state(stocks)
    sector_map = _sector_strengths(stocks, market["vn_pct"])
    pool = _build_pool(stocks)

    enriched = _fetch_enrich(pool)
    scored: list[dict[str, Any]] = []
    skipped = {"bars": 0, "score": 0, "valid": 0}
    for cand, bars in enriched:
        if not bars or len(bars) < _MIN_BARS:
            skipped["bars"] += 1
            continue
        try:
            item = _score_one(cand, bars, sector_map)
        except Exception as exc:  # bảo vệ: 1 mã lỗi không làm hỏng cả phiên
            logger.warning("opportunity: lỗi tính %s: %s", cand.get("symbol"), exc)
            skipped["valid"] += 1
            continue
        if item is None:
            skipped["score"] += 1
            continue
        scored.append(item)

    scored.sort(key=lambda x: x["opportunity_score"], reverse=True)

    # Hiển thị TỐI THIỂU 4 mã (trừ khi thị trường rủi ro cao): ưu tiên mã >= 65,
    # nếu chưa đủ thì bổ sung các mã đạt tiếp theo (điểm thực, không giả) tới đủ cap.
    items: list[dict[str, Any]] = []
    if market["status"] != "negative" and scored:
        cap = 5 if market["status"] == "positive" else 4
        items = [x for x in scored if x["opportunity_score"] >= _MIN_SCORE][:cap]
        seen = {x["symbol"] for x in items}
        for x in scored:
            if len(items) >= cap:
                break
            if x["symbol"] in seen:
                continue
            items.append(x)
            seen.add(x["symbol"])

    # Log audit chi tiết (cho admin kiểm tra tại sao chọn/loại).
    global _AUDIT
    _AUDIT = scored
    logger.info(
        "opportunity: pool=%d | market=%s | tính được=%d (>=65: %d) | hiển thị=%d | loại_data=%d | %.1fs",
        len(pool), market["status"],
        len(scored), sum(1 for x in scored if x["opportunity_score"] >= _MIN_SCORE),
        len(items), skipped["bars"] + skipped["valid"], time.time() - t0,
    )
    for it in scored[:15]:
        bd = it["breakdown"]
        logger.info(
            "  %s score=%d (trend %d/25, flow %d/25, mom %d/20, pos %d/15, fnd %d/15, sec %d/10) | reasons=%s",
            it["symbol"], it["opportunity_score"], bd["trend"], bd["flow"], bd["momentum"],
            bd["position"], bd["fundamental"], bd["sector"], " | ".join(it["reasons"]),
        )

    payload = {
        "ok": True,
        "market": market,
        "items": items,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_scored": len(scored),
    }
    return payload


# ---------------- Cache + SWR (single-flight) ----------------
_COMPUTE_TIMEOUT = 8.0   # chờ tối đa khi chưa từng có cache (lần đầu sau khi khởi động)

_inflight_guard = threading.Lock()
_inflight_evt: threading.Event | None = None
_inflight_result: dict[str, Any] | None = None


def _spawn_compute() -> threading.Event:
    """Đảm bảo CHỈ MỘT luồng tính toán tại một thời điểm (kể cả lúc warm / nhiều request
    cùng lúc) — chống stampede. Trả về Event để caller chờ; đọc _inflight_result sau khi set."""
    global _inflight_evt, _inflight_result
    with _inflight_guard:
        evt = _inflight_evt
        if evt is not None and not evt.is_set():
            return evt  # đã có luồng đang tính
        evt = threading.Event()
        _inflight_evt = evt
        _inflight_result = None

    def _run() -> None:
        global _inflight_result
        try:
            data = _compute_impl()
        except Exception as exc:  # pragma: no cover
            logger.error("opportunity: compute lỗi: %s", exc)
            data = None
        if data is not None:
            with _LOCK:
                _CACHE["opp"] = {"ts": time.time(), "data": data, "computing": False}
        with _inflight_guard:
            _inflight_result = data
            _inflight_evt = None
            evt.set()

    threading.Thread(target=_run, daemon=True).start()
    return evt


def get_top_opportunities(force: bool = False) -> dict[str, Any]:
    """Trả kết quả cached; hết hạn → trả bản cũ (stale) NGAY + refresh nền (không chặn user).
    Lần đầu (chưa có cache) → chờ luồng compute single-flight (tối đa _COMPUTE_TIMEOUTs)
    để không tính trùng khi nhiều request tới cùng lúc.
    """
    now = time.time()
    with _LOCK:
        entry = _CACHE.get("opp")
    if force and entry is not None:
        with _LOCK:
            _CACHE.pop("opp", None)
        entry = None

    if entry and now - entry["ts"] < _CACHE_TTL_SECONDS:
        return entry["data"]

    stale = entry["data"] if entry else None

    # Có dữ liệu cũ (hết hạn hoặc force) → trả ngay, tính lại ở nền.
    if stale is not None:
        _spawn_compute()
        return stale

    # Chưa từng có dữ liệu → đợi kết quả compute (chung 1 luồng cho mọi caller).
    evt = _spawn_compute()
    evt.wait(timeout=_COMPUTE_TIMEOUT)
    with _inflight_guard:
        data = _inflight_result
    if data:
        return data
    return {
        "ok": False,
        "market": None,
        "items": [],
        "computed_at": None,
        "error": "Hệ thống đang tải dữ liệu thị trường, vui lòng thử lại sau vài giây.",
    }


def get_last_audit() -> list[dict[str, Any]]:
    return _AUDIT
