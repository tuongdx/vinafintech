"""Technical Indicator Engine (Phase 4) — thuần Python, KHÔNG phụ thuộc numpy/pandas.

Nguyên tắc chống LOOK-AHEAD BIAS (spec §22):
- Mọi hàm chỉ đọc dữ liệu ĐÃ CÓ tại thời điểm tính (``values[:i+1]`` khi backtest);
  không hàm nào tự dịch cửa sổ về tương lai hay dùng ``max(values)`` toàn chuỗi cho
  giá trị "hiện tại".
- Các hàm ``*_series`` trả về list CÙNG ĐỘ DÀI với input (phần đầu là ``None`` khi
  chưa đủ dữ liệu) → backtest có thể lấy ``series[i]`` đúng tại nến i.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# TREND / MA
# ---------------------------------------------------------------------------
def sma_series(values: list[float], period: int) -> list[float | None]:
    """Trung bình động đơn giản — ``out[i]`` dùng values[i-period+1 .. i]."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def sma(values: list[float], period: int) -> float | None:
    return sma_series(values, period)[-1] if values else None


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA chuẩn (seed = SMA của ``period`` phần tử đầu) — cùng độ dài input."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema(values: list[float], period: int) -> float | None:
    return ema_series(values, period)[-1] if values else None


# ---------------------------------------------------------------------------
# MOMENTUM
# ---------------------------------------------------------------------------
def momentum_pct(values: list[float], lookback: int) -> float | None:
    """Momentum (%) = close/close[-lookback] - 1. ``None`` nếu thiếu dữ liệu."""
    if lookback <= 0 or len(values) <= lookback:
        return None
    base = values[-1 - lookback]
    if not base:
        return None
    return (values[-1] / base - 1) * 100


def composite_momentum(values: list[float], periods: dict[str, int]) -> dict[str, float | None]:
    """Gộp momentum nhiều khung — trung bình có trọng số (khung ngắn nhạy hơn)."""
    short = momentum_pct(values, int(periods.get("short", 10)))
    medium = momentum_pct(values, int(periods.get("medium", 21)))
    long = momentum_pct(values, int(periods.get("long", 63)))
    parts = [(short, 0.5), (medium, 0.3), (long, 0.2)]
    avail = [(v, w) for v, w in parts if v is not None]
    composite = None
    if avail:
        total_w = sum(w for _, w in avail)
        composite = sum(v * w for v, w in avail) / total_w if total_w else None
    return {
        "short_term": round(short, 4) if short is not None else None,
        "medium_term": round(medium, 4) if medium is not None else None,
        "long_term": round(long, 4) if long is not None else None,
        "composite": round(composite, 4) if composite is not None else None,
    }


