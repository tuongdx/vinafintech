"""Market Regime (spec §15) — bối cảnh thị trường để chiến thuật ưu tiên khác nhau.

Nguồn dữ liệu: chỉ số benchmark (mặc định VNINDEX) + độ rộng thị trường (từ market map
nếu có). Khi THIẾU benchmark ⇒ ``regime = UNKNOWN`` và strategy KHÔNG được phép coi là
"thuận thị trường" (spec §15: không kích hoạt strategy chỉ dựa vào regime).
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import indicators as ind

STRONG_UPTREND = "STRONG_UPTREND"
UPTREND = "UPTREND"
SIDEWAYS = "SIDEWAYS"
WEAK_MARKET = "WEAK_MARKET"
DOWNTREND = "DOWNTREND"
UNKNOWN = "UNKNOWN"

_REGIME_ORDER = {STRONG_UPTREND: 4, UPTREND: 3, SIDEWAYS: 2, WEAK_MARKET: 1, DOWNTREND: 0, UNKNOWN: -1}


def detect(benchmark_bars: list[dict] | None, breadth: dict | None = None,
           symbol: str = "", cfg: dict | None = None) -> dict[str, Any]:
    """Xác định Market Regime từ benchmark (+ breadth nếu có)."""
    cfg = cfg or cfg_module.get_config()
    regime_cfg = cfg.get("regime") or {}
    bars = benchmark_bars or []
    if len(bars) < 60:
        return {
            "regime": UNKNOWN,
            "score": None,
            "benchmark": symbol or None,
            "reason": "Thiếu dữ liệu benchmark (cần >= 60 phiên)",
            "components": {},
        }

    closes = [float(b.get("close") or 0) for b in bars if float(b.get("close") or 0) > 0]
    price = closes[-1]
    ma_fast = ind.sma(closes, int(regime_cfg.get("benchmark_ma_fast", 20)))
    ma_slow = ind.sma(closes, int(regime_cfg.get("benchmark_ma_slow", 50)))
    momentum_1m = ind.momentum_pct(closes, 21)

    components: dict[str, Any] = {
        "price_above_ma20": bool(ma_fast and price > ma_fast),
        "price_above_ma50": bool(ma_slow and price > ma_slow),
        "ma20_above_ma50": bool(ma_fast and ma_slow and ma_fast > ma_slow),
        "momentum_1m_pct": round(momentum_1m, 3) if momentum_1m is not None else None,
        "breadth_ratio": None,
    }

    breadth_score = None
    if breadth and (breadth.get("total") or 0) > 0:
        ratio = (breadth.get("up") or 0) / max(1, breadth.get("total") or 1)
        components["breadth_ratio"] = round(ratio, 4)
        breadth_score = ratio

    score = 0
    if components["price_above_ma20"]:
        score += 1
    if components["price_above_ma50"]:
        score += 1
    if components["ma20_above_ma50"]:
        score += 1
    if momentum_1m is not None:
        if momentum_1m >= float(regime_cfg.get("momentum_strong_up", 3.0)):
            score += 2
        elif momentum_1m >= float(regime_cfg.get("momentum_up", 1.0)):
            score += 1
        elif momentum_1m <= float(regime_cfg.get("momentum_strong_down", -7.0)):
            score -= 2
        elif momentum_1m <= float(regime_cfg.get("momentum_down", -3.0)):
            score -= 1
    if breadth_score is not None:
        if breadth_score >= float(regime_cfg.get("breadth_strong_up", 0.65)):
            score += 1
        elif breadth_score <= float(regime_cfg.get("breadth_weak", 0.35)):
            score -= 1

    if score >= 4:
        regime = STRONG_UPTREND
    elif score >= 2:
        regime = UPTREND
    elif score >= 0:
        regime = SIDEWAYS
    elif score >= -2:
        regime = WEAK_MARKET
    else:
        regime = DOWNTREND

    return {
        "regime": regime,
        "score": score,
        "benchmark": symbol or None,
        "components": components,
        "reason": f"score={score} (MA + momentum + breadth)",
        "as_of": bars[-1].get("time"),
    }


def allows(regime: str, strategy: str, cfg: dict | None = None) -> dict[str, Any]:
    """Regime có nằm trong danh sách ưu tiên của strategy không (CHỈ để ưu tiên/ghi chú)."""
    cfg = cfg or cfg_module.get_config()
    preferences = (((cfg.get("strategies") or {}).get(strategy.lower()) or {}).get("preferred_regimes") or [])
    ok = regime in preferences
    return {
        "preferred": ok,
        "preferred_regimes": preferences,
        "note": "" if ok else "Regime hiện tại không thuộc nhóm ưu tiên của chiến thuật này",
    }


def priority_weight(regime: str, strategy: str, cfg: dict | None = None) -> float:
    """Trọng số ưu tiên 0.5..1.0 — dùng khi xếp hạng, KHÔNG dùng để bật/tắt signal."""
    return 1.0 if allows(regime, strategy, cfg)["preferred"] else 0.7
