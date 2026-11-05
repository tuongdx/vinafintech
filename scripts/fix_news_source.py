# -*- coding: utf-8 -*-
"""Sửa nhãn NGUỒN cho bài viết thuộc 3 chuyên mục của An ninh Tiền tệ.

Bài Chứng khoán / Ngân hàng / Doanh nghiệp (crawl từ antt.nguoiduatin.vn) trước đây
bị gán nhầm source='MarketTimes' khi import → đổi thành 'An ninh tiền tệ'.

Chạy trên DB local (SQLite — đọc DATABASE_URL trong .env):
    py scripts/fix_news_source.py

Chạy trên DB production (Render Postgres) — truyền URL đích:
    py scripts/fix_news_source.py "postgresql://USER:PASS@host:5432/VinaFintech?sslmode=require"
    (hoặc set env DATABASE_URL rồi chạy không tham số)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import news_source


def main() -> None:
    url = (sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("DATABASE_URL", "")
    if not url:
        from app.core.config import settings

        url = settings.database_url

    host = url.split("@")[-1].split("/")[0] if "@" in url else "local"
    print(f"→ Database: {host}")

    engine = create_engine(url)
    db = sessionmaker(bind=engine)()
    try:
        updated = news_source.apply_legacy_fix(db)
        print(
            f"✅ Đã cập nhật {updated} bài (chuyên mục Chứng khoán/Ngân hàng/Doanh nghiệp) "
            f"→ nguồn '{news_source.SOURCE_ANTT}'."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
