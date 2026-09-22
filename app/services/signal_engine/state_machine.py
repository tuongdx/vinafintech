"""Signal State Machine (spec §16) — vòng đời của một setup.

    NO_SETUP → WATCH → SETUP_FORMING → CONFIRMED → ACTIVE → WEAKENING → EXIT
                                                              ↘ INVALIDATED

- Trạng thái dựa trên số điều kiện xác nhận + số tín hiệu XẤU tích luỹ (spec §10).
- Áp dụng cho CẢ Admin và User; Admin thấy thêm signal/entry/SL, User chỉ thấy trạng thái.
- Chuyển trạng thái chỉ xảy ra khi có lý do cụ thể (``reason``) — phục vụ audit (spec §27).
"""
from __future__ import annotations

from typing import Any

from app.services.signal_engine import config as cfg_module

NO_SETUP = "NO_SETUP"
WATCH = "WATCH"
SETUP_FORMING = "SETUP_FORMING"
CONFIRMED = "CONFIRMED"
ACTIVE = "ACTIVE"
WEAKENING = "WEAKENING"
EXIT = "EXIT"
INVALIDATED = "INVALIDATED"

STATES = (NO_SETUP, WATCH, SETUP_FORMING, CONFIRMED, ACTIVE, WEAKENING, EXIT, INVALIDATED)

_ACTIVE_LIKE = {CONFIRMED, ACTIVE, WEAKENING}


def next_state(prev_state: str | None, confirmations: int, total_conditions: int,
               required_passed: bool, bad_signal_count: int, exit_condition: bool,
               data_ok: bool = True, cfg: dict | None = None) -> dict[str, Any]:
    """Trả trạng thái kế tiếp + lý do. Hàm thuần (dễ test, dễ audit)."""
    cfg = cfg or cfg_module.get_config()
    sm_cfg = cfg.get("state_machine") or {}
    prev = (prev_state or "").upper() or None
    ratio = (confirmations / total_conditions) if total_conditions else 0.0

    def out(state: str, reason: str) -> dict[str, Any]:
        return {
            "state": state,
            "previous_state": prev,
            "changed": state != (prev or NO_SETUP),
            "reason": reason,
            "confirm_ratio": round(ratio, 4),
            "bad_signal_count": bad_signal_count,
        }

    if not data_ok:
        return out(NO_SETUP, "Dữ liệu không đủ/không hợp lệ ⇒ không xác lập setup")

    # 1) Thoát: đủ tín hiệu xấu hoặc điều kiện thoát của chiến thuật
    if exit_condition:
        if prev in _ACTIVE_LIKE or prev == EXIT:
            return out(EXIT, f"Điều kiện thoát đã thoả (bad signals = {bad_signal_count})")
        return out(INVALIDATED, "Setup chưa từng được xác nhận và đã xuất hiện tín hiệu thoát")

    # 2) Đang hiệu lực mà suy yếu dần
    if prev == ACTIVE:
        if bad_signal_count >= int(sm_cfg.get("weakening_bad_signals", 2)):
            return out(WEAKENING, f"Tín hiệu xấu tích luỹ = {bad_signal_count}")
        return out(ACTIVE, "Setup vẫn còn hiệu lực")

    if prev == WEAKENING:
        if bad_signal_count == 0 and ratio >= 1.0:
            return out(ACTIVE, "Tín hiệu xấu đã hết, setup phục hồi")
        return out(WEAKENING, f"Tiếp tục suy yếu (bad signals = {bad_signal_count})")

    if prev == CONFIRMED:
        if bad_signal_count >= int(sm_cfg.get("weakening_bad_signals", 2)):
            return out(WEAKENING, f"Setup mới xác nhận nhưng đã có {bad_signal_count} tín hiệu xấu")
        return out(ACTIVE, "Setup đã xác nhận và đang hiệu lực")

    # 3) Chưa/không còn hiệu lực → đánh giá lại từ đầu
    if required_passed:
        return out(CONFIRMED, "Toàn bộ điều kiện bắt buộc đã thoả")

    if prev in (CONFIRMED, ACTIVE, WEAKENING, EXIT):
        return out(INVALIDATED, "Điều kiện bắt buộc không còn thoả")

    if ratio >= float(sm_cfg.get("forming_confirm_ratio", 0.7)):
        return out(SETUP_FORMING, f"{confirmations}/{total_conditions} điều kiện đã hội tụ")
    if ratio >= float(sm_cfg.get("watch_confirm_ratio", 0.4)):
        return out(WATCH, f"{confirmations}/{total_conditions} điều kiện đã hội tụ")
    return out(NO_SETUP, f"{confirmations}/{total_conditions} điều kiện — chưa đủ để theo dõi")


def is_actionable(state: str) -> bool:
    """Trạng thái có phát signal giao dịch cho Admin hay không."""
    return state in (CONFIRMED, ACTIVE)
