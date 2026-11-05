"""Cờ tính năng block website: MỞ cho tất cả / KHOÁ hội viên.

Super Admin bật/tắt từng block ở trang quản trị (panel "Các tính năng đang
khoá hội viên"). Trạng thái lưu trong bảng `features` (key, enabled):

- enabled = True  -> MỞ cho tất cả (kể cả người chưa mở gói hội viên).
- enabled = False -> KHOÁ hội viên (chỉ người đã mở gói hội viên dùng được).

Mặc định các block được tạo với enabled=True — phù hợp giai đoạn hiện tại
chưa thu phí; khi nào bắt đầu thu phí, Super Admin chỉ cần tắt từng block.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Feature, PlanTierEnum
from app.services import access

# (key, name, description) — đồng bộ với các block trên website.
BLOCK_FEATURES: list[dict[str, str]] = [
    {
        "key": "notable_stocks",
        "name": "Cổ phiếu nổi bật",
        "description": 'Khối "Cổ phiếu nổi bật" theo nhóm (Tăng trưởng, Định giá, Dòng tiền, Breakout, Score) — Trang Cổ phiếu',
    },
    {
        "key": "strategy_cashflow",
        "name": "Chiến lược: Dòng tiền mạnh",
        "description": 'Nhóm chiến lược "Dòng tiền mạnh" — Trang Khám phá',
    },
    {
        "key": "strategy_breakout",
        "name": "Chiến lược: Breakout",
        "description": 'Nhóm chiến lược "Breakout" — Trang Khám phá',
    },
    {
        "key": "rank_cashflow",
        "name": "Xếp hạng: Top dòng tiền",
        "description": 'Bảng "Top dòng tiền" — Trang Khám phá',
    },
    {
        "key": "rank_score",
        "name": "Xếp hạng: Top Score",
        "description": 'Bảng "Top Score" — Trang Khám phá',
    },
    {
        "key": "alert_center",
        "name": "Trung tâm cảnh báo",
        "description": "Cảnh báo gần hỗ trợ/kháng cự, breakout, MA… (kể cả quét tín hiệu)",
    },
    {
        "key": "ai_unlimited",
        "name": "VinaF AI không giới hạn",
        "description": "Hỏi đáp không giới hạn lượt/cuộc trò chuyện cho hội viên",
    },
]

KEYS: set[str] = {b["key"] for b in BLOCK_FEATURES}


def ensure_block_features(db: Session) -> int:
    """Tự tạo các dòng block nếu chưa tồn tại (KHÔNG ghi đè lựa chọn hiện tại).

    Mặc định enabled=True (MỞ cho tất cả) vì giai đoạn hiện tại chưa thu phí.
    """
    created = 0
    for b in BLOCK_FEATURES:
        row = db.query(Feature).filter(Feature.key == b["key"]).first()
        if row is None:
            db.add(
                Feature(
                    key=b["key"],
                    name=b["name"],
                    description=b["description"],
                    access_level=PlanTierEnum.free,
                    enabled=True,
                )
            )
            created += 1
    if created:
        db.commit()
    return created


def is_open(db: Session, key: str) -> bool:
    """True = block đang MỞ cho tất cả; False = block đang KHOÁ hội viên.

    Chưa có dòng khai báo -> coi như MỞ (mặc định an toàn, không khóa nhầm).
    """
    row = db.query(Feature).filter(Feature.key == key).first()
    return bool(row.enabled) if row is not None else True


def can_use(db: Session, key: str, user) -> bool:
    """User hiện tại có dùng được block `key` không?"""
    if is_open(db, key):
        return True
    return access.is_active_member(user)


def block_rows(db: Session) -> list[dict]:
    """Danh sách block website theo thứ tự cố định, kèm trạng thái enabled."""
    rows = db.query(Feature).filter(Feature.key.in_(KEYS)).all()
    by_key = {r.key: r for r in rows}
    out: list[dict] = []
    for b in BLOCK_FEATURES:
        r = by_key.get(b["key"])
        out.append(
            {
                "id": r.id if r is not None else None,
                "key": b["key"],
                "name": b["name"],
                "description": b["description"],
                "enabled": bool(r.enabled) if r is not None else True,
            }
        )
    return out
