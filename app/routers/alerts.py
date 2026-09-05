"""Trung tâm cảnh báo (Alert Center).

- GET   /api/alerts            → danh sách cảnh báo của user (lọc theo category)
- GET   /api/alerts/unread-count → số cảnh báo chưa đọc
- POST  /api/alerts/{id}/read  → đánh dấu đã đọc 1 cảnh báo
- POST  /api/alerts/read-all   → đánh dấu đã đọc tất cả
- POST  /api/alerts/scan       → chạy quét tín hiệu ngay cho holdings + watchlist
Yêu cầu đăng nhập (JWT). Dữ liệu cảnh báo từ AlertDetectionService (dữ liệu thật).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import Alert, AlertCategory, User
from app.services import access, feature_flags
from app.services.alert_service import alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _require_alert_access(db: Session, user: User) -> None:
    """Quét tín hiệu (scan) theo block 'alert_center'.

    - Block MỞ (enabled=True): mọi user đã đăng nhập đều quét được.
    - Block KHOÁ (enabled=False): chỉ hội viên đã mở gói được quét.
    """
    if feature_flags.is_open(db, "alert_center"):
        return
    if not access.is_active_member(user):
        raise HTTPException(
            status_code=403,
            detail="Trung tâm cảnh báo thuộc gói hội viên. Vui lòng mở gói để sử dụng.",
        )


def _alert_out(a: Alert) -> dict:
    meta: dict = {}
    if a.alert_metadata:
        try:
            meta = json.loads(a.alert_metadata)
        except (TypeError, ValueError):
            meta = {}
    return {
        "id": a.id,
        "symbol": a.symbol,
        "alert_type": a.alert_type.value,
        "category": a.category.value,
        "importance": a.importance.value,
        "title": a.title,
        "description": a.description,
        "metadata": meta,
        "is_read": a.is_read,
        "created_at": a.created_at,
    }


@router.get("")
def list_alerts(
    category: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Danh sách cảnh báo (mới nhất trước). Lọc tuỳ chọn theo category.
    Mọi user đã đăng nhập đều lấy được (frontend tự mờ + khóa cho user free)."""
    q = db.query(Alert).filter(Alert.user_id == user.id)
    if category:
        try:
            q = q.filter(Alert.category == AlertCategory(category))
        except ValueError:
            raise HTTPException(status_code=400, detail="category không hợp lệ")
    rows = q.order_by(Alert.created_at.desc()).limit(max(1, min(limit, 200))).all()
    return [_alert_out(a) for a in rows]


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.query(Alert.id).filter(Alert.user_id == user.id, Alert.is_read.is_(False)).count()
    return {"count": n}


@router.post("/{alert_id}/read")
def mark_read(alert_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.query(Alert).filter(Alert.id == alert_id, Alert.user_id == user.id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Không tìm thấy cảnh báo")
    a.is_read = True
    db.commit()
    return {"ok": True, "id": alert_id}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(Alert).filter(Alert.user_id == user.id, Alert.is_read.is_(False)).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.post("/scan")
def scan(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Chạy quét tín hiệu cho toàn bộ cổ phiếu đang nắm giữ + theo dõi của user."""
    _require_alert_access(db, user)
    return alert_service.scan_user(db, user)
