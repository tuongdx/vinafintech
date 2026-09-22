"""Adapter Model 1 — Wavelet Transformer (MODWT đa tầng tần số + Transformer mỗi tầng).

Kiến trúc mô tả (spec §6): chuỗi giá → MODWT tách ``levels`` tầng tần số (giữ nguyên
độ dài, không lệch timing) → mỗi tầng đi qua một nhánh Transformer riêng (không share
weight) → gộp biểu diễn → dự đoán hướng.

Hiện trạng: **CHƯA TRAIN** (không có checkpoint). Phần MODWT ở ``wavelet.py`` là code
THẬT và được dùng cho feature engineering; phần Transformer chỉ có metadata + interface
để nối checkpoint sau này.
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine.ai.base import (
    AIModel,
    ModelNotTrainedError,
    ModelStatus,
    Prediction,
)


class WaveletTransformerModel(AIModel):
    name = "wavelet_transformer"
    version = "0.1.0-spec"
    artifact_name = "wavelet_transformer.pt"

    def __init__(self, cfg: dict | None = None) -> None:
        super().__init__(cfg)
        ai_cfg = (cfg or {}).get("ai") or {}
        feat_cfg = (cfg or {}).get("features") or {}
        self.levels = int(feat_cfg.get("wavelet_levels", 4))
        self.sequence_length = int(ai_cfg.get("sequence_length", 60))
        self.d_model = 64
        self.n_head = 4
        self.num_layers = 2
        self.horizon_days = 10

    def predict(self, features: dict) -> Prediction:
        """Chưa train ⇒ raise (spec §8). Không trả về số liệu mô phỏng."""
        raise ModelNotTrainedError(
            "WaveletTransformerModel chưa được huấn luyện (thiếu checkpoint "
            f"'{self.artifact_name}' và pipeline train). Không có prediction được tạo."
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "backend": "MODWT (Haar, à trous) + per-level Transformer encoder",
            "status": self.status(),
            "artifact": str(self.artifact_path()),
            "trained": False,
            "note": "MODWT trong app/services/signal_engine/wavelet.py là code thật (dùng cho feature); "
                    "nhánh Transformer chưa có checkpoint nên KHÔNG tạo prediction.",
            "config": {
                "levels": self.levels,
                "sequence_length": self.sequence_length,
                "d_model": self.d_model,
                "n_head": self.n_head,
                "num_layers": self.num_layers,
                "share_weights_across_levels": False,
                "horizon_days": self.horizon_days,
            },
        }

    def feature_vector_spec(self) -> dict[str, Any]:
        """Mô tả input mà model SẼ cần khi được train (để chuẩn bị dataset)."""
        return {
            "per_level": ["detail_series (len = sequence_length)", "level_energy_share"],
            "shared": ["log_return", "volume_ratio", "rsi", "cmf", "rs_vs_benchmark"],
            "scaling": "z-score theo cửa sổ cuốn (chỉ dùng dữ liệu <= t)",
        }
