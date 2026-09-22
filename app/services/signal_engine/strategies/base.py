"""Khung chung cho các chiến thuật — Condition, kết quả đánh giá, tiện ích dùng chung.

Một điều kiện có 3 trạng thái (KHÔNG dùng True/False đơn giản):
- ``PASS`` / ``FAIL``   : có dữ liệu và kết luận được.
- ``UNAVAILABLE``       : thiếu dữ liệu hoặc model chưa train ⇒ **không tính vào mẫu số**
  (spec §28: không được tạo signal dựa trên dữ liệu không đầy đủ mà không đánh dấu).

Nhờ vậy khi model AI chưa train, hệ thống vẫn chạy được nhưng ghi rõ "AI chưa có",
và ngưỡng xác nhận được quy đổi minh bạch qua ``scale_minimum()``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
UNAVAILABLE = "UNAVAILABLE"

GROUP_AI = "ai"
GROUP_TECHNICAL = "technical"
GROUP_MARKET = "market"


@dataclass
class Condition:
    key: str
    label: str
    group: str = GROUP_TECHNICAL
    required: bool = False
    state: str = UNAVAILABLE
    value: Any = None
    threshold: Any = None
    reason: str = ""

    @property
    def passed(self) -> bool | None:
        if self.state == PASS:
            return True
        if self.state == FAIL:
            return False
        return None

    @property
    def available(self) -> bool:
        return self.state in (PASS, FAIL)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["passed"] = self.passed
        data["available"] = self.available
        return data


def cond(key: str, label: str, passed: bool | None, value: Any, threshold: Any = None,
         group: str = GROUP_TECHNICAL, required: bool = False, reason: str = "") -> Condition:
    """Tạo Condition; ``passed=None`` ⇒ UNAVAILABLE (thiếu dữ liệu/model chưa train)."""
    if passed is None:
        state = UNAVAILABLE
    else:
        state = PASS if passed else FAIL
    return Condition(key=key, label=label, group=group, required=required, state=state,
                     value=value, threshold=threshold, reason=reason)


def ai_condition(model_name: str, ai_payload: dict | None, required: bool = False) -> Condition:
    """Điều kiện AI: khi model chưa train ⇒ UNAVAILABLE (KHÔNG coi là PASS/FAIL)."""
    label_map = {
        "wavelet_transformer": "Wavelet Transformer dự đoán tăng",
        "cmdmamba": "CMDMamba dự đoán tăng",
    }
    label = label_map.get(model_name, f"AI {model_name} dự đoán tăng")
    models = (ai_payload or {}).get("models") or []
    found = next((m for m in models if m.get("model") == model_name), None)
    if not found or not found.get("trained"):
        return cond(
            f"ai_{model_name}", label, None, value=found.get("status") if found else "MODEL_NOT_TRAINED",
            threshold="BULLISH", group=GROUP_AI, required=required,
            reason="Model chưa được train — điều kiện AI không được tính (không bịa prediction)",
        )
    direction = found.get("direction")
    return cond(f"ai_{model_name}", label, direction == "BULLISH", value=direction,
                threshold="BULLISH", group=GROUP_AI, required=required,
                reason=f"model status = {found.get('status')}")


def evaluate(conditions: list[Condition], cfg: dict, *, min_confirmations: int | None = None) -> dict[str, Any]:
    """Gộp danh sách điều kiện → kết quả chiến thuật (chưa gắn state machine)."""
    available = [c for c in conditions if c.available]
    unavailable = [c for c in conditions if not c.available]
    passed = [c for c in available if c.state == PASS]
    failed = [c for c in available if c.state == FAIL]
    required = [c for c in available if c.required]
    required_failed = [c for c in required if c.state == FAIL]

    minimum = min_confirmations
    effective_minimum = minimum
    if minimum is not None and unavailable:
        # Quy đổi minh bạch: điều kiện không có dữ liệu bị loại khỏi mẫu số,
        # ngưỡng cũng quy đổi tương ứng (làm tròn LÊN để giữ độ chặt).
        declared_total = len(conditions)
        if declared_total:
            effective_minimum = max(1, math.ceil(minimum * len(available) / declared_total))

    confirmation_ok = True
    if effective_minimum is not None:
        confirmation_ok = len(passed) >= effective_minimum

    return {
        "conditions": [c.as_dict() for c in conditions],
        "confirmations": len(passed),
        "total_conditions": len(available),
        "declared_conditions": len(conditions),
        "unavailable_conditions": [c.as_dict() for c in unavailable],
        "failed_conditions": [c.as_dict() for c in failed],
        "required_total": len(required),
        "required_passed": len(required_failed) == 0 and len(required) > 0,
        "minimum_confirmations": minimum,
        "effective_minimum_confirmation": effective_minimum,
        "confirmation_ok": bool(confirmation_ok),
    }


def finalize(result: dict[str, Any], bad_signals: list[dict], exit_threshold: int,
             signal: str | None, score: float | None, cfg: dict | None = None) -> dict[str, Any]:
    """Thêm bad signals + exit condition + quyết định signal (ADMIN)."""
    bad_count = len(bad_signals)
    result.update({
        "bad_signals": bad_signals,
        "bad_signal_count": bad_count,
        "bad_signal_threshold": exit_threshold,
        "exit_condition": bad_count >= exit_threshold,
        "signal": signal,
        "rank_score": score,
    })
    return result


@dataclass
class StrategyOutcome:
    strategy: str
    enabled: bool = True
    evaluated: bool = False
    result: dict[str, Any] = field(default_factory=dict)
