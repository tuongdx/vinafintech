"""Adapter Model 2 — CMDMamba (DoubleMamba state space + DConvFFN).

Kiến trúc mô tả (spec §6):
- Nhánh NHANH: state nhỏ → phản ứng nhanh với biến động ngắn hạn.
- Nhánh CHẬM: state lớn hơn → giữ xu hướng dài hạn.
- Hai nhánh gộp thành representation chứa cả short-term & long-term behavior.
- Module DConvFFN xử lý tương tác giữa các feature kỹ thuật (RSI, volume, momentum…)
  để model nhận biết khi các feature ĐỒNG THUẬN hay XUNG ĐỘT.

Hiện trạng: **CHƯA TRAIN** — không có checkpoint/mamba runtime trong project.
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine.ai.base import (
    AIModel,
    ModelNotTrainedError,
    Prediction,
)


class CMDMambaModel(AIModel):
    name = "cmdmamba"
    version = "0.1.0-spec"
    artifact_name = "cmdmamba.pt"

    def __init__(self, cfg: dict | None = None) -> None:
        super().__init__(cfg)
        ai_cfg = (cfg or {}).get("ai") or {}
        self.sequence_length = int(ai_cfg.get("sequence_length", 60))
        self.fast_state = 16
        self.slow_state = 128
        self.d_conv = 4
        self.ffn_mult = 2
        self.horizon_days = 10

    def predict(self, features: dict) -> Prediction:
        raise ModelNotTrainedError(
            "CMDMambaModel chưa được huấn luyện (thiếu checkpoint "
            f"'{self.artifact_name}' và runtime state-space). Không có prediction được tạo."
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "backend": "DoubleMamba (fast + slow state) + DConvFFN feature interaction",
            "status": self.status(),
            "artifact": str(self.artifact_path()),
            "trained": False,
            "note": "Chưa có checkpoint nên KHÔNG tạo prediction.",
            "config": {
                "sequence_length": self.sequence_length,
                "fast_state_dim": self.fast_state,
                "slow_state_dim": self.slow_state,
                "d_conv": self.d_conv,
                "ffn_mult": self.ffn_mult,
                "horizon_days": self.horizon_days,
                "fusion": "concat(fast, slow) -> linear",
            },
        }

    def feature_vector_spec(self) -> dict[str, Any]:
        return {
            "channels": ["log_return", "high_low_range_pct", "volume_zscore", "rsi_norm",
                         "cmf", "momentum_short", "momentum_medium", "rs_vs_benchmark"],
            "interaction_module": "DConvFFN (depthwise conv across feature axis + FFN)",
            "scaling": "z-score theo cửa sổ cuốn (chỉ dữ liệu <= t)",
        }