# ---------------------------------------------------------------------------
# RSI (Wilder — khác hàm _rsi "SMA" đang dùng ở stock_signal_service)
# ---------------------------------------------------------------------------
def rsi_wilder_series(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if period <= 0 or len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = ch if ch > 0 else 0.0
        loss = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if not closes:
        return None
    value = rsi_wilder_series(closes, period)[-1]
    return round(value, 4) if value is not None else None


def rsi_state(value: float | None, overbought: float, oversold: float) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= overbought:
        return "OVERBOUGHT"
    if value <= oversold:
        return "OVERSOLD"
    if value >= 55:
        return "BULLISH"
    if value <= 45:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# VOLATILITY (ATR)
# ---------------------------------------------------------------------------
def true_ranges(bars: list[dict]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        high = float(b.get("high") or 0)
        low = float(b.get("low") or 0)
        prev_close = float(bars[i - 1].get("close") or 0) if i else high
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def atr_series(bars: list[dict], period: int = 14) -> list[float | None]:
    """ATR Wilder — tại nến i chỉ dùng dữ liệu <= i."""
    out: list[float | None] = [None] * len(bars)
    if period <= 0 or len(bars) < period + 1:
        return out
    trs = true_ranges(bars)
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(bars)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def atr(bars: list[dict], period: int = 14) -> float | None:
    if not bars:
        return None
    value = atr_series(bars, period)[-1]
    return round(value, 4) if value is not None else None


# ---------------------------------------------------------------------------
# MONEY FLOW — Chaikin Money Flow
# ---------------------------------------------------------------------------
def money_flow_volume(bars: list[dict]) -> list[float | None]:
    """MFV = ((close-low)-(high-close))/(high-low) * volume. high==low -> 0 (không chia 0)."""
    out: list[float | None] = []
    for b in bars:
        high = float(b.get("high") or 0)
        low = float(b.get("low") or 0)
        close = float(b.get("close") or 0)
        volume = float(b.get("volume") or 0)
        if high <= low:
            out.append(0.0)
        else:
            out.append(((close - low) - (high - close)) / (high - low) * volume)
    return out


def cmf_series(bars: list[dict], period: int = 20) -> list[float | None]:
    """CMF = tổng(MFV, period) / tổng(volume, period) — same-length series."""
    out: list[float | None] = [None] * len(bars)
    if period <= 0 or len(bars) < period:
        return out
    mfv = money_flow_volume(bars)
    vols = [float(b.get("volume") or 0) for b in bars]
    for i in range(period - 1, len(bars)):
        vol_sum = sum(vols[i - period + 1 : i + 1])
        if vol_sum <= 0:
            out[i] = None
        else:
            out[i] = sum(v or 0.0 for v in mfv[i - period + 1 : i + 1]) / vol_sum
    return out


def cmf(bars: list[dict], period: int = 20) -> float | None:
    if not bars:
        return None
    value = cmf_series(bars, period)[-1]
    return round(value, 5) if value is not None else None


def cmf_state(value: float | None, positive: float, strong: float, negative: float) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= strong:
        return "STRONG_INFLOW"
    if value >= positive:
        return "INFLOW"
    if value <= negative:
        return "OUTFLOW"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# VOLUME
# ---------------------------------------------------------------------------
def volume_metrics(bars: list[dict], short: int = 5, long: int = 40,
                   surge_ratio: float = 1.5, exhaustion_ratio: float = 0.6) -> dict:
    vols = [float(b.get("volume") or 0) for b in bars]
    avg_short = sum(vols[-short:]) / short if len(vols) >= short else None
    avg_long = sum(vols[-long:]) / long if len(vols) >= long else None
    ratio = None
    if avg_short and avg_long:
        ratio = avg_short / avg_long
    return {
        "average_volume": round(avg_long, 2) if avg_long else None,
        "recent_volume": round(avg_short, 2) if avg_short else None,
        "volume_ratio": round(ratio, 3) if ratio is not None else None,
        "volume_surge": bool(ratio is not None and ratio >= surge_ratio),
        "volume_exhaustion": bool(ratio is not None and ratio <= exhaustion_ratio),
    }


# ---------------------------------------------------------------------------
# PRICE STRUCTURE
# ---------------------------------------------------------------------------
def price_structure(bars: list[dict], bars_for_52w: int = 260, breakout_lookback: int = 20,
                    consolidation_min_bars: int = 15, consolidation_max_range_pct: float = 12.0) -> dict:
    """Đỉnh/đáy 52 tuần, mức giảm từ đỉnh, nền tích luỹ, breakout/breakdown."""
    if not bars:
        return {"available": False}
    window = bars[-bars_for_52w:] if len(bars) > bars_for_52w else bars
    highs = [float(b.get("high") or 0) for b in window]
    lows = [float(b.get("low") or 0) for b in window]
    closes = [float(b.get("close") or 0) for b in bars]
    price = closes[-1] if closes else None
    high_52w = max(highs) if highs else None
    low_52w = min(lows) if lows else None
    dist_high = ((price / high_52w - 1) * 100) if (price and high_52w) else None
    dist_low = ((price / low_52w - 1) * 100) if (price and low_52w) else None
    position = None
    if price and high_52w and low_52w and high_52w > low_52w:
        position = (price - low_52w) / (high_52w - low_52w) * 100

    # Nền tích luỹ: tìm chuỗi nến gần nhất mà biên độ đỉnh-đáy <= ngưỡng
    consolidation_bars = 0
    for size in range(min(len(bars), 120), consolidation_min_bars - 1, -1):
        seg = bars[-size:]
        seg_high = max(float(b.get("high") or 0) for b in seg)
        seg_low = min(float(b.get("low") or 0) for b in seg)
        if seg_low <= 0 or not (price and price > 0):
            continue
        if (seg_high / seg_low - 1) * 100 <= consolidation_max_range_pct:
            consolidation_bars = size
            break

    prev_high = max(highs[-breakout_lookback - 1 : -1]) if len(highs) > breakout_lookback else None
    prev_low = min(lows[-breakout_lookback - 1 : -1]) if len(lows) > breakout_lookback else None
    return {
        "available": True,
        "high_52w": round(high_52w, 2) if high_52w else None,
        "low_52w": round(low_52w, 2) if low_52w else None,
        "distance_from_high_pct": round(dist_high, 3) if dist_high is not None else None,
        "distance_from_low_pct": round(dist_low, 3) if dist_low is not None else None,
        "position_in_range_pct": round(position, 2) if position is not None else None,
        "consolidation_bars": consolidation_bars,
        "consolidation_ok": consolidation_bars >= consolidation_min_bars,
        "breakout": bool(prev_high is not None and price is not None and price > prev_high),
        "breakdown": bool(prev_low is not None and price is not None and price < prev_low),
        "prev_range_high": round(prev_high, 2) if prev_high else None,
        "prev_range_low": round(prev_low, 2) if prev_low else None,
    }


# ---------------------------------------------------------------------------
# MA ALIGNMENT / TREND
# ---------------------------------------------------------------------------
def ma_alignment(closes: list[float], periods: list[int]) -> dict:
    """MA10 > MA20 > MA50 ⇒ điểm 0..100 + nhãn."""
    values = {p: sma(closes, p) for p in periods}
    available = [p for p in periods if values.get(p)]
    price = closes[-1] if closes else None
    if len(available) < len(periods) or not price:
        return {"available": False, "values": {f"ma{p}": None for p in periods}, "score": None, "label": "UNKNOWN"}

    ordered = all(values[periods[i]] > values[periods[i + 1]] for i in range(len(periods) - 1))
    reversed_ordered = all(values[periods[i]] < values[periods[i + 1]] for i in range(len(periods) - 1))
    above_all = all(price > values[p] for p in periods)
    below_all = all(price < values[p] for p in periods)

    score = 0.0
    if ordered:
        score += 55
    if above_all:
        score += 25
    # độ dốc MA ngắn (MA10 hôm nay vs 5 phiên trước)
    ma10_series = sma_series(closes, periods[0])
    slope_up = None
    if len(ma10_series) > 5 and ma10_series[-1] is not None and ma10_series[-6] is not None:
        slope_up = ma10_series[-1] > ma10_series[-6]
        if slope_up:
            score += 20
    if reversed_ordered:
        score = min(score, 25.0)

    label = "STRONG" if score >= 80 else "MODERATE" if score >= 50 else "WEAK"
    return {
        "available": True,
        "values": {f"ma{p}": round(values[p], 2) for p in periods},
        "ordered_up": ordered,
        "ordered_down": reversed_ordered,
        "price_above_all": above_all,
        "price_below_all": below_all,
        "ma_short_slope_up": slope_up,
        "score": round(min(100.0, score), 2),
        "label": label,
        "price_vs_ma20_pct": round((price / values[periods[1]] - 1) * 100, 3) if len(periods) > 1 and values.get(periods[1]) else None,
    }


def trend_direction(closes: list[float], periods: list[int]) -> dict:
    """Hướng xu hướng + độ mạnh 0..100 từ MA + vị trí giá."""
    ma = ma_alignment(closes, periods)
    if not ma.get("available"):
        return {"direction": "UNKNOWN", "strength": None, "label": "UNKNOWN"}
    strength = ma["score"]
    if ma["ordered_up"] and ma["price_above_all"]:
        direction = "UP"
    elif ma["ordered_down"] and ma["price_below_all"]:
        direction = "DOWN"
    else:
        direction = "SIDEWAYS"
    return {"direction": direction, "strength": strength, "label": ma["label"]}


# ---------------------------------------------------------------------------
# RELATIVE STRENGTH (so với benchmark)
# ---------------------------------------------------------------------------
def relative_strength(closes: list[float], bench_closes: list[float],
                      periods: dict[str, int]) -> dict:
    """RS ratio = momentum(cổ phiếu) - momentum(benchmark) theo từng khung."""
    if not bench_closes or len(bench_closes) < 3:
        return {"available": False, "reason": "NO_BENCHMARK"}
    out: dict = {"available": True, "periods": {}}
    weight_sum = 0.0
    weighted = 0.0
    for name, lookback in (("short", int(periods.get("short", 21))), ("medium", int(periods.get("medium", 63)))):
        stock_m = momentum_pct(closes, lookback)
        bench_m = momentum_pct(bench_closes, lookback)
        if stock_m is None or bench_m is None:
            out["periods"][name] = None
            continue
        diff = stock_m - bench_m
        out["periods"][name] = {"stock_momentum": round(stock_m, 3), "benchmark_momentum": round(bench_m, 3), "rs": round(diff, 3)}
        w = 0.6 if name == "short" else 0.4
        weighted += diff * w
        weight_sum += w
    out["rs_score_raw"] = round(weighted / weight_sum, 3) if weight_sum else None
    rs = out["rs_score_raw"]
    out["label"] = "UNKNOWN" if rs is None else "STRONG" if rs >= 3 else "POSITIVE" if rs > 0 else "WEAK" if rs > -3 else "VERY_WEAK"
    return out


# ---------------------------------------------------------------------------
# Trợ giúp chung
# ---------------------------------------------------------------------------
def clamp(value: float | None, low: float = 0.0, high: float = 100.0) -> float | None:
    if value is None:
        return None
    return max(low, min(high, value))


def scale(value: float | None, low: float, high: float) -> float | None:
    """Chuẩn hoá value trong [low, high] về 0..100 (None nếu thiếu)."""
    if value is None or high == low:
        return None
    return clamp((value - low) / (high - low) * 100)
