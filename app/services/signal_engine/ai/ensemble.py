"""Ensemble Engine (Phase 6) — kết hợp Wavelet Transformer + CMDMamba.

Mục tiêu (spec §7): giảm variance, giảm ảnh hưởng của noise → prediction ổn định hơn.
Prediction ensemble **KHÔNG** được dùng trực tiếp làm BUY/SELL (spec §2, §7).

Hiện trạng: cả 2 model chưa train ⇒ ``status = MODEL_NOT_TRAINED``,
``ensemble_prediction = None``, ``agreement = None``. Tầng scoring sẽ TỰ ĐỘNG
KHÔNG tính điểm AI (và renormalize trọng số) thay vì bịa số.
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine.ai.base import ModelStatus, Prediction
from app.services.signal_engine.ai.cmdmamba import CMDMambaModel
from app.services.signal_engine.ai.wavelet_transformer import WaveletTransformerModel


class EnsembleModel:
    name = "signal_ensemble"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        ai_cfg = self.cfg.get("ai") or {}
        self.min_agreement = float(ai_cfg.get("ensemble_min_agreement", 0.5))
        self.allow_untrained_boost = bool(ai_cfg.get("allow_untrained_boost", False))
        self.models: list[Any] = [WaveletTransformerModel(self.cfg), CMDMambaModel(self.cfg)]

    # ------------------------------------------------------------------
    def model_descriptions(self) -> list[dict]:
        return [m.describe() for m in self.models]

    def status(self) -> str:
        statuses = [m.status() for m in self.models]
        if all(s == ModelStatus.MODEL_READY.value for s in statuses):
            return ModelStatus.MODEL_READY.value
        if any(s == ModelStatus.MODEL_UNAVAILABLE.value for s in statuses):
            return ModelStatus.MODEL_UNAVAILABLE.value
        return ModelStatus.MODEL_NOT_TRAINED.value

    # ------------------------------------------------------------------
    def predict(self, features: dict[str, Any] | None = None) -> dict[str, Any]:
        """Chạy tất cả model + hợp nhất. Không bao giờ tạo số liệu giả."""
        features = features or {}
        results: list[Prediction] = [m.predict_safe(features) for m in self.models]
        payload = [r.as_dict() for r in results]
        trained = [r for r in results if r.trained]

        if not trained:
            return {
                "status": self.status(),
                "trained_models": 0,
                "total_models": len(results),
                "ensemble_prediction": None,
                "agreement": None,
                "agreement_ratio": None,
                "models": payload,
                "note": "Chưa có model nào được train — hệ thống KHÔNG tạo prediction AI. "
                        "Điểm AI bị loại khỏi Signal Score và trọng số được phân bổ lại.",
                "used_in_scoring": False,
            }

        bullish = sum(1 for r in trained if r.direction == "BULLISH")
        bearish = sum(1 for r in trained if r.direction == "BEARISH")
        agreement_ratio = max(bullish, bearish) / len(trained)
        probs = [r.probability_up for r in trained if r.probability_up is not None]
        probability_up = sum(probs) / len(probs) if probs else None
        if bullish > bearish:
            direction = "BULLISH"
        elif bearish > bullish:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return {
            "status": ModelStatus.MODEL_READY.value if len(trained) == len(results) else ModelStatus.MODEL_UNAVAILABLE.value,
            "trained_models": len(trained),
            "total_models": len(results),
            "ensemble_prediction": {
                "direction": direction,
                "probability_up": round(probability_up, 4) if probability_up is not None else None,
                "models_agreeing": max(bullish, bearish),
                "models_total": len(trained),
            },
            "agreement": agreement_ratio >= self.min_agreement,
            "agreement_ratio": round(agreement_ratio, 4),
            "models": payload,
            "note": None,
            "used_in_scoring": True,
        }
