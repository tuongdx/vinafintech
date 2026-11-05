"""Interface chuẩn cho mọi AI model của Signal Engine.

Quy ước:
- ``predict(features)`` là điểm vào DUY NHẤT. Model chưa train ⇒ ``ModelNotTrainedError``.
- ``status()`` cho biết: ``MODEL_READY`` (có checkpoint + runtime),
  ``MODEL_UNAVAILABLE`` (có checkpoint nhưng thiếu runtime/thư viện),
  ``MODEL_NOT_TRAINED`` (không có checkpoint).
- ``describe()`` trả metadata kiến trúc (hiển thị cho Admin biết hệ thống đang có gì).
"""
from __future__ import annotations

import enum
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ModelStatus(str, enum.Enum):
    MODEL_READY = "MODEL_READY"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_NOT_TRAINED = "MODEL_NOT_TRAINED"


class ModelNotTrainedError(NotImplementedError):
    """Raise khi model chưa có checkpoint — KHÔNG được thay bằng prediction giả."""


def model_dir() -> Path:
    """Thư mục chứa checkpoint (đổi bằng ENV ``SIGNAL_MODEL_DIR``).

    Mặc định ``<repo>/models/signal_engine`` (file này nằm ở app/services/signal_engine/ai/).
    """
    default = Path(__file__).resolve().parents[4] / "models" / "signal_engine"
    return Path(os.environ.get("SIGNAL_MODEL_DIR") or default)


@dataclass
class Prediction:
    """Kết quả dự đoán. Mọi trường số là ``None`` khi model chưa train.

    KHÔNG được điền giá trị ước lượng/mô phỏng vào đây: Admin phải phân biệt được
    "model chưa chạy" với "model dự đoán 62%".
    """

    model: str
    status: str = ModelStatus.MODEL_NOT_TRAINED.value
    direction: str | None = None          # BULLISH | BEARISH | NEUTRAL
    probability_up: float | None = None   # 0..1
    confidence: float | None = None       # 0..1
    horizon_days: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def trained(self) -> bool:
        return self.status == ModelStatus.MODEL_READY.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "status": self.status,
            "trained": self.trained,
            "direction": self.direction,
            "probability_up": self.probability_up,
            "confidence": self.confidence,
            "horizon_days": self.horizon_days,
            "detail": self.detail,
        }


class AIModel(ABC):
    """Base class cho mọi model (spec §8)."""

    name: str = "base"
    version: str = "0.0.0"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self.artifact_name: str = ""
        self._loaded = False

    # -- vòng đời ---------------------------------------------------------
    def artifact_path(self) -> Path:
        return model_dir() / self.artifact_name if self.artifact_name else model_dir()

    def load(self) -> bool:
        """Nạp checkpoint nếu có. Trả ``True`` khi model sẵn sàng suy luận."""
        path = self.artifact_path()
        if not self.artifact_name or not path.exists():
            self._loaded = False
            return False
        self._loaded = False  # có checkpoint nhưng CHƯA có runtime huấn luyện trong project
        return self._loaded

    def status(self) -> str:
        if not self.artifact_name or not self.artifact_path().exists():
            return ModelStatus.MODEL_NOT_TRAINED.value
        return ModelStatus.MODEL_UNAVAILABLE.value  # có file nhưng thiếu runtime ⇒ chưa dùng được

    def ready(self) -> bool:
        return self.status() == ModelStatus.MODEL_READY.value

    # -- suy luận ---------------------------------------------------------
    @abstractmethod
    def predict(self, features: dict) -> Prediction:
        """Suy luận. Model chưa train ⇒ raise ``ModelNotTrainedError``."""
        raise NotImplementedError

    def predict_safe(self, features: dict) -> Prediction:
        """Bọc ``predict`` để tầng trên không phải try/except — vẫn KHÔNG bịa kết quả."""
        try:
            return self.predict(features)
        except ModelNotTrainedError as exc:
            return Prediction(model=self.name, status=ModelStatus.MODEL_NOT_TRAINED.value, detail={"reason": str(exc)})
        except NotImplementedError as exc:
            return Prediction(model=self.name, status=ModelStatus.MODEL_NOT_TRAINED.value, detail={"reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return Prediction(model=self.name, status=ModelStatus.MODEL_UNAVAILABLE.value, detail={"error": str(exc)})

    def train(self, dataset: Any) -> None:
        """Huấn luyện — chưa triển khai trong project này."""
        raise NotImplementedError(
            "VinaFintech Signal Engine chưa có pipeline huấn luyện cho model này. "
            "Cần dataset gán nhãn + pipeline riêng trước khi bật model."
        )

    # -- metadata ---------------------------------------------------------
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        raise NotImplementedError
