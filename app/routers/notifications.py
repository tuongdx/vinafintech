"""Thông báo: Cổ phiếu / Tin tức / Hệ thống — có lịch sử + trạng thái đã đọc.

- Cổ phiếu: báo cáo mới về cổ phiếu yêu thích, tăng trần / giảm sàn, biến động bất thường.
- Tin tức: cổ phiếu yêu thích có tin mới.
- Hệ thống: thay đổi / tính năng mới của TuniX gửi đến toàn bộ thành viên.

Thông báo được sinh idempotent khi user mở danh sách (dedupe bằng (user, type, ref_key)),
lưu lại làm lịch sử; is_read đánh dấu đã đọc.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_roles
from app.db import get_db
from app.models import (
    News,
    NewsStatusEnum,
    Notification,
    NotificationType,
    RoleEnum,
    SystemAnnouncement,
    User,
    Watchlist,
)
from app.routers.watchlist import _news_matches_symbol
from app.schemas import (
    NotificationListOut,
    NotificationOut,
    NotificationReadAllCreate,
    SystemAnnouncementCreate,
    SystemAnnouncementOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _add_notification(
    db: Session,
    user: User,
    ntype: NotificationType,
    title: str,
    body: str = "",
    symbol: str | None = None,
    link: str | None = None,
    ref_key: str = "",
) -> None:
    exists = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.type == ntype,
            Notification.ref_key == ref_key,
        )
        .first()
    )
    if exists:
        return
    db.add(
        Notification(
            user_id=user.id,
            type=ntype,
            title=title,
            body=body,
            symbol=symbol,
            link=link,
            ref_key=ref_key,
        )
    )
    db.commit()


def _price_limit_notification(symbol: str) -> tuple[str, str] | None:
    """Trả về (title, body) nếu cổ phiếu đang tăng trần / giảm sàn, ngược lại None.

    Chỉ dùng quote đã cache để tránh làm chậm khi mở danh sách thông báo.
    """
    from app.services.vnstock_service import _compute_ceiling_floor, cache, get_quote

    sym = symbol.upper().strip()
    if not sym or cache.get(f"quote:{sym}") is None:
        return None
    try:
        quote = get_quote(sym)
        price = float(quote.get("price") or 0)
        reference = float(quote.get("reference_price") or 0)
        exchange = quote.get("exchange") or ""
        if price <= 0 or reference <= 0:
            return None
        ceiling, floor = _compute_ceiling_floor(exchange, reference)
        if price >= ceiling - 0.01:
            return (f"{sym} tăng trần", f"{sym} đang giao dịch ở mức giá trần ({price:,.0f} đ).")
        if price <= floor + 0.01:
            return (f"{sym} giảm sàn", f"{sym} đang giao dịch ở mức giá sàn ({price:,.0f} đ).")
    except Exception:  # noqa: BLE001
        return None
    return None


def _prune_stale_notifications(db: Session, user: User) -> None:
    """Xoá các thông báo trỏ tới nội dung đã bị xoá (tin/báo cáo/thông báo hệ thống).

    Tránh việc bấm thông báo dẫn tới trang tĩnh / bài mẫu khi bài viết gốc không còn.
    """
    rows = db.query(Notification).filter(Notification.user_id == user.id).all()
    stale_ids: list[int] = []
    for n in rows:
        ref = n.ref_key or ""
        if ref.startswith("news:") or ref.startswith("report:"):
            try:
                news_id = int(ref.split(":")[-1])
            except ValueError:
                continue
            if not db.query(News.id).filter(News.id == news_id).first():
                stale_ids.append(n.id)
        elif ref.startswith("sys:"):
            try:
                ann_id = int(ref.split(":")[-1])
            except ValueError:
                continue
            if not db.query(SystemAnnouncement.id).filter(SystemAnnouncement.id == ann_id).first():
                stale_ids.append(n.id)
    if stale_ids:
        db.query(Notification).filter(Notification.id.in_(stale_ids)).delete(
            synchronize_session=False
        )
        db.commit()


def _sync_notifications(db: Session, user: User) -> None:
    """Sinh các thông báo còn thiếu cho user (idempotent, giữ lịch sử)."""
    favs = db.query(Watchlist).filter(Watchlist.user_id == user.id).all()
    notify_reports = user.notify_reports if user.notify_reports is not None else True
    notify_news = user.notify_news if user.notify_news is not None else True
    today = date.today().isoformat()

    for fav in favs:
        sym = fav.symbol.upper().strip()
        if not sym:
            continue
        rows = (
            db.query(News)
            .filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
            .filter(News.created_at >= fav.created_at)
            .order_by(News.created_at.desc())
            .all()
        )
        for n in rows:
            if not _news_matches_symbol(n, sym):
                continue
            is_report = (n.category or "tin-tuc") == "bao-cao"
            if is_report:
                if not notify_reports:
                    continue
                _add_notification(
                    db, user, NotificationType.stock,
                    title=f"Có báo cáo mới về {sym}",
                    body=n.title,
                    symbol=sym,
                    link=f"/news/{n.id}",
                    ref_key=f"report:{n.id}",
                )
            else:
                if not notify_news:
                    continue
                _add_notification(
                    db, user, NotificationType.news,
                    title=f"Tin mới về {sym}",
                    body=n.title,
                    symbol=sym,
                    link=f"/news/{n.id}",
                    ref_key=f"news:{n.id}",
                )

        # Tăng trần / giảm sàn
        limit = _price_limit_notification(sym)
        if limit:
            title, body = limit
            is_ceiling = title.endswith("tăng trần")
            ref_key = f"ceiling:{sym}:{today}" if is_ceiling else f"floor:{sym}:{today}"
            _add_notification(
                db, user, NotificationType.stock,
                title=title, body=body, symbol=sym,
                link=f"/cophieu-{sym}", ref_key=ref_key,
            )

    # Thông báo hệ thống → toàn bộ thành viên (sinh khi user mở danh sách)
    announcements = db.query(SystemAnnouncement).order_by(SystemAnnouncement.created_at.desc()).all()
    for ann in announcements:
        _add_notification(
            db, user, NotificationType.system,
            title=ann.title, body=ann.body, link=ann.link,
            ref_key=f"sys:{ann.id}",
        )


@router.get("", response_model=NotificationListOut)
def list_notifications(
    type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _prune_stale_notifications(db, user)
    _sync_notifications(db, user)
    query = db.query(Notification).filter(Notification.user_id == user.id)
    if type in ("stock", "news", "system"):
        query = query.filter(Notification.type == type)
    items = query.order_by(Notification.created_at.desc()).limit(100).all()
    unread = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read.is_(False))
        .count()
    )
    return NotificationListOut(items=[NotificationOut.model_validate(i) for i in items], unread=unread)


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    n = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user.id)
        .first()
    )
    if not n:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông báo.")
    if not n.is_read:
        n.is_read = True
        db.commit()
    return {"message": "Đã đánh dấu đã đọc", "id": notification_id}


@router.post("/read-all")
def mark_all_notifications_read(
    payload: NotificationReadAllCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read.is_(False)
    )
    if payload.type in ("stock", "news", "system"):
        query = query.filter(Notification.type == payload.type)
    count = query.update({Notification.is_read: True}, synchronize_session=False)
    db.commit()
    return {"message": "Đã đọc tất cả", "count": count}


@router.get("/system-announcements", response_model=list[SystemAnnouncementOut])
def list_system_announcements(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    return (
        db.query(SystemAnnouncement)
        .order_by(SystemAnnouncement.created_at.desc())
        .all()
    )


@router.post("/system-announcements", response_model=SystemAnnouncementOut)
def create_system_announcement(
    payload: SystemAnnouncementCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    ann = SystemAnnouncement(title=payload.title, body=payload.body, link=payload.link)
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return ann
