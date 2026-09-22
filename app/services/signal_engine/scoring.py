"""Signal Scoring (Phase 10) — TuniX Signal Score 0..100 + Setup Strength.

Điểm gồm 9 thành phần (spec §13). Mỗi thành phần 0..100, càng cao càng ủng hộ setup.
Trọng số nằm ở ``config.signal.weights`` (spec §26) — KHÔNG hard-code ở nơi khác.

⚠️ Khi model AI chưa train (``used_in_scoring = False``):
- thành phần ``ai`` = ``None`` ⇒ **loại khỏi** tổng và **renormalize** trọng số;
- ``signal_score`` KHÔNG được cộng/trừ bù bằng số ảo, chỉ báo ``ai_included: false``
  để UI hiển thị "model chưa train".

Hai chế độ chấm điểm:
- ``bullish`` (UPTREND/SWING): thưởng xu hướng tăng, momentum dương, RSI 55-70, dòng tiền vào.
- ``bottom`` (BOTTOM_FISHING): thưởng quá bán + áp lực bán yếu + chiết khấu sâu so với đỉnh.
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import indicators as ind

COMPONENTS = (
    "ai",
    "trend",
    "momentum",
    "ma",
    "rsi",
    "volume",
    "money_flow",
    "relative_strength",
    "price_structure",
)


def _rsi_score_bullish(value: float | None) -> float | None:
    """Vùng tăng tốc 55-70 là đẹp nhất; >80 là quá mua (rủi ro), <45 là chưa đủ lực."""
    if value is None:
        return None
    if value < 30:
        return 15.0
    if value < 45:
        return 15.0 + (value - 30) / 15 * 30      # 15 → 45
    if value < 55:
        return 45.0 + (value - 45) / 10 * 30      # 45 → 75
    if value <= 70:
        return 75.0 + (value - 55) / 15 * 25      # 75 → 100
    if value <= 80:
        return 100.0 - (value - 70) / 10 * 40     # 100 → 60
    return 35.0


def _rsi_score_bottom(value: float | None) -> float | None:
    """Quá bán (20-35) là vùng tốt cho bắt đáy; quá thấp (<15) là dao rơi."""
    if value is None:
        return None
    if value < 15:
        return 30.0
    if value <= 35:
        return 60.0 + (35 - value) / 20 * 40      # 35 → 100 (về 15)
    if value <= 50:
        return 60.0 - (value - 35) / 15 * 30      # 60 → 30
    return max(0.0, 30.0 - (value - 50))


def _trend_component(trend: dict, mode: str) -> float | None:
    strength = trend.get("strength")
    if strength is None:
        return None
    direction = trend.get("direction")
    if mode == "bottom":
        # Bắt đáy cần xu hướng ĐANG YẾU nhưng không sụp thêm: điểm cao khi strength thấp
        score = 100.0 - strength
        if direction == "DOWN":
            score *= 0.75                          # vẫn đang rơi thì bớt hấp dẫn
        return ind.clamp(score)
    if direction == "UP":
        return ind.clamp(strength)
    if direction == "SIDEWAYS":
        return ind.clamp(strength * 0.6)
    return ind.clamp(100.0 - strength)


def _momentum_component(momentum: dict, mode: str) -> float | None:
    composite = momentum.get("composite")
    if composite is None:
        return None
    if mode == "bottom":
        # momentum đang âm vừa phải (-20..0) là vùng hấp dẫn; âm quá sâu là dao rơi
        return ind.clamp(50.0 + composite * 2.0)
    return ind.clamp(50.0 + composite * 3.0)


def _volume_component(volume: dict, momentum: dict, mode: str) -> float | None:
    ratio = volume.get("volume_ratio")
    if ratio is None:
        return None
    composite = momentum.get("composite") or 0.0
    if mode == "bottom":
        # Volume cạn dần = áp lực bán suy yếu (spec §5)
        if volume.get("volume_exhaustion"):
            return 85.0
        if ratio <= 1.0:
            return 70.0
        if ratio <= 1.5:
            return 55.0
        return 40.0
    if volume.get("volume_surge"):
        return 100.0 if composite > 0 else 45.0   # volume lớn mà giá giảm = phân phối
    if ratio >= 1.1:
        return 75.0
    if ratio >= 0.8:
        return 60.0
    if volume.get("volume_exhaustion"):
        return 45.0
    return 50.0


def _structure_component(structure: dict, mode: str) -> float | None:
    if not structure.get("available"):
        return None
    dist_high = structure.get("distance_from_high_pct")
    if mode == "bottom":
        if dist_high is None:
            return None
        deep = ind.scale(-dist_high, 15.0, 55.0)          # sâu 15% → 55%
        recovered = ind.scale(structure.get("distance_from_low_pct") or 0.0, 0.0, 15.0)
        base = deep if deep is not None else 50.0
        if recovered is not None:
            base = base * 0.7 + recovered * 0.3           # đã hồi nhẹ khỏi đáy
        return ind.clamp(base)
    score = 50.0
    if structure.get("breakout"):
        score += 30.0
    if structure.get("consolidation_ok"):
        score += 10.0
    position = structure.get("position_in_range_pct")
    if position is not None:
        scaled = ind.scale(position, 30.0, 95.0)
        if scaled is not None:
            score += scaled * 0.2 - 10.0
        if position < 20:
            score -= 15.0                                  # vùng đáy 52 tuần
    if structure.get("breakdown"):
        score -= 25.0
    return ind.clamp(score)


def _ai_component(ai_payload: dict | None) -> float | None:
    """Điểm AI chỉ tồn tại khi có model ĐÃ TRAIN và ensemble có kết quả."""
    if not ai_payload or not ai_payload.get("used_in_scoring"):
        return None
    ensemble = ai_payload.get("ensemble_prediction") or {}
    direction = ensemble.get("direction")
    probability = ensemble.get("probability_up")
    if direction is None:
        return None
    if direction == "BULLISH":
        base = 60.0
        if probability is not None:
            base = 50.0 + max(0.0, min(1.0, probability)) * 50.0
        return ind.clamp(base)
    if direction == "BEARISH":
        base = 40.0
        if probability is not None:
            base = 50.0 - max(0.0, min(1.0, probability)) * 50.0
        return ind.clamp(base)
    return 50.0


def component_scores(features: dict, ai_payload: dict | None = None, mode: str = "bullish") -> dict[str, float | None]:
    trend = features.get("trend") or {}
    momentum = features.get("momentum") or {}
    ma = trend.get("ma") or {}
    rsi = features.get("rsi") or {}
    volume = features.get("volume") or {}
    money_flow = features.get("money_flow") or {}
    rs = features.get("relative_strength") or {}
    structure = features.get("price_structure") or {}

    rsi_value = rsi.get("value")
    return {
        "ai": _ai_component(ai_payload),
        "trend": _trend_component(trend, mode),
        "momentum": _momentum_component(momentum, mode),
        "ma": ma.get("score"),
        "rsi": (_rsi_score_bottom if mode == "bottom" else _rsi_score_bullish)(rsi_value),
        "volume": _volume_component(volume, momentum, mode),
        "money_flow": ind.scale(money_flow.get("cmf"), -0.2, 0.2),
        "relative_strength": ind.scale(rs.get("rs_score_raw"), -8.0, 8.0),
        "price_structure": _structure_component(structure, mode),
    }


def signal_score(features: dict, ai_payload: dict | None = None, mode: str = "bullish",
                 cfg: dict | None = None) -> dict[str, Any]:
    """Tổng điểm 0..100 + chi tiết từng thành phần/trọng số đã dùng."""
    cfg = cfg or cfg_module.get_config()
    signal_cfg = cfg.get("signal") or {}
    weights: dict[str, float] = dict(signal_cfg.get("weights") or {})
    components = component_scores(features, ai_payload, mode)

    available = {k: v for k, v in components.items() if v is not None}
    weights_used = {k: weights.get(k, 0.0) for k in available}
    total_weight = sum(weights_used.values())

    if not available or total_weight <= 0:
        total = None
    else:
        weights_used = {k: round(w / total_weight, 4) for k, w in weights_used.items()}
        total = round(sum(available[k] * weights_used[k] for k in available), 2)

    return {
        "total": total,
        "mode": mode,
        "components": {k: (round(v, 2) if v is not None else None) for k, v in components.items()},
        "weights_configured": weights,
        "weights_used": weights_used,
        "components_available": sorted(available.keys()),
        "components_missing": sorted(k for k in COMPONENTS if components.get(k) is None),
        "ai_included": components.get("ai") is not None,
        "label": score_label(total, cfg),
    }


def score_label(total: float | None, cfg: dict | None = None) -> str:
    cfg = cfg or cfg_module.get_config()
    signal_cfg = cfg.get("signal") or {}
    if total is None:
        return "UNKNOWN"
    if total >= float(signal_cfg.get("score_very_strong", 85)):
        return "VERY_STRONG"
    if total >= float(signal_cfg.get("score_strong", 70)):
        return "STRONG"
    if total >= float(signal_cfg.get("score_moderate", 55)):
        return "MODERATE"
    return "WEAK"


def technical_agreement(conditions: list[dict]) -> float | None:
    """Tỉ lệ điều kiện KỸ THUẬT đồng thuận (bỏ nhóm AI ra khỏi mẫu số)."""
    technical = [c for c in conditions if (c.get("group") or "technical") != "ai"]
    if not technical:
        return None
    passed = sum(1 for c in technical if c.get("passed"))
    return round(passed / len(technical), 4)


def setup_strength(score_payload: dict, confirmations: int, total_conditions: int,
                   conditions: list[dict], ai_payload: dict | None = None,
                   cfg: dict | None = None) -> dict[str, Any]:
    """WEAK/MODERATE/STRONG/VERY_STRONG (spec §14).

    KHÔNG để 1 AI prediction quyết định: điểm yêu cầu đồng thời (score, số điều kiện
    xác nhận, tỉ lệ đồng thuận kỹ thuật). AI (nếu có) chỉ là 1 điều kiện phụ.
    """
    cfg = cfg or cfg_module.get_config()
    bands = ((cfg.get("signal") or {}).get("setup_strength") or {})
    total = score_payload.get("total")
    tech = technical_agreement(conditions)
    confirm_ratio = (confirmations / total_conditions) if total_conditions else 0.0
    ai_agreement = (ai_payload or {}).get("agreement_ratio")

    def meets(key: str) -> bool:
        band = bands.get(key) or {}
        if total is None or total < float(band.get("score", 101)):
            return False
        if confirmations < int(band.get("confirmations", 99)):
            return False
        if tech is not None and tech < float(band.get("technical_agreement", 1.1)):
            return False
        return True

    if meets("very_strong"):
        level = "VERY_STRONG"
    elif meets("strong"):
        level = "STRONG"
    elif meets("moderate"):
        level = "MODERATE"
    else:
        level = "WEAK"

    return {
        "level": level,
        "score": total,
        "confirmations": confirmations,
        "total_conditions": total_conditions,
        "confirm_ratio": round(confirm_ratio, 4),
        "technical_agreement": tech,
        "ai_agreement": ai_agreement,
        "ai_used": bool(ai_payload and ai_payload.get("used_in_scoring")),
    }


def confidence(score_payload: dict, confirm_ratio: float, conditions: list[dict],
               data_quality: dict | None = None, ai_payload: dict | None = None) -> dict[str, Any]:
    """Confidence 0..100 = điểm + mức đồng thuận + chất lượng dữ liệu (không có AI vẫn tính được)."""
    total = score_payload.get("total")
    if total is None:
        return {"value": None, "parts": {"score": None, "agreement": None, "data_quality": None}}
    tech = technical_agreement(conditions)
    quality = ((data_quality or {}).get("score") or 100) / 100
    value = total * 0.55 + confirm_ratio * 100 * 0.3 + (tech or 0) * 100 * 0.15
    value *= 0.6 + 0.4 * quality                      # dữ liệu mỏng ⇒ giảm confidence
    ai_ratio = (ai_payload or {}).get("agreement_ratio")
    if ai_ratio is not None:
        value = value * 0.85 + ai_ratio * 100 * 0.15
    return {
        "value": round(max(0.0, min(100.0, value)), 2),
        "parts": {
            "score": round(total, 2),
            "agreement": round((tech or 0) * 100, 2),
            "confirm_ratio": round(confirm_ratio, 4),
            "data_quality": round(quality * 100, 2),
            "ai_agreement": ai_ratio,
        },
    }
