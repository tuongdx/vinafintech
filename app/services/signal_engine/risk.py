"""Risk Analysis (Phase 10) + mức giao dịch dành riêng cho ADMIN (spec §10, §17, §34).

- ``risk_score``: 0..100 (cao = rủi ro cao) + nhãn LOW/MEDIUM/HIGH + lý do cụ thể.
- ``trade_levels``: Entry / Stop Loss / Take Profit / Trailing Stop — CHỈ dùng cho
  payload Admin. Public API/UI KHÔNG được nhận các trường này (spec §4, §33.3).

Mọi hệ số lấy từ ``config.risk`` (spec §26); mức giá làm tròn tới đồng.
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import indicators as ind


def risk_score(features: dict, market_regime: dict | None = None, cfg: dict | None = None) -> dict[str, Any]:
    cfg = cfg or cfg_module.get_config()
    risk_cfg = cfg.get("risk") or {}
    volatility = features.get("volatility") or {}
    structure = features.get("price_structure") or {}
    volume = features.get("volume") or {}
    trend = features.get("trend") or {}
    atr_pct = volatility.get("atr_pct")
    price = features.get("price")

    parts: dict[str, float | None] = {}
    reasons: list[str] = []

    if atr_pct is not None:
        parts["volatility"] = ind.scale(atr_pct, float(risk_cfg.get("min_atr_pct", 0.5)), float(risk_cfg.get("max_atr_pct", 9.0)))
        if atr_pct >= float(risk_cfg.get("max_atr_pct", 9.0)):
            reasons.append(f"Biến động quá cao (ATR {atr_pct}% giá)")
        elif atr_pct <= float(risk_cfg.get("min_atr_pct", 0.5)):
            reasons.append(f"Biến động quá thấp (ATR {atr_pct}%), khó có biên lợi nhuận")
    else:
        parts["volatility"] = None

    dist_high = structure.get("distance_from_high_pct")
    if dist_high is not None:
        # Rơi càng sâu so với đỉnh 52 tuần ⇒ rủi ro xu hướng càng lớn (tối đa ở -60%)
        parts["drawdown"] = ind.scale(-dist_high, 5.0, 60.0)
    else:
        parts["drawdown"] = None

    ma = trend.get("ma") or {}
    if ma.get("available"):
        parts["trend"] = round(100.0 - float(ma.get("score") or 0), 2)
    else:
        parts["trend"] = None

    ratio = volume.get("volume_ratio")
    if ratio is not None:
        # Thanh khoản thấp (ratio << 1) ⇒ rủi ro không khớp được lệnh
        parts["liquidity"] = ind.clamp(100.0 - min(100.0, ratio * 100.0)) if ratio < 1 else 20.0
        if ratio < 0.5:
            reasons.append("Thanh khoản yếu so với trung bình 40 phiên")
    else:
        parts["liquidity"] = None

    regime_name = (market_regime or {}).get("regime")
    regime_penalty = {"STRONG_UPTREND": 0.0, "UPTREND": 10.0, "SIDEWAYS": 35.0, "WEAK_MARKET": 65.0, "DOWNTREND": 85.0}.get(regime_name or "", None)
    parts["market_regime"] = regime_penalty
    if regime_name in ("WEAK_MARKET", "DOWNTREND"):
        reasons.append(f"Bối cảnh thị trường {regime_name}")

    available = {k: v for k, v in parts.items() if v is not None}
    score = round(sum(available.values()) / len(available), 2) if available else None
    level = "UNKNOWN"
    if score is not None:
        if score <= float(risk_cfg.get("risk_low_max", 35)):
            level = "LOW"
        elif score <= float(risk_cfg.get("risk_medium_max", 60)):
            level = "MEDIUM"
        else:
            level = "HIGH"

    return {
        "risk_score": score,
        "level": level,
        "components": {k: (round(v, 2) if v is not None else None) for k, v in parts.items()},
        "reasons": reasons,
        "price": price,
    }


def trade_levels(features: dict, strategy: str = "UPTREND", cfg: dict | None = None) -> dict[str, Any]:
    """Entry / SL / TP / Trailing Stop — CHỈ trả cho Admin (spec §17)."""
    cfg = cfg or cfg_module.get_config()
    risk_cfg = cfg.get("risk") or {}
    price = features.get("price")
    atr = (features.get("volatility") or {}).get("atr14")
    if not price or not atr:
        return {
            "available": False,
            "reason": "INSUFFICIENT_DATA",
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "trailing_stop": None,
        }

    stop_mult = float(risk_cfg.get("atr_stop_multiple", 2.0))
    target_mult = float(risk_cfg.get("atr_target_multiple", 4.0))
    trail_mult = float(risk_cfg.get("trailing_atr_multiple", 3.0))

    entry = round(price, 0)
    stop_loss = round(price - stop_mult * atr, 0)
    take_profit = round(price + target_mult * atr, 0)
    trailing_stop = round(max(entry - stop_mult * atr, price - trail_mult * atr), 0)
    risk_per_share = entry - stop_loss
    reward_per_share = take_profit - entry
    return {
        "available": True,
        "strategy": strategy,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trailing_stop": trailing_stop,
        "atr": round(atr, 2),
        "risk_per_share": round(risk_per_share, 0),
        "reward_per_share": round(reward_per_share, 0),
        "rr_ratio": round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else None,
        "risk_pct_from_entry": round(stop_mult * atr / price * 100, 2),
        "note": "Entry = giá đóng cửa phiên tính tín hiệu; SL/TP theo bội số ATR (config.risk).",
    }
