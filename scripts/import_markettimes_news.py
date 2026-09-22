# -*- coding: utf-8 -*-
"""Nhập tin tức Tài chính từ MarketTimes (markettimes_finance_articles.json) vào DB.

Mapping:
  title         -> News.title
  category      -> News.category   (giá trị từ file: "Tài chính")
  sapo          -> News.summary
  main_image    -> News.image_url
  image_caption -> News.image_caption
  tags          -> News.tags       (list -> chuỗi phân tách bằng dấu phẩy, bỏ tag rỗng)
  url           -> News.source_url
  content_html  -> News.content    (giữ text + ảnh, bỏ link nếu có, bỏ banner/quảng cáo)
  published_at  -> News.published_at (ngày giờ đăng, giữ đúng ngày giờ trong file;
                   file không có thì lấy thời điểm import - giờ VN)

Trạng thái ban đầu: pending_approval (chờ duyệt).
Author mặc định: "Ban biên tập TuniX".

Chạy:
    python scripts/import_markettimes_news.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup, Comment
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import News, NewsStatusEnum
from app.services import news_time

JSON_PATH = Path(r"C:\4.Deverlop\1.Python\Crawl-tintuc\markettimes_finance_articles.json")

# Class của các block quảng cáo / banner cần XÓA bỏ hoàn toàn
_AD_BLOCK_SELECTOR = (
    ".c-banner, .h-show-pc, .h-show-mobile, "
    ".c-banner-item, .c-banner-item__inner, .c-banner-item__box"
)

# Các thuộc tính data-* của wrapper "rác" (crawl từ trình soạn thảo AI) -> unwrap giữ nội dung
_JUNK_ATTRS = (
    "data-is-intersecting",
    "data-conversation-screenshot-content",
    "data-message-author-role",
    "data-testid",
    "data-turn-id",
    "data-turn-id-container",
    "data-message-model-slug",
    "data-turn-start-message",
)


def clean_content(html: str) -> str:
    """Làm sạch content_html: giữ text + ảnh, bỏ link & banner, gỡ wrapper rác."""
    soup = BeautifulSoup(html, "html.parser")

    # Bỏ comment
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # Xóa block quảng cáo / banner
    for el in soup.select(_AD_BLOCK_SELECTOR):
        el.decompose()

    # Unwrap wrapper AI rác (giữ text/ảnh bên trong)
    for el in soup.find_all(True):
        if any(el.has_attr(a) for a in _JUNK_ATTRS):
            el.unwrap()

    # Bỏ link (giữ text) — an toàn kể cả khi nguồn có <a>
    for a in soup.find_all("a"):
        a.unwrap()

    # Gỡ div bọc ngoài bài viết
    for w in soup.select("div.content-main-normal"):
        w.unwrap()

    # Gỡ mọi thuộc tính data-*
    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.startswith("data-")]:
            del el.attrs[attr]

    # Xóa các tag rỗng (không text, không ảnh)
    changed = True
    while changed:
        changed = False
        for el in soup.find_all(["p", "div", "span", "section"]):
            text = el.get_text(strip=True)
            has_media = el.find("img") or el.find("figure")
            if not text and not has_media and not el.find(
                ["p", "ul", "ol", "h1", "h2", "h3", "h4", "table"]
            ):
                el.decompose()
                changed = True

    return str(soup).strip()


def _join_tags(tags) -> str | None:
    """tags (list) -> chuỗi phân tách bằng phẩy, bỏ phần tử rỗng."""
    if not tags:
        return None
    items = [str(t).strip() for t in tags if str(t).strip()]
    return ", ".join(items) if items else None


def import_news(db: Session) -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON không phải danh sách bài viết")

    # Tránh nhập trùng theo source_url (đồng thời dùng để bổ sung ngày giờ đăng còn thiếu)
    existing_news = {
        row.source_url: row
        for row in db.query(News).filter(News.source_url.isnot(None)).all()
    }

    inserted = skipped = updated = 0
    for i, item in enumerate(data, start=1):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title:
            print(f"  [{i}] BỎ QUA: thiếu title")
            continue
        published_at = news_time.parse_published_at(item)
        if url in existing_news:
            row = existing_news[url]
            if published_at is not None and row.published_at != published_at:
                row.published_at = published_at
                updated += 1
                print(f"  [{i}] CẬP NHẬT NGÀY GIỜ: {published_at:%Y-%m-%d %H:%M} - {title[:50]}")
            else:
                print(f"  [{i}] BỎ QUA (đã tồn tại source_url): {title[:60]}")
            skipped += 1
            continue

        content = clean_content(item.get("content_html") or "")
        caption = (item.get("image_caption") or "").strip() or None

        news = News(
            title=title,
            summary=(item.get("sapo") or "").strip(),
            content=content,
            category=(item.get("category") or "Tài chính").strip(),
            image_url=(item.get("main_image") or "").strip() or None,
            image_caption=caption,
            tags=_join_tags(item.get("tags")),
            source="MarketTimes",
            source_url=url,
            author_name="Ban biên tập TuniX",
            status=NewsStatusEnum.pending_approval,
            published_at=published_at or news_time.now_vn(),
        )
        db.add(news)
        existing_news[url] = news
        inserted += 1
        print(f"  [{i}] ĐÃ THÊM: {title[:70]}")

    db.commit()
    print(f"\nTổng: đã thêm {inserted}, cập nhật ngày giờ {updated}, bỏ qua {skipped} bài (trùng).")


def main() -> None:
    db: Session = SessionLocal()
    try:
        import_news(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
