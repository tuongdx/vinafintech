"""Strategy 2 — BOTTOM FISHING (Phase 8, spec §11).

Tìm cổ phiếu đã giảm sâu nhưng **áp lực bán suy yếu** — KHÔNG bắt dao đang rơi tự do.

8 điều kiện xác nhận (mặc định cần >= 7):
 1. Wavelet Transformer dự đoán tăng (nhóm AI — UNAVAILABLE nếu chưa train)
 2. CMDMamba dự đoán tăng            (nhóm AI)
 3. Momentum đang âm
 4. MA alignment thấp (chưa hồi phục)
 5. RSI vùng quá bán
 6. Giá cách xa đỉnh 52 tuần
 7. Volume thấp / cạn ⇒ dấu hiệu áp lực bán suy yếu
 8. **Phanh an toàn**: giá đã ổn định khỏi đáy (không còn rơi tự do)

Quy đổi ngưỡng: ``minimum_confirmations`` áp trên tập điều kiện KHAI BÁO (8). Khi một số
điều kiện UNAVAILABLE (model chưa train), ngưỡng được quy đổi theo tỉ lệ và **làm tròn lên**
⇒ không nới lỏng hệ thống. Cả 2 giá trị đều được trả về để audit.
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

NAME = "BOTTOM_FISHING"


def run(features: dict, ai_payload: dict | None, regime: dict | None,
        previous_state: str | None = None, cfg: dict | None = None,
        score: float | None = None) -> dict[str, Any]:
    cfg = cfg or cfg_module.get_config()
    scfg = ((cfg.get("strategies") or {}).get("bottom_fishing") or {})
    if not scfg.get("enabled", True):
        return {"strategy": NAME, "enabled": False, "evaluated": False,
                "reason": "Chiến thuật đang tắt trong cấu hình"}

    trend = features.get("trend") or {}
    ma = trend.get("ma") or {}
    momentum = features.get("momentum") or {}
    rsi = features.get("rsi") or {}
    volume = features.get("volume") or {}
    structure = features.get("price_structure") or {}

    require_ai = bool(scfg.get("require_ai", False))
    max_dist_high = float(scfg.get("max_distance_from_high_pct", 25.0))
    rsi_max = float(scfg.get("rsi_max", 35.0))
    momentum_max = float(scfg.get("momentum_max", -3.0))
    ma_max = float(scfg.get("ma_alignment_max", 30.0))
    max_dist_low = float(scfg.get("max_distance_from_low_pct", 12.0))
    minimum = int(scfg.get("minimum_confirmations", 7))

    composite = momentum.get("composite")
    rsi_value = rsi.get("value")
    dist_high = structure.get("distance_from_high_pct")
    dist_low = structure.get("distance_from_low_pct")
    ratio = volume.get("volume_ratio")

    not_free_falling = None
    if dist_low is not None:
        not_free_falling = dist_low <= max_dist_low
    elif features.get("price") and ma.get("values", {}).get("ma10"):
        not_free_falling = features["price"] > ma["values"]["ma10"]

    conditions = [
        ai_condition("wavelet_transformer", ai_payload, required=require_ai),
        ai_condition("cmdmamba", ai_payload, required=require_ai),
        cond("negative_momentum", "Momentum đang âm (đã chiết khấu)",
             None if composite is None else composite <= momentum_max, composite, f"<= {momentum_max:g}"),
        cond("low_ma_alignment", "MA alignment thấp (chưa hồi phục)",
             None if ma.get("score") is None else float(ma.get("score")) <= ma_max, ma.get("score"), f"<= {ma_max:g}"),
        cond("oversold_rsi", f"RSI vùng quá bán (<= {rsi_max:g})",
             None if rsi_value is None else rsi_value <= rsi_max, rsi_value, f"<= {rsi_max:g}"),
        cond("far_from_high", f"Giá cách xa đỉnh 52 tuần (>= {max_dist_high:g}%)",
             None if dist_high is None else dist_high <= -max_dist_high, dist_high, f"<= -{max_dist_high:g}%"),
        cond("weak_selling_pressure", "Volume thấp/cạn — áp lực bán suy yếu",
             None if ratio is None else (ratio <= 1.0 or bool(volume.get("volume_exhaustion"))),
             {"volume_ratio": ratio, "exhaustion": volume.get("volume_exhaustion")}, "<= 1.0 hoặc exhaustion"),
        cond("not_free_falling", "Giá đã ổn định khỏi đáy (không rơi tự do)", not_free_falling,
             {"distance_from_low_pct": dist_low, "price": features.get("price")},
             f"<= {max_dist_low:g}% so với đáy 52 tuần", required=True),
    ]

    regime_info = market_regime.allows((regime or {}).get("regime") or market_regime.UNKNOWN, "bottom_fishing", cfg)
    conditions.append(cond(
        "market_regime", "Market Regime phù hợp chiến thuật bắt đáy",
        None if not regime or (regime or {}).get("regime") in (None, market_regime.UNKNOWN) else regime_info["preferred"],
        (regime or {}).get("regime"), regime_info["preferred_regimes"], group=GROUP_MARKET, required=False,
    ))

    result = evaluate(conditions, cfg, min_confirmations=minimum)
    result["strategy"] = NAME
    result["enabled"] = True
    result["evaluated"] = True
    result["regime_note"] = regime_info["note"]

    bad_signals = _bad_signals(features, ai_payload, cfg)
    signal = "BUY" if (result["required_passed"] and result["confirmation_ok"]) else None
    return finalize(result, bad_signals, int(scfg.get("bad_signal_exit_threshold", 3)), signal, score, cfg)


def _bad_signals(features: dict, ai_payload: dict | None, cfg: dict) -> list[dict]:
    scfg = ((cfg.get("strategies") or {}).get("bottom_fishing") or {})
    max_dist_low = float(scfg.get("max_distance_from_low_pct", 12.0))
    structure = features.get("price_structure") or {}
    momentum = features.get("momentum") or {}
    rsi = features.get("rsi") or {}
    volume = features.get("volume") or {}
    out: list[dict] = []

    for model in (ai_payload or {}).get("models") or []:
        if model.get("trained") and model.get("direction") == "BEARISH":
            out.append({"key": f"ai_reversal_{model.get('model')}", "label": f"AI {model.get('model')} vẫn dự đoán giảm",
                        "detail": model.get("status")})

    if structure.get("breakdown"):
        out.append({"key": "new_low", "label": "Giá phá đáy 20 phiên (vẫn đang rơi)", "detail": structure.get("prev_range_low")})
    dist_low = structure.get("distance_from_low_pct")
    if dist_low is not None and dist_low > max_dist_low * 1.7:
        out.append({"key": "falling_further", "label": "Đã rơi thêm đáng kể từ đáy", "detail": dist_low})
    rsi_value = rsi.get("value")
    if rsi_value is not None and rsi_value < 15:
        out.append({"key": "rsi_deep", "label": "RSI quá sâu (dao rơi)", "detail": rsi_value})
    composite = momentum.get("composite")
    if composite is not None and composite < (float(scfg.get("momentum_max", -3.0)) * 2):
        out.append({"key": "momentum_collapse", "label": "Momentum suy giảm mạnh thêm", "detail": composite})
    ratio = volume.get("volume_ratio")
    if ratio is not None and ratio >= 2.0 and (composite or 0) < 0:
        out.append({"key": "heavy_selling", "label": "Volume lớn khi giá giảm (bán tháo)", "detail": ratio})
    return out
