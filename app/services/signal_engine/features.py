"""Feature Engineering (Phase 3) — biến dữ liệu OHLCV thô thành bộ feature có nhóm rõ ràng.

Nguyên tắc:
- **Không look-ahead**: chỉ dùng dữ liệu <= thời điểm tính (``bars[:i+1]`` khi backtest).
- **Không bịa**: feature thiếu dữ liệu trả ``None`` + ``available: False`` kèm ``reason``;
  nếu lỗi tính toán → ``status = FEATURE_CALCULATION_ERROR`` và ghi lại vào ``errors``.
- Mọi ngưỡng lấy từ ``config`` (spec §26).
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import indicators as ind
from app.services.signal_engine import wavelet as wav

logger = logging.getLogger("tunix.signal.features")

STATUS_OK = "OK"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
STATUS_FEATURE_ERROR = "FEATURE_CALCULATION_ERROR"

_REQUIRED_FIELDS = ("open", "high", "low", "close", "volume")


def normalize_bars(raw: Any) -> list[dict]:
    """Chuẩn hoá + sắp xếp tăng dần theo thời gian; bỏ nến thiếu trường bắt buộc."""
    if not isinstance(raw, (list, tuple)):
        return []
    bars: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            bar = {
                "time": str(item.get("time") or ""),
                "open": float(item.get("open") or 0),
                "high": float(item.get("high") or 0),
                "low": float(item.get("low") or 0),
                "close": float(item.get("close") or 0),
                "volume": float(item.get("volume") or 0),
            }
        except (TypeError, ValueError):
            continue
        if bar["close"] <= 0 or bar["high"] <= 0 or bar["low"] <= 0:
            continue
        bars.append(bar)
    bars.sort(key=lambda b: b["time"])
    # Bỏ nến trùng thời gian (nguồn đôi khi trả lặp)
    dedup: list[dict] = []
    seen: set[str] = set()
    for b in bars:
        key = b["time"]
        if key and key in seen:
            continue
        seen.add(key)
        dedup.append(b)
    return dedup


def _group_trend(closes: list[float], periods: list[int]) -> dict:
    ma = ind.ma_alignment(closes, periods)
    trend = ind.trend_direction(closes, periods)
    return {"ma": ma, "direction": trend["direction"], "strength": trend["strength"], "label": trend["label"]}


def _group_momentum(closes: list[float], periods: dict[str, int]) -> dict:
    mom = ind.composite_momentum(closes, periods)
    composite = mom.get("composite")
    label = "UNKNOWN"
    if composite is not None:
        label = "STRONG" if composite >= 5 else "POSITIVE" if composite > 0 else "WEAK" if composite > -5 else "NEGATIVE"
    return {**mom, "label": label}


def _group_rsi(closes: list[float], period: int, overbought: float, oversold: float) -> dict:
    value = ind.rsi_wilder(closes, period)
    state = ind.rsi_state(value, overbought, oversold)
    return {
        "value": value,
        "state": state,
        "overbought": bool(value is not None and value >= overbought),
        "oversold": bool(value is not None and value <= oversold),
        "period": period,
    }


def _group_volume(bars: list[dict], feat: dict) -> dict:
    metrics = ind.volume_metrics(
        bars,
        short=int(feat.get("volume_short", 5)),
        long=int(feat.get("volume_long", 40)),
        surge_ratio=float(feat.get("volume_surge_ratio", 1.5)),
        exhaustion_ratio=float(feat.get("volume_exhaustion_ratio", 0.6)),
    )
    label = "UNKNOWN"
    if metrics["volume_ratio"] is not None:
        if metrics["volume_surge"]:
            label = "SURGE"
        elif metrics["volume_exhaustion"]:
            label = "EXHAUSTION"
        else:
            label = "NORMAL"
    return {**metrics, "label": label}


def _group_money_flow(bars: list[dict], feat: dict) -> dict:
    period = int(feat.get("cmf_period", 20))
    short_period = int(feat.get("cmf_short_period", 10))
    value = ind.cmf(bars, period)
    short_value = ind.cmf(bars, short_period)
    state = ind.cmf_state(
        value,
        positive=float(feat.get("cmf_positive", 0.05)),
        strong=float(feat.get("cmf_strong", 0.10)),
        negative=float(feat.get("cmf_negative", -0.05)),
    )
    rising = None
    if value is not None and short_value is not None:
        rising = short_value > value
    return {"cmf": value, "cmf_short": short_value, "state": state, "rising": rising, "period": period}


def _group_relative_strength(closes: list[float], bench_closes: list[float], feat: dict) -> dict:
    periods = feat.get("relative_strength_periods") or {"short": 21, "medium": 63}
    return ind.relative_strength(closes, bench_closes, periods)


def _group_price_structure(bars: list[dict], feat: dict) -> dict:
    return ind.price_structure(
        bars,
        bars_for_52w=int(feat.get("bars_for_52w", 260)),
        breakout_lookback=int(feat.get("breakout_lookback", 20)),
        consolidation_min_bars=int(feat.get("consolidation_min_bars", 15)),
        consolidation_max_range_pct=float(feat.get("consolidation_max_range_pct", 12.0)),
    )


def _group_volatility(bars: list[dict], price: float | None) -> dict:
    value = ind.atr(bars, 14)
    atr_pct = None
    if value and price:
        atr_pct = value / price * 100
    return {"atr14": value, "atr_pct": round(atr_pct, 3) if atr_pct is not None else None}


def build_features(bars_raw: Any, benchmark_raw: Any = None, cfg: dict | None = None,
                   symbol: str = "") -> dict:
    """Tạo bộ feature đầy đủ cho 1 mã. Không gọi mạng, không side effect."""
    cfg = cfg or cfg_module.get_config()
    feat_cfg = cfg.get("features") or {}
    engine_cfg = cfg.get("engine") or {}
    min_bars = int(engine_cfg.get("min_bars", 200))

    result: dict[str, Any] = {
        "symbol": (symbol or "").upper(),
        "status": STATUS_OK,
        "errors": [],
        "bars": 0,
        "as_of": None,
        "price": None,
        "config_version": cfg_module.config_version(),
    }

    try:
        bars = normalize_bars(bars_raw)
    except Exception as exc:  # noqa: BLE001
        result["status"] = STATUS_FEATURE_ERROR
        result["errors"].append(f"normalize_bars: {exc}")
        return result

    if not bars:
        result["status"] = STATUS_DATA_UNAVAILABLE
        return result

    result["bars"] = len(bars)
    result["as_of"] = bars[-1]["time"] or None
    result["price"] = round(bars[-1]["close"], 2)

    if len(bars) < min_bars:
        result["status"] = STATUS_INSUFFICIENT_DATA
        result["errors"].append(f"chỉ có {len(bars)} nến, cần >= {min_bars}")

    closes = [b["close"] for b in bars]
    bench_closes = [b["close"] for b in normalize_bars(benchmark_raw)] if benchmark_raw else []

    try:
        result["trend"] = _group_trend(closes, list(feat_cfg.get("ma_periods") or [10, 20, 50]))
        result["momentum"] = _group_momentum(closes, dict(feat_cfg.get("momentum_periods") or {"short": 10, "medium": 21, "long": 63}))
        result["rsi"] = _group_rsi(
            closes,
            int(feat_cfg.get("rsi_period", 14)),
            float(feat_cfg.get("rsi_overbought", 70)),
            float(feat_cfg.get("rsi_oversold", 30)),
        )
        result["volume"] = _group_volume(bars, feat_cfg)
        result["money_flow"] = _group_money_flow(bars, feat_cfg)
        result["relative_strength"] = _group_relative_strength(closes, bench_closes, feat_cfg)
        result["price_structure"] = _group_price_structure(bars, feat_cfg)
        result["volatility"] = _group_volatility(bars, result["price"])
        result["wavelet"] = wav.wavelet_features(closes, levels=int(feat_cfg.get("wavelet_levels", 4)))
    except Exception as exc:  # noqa: BLE001 — 1 nhóm lỗi không được làm mất toàn bộ kết quả
        logger.warning("FEATURE_CALCULATION_ERROR %s: %s", symbol, exc)
        result["status"] = STATUS_FEATURE_ERROR
        result["errors"].append(f"FEATURE_CALCULATION_ERROR: {exc}")

    result["data_quality"] = _data_quality(result, len(bars), min_bars)
    return result


def _data_quality(result: dict, bars: int, min_bars: int) -> dict:
    """Điểm chất lượng dữ liệu 0..100 — dùng để hạ Confidence khi dữ liệu mỏng."""
    score = 100.0
    if bars < min_bars:
        score -= min(50.0, (min_bars - bars) / max(min_bars, 1) * 100)
    missing = [k for k in ("trend", "momentum", "rsi", "volume", "money_flow", "volatility") if not result.get(k)]
    score -= len(missing) * 8
    if not (result.get("relative_strength") or {}).get("available"):
        score -= 5
    if not (result.get("wavelet") or {}).get("available"):
        score -= 5
    return {"score": round(max(0.0, min(100.0, score)), 2), "missing_groups": missing, "bars": bars}
