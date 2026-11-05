# -*- coding: utf-8 -*-
"""Xác định NGUỒN (source) của bài viết theo chuyên mục.

Trang tin VinaFintech gộp bài crawl từ 2 nguồn báo:
  - An ninh Tiền tệ (antt.nguoiduatin.vn): Chứng khoán, Ngân hàng, Doanh nghiệp.
  - MarketTimes (markettimes.vn): Bất động sản, Kinh doanh, Quốc tế, Công nghệ.

Vì vậy `source` KHÔNG được hard-code cho mọi bài mà suy ra theo `category`
(xem crawler: scripts/crawl_markettimes_gui.py trong dự án Crawl-tintuc).
"""
from sqlalchemy.orm import Session

from app.models import News

# Nhãn hiển thị trên trang chi tiết bài viết ("Nguồn: <nhãn>").
SOURCE_ANTT = "An ninh tiền tệ"
SOURCE_MARKETTIMES = "MarketTimes"

# Chuyên mục lấy từ An ninh Tiền tệ.
ANTT_CATEGORIES = ("chung-khoan", "ngan-hang", "doanh-nghiep")


def source_for_category(category: str | None) -> str:
    """Suy ra nguồn bài viết theo chuyên mục (mặc định MarketTimes)."""
    cat = (category or "").strip().lower()
    return SOURCE_ANTT if cat in ANTT_CATEGORIES else SOURCE_MARKETTIMES


def apply_legacy_fix(db: Session) -> int:
    """Sửa nhãn nguồn cho bài ANTT từng bị gán nhầm 'MarketTimes' (idempotent).

    Chạy ở startup: sau lần đầu không còn dòng nào khớp nên gần như không tốn chi phí.
    Trả về số bài đã cập nhật.
    """
    updated = (
        db.query(News)
        .filter(News.source == SOURCE_MARKETTIMES, News.category.in_(ANTT_CATEGORIES))
        .update({News.source: SOURCE_ANTT}, synchronize_session=False)
    )
    if updated:
        db.commit()
    return int(updated or 0)
