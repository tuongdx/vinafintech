import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import News, NewsStatusEnum, RoleEnum, User
from app.schemas import NewsCreate, NewsOut, NewsUpdate

router = APIRouter(prefix="/news", tags=["news"])


def _slugify(text: str) -> str:
    """Tạo slug từ tiêu đề: bỏ dấu tiếng Việt, hạ thường, ký tự đặc biệt → dấu -."""
    text = (text or "").replace("đ", "d").replace("Đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")

_ADMIN_NEWS_ROLES = (
    RoleEnum.editor,
    RoleEnum.content_manager,
    RoleEnum.super_admin,
)


def _news_out(n: News) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "summary": n.summary,
        "content": n.content,
        "category": n.category or "tin-tuc",
        "image_url": n.image_url,
        "image_caption": n.image_caption,
        "tags": n.tags,
        "source": n.source,
        "source_url": n.source_url,
        "views": n.views or 0,
        "slug": _slugify(n.title),
        "status": n.status,
        "author_name": (n.author_name or (n.author.full_name if n.author else None)),
        "created_at": n.created_at,
    }


# ---------------------------------------------------------------------------
# Public: chỉ trả bài ĐÃ XUẤT BẢN (trang người dùng)
# ---------------------------------------------------------------------------
@router.get("", response_model=list[NewsOut])
def list_published_news(
    limit: int = 20,
    category: str | None = None,
    exclude_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(News).filter(
        News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published])
    )
    if category:
        query = query.filter(News.category == category)
    if exclude_id:
        query = query.filter(News.id != exclude_id)
    rows = query.order_by(News.id.desc()).limit(max(1, min(limit, 50))).all()
    return [_news_out(n) for n in rows]


# ---------------------------------------------------------------------------
# Quản trị: tất cả bài (quản lý tin tức)
# ---------------------------------------------------------------------------
@router.get("/all", response_model=list[NewsOut])
def list_all_news(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_ADMIN_NEWS_ROLES)),
):
    rows = db.query(News).order_by(News.id.desc()).all()
    return [_news_out(n) for n in rows]


@router.get("/pending", response_model=list[NewsOut])
def pending_news(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.content_manager, RoleEnum.super_admin)),
):
    rows = db.query(News).filter(News.status == NewsStatusEnum.pending_approval).order_by(News.id.desc()).all()
    return [_news_out(n) for n in rows]


# ---------------------------------------------------------------------------
# Public: 6 tin có lượt xem nhiều nhất (trang người dùng)
# ---------------------------------------------------------------------------
@router.get("/popular", response_model=list[NewsOut])
def popular_news(limit: int = 6, db: Session = Depends(get_db)):
    rows = (
        db.query(News)
        .filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
        .order_by(News.views.desc(), News.id.desc())
        .limit(max(1, min(limit, 50)))
        .all()
    )
    return [_news_out(n) for n in rows]


# ---------------------------------------------------------------------------
# Public: tin liên quan theo mã cổ phiếu (lọc trong tags) — dùng ở trang chi tiết cổ phiếu
# ---------------------------------------------------------------------------
@router.get("/related", response_model=list[NewsOut])
def related_news(symbol: str = "", limit: int = 8, db: Session = Depends(get_db)):
    """Tin liên quan cổ phiếu: chỉ trả các bài thực sự có mã trong tags/tiêu đề.
    Có bao nhiêu trả bấy nhiêu (tối đa `limit`, mặc định 8), KHÔNG lấp bằng tin
    không liên quan. Không có bài nào → trả về danh sách rỗng."""
    limit = max(1, min(limit, 50))
    sym = (symbol or "").strip().upper()

    if not sym:
        return []

    matched = (
        db.query(News)
        .filter(
            News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]),
            (News.tags.ilike(f"%{sym}%")) | (News.title.ilike(f"%{sym}%")),
        )
        .order_by(News.id.desc())
        .limit(limit)
        .all()
    )
    return [_news_out(n) for n in matched]


# ---------------------------------------------------------------------------
# Public: chi tiết 1 bài ĐÃ XUẤT BẢN (trang chi tiết tin)
# ---------------------------------------------------------------------------
@router.get("/{news_id}", response_model=NewsOut)
def get_news(news_id: int, db: Session = Depends(get_db)):
    news = db.query(News).filter(News.id == news_id).first()
    if not news or news.status not in (NewsStatusEnum.approved, NewsStatusEnum.published):
        raise HTTPException(status_code=404, detail="News not found")
    news.views = (news.views or 0) + 1
    db.commit()
    db.refresh(news)
    return _news_out(news)


# ---------------------------------------------------------------------------
# Tạo bài (biên tập viên / quản trị) — luôn ở trạng thái CHỜ DUYỆT
# ---------------------------------------------------------------------------
@router.post("", response_model=NewsOut)
def create_news(
    payload: NewsCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ADMIN_NEWS_ROLES)),
):
    news = News(
        title=payload.title,
        summary=payload.summary,
        content=payload.content,
        category=payload.category or "tin-tuc",
        image_url=payload.image_url,
        author_name=payload.author_name,
        image_caption=payload.image_caption,
        tags=payload.tags,
        source=payload.source,
        source_url=payload.source_url,
        author_id=user.id,
        status=NewsStatusEnum.pending_approval,
    )
    db.add(news)
    db.commit()
    db.refresh(news)
    return _news_out(news)


@router.put("/{news_id}", response_model=NewsOut)
def update_news(
    news_id: int,
    payload: NewsUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_ADMIN_NEWS_ROLES)),
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    if payload.title is not None:
        news.title = payload.title
    if payload.summary is not None:
        news.summary = payload.summary
    if payload.content is not None:
        news.content = payload.content
    if payload.category is not None:
        news.category = payload.category
    if payload.image_url is not None:
        news.image_url = payload.image_url
    if payload.author_name is not None:
        news.author_name = payload.author_name
    if payload.image_caption is not None:
        news.image_caption = payload.image_caption
    if payload.tags is not None:
        news.tags = payload.tags
    if payload.source is not None:
        news.source = payload.source
    if payload.source_url is not None:
        news.source_url = payload.source_url
    # Bài chỉnh sửa → quay lại luồng duyệt (Chờ duyệt → Duyệt → Xuất bản)
    news.status = NewsStatusEnum.pending_approval
    db.commit()
    db.refresh(news)
    return _news_out(news)


@router.delete("/{news_id}")
def delete_news(
    news_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    db.delete(news)
    db.commit()
    return {"message": "Deleted"}


@router.post("/{news_id}/approve", response_model=NewsOut)
def approve_news(
    news_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.content_manager, RoleEnum.super_admin)),
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    news.status = NewsStatusEnum.approved
    db.commit()
    db.refresh(news)
    return _news_out(news)


@router.post("/{news_id}/publish", response_model=NewsOut)
def publish_news(
    news_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.content_manager, RoleEnum.super_admin)),
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    news.status = NewsStatusEnum.published
    db.commit()
    db.refresh(news)
    return _news_out(news)


@router.post("/{news_id}/reject", response_model=NewsOut)
def reject_news(
    news_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.content_manager, RoleEnum.super_admin)),
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    news.status = NewsStatusEnum.rejected
    db.commit()
    db.refresh(news)
    return _news_out(news)
