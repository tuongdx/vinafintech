# -*- coding: utf-8 -*-
"""Bổ sung ``News.published_at`` cho các bài CŨ còn thiếu (NULL).

Trước đây bảng news chưa có cột ``published_at`` nên mọi bài cũ đều NULL. API vẫn trả
tạm ngày giờ suy ra từ ``created_at`` (+7 giờ, giờ VN), nhưng để thứ tự bài viết đúng theo
ngày giờ đăng thì nên ghi giá trị đó vào DB một lần.

Giá trị gán: ``created_at`` (đang lưu UTC) + 7 giờ = thời điểm tạo bài theo giờ VN.

Chạy (mặc định lấy ``database_url`` trong .env / SQLite local):
    py scripts/backfill_news_published_at.py

Chạy cho DB khác (VD Postgres trên Render): đặt biến môi trường DATABASE_URL trỏ tới DB đích
rồi chạy lệnh trên.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.db_schema import ensure_schema
from app.models import News
from app.services.news_time import VN_DELTA, now_vn


def backfill(db: Session) -> int:
    """Gán published_at cho mọi bài còn thiếu. Trả về số bài đã cập nhật."""
    rows = db.query(News).filter(News.published_at.is_(None)).all()
    for n in rows:
        n.published_at = (n.created_at + VN_DELTA) if n.created_at else now_vn()
    if rows:
        db.commit()
    return len(rows)


def main() -> None:
    ensure_schema()  # tạo cột published_at nếu DB chưa có
    db: Session = SessionLocal()
    try:
        total = db.query(News).count()
        changed = backfill(db)
        missing = db.query(News).filter(News.published_at.is_(None)).count()
        print(f"Tong bai: {total} | Da bo sung: {changed} | Con thieu: {missing}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
