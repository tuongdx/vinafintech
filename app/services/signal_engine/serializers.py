"""Serializers — TÁCH payload Admin và payload public (spec §4, §20, §33.2-§33.4).

Nguyên tắc BẢO MẬT: public KHÔNG dùng blacklist mà **whitelist** — chỉ dựng lại những
field được phép. Field quyết định giao dịch (signal/entry/stop_loss/take_profit/
trailing_stop/exit…) không bao giờ được đưa vào dict trả cho USER, kể cả khi frontend
sau đó có ẩn hay không.

``assert_public_clean()`` được dùng trong test để chặn hồi quy rò rỉ dữ liệu.
"""
from __future__ import annotations

from typing import Any

# Từ khoá (lowercase) TUYỆT ĐỐI không được xuất hiện trong payload public
FORBIDDEN_PUBLIC_KEYS = {
    "signal",
    "signal_type",
    "recommendation",
    "buy",
    "sell",
    "entry",
    "entry_price",
    "stop_loss",
    "stoploss",
    "sl",
    "take_profit",
    "takeprofit",
    "tp",
    "target_price",
    "trailing_stop",
    "trailingstop",
    "exit_price",
    "exit_condition",
    "exit_reason",
    "position_size",
    "confidence",
}


def assert_public_clean(payload: Any, path: str = "") -> None:
    """Raise ``ValueError`` nếu payload public chứa field bị cấm (đệ quy toàn cây)."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Payload public rò rỉ field bị cấm: {path}.{key}")
            assert_public_clean(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for idx, item in enumerate(payload):
            assert_public_clean(item, f"{path}[{idx}]")


# ---------------------------------------------------------------------------
# ADMIN
# ---------------------------------------------------------------------------
def _admin_conditions(conditions: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for cond in conditions or []:
        out.append({
            "key": cond.get("key"),
            "label": cond.get("label"),
            "group": cond.get("group"),
            "required": bool(cond.get("required")),
            "state": cond.get("state"),
            "passed": cond.get("passed"),
            "value": cond.get("value"),
            "threshold": cond.get("threshold"),
            "reason": cond.get("reason") or "",
        })
    return out


def _admin_strategy(name: str, strat: dict) -> dict:
    levels = strat.get("levels") or {}
    return {
        "strategy": name,
        "evaluated": bool(strat.get("evaluated")),
        "state": strat.get("state"),
        "signal": strat.get("signal"),
        "score": strat.get("score"),
        "confidence": (strat.get("confidence") or {}).get("value"),
        "setup_strength": (strat.get("setup_strength") or {}).get("level"),
        "setup_detail": strat.get("setup_strength"),
        "confirmations": strat.get("confirmations"),
        "total_conditions": strat.get("total_conditions"),
        "declared_conditions": strat.get("declared_conditions"),
        "minimum_confirmations": strat.get("minimum_confirmations"),
        "effective_minimum_confirmation": strat.get("effective_minimum_confirmation"),
        "required_passed": strat.get("required_passed"),
        "bad_signal_count": strat.get("bad_signal_count"),
        "bad_signals": strat.get("bad_signals") or [],
        "exit_condition": bool(strat.get("exit_condition")),
        "signal_gate": strat.get("signal_gate"),
        "transition": strat.get("transition"),
        "levels": {
            "entry": levels.get("entry"),
            "stop_loss": levels.get("stop_loss"),
            "take_profit": levels.get("take_profit"),
            "trailing_stop": levels.get("trailing_stop"),
            "atr": levels.get("atr"),
            "rr_ratio": levels.get("rr_ratio"),
            "risk_pct_from_entry": levels.get("risk_pct_from_entry"),
        },
        "conditions": _admin_conditions(strat.get("conditions")),
        "failed_conditions": _admin_conditions(strat.get("failed_conditions")),
        "unavailable_conditions": _admin_conditions(strat.get("unavailable_conditions")),
        "reasons": strat.get("reasons") or [],
        "error": strat.get("error"),
    }


def admin_view(snapshot: dict) -> dict:
    """Payload ĐẦY ĐỦ cho ADMIN (kèm BUY/SELL, Entry, SL, TP, Trailing, confidence)."""
    scores = snapshot.get("scores") or {}
    return {
        "symbol": snapshot.get("symbol"),
        "engine": snapshot.get("engine"),
        "as_of": snapshot.get("as_of"),
        "generated_at": snapshot.get("generated_at"),
        "price": snapshot.get("price"),
        "status": snapshot.get("status"),
        "errors": snapshot.get("errors") or [],
        "config_version": snapshot.get("config_version"),
        "market": snapshot.get("market"),
        "risk": snapshot.get("risk"),
        "decision": snapshot.get("decision"),
        "primary_strategy": snapshot.get("primary_strategy"),
        "setup": snapshot.get("setup"),
        "score": {
            "primary_mode": scores.get("primary_mode"),
            "bullish": scores.get("bullish"),
            "bottom": scores.get("bottom"),
        },
        "ai": snapshot.get("ai"),
        "features": snapshot.get("features"),
        "strategies": {name: _admin_strategy(name, strat) for name, strat in (snapshot.get("strategies") or {}).items()},
    }


def admin_list_row(snapshot: dict, signal_row: dict | None = None) -> dict:
    """Dòng bảng của menu Admin (spec §17) — gọn, đủ cột hiển thị."""
    decision = snapshot.get("decision") or {}
    levels = decision.get("levels") or {}
    return {
        "symbol": snapshot.get("symbol"),
        "strategy": decision.get("strategy"),
        "signal": decision.get("signal"),
        "state": decision.get("state"),
        "score": (snapshot.get("setup") or {}).get("score"),
        "confidence": decision.get("confidence"),
        "setup_strength": (snapshot.get("setup") or {}).get("level"),
        "entry": levels.get("entry"),
        "stop_loss": levels.get("stop_loss"),
        "take_profit": levels.get("take_profit"),
        "trailing_stop": levels.get("trailing_stop"),
        "risk_level": (snapshot.get("risk") or {}).get("level"),
        "market_regime": ((snapshot.get("market") or {}).get("regime") or {}).get("regime"),
        "confirmations": (snapshot.get("setup") or {}).get("confirmations"),
        "total_conditions": (snapshot.get("setup") or {}).get("total_conditions"),
        "as_of": snapshot.get("as_of"),
        "created_at": (signal_row or {}).get("created_at"),
        "id": (signal_row or {}).get("id"),
    }


# ---------------------------------------------------------------------------
# PUBLIC / USER
# ---------------------------------------------------------------------------
def public_view(snapshot: dict) -> dict:
    """Payload cho USER (spec §19) — KHÔNG có BUY/SELL/Entry/SL/TP/Trailing/Confidence."""
    features = snapshot.get("features") or {}
    scores = snapshot.get("scores") or {}
    primary_name = snapshot.get("primary_strategy")
    primary = (snapshot.get("strategies") or {}).get(primary_name) or {}
    score_payload = scores.get(scores.get("primary_mode") or "bullish") or {}
    ai = snapshot.get("ai") or {}
    risk_payload = snapshot.get("risk") or {}
    trend = features.get("trend") or {}
    ma = trend.get("ma") or {}

    payload = {
        "symbol": snapshot.get("symbol"),
        "engine": snapshot.get("engine"),
        "as_of": snapshot.get("as_of"),
        "generated_at": snapshot.get("generated_at"),
        "price": snapshot.get("price"),
        "status": snapshot.get("status"),
        "config_version": snapshot.get("config_version"),
        "market_state": {
            "regime": ((snapshot.get("market") or {}).get("regime") or {}).get("regime"),
            "benchmark": (snapshot.get("market") or {}).get("benchmark"),
        },
        "analysis": {
            "trend": {
                "direction": trend.get("direction"),
                "strength": trend.get("strength"),
                "label": trend.get("label"),
                "ma_alignment": ma.get("label"),
                "ma_values": ma.get("values"),
                "price_above_all_ma": ma.get("price_above_all"),
            },
            "momentum": {
                "short_term": (features.get("momentum") or {}).get("short_term"),
                "medium_term": (features.get("momentum") or {}).get("medium_term"),
                "long_term": (features.get("momentum") or {}).get("long_term"),
                "composite": (features.get("momentum") or {}).get("composite"),
                "label": (features.get("momentum") or {}).get("label"),
            },
            "rsi": {
                "value": (features.get("rsi") or {}).get("value"),
                "state": (features.get("rsi") or {}).get("state"),
                "overbought": (features.get("rsi") or {}).get("overbought"),
                "oversold": (features.get("rsi") or {}).get("oversold"),
            },
            "volume": {
                "volume_ratio": (features.get("volume") or {}).get("volume_ratio"),
                "label": (features.get("volume") or {}).get("label"),
                "surge": (features.get("volume") or {}).get("volume_surge"),
                "exhaustion": (features.get("volume") or {}).get("volume_exhaustion"),
            },
            "money_flow": {
                "cmf": (features.get("money_flow") or {}).get("cmf"),
                "state": (features.get("money_flow") or {}).get("state"),
                "rising": (features.get("money_flow") or {}).get("rising"),
            },
            "relative_strength": {
                "available": (features.get("relative_strength") or {}).get("available"),
                "score": (features.get("relative_strength") or {}).get("rs_score_raw"),
                "label": (features.get("relative_strength") or {}).get("label"),
                "periods": (features.get("relative_strength") or {}).get("periods"),
            },
            "price_structure": {
                "high_52w": (features.get("price_structure") or {}).get("high_52w"),
                "low_52w": (features.get("price_structure") or {}).get("low_52w"),
                "distance_from_high_pct": (features.get("price_structure") or {}).get("distance_from_high_pct"),
                "distance_from_low_pct": (features.get("price_structure") or {}).get("distance_from_low_pct"),
                "position_in_range_pct": (features.get("price_structure") or {}).get("position_in_range_pct"),
                "consolidation_bars": (features.get("price_structure") or {}).get("consolidation_bars"),
                "breakout": (features.get("price_structure") or {}).get("breakout"),
            },
            "wavelet": {
                "available": (features.get("wavelet") or {}).get("available"),
                "energy_share": (features.get("wavelet") or {}).get("energy_share"),
                "noise_ratio": (features.get("wavelet") or {}).get("noise_ratio"),
                "smooth_trend": (features.get("wavelet") or {}).get("smooth_trend"),
            },
        },
        "ai": {
            "status": ai.get("status"),
            "trained_models": ai.get("trained_models"),
            "total_models": ai.get("total_models"),
            "used_in_scoring": bool(ai.get("used_in_scoring")),
            "note": ai.get("note"),
            "models": [
                {"model": m.get("model"), "status": m.get("status"), "trained": bool(m.get("trained")), "direction": m.get("direction")}
                for m in (ai.get("models") or [])
            ],
        },
        "scores": {
            "ai_score": (score_payload.get("components") or {}).get("ai"),
            "trend_score": (score_payload.get("components") or {}).get("trend"),
            "momentum_score": (score_payload.get("components") or {}).get("momentum"),
            "ma_score": (score_payload.get("components") or {}).get("ma"),
            "rsi_score": (score_payload.get("components") or {}).get("rsi"),
            "volume_score": (score_payload.get("components") or {}).get("volume"),
            "money_flow_score": (score_payload.get("components") or {}).get("money_flow"),
            "relative_strength_score": (score_payload.get("components") or {}).get("relative_strength"),
            "price_structure_score": (score_payload.get("components") or {}).get("price_structure"),
            "signal_score": score_payload.get("total"),
            "label": score_payload.get("label"),
            "ai_included": bool(score_payload.get("ai_included")),
            "components_missing": score_payload.get("components_missing"),
        },
        "risk": {
            "risk_score": risk_payload.get("risk_score"),
            "level": risk_payload.get("level"),
            "reasons": risk_payload.get("reasons"),
        },
        "setup": {
            "strategy": primary_name,
            "strength": (snapshot.get("setup") or {}).get("level"),
            "state": primary.get("state"),
            "confirmations": (snapshot.get("setup") or {}).get("confirmations"),
            "total_conditions": (snapshot.get("setup") or {}).get("total_conditions"),
            "confirm_ratio": (snapshot.get("setup") or {}).get("confirm_ratio"),
            "technical_agreement": (snapshot.get("setup") or {}).get("technical_agreement"),
            "unavailable_conditions": len(primary.get("unavailable_conditions") or []),
        },
        "conditions": [
            {
                "key": c.get("key"),
                "label": c.get("label"),
                "group": c.get("group"),
                "state": c.get("state"),
                "value": c.get("value"),
                "threshold": c.get("threshold"),
            }
            for c in (primary.get("conditions") or [])
        ],
    }
    assert_public_clean(payload)
    return payload


def public_scores(snapshot: dict) -> dict:
    """Endpoint /scores — chỉ phần điểm số."""
    full = public_view(snapshot)
    return {
        "symbol": full["symbol"],
        "as_of": full["as_of"],
        "price": full["price"],
        "scores": full["scores"],
        "risk": full["risk"],
        "market_state": full["market_state"],
    }


def public_setup(snapshot: dict) -> dict:
    """Endpoint /setup — chỉ trạng thái setup + điều kiện hội tụ."""
    full = public_view(snapshot)
    return {
        "symbol": full["symbol"],
        "as_of": full["as_of"],
        "status": full["status"],
        "setup": full["setup"],
        "conditions": full["conditions"],
        "market_state": full["market_state"],
    }
