"""Strategy Engine — registry 3 chiến thuật (Uptrend / Bottom Fishing / Swing)."""
from __future__ import annotations

from typing import Any

from app.services.signal_engine.strategies import bottom_fishing, swing, uptrend

STRATEGIES = (uptrend, bottom_fishing, swing)
STRATEGY_NAMES = tuple(m.NAME for m in STRATEGIES)


def evaluate_all(features: dict, ai_payload: dict | None, regime: dict | None,
                 previous_states: dict[str, str] | None = None,
                 scores: dict[str, float | None] | None = None,
                 cfg: dict | None = None) -> dict[str, dict[str, Any]]:
    """Chạy TẤT CẢ chiến thuật trên cùng một snapshot feature (1 lần tính, nhiều chiến thuật)."""
    previous_states = previous_states or {}
    scores = scores or {}
    out: dict[str, dict[str, Any]] = {}
    for module in STRATEGIES:
        name = module.NAME
        try:
            out[name] = module.run(
                features,
                ai_payload,
                regime,
                previous_state=previous_states.get(name),
                cfg=cfg,
                score=scores.get(name),
            )
        except Exception as exc:  # noqa: BLE001 — 1 chiến thuật lỗi không làm sập cả scan
            out[name] = {
                "strategy": name,
                "enabled": True,
                "evaluated": False,
                "error": f"FEATURE_CALCULATION_ERROR: {exc}",
                "signal": None,
            }
    return out
