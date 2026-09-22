"""Strategy 3 — SWING TRADING (Phase 9, spec §12).

- Tín hiệu CHÍNH: Chaikin Money Flow (CMF).
  Vào: CMF vượt ``cmf_entry`` (mặc định 0.10). Thoát: CMF xuống dưới ``cmf_exit`` (0.05).
- Kết hợp Relative Strength để ưu tiên mã mạnh hơn thị trường.
- Portfolio rotation: giữ TOP ``portfolio_size`` (mặc định 5) theo ``rank_score``;
  mã có thứ hạng tốt hơn vị thế đang nắm ⇒ đánh dấu vị thế yếu hơn để thay thế.
  (Phần xếp hạng/rotation do ``engine.rank_and_rotate`` thực hiện.)
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import market_regime
from app.services.signal_engine.strategies.base import (
    GROUP_MARKET,
    ai_condition,
    cond,
    evaluate,
    finalize,
)

NAME = "SWING"


def run(features: dict, ai_payload: dict | None, regime: dict | None,
        previous_state: str | None = None, cfg: dict | None = None,
        score: float | None = None) -> dict[str, Any]:
    cfg = cfg or cfg_module.get_config()
    scfg = ((cfg.get("strategies") or {}).get("swing") or {})
    if not scfg.get("enabled", True):
        return {"strategy": NAME, "enabled": False, "evaluated": False,
                "reason": "Chiến thuật đang tắt trong cấu hình"}

    require_ai = bool(scfg.get("require_ai", False))
    cmf_entry = float(scfg.get("cmf_entry", 0.10))
    cmf_exit = float(scfg.get("cmf_exit", 0.05))
    min_rs = float(scfg.get("min_relative_strength", 0.0))

    money_flow = features.get("money_flow") or {}
    volume = features.get("volume") or {}
    rs = features.get("relative_strength") or {}
    trend = features.get("trend") or {}
    ma = trend.get("ma") or {}
    momentum = features.get("momentum") or {}
    structure = features.get("price_structure") or {}

    cmf_value = money_flow.get("cmf")
    cmf_short = money_flow.get("cmf_short")
    rs_raw = rs.get("rs_score_raw")
    price = features.get("price")
    ma20 = (ma.get("values") or {}).get("ma20")
    composite = momentum.get("composite")
    ratio = volume.get("volume_ratio")

    conditions = [
        cond("cmf_entry", f"CMF >= {cmf_entry:g} (dòng tiền vào)",
             None if cmf_value is None else cmf_value >= cmf_entry, cmf_value, f">= {cmf_entry:g}",
             required=True),
        cond("cmf_rising", "CMF đang tăng (CMF ngắn hạn > CMF kỳ)",
             None if (cmf_value is None or cmf_short is None) else cmf_short > cmf_value,
             {"cmf": cmf_value, "cmf_short": cmf_short}, "cmf_short > cmf"),
        cond("volume_confirmation", "Volume hỗ trợ (không cạn)",
             None if ratio is None else ratio >= 0.8, ratio, ">= 0.8"),
        cond("above_ma20", "Giá trên MA20",
             None if (price is None or not ma20) else price > ma20, price, f"> {round(ma20, 2) if ma20 else 'MA20'}",
             required=bool(scfg.get("require_above_ma20", True))),
        cond("relative_strength", f"Relative Strength > {min_rs:g}",
             None if rs_raw is None else rs_raw > min_rs, rs_raw, f"> {min_rs:g}", required=True),
        cond("momentum_not_negative", "Momentum ngắn hạn không âm",
             None if composite is None else composite >= 0, composite, ">= 0"),
        cond("no_breakdown", "Không phá đáy 20 phiên", (not structure.get("breakdown")) if structure.get("available") else None,
             structure.get("breakdown"), "False"),
        ai_condition("wavelet_transformer", ai_payload, required=require_ai),
        ai_condition("cmdmamba", ai_payload, required=require_ai),
    ]

    regime_name = (regime or {}).get("regime") or market_regime.UNKNOWN
    regime_info = market_regime.allows(regime_name, "swing", cfg)
    conditions.append(cond(
        "market_regime", "Market Regime phù hợp swing (sideway/uptrend)",
        None if regime_name == market_regime.UNKNOWN else regime_info["preferred"],
        regime_name, regime_info["preferred_regimes"], group=GROUP_MARKET, required=False,
    ))

    result = evaluate(conditions, cfg)
    result["strategy"] = NAME
    result["enabled"] = True
    result["evaluated"] = True
    result["regime_note"] = regime_info["note"]
    result["portfolio_size"] = int(scfg.get("portfolio_size", 5))

    bad_signals = _bad_signals(features, ai_payload, cmf_exit, cfg)
    signal = "BUY" if (result["required_passed"] and result["confirmation_ok"]) else None
    out = finalize(result, bad_signals, int(scfg.get("bad_signal_exit_threshold", 3)), signal, score, cfg)
    out["cmf_exit_level"] = cmf_exit
    out["cmf_exit_triggered"] = bool(cmf_value is not None and cmf_value < cmf_exit)
    # Spec §12: với SWING, CMF xuống dưới ngưỡng thoát LÀ điều kiện thoát (không chỉ là 1 tín hiệu xấu).
    if out["cmf_exit_triggered"] and not out["exit_condition"]:
        out["exit_condition"] = True
        out["exit_reason"] = f"CMF {cmf_value} < ngưỡng thoát {cmf_exit:g}"
    return out


def _bad_signals(features: dict, ai_payload: dict | None, cmf_exit: float, cfg: dict) -> list[dict]:
    money_flow = features.get("money_flow") or {}
    rs = features.get("relative_strength") or {}
    trend = features.get("trend") or {}
    volume = features.get("volume") or {}
    out: list[dict] = []
    cmf_value = money_flow.get("cmf")
    if cmf_value is not None and cmf_value < cmf_exit:
        out.append({"key": "cmf_exit", "label": f"CMF < {cmf_exit:g} (dòng tiền rút)", "detail": cmf_value})
    if money_flow.get("state") == "OUTFLOW":
        out.append({"key": "money_outflow", "label": "Dòng tiền đang chảy ra", "detail": money_flow.get("cmf")})
    rs_raw = rs.get("rs_score_raw")
    if rs_raw is not None and rs_raw < 0:
        out.append({"key": "rs_weakening", "label": "Relative Strength suy giảm", "detail": rs_raw})
    if trend.get("available") and not trend.get("ma", {}).get("price_above_all"):
        out.append({"key": "below_ma", "label": "Giá dưới các đường MA ngắn", "detail": trend.get("ma", {}).get("values")})
    for model in (ai_payload or {}).get("models") or []:
        if model.get("trained") and model.get("direction") == "BEARISH":
            out.append({"key": f"ai_reversal_{model.get('model')}", "label": f"AI {model.get('model')} đảo chiều giảm",
                        "detail": model.get("status")})
    ratio = volume.get("volume_ratio")
    if ratio is not None and ratio < 0.5:
        out.append({"key": "liquidity_drop", "label": "Thanh khoản sụt giảm", "detail": ratio})
    return out
