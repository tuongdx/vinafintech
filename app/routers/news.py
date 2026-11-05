import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import News, NewsStatusEnum, RoleEnum, User
from app.schemas import NewsCreate, NewsOut, NewsUpdate
from app.services import news_tags, news_time

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
        "tags": news_tags.with_default_news_tags(n.tags),
        "source": n.source,
        "source_url": n.source_url,
        "views": n.views or 0,
        "slug": _slugify(n.title),
        "status": n.status,
        "author_name": (n.author_name or (n.author.full_name if n.author else None)),
        "created_at": n.created_at,
        # Ngày giờ đăng bài (đúng định dạng file input: 2026-09-12T15:50:01+07:00).
        # Frontend ưu tiên hiển thị published_at thay cho created_at.
        **news_time.fields_for_row(n),
    }


# Sắp xếp bài mới nhất trước theo NGÀY GIỜ ĐĂNG (xem app/services/news_time.latest_first)
_latest_first = news_time.latest_first


# ---------------------------------------------------------------------------
# Public: chỉ trả bài ĐÃ XUẤT BẢN (trang người dùng)
# ---------------------------------------------------------------------------
def _strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt (đ→d, nguyên âm có dấu → không dấu) để tìm kiếm không phân biệt dấu."""
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


@router.get("", response_model=list[NewsOut])
def list_published_news(
    limit: int = 20,
    offset: int = 0,
    category: str | None = None,
    search: str | None = None,
    exclude_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Danh sách tin đã xuất bản. Hỗ trợ phân trang (limit + offset) để
    frontend tải thêm bài khi người dùng cuộn (infinite scroll)."""
    query = db.query(News).filter(
        News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published])
    )
    if category:
        # Hỗ trợ 1 chuyên mục ("ngan-hang") hoặc NHIỀU chuyên mục cách nhau dấu phẩy
        # ("chung-khoan,ngan-hang,bat-dong-san") — dùng cho block "Tin tức quan trọng" trang chủ.
        cats = [c.strip().lower() for c in str(category).split(",") if c.strip()]
        if len(cats) > 1:
            query = query.filter(News.category.in_(cats))
        elif cats:
            query = query.filter(News.category == cats[0])

    count = max(1, min(limit, 50))
    start = max(0, offset)

    if search and search.strip():
        # Tìm kiếm không phân biệt dấu: bỏ dấu cả 2 vế rồi so sánh chuỗi con.
        nkw = _strip_diacritics(search)
        nkw_nospace = nkw.replace(" ", "")
        rows = _latest_first(query).all()
        if exclude_id:
            rows = [n for n in rows if n.id != exclude_id]
        if nkw:
            matched = []
            for n in rows:
                hay = " ".join(
                    [
                        _strip_diacritics(n.title or ""),
                        _strip_diacritics(n.summary or ""),
                        _strip_diacritics(n.tags or ""),
                    ]
                )
                hay_nospace = hay.replace(" ", "")
                if nkw in hay or (nkw_nospace and nkw_nospace in hay_nospace):
                    matched.append(n)
            rows = matched
        return [_news_out(n) for n in rows[start : start + count]]

    if exclude_id:
        query = query.filter(News.id != exclude_id)
    rows = _latest_first(query).offset(start).limit(count).all()
    return [_news_out(n) for n in rows]


# ---------------------------------------------------------------------------
# Quản trị: tất cả bài (quản lý tin tức)
# ---------------------------------------------------------------------------
@router.get("/all", response_model=list[NewsOut])
def list_all_news(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_ADMIN_NEWS_ROLES)),
):
    rows = _latest_first(db.query(News)).all()
    return [_news_out(n) for n in rows]


@router.get("/pending", response_model=list[NewsOut])
def pending_news(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.content_manager, RoleEnum.super_admin)),
):
    rows = _latest_first(db.query(News).filter(News.status == NewsStatusEnum.pending_approval)).all()
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
# Public: tin liên quan theo mã cổ phiếu — dùng ở trang chi tiết cổ phiếu
# ---------------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html: str | None) -> str:
    """Bỏ thẻ HTML để tìm mã cổ phiếu trong phần nội dung bài viết."""
    return _HTML_TAG_RE.sub(" ", html or "")


