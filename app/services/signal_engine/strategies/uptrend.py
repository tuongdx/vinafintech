"""Strategy 1 — UPTREND (Phase 7, spec §9-10).

Điều kiện vào (10 điều kiện, threshold trong ``config.strategies.uptrend``):
 1. Wavelet Transformer dự đoán tăng      (nhóm AI  — UNAVAILABLE nếu chưa train)
 2. CMDMamba dự đoán tăng                 (nhóm AI)
 3. Composite momentum >= ngưỡng tích cực
 4. MA alignment đạt yêu cầu
 5. MA10 > MA20 > MA50
 6. Đã tích luỹ đủ dài
 7. RSI trong vùng tăng tốc (mặc định 55-82)
 8. Volume surge xác nhận dòng tiền
 9. Relative Strength tốt hơn thị trường
10. Breakout / nằm sát đỉnh 52 tuần

Exit logic (spec §10): **Bad Signal Accumulation** — KHÔNG thoát chỉ vì 1 tín hiệu xấu.
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import market_regime
from app.services.signal_engine.strategies.base import (
    GROUP_MARKET,
    GROUP_TECHNICAL,
    ai_condition,
    cond,
    evaluate,
    finalize,
)

NAME = "UPTREND"


def run(features: dict, ai_payload: dict | None, regime: dict | None,
        previous_state: str | None = None, cfg: dict | None = None,
        score: float | None = None) -> dict[str, Any]:
    cfg = cfg or cfg_module.get_config()
    scfg = ((cfg.get("strategies") or {}).get("uptrend") or {})
    if not scfg.get("enabled", True):
        return {"strategy": NAME, "enabled": False, "evaluated": False,
                "reason": "Chiến thuật đang tắt trong cấu hình"}

    trend = features.get("trend") or {}
    ma = trend.get("ma") or {}
    momentum = features.get("momentum") or {}
    rsi = features.get("rsi") or {}
    volume = features.get("volume") or {}
    rs = features.get("relative_strength") or {}
    structure = features.get("price_structure") or {}

    require_ai = bool(scfg.get("require_ai", False))
    rsi_min = float(scfg.get("rsi_min", 55))
    rsi_max = float(scfg.get("rsi_max", 82))
    min_momentum = float(scfg.get("min_composite_momentum", 2))
    min_consolidation = int(scfg.get("min_consolidation_bars", 15))
    max_dist_high = float(scfg.get("max_distance_from_high_pct", 6.0))

    composite = momentum.get("composite")
    rsi_value = rsi.get("value")
    dist_high = structure.get("distance_from_high_pct")
    rs_raw = rs.get("rs_score_raw")

    conditions = [
        ai_condition("wavelet_transformer", ai_payload, required=require_ai),
        ai_condition("cmdmamba", ai_payload, required=require_ai),
        cond("composite_momentum", "Composite momentum vượt ngưỡng tích cực",
             None if composite is None else composite >= min_momentum, composite, min_momentum,
             required=True),
        cond("ma_alignment", "MA alignment đạt yêu cầu",
             None if ma.get("score") is None else float(ma.get("score")) >= 50,
             ma.get("score"), ">= 50", required=bool(scfg.get("require_ma_alignment", True))),
        cond("ma_stack", "MA10 > MA20 > MA50", ma.get("ordered_up"), ma.get("values"),
             "ordered", required=bool(scfg.get("require_ma_alignment", True))),
        cond("consolidation", "Đã có giai đoạn tích luỹ đủ dài",
             None if structure.get("consolidation_bars") is None else structure.get("consolidation_bars") >= min_consolidation,
             structure.get("consolidation_bars"), f">= {min_consolidation} phiên", required=True),
        cond("rsi_range", f"RSI trong vùng tăng tốc {rsi_min:g}-{rsi_max:g}",
             None if rsi_value is None else (rsi_min <= rsi_value <= rsi_max), rsi_value,
             [rsi_min, rsi_max], required=True),
        cond("volume_surge", "Volume surge xác nhận dòng tiền", volume.get("volume_surge"),
             volume.get("volume_ratio"), f">= {scfg.get('volume_surge_ratio', 1.5)}",
             required=bool(scfg.get("require_volume_surge", True))),
        cond("relative_strength", "Relative Strength tốt hơn thị trường",
             None if rs_raw is None else rs_raw > 0, rs_raw, "> 0",
             required=bool(scfg.get("require_relative_strength", True)),
             reason="" if rs.get("available") else "Không có dữ liệu benchmark"),
        cond("breakout_or_near_high", "Breakout khỏi nền hoặc nằm sát đỉnh 52 tuần",
             None if dist_high is None else (bool(structure.get("breakout")) or abs(dist_high) <= max_dist_high),
             {"breakout": structure.get("breakout"), "distance_from_high_pct": dist_high},
             f"breakout hoặc <= {max_dist_high:g}% dưới đỉnh",
             required=bool(scfg.get("require_breakout", True))),
    ]

    regime_info = market_regime.allows((regime or {}).get("regime") or market_regime.UNKNOWN, "uptrend", cfg)
    conditions.append(cond(
        "market_regime", "Market Regime thuận cho Uptrend",
        None if not regime or (regime or {}).get("regime") in (None, market_regime.UNKNOWN) else regime_info["preferred"],
        (regime or {}).get("regime"), regime_info["preferred_regimes"], group=GROUP_MARKET, required=False,
    ))

    result = evaluate(conditions, cfg)
    result["strategy"] = NAME
    result["enabled"] = True
    result["evaluated"] = True
    result["regime_note"] = regime_info["note"]

    bad_signals = _bad_signals(features, ai_payload, cfg)
    signal = "BUY" if (result["required_passed"] and result["confirmation_ok"]) else None
    return finalize(result, bad_signals, int(scfg.get("bad_signal_exit_threshold", 4)), signal, score, cfg)


def _bad_signals(features: dict, ai_payload: dict | None, cfg: dict) -> list[dict]:
    """Tín hiệu XẤU tích luỹ — dùng cho exit logic (spec §10)."""
    scfg = ((cfg.get("strategies") or {}).get("uptrend") or {})
    rsi_max = float(scfg.get("rsi_max", 82))
    trend = features.get("trend") or {}
    ma = trend.get("ma") or {}
    momentum = features.get("momentum") or {}
    rsi = features.get("rsi") or {}
    volume = features.get("volume") or {}
    rs = features.get("relative_strength") or {}
    out: list[dict] = []

    for model in (ai_payload or {}).get("models") or []:
        if model.get("trained") and model.get("direction") == "BEARISH":
            out.append({"key": f"ai_reversal_{model.get('model')}", "label": f"AI {model.get('model')} đảo chiều giảm",
                        "detail": model.get("status")})

    ma_score = ma.get("score")
    if ma_score is not None and float(ma_score) < 40:
        out.append({"key": "ma_weakening", "label": "MA alignment suy giảm", "detail": ma_score})
    if ma.get("available") and not ma.get("ordered_up"):
        out.append({"key": "ma_stack_broken", "label": "MA10 > MA20 > MA50 đã bị phá", "detail": ma.get("values")})

    rsi_value = rsi.get("value")
    if rsi_value is not None and rsi_value > rsi_max:
        out.append({"key": "rsi_overbought", "label": "RSI quá mua", "detail": rsi_value})

    composite = momentum.get("composite")
    if composite is not None and composite < 0:
        out.append({"key": "momentum_weakening", "label": "Momentum yếu dần (âm)", "detail": composite})

    ratio = volume.get("volume_ratio")
    if ratio is not None and ratio < 0.7 and (composite or 0) < 0:
        out.append({"key": "bad_volume", "label": "Volume xấu khi giá giảm", "detail": ratio})

    rs_raw = rs.get("rs_score_raw")
    if rs_raw is not None and rs_raw < 0:
        out.append({"key": "rs_weakening", "label": "Relative Strength suy giảm", "detail": rs_raw})

    if trend.get("direction") == "DOWN":
        out.append({"key": "trend_deterioration", "label": "Xu hướng suy giảm", "detail": trend.get("direction")})
    return out
