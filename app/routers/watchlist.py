from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import News, NewsStatusEnum, NotificationRead, User, Watchlist
from app.routers.news import _news_out
from app.schemas import (
    NotificationItem,
    NotificationReadCreate,
    NotificationsOut,
    NewsOut,
    WatchlistCreate,
    WatchlistOut,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _news_matches_symbol(news: News, symbol: str) -> bool:
    """Tin/báo cáo có gắn cổ phiếu symbol (theo tags hoặc tiêu đề)."""
    sym = symbol.strip().upper()
    if not sym:
        return False
    if news.tags:
        for t in news.tags.split(","):
            tag = t.strip().upper().lstrip("#").replace(".", "").replace("-", "")
            if tag and tag == sym:
                return True
    if news.title and sym in news.title.upper():
        return True
    return False


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(Watchlist)
        .filter(Watchlist.user_id == user.id)
        .order_by(Watchlist.created_at.desc())
        .all()
    )
    return rows


@router.post("", response_model=WatchlistOut)
def add_watchlist(payload: WatchlistCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    symbol = payload.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Mã cổ phiếu không hợp lệ.")
    row = db.query(Watchlist).filter(Watchlist.user_id == user.id, Watchlist.symbol == symbol).first()
    if row:
        return row
    row = Watchlist(user_id=user.id, symbol=symbol)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{symbol}")
def remove_watchlist(symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sym = symbol.strip().upper()
    row = db.query(Watchlist).filter(Watchlist.user_id == user.id, Watchlist.symbol == sym).first()
    if not row:
        raise HTTPException(status_code=404, detail="Không có mã này trong danh sách theo dõi.")
    db.delete(row)
    db.commit()
    return {"message": "Đã bỏ theo dõi", "symbol": sym}


@router.get("/notifications", response_model=NotificationsOut)
def notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Tin/báo cáo liên quan đến cổ phiếu đang theo dõi, từ lúc theo dõi về sau."""
    favs = (
        db.query(Watchlist)
        .filter(Watchlist.user_id == user.id)
        .order_by(Watchlist.created_at.desc())
        .all()
    )
    read_ids = {
        r.news_id
        for r in db.query(NotificationRead).filter(NotificationRead.user_id == user.id).all()
    }
    notify_reports = user.notify_reports if user.notify_reports is not None else True
    notify_news = user.notify_news if user.notify_news is not None else True
    items: list[NotificationItem] = []
    for fav in favs:
        rows = (
            db.query(News)
            .filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
            .filter(News.created_at >= fav.created_at)
            .order_by(News.created_at.desc())
            .all()
        )
        for n in rows:
            if n.id in read_ids:
                continue
            # Lọc theo cài đặt thông báo: "bao-cao" = Báo cáo, còn lại = Tin tức
            is_report = (n.category or "tin-tuc") == "bao-cao"
            if is_report and not notify_reports:
                continue
            if not is_report and not notify_news:
                continue
            if _news_matches_symbol(n, fav.symbol):
                items.append(
                    NotificationItem(
                        symbol=fav.symbol,
                        favorited_at=fav.created_at,
                        news=NewsOut.model_validate(_news_out(n)),
                    )
                )
    items.sort(key=lambda it: it.news.created_at, reverse=True)
    return NotificationsOut(
        favorites=[WatchlistOut.model_validate(f) for f in favs],
        items=items,
    )


@router.post("/notifications/read")
def mark_notification_read(
    payload: NotificationReadCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Đánh dấu 1 tin/báo cáo là đã đọc → không đếm lại trong thông báo."""
    exists = (
        db.query(NotificationRead)
        .filter(
            NotificationRead.user_id == user.id,
            NotificationRead.news_id == payload.news_id,
        )
        .first()
    )
    if not exists:
        db.add(NotificationRead(user_id=user.id, news_id=payload.news_id))
        db.commit()
    return {"message": "Đã đánh dấu đã đọc", "news_id": payload.news_id}