def _symbol_re(sym: str) -> re.Pattern:
    """Mã cổ phiếu phải đứng riêng (FPT trong 'FPT' nhưng không khớp 'FPTS'/'VN30FPT')."""
    return re.compile(r"(?<![0-9A-Za-z])" + re.escape(sym) + r"(?![0-9A-Za-z])")


@router.get("/related", response_model=list[NewsOut])
def related_news(
    symbol: str = "",
    keyword: str = "",
    limit: int = 8,
    db: Session = Depends(get_db),
):
    """Tin liên quan cổ phiếu (trang chi tiết cổ phiếu).

    Mã cổ phiếu được tìm trong **tiêu đề, tags, tóm tắt và nội dung** bài viết
    (khớp theo TỪ, không khớp chuỗi con: "VND" không khớp "VNDIRECT").

    `keyword` là từ khoá phụ (VD tên thương hiệu: "Techcombank", "Vinhomes") do
    frontend lấy từ tên công ty — dùng để bắt các bài viết gọi mã bằng tên.

    Kết quả = **tối đa `limit` tin GẦN NHẤT** trong số các bài có liên quan
    (sắp theo NGÀY GIỜ ĐĂNG giảm dần — không sắp theo mức độ liên quan).
    Không có bài nào → trả danh sách rỗng (KHÔNG lấp bằng tin không liên quan).
    """
    limit = max(1, min(limit, 50))
    sym = re.sub(r"[^A-Z0-9]", "", (symbol or "").strip().upper())
    kw = (keyword or "").strip()

    if not sym:
        return []

    sym_like = f"%{sym}%"
    cond = (
        News.title.ilike(sym_like)
        | News.summary.ilike(sym_like)
        | News.tags.ilike(sym_like)
        | News.content.ilike(sym_like)
    )
    if len(kw) >= 3:
        kw_like = f"%{kw}%"
        cond = cond | News.title.ilike(kw_like) | News.summary.ilike(kw_like)

    # Quét tối đa 400 bài (mới nhất trước) rồi chấm điểm LIÊN QUAN trong Python.
    # `rows` đã sắp mới-nhất-trước ⇒ giữ nguyên thứ tự này = lấy `limit` tin GẦN NHẤT.
    rows = (
        _latest_first(
            db.query(News).filter(
                News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]),
                cond,
            )
        )
        .limit(400)
        .all()
    )

    sym_re = _symbol_re(sym)
    kw_low = kw.lower() if len(kw) >= 3 else ""
    related: list[News] = []
    for n in rows:
        title = n.title or ""
        summary = n.summary or ""
        tags = n.tags or ""
        hit = bool(sym_re.search(title) or sym_re.search(tags) or sym_re.search(summary)
                   or sym_re.search(_plain_text(n.content)))
        if not hit and kw_low:
            hit = kw_low in title.lower() or kw_low in summary.lower()
        if hit:
            related.append(n)

    # Chỉ giữ bài CÓ LIÊN QUAN và lấy `limit` tin GẦN NHẤT:
    # `related` giữ nguyên thứ tự của `rows` (mới nhất trước) nên không cần sort lại.
    return [_news_out(n) for n in related[:limit]]


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
        category=payload.category or "chung-khoan",
        image_url=payload.image_url,
        author_name=payload.author_name,
        image_caption=payload.image_caption,
        tags=news_tags.with_default_news_tags(payload.tags),
        source=payload.source,
        source_url=payload.source_url,
        author_id=user.id,
        status=NewsStatusEnum.pending_approval,
        # Bài viết tay: ngày giờ đăng = thời điểm tạo (giờ VN)
        published_at=news_time.now_vn(),
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
        news.tags = news_tags.with_default_news_tags(payload.tags)
    if payload.source is not None:
        news.source = payload.source
    if payload.source_url is not None:
        news.source_url = payload.source_url
    # Bài chỉnh sửa → quay lại luồng duyệt (Chờ duyệt → Duyệt → Xuất bản)
    news.status = NewsStatusEnum.pending_approval
    db.commit()
    db.refresh(news)
    return _news_out(news)


@router.delete("/all")
def delete_all_news(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    count = db.query(News).delete()
    db.commit()
    return {"message": f"Deleted {count} articles"}


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
