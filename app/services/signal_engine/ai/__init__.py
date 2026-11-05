"""AI models cho VinaFintech Signal Engine.

⚠️ TRUNG THỰC (spec §8, §33.6, §33.12):
Project hiện **CHƯA có model AI nào được train** (không có checkpoint, không có
pipeline huấn luyện, không có torch trong requirements). Vì vậy các adapter ở đây:

- Có interface thật (``AIModel.predict``) + metadata kiến trúc thật (spec §6).
- Trả ``status = MODEL_NOT_TRAINED`` và ``predict()`` **raise** — KHÔNG sinh
  prediction giả để hiển thị như prediction thật.
- Khi có checkpoint hợp lệ (đặt ở ``SIGNAL_MODEL_DIR``, mặc định ``models/signal_engine``),
  adapter tự chuyển ``MODEL_READY`` và dùng runtime tương ứng — phần này được thiết kế
  sẵn nhưng chưa kích hoạt vì chưa có checkpoint.
"""
from app.services.signal_engine.ai.base import (  # noqa: F401
    AIModel,
    ModelNotTrainedError,
    ModelStatus,
    Prediction,
)
from app.services.signal_engine.ai.cmdmamba import CMDMambaModel  # noqa: F401
from app.services.signal_engine.ai.ensemble import EnsembleModel  # noqa: F401
from app.services.signal_engine.ai.wavelet_transformer import WaveletTransformerModel  # noqa: F401
