# -*- coding: utf-8 -*-
"""Đẩy 20 tin MarketTimes từ SQLite local lên Render PostgreSQL (production).

Chỉ CHÈN THÊM các bài `source == 'MarketTimes'` — bỏ qua bài đã tồn tại theo
`source_url` để không trùng / không đụng dữ liệu production hiện có.

Cách dùng (chạy 1 lần, giữ kín credential):
    $env:TARGET_DATABASE_URL="postgresql://USER:PASS@host:5432/tunix?sslmode=require"
    .venv\Scripts\python.exe scripts/push_news_to_render.py
    (hoặc truyền URL làm đối số đầu tiên)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.db import Base

SOURCE_SQLITE = os.environ.get("SOURCE_SQLITE", "tunix.db")
TARGET_URL = os.environ.get("TARGET_DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")


def main() -> None:
    if not TARGET_URL or TARGET_URL.startswith("sqlite"):
        print("❌ Thiếu TARGET_DATABASE_URL (External Database URL của Render).")
        sys.exit(1)

    try:
        parsed = make_url(TARGET_URL)
        host = parsed.host or ""
    except Exception as e:
        print(f"❌ URL không hợp lệ: {e}")
        sys.exit(1)
    print(f"→ Host database đích: {host}")
    if ".render.com" not in host:
        print("❌ Hostname phải kết thúc bằng '...-postgres.render.com' (copy ĐẦY ĐỦ External DB URL).")
        sys.exit(1)

    src = create_engine(f"sqlite:///{SOURCE_SQLITE}")
    tgt = create_engine(TARGET_URL)

    Src = sessionmaker(bind=src)()
    Tgt = sessionmaker(bind=tgt)()
    try:
        # Đảm bảo bảng tồn tại (không làm mất dữ liệu)
        Base.metadata.create_all(bind=tgt)

        # Tránh trùng theo source_url đã có trên production
        existing = {
            row.source_url
            for row in Tgt.query(m.News.source_url).filter(m.News.source_url.isnot(None)).all()
        }
        print(f"→ Production đang có {Tgt.query(m.News).count()} tin.")

        rows = Src.query(m.News).filter(m.News.source == "MarketTimes").order_by(m.News.id).all()
        print(f"→ Tìm thấy {len(rows)} bài MarketTimes trong DB local.")

        added = skipped = 0
        for r in rows:
            if r.source_url in existing:
                print(f"  ⏭️  Bỏ qua (đã có): {r.title[:60]}")
                skipped += 1
                continue
            Tgt.add(m.News(
                title=r.title,
                summary=r.summary,
                content=r.content,
                category=r.category,
                image_url=r.image_url,
                author_name=r.author_name,
                image_caption=r.image_caption,
                tags=r.tags,
                source=r.source,
                source_url=r.source_url,
                views=r.views,
                author_id=None,
                status=r.status,
                created_at=r.created_at,
            ))
            existing.add(r.source_url)
            added += 1
            print(f"  ✅ Đã thêm: {r.title[:60]}")

        Tgt.commit()

        # Đồng bộ sequence (an toàn cho Postgres)
        try:
            Tgt.execute(text(
                "SELECT setval(pg_get_serial_sequence('news','id'), "
                "COALESCE((SELECT MAX(id) FROM news), 1))"
            ))
            Tgt.commit()
        except Exception:
            pass

        print(f"\n🎉 Hoàn tất: đã thêm {added} bài, bỏ qua {skipped} bài trùng.")
        print("   Kiểm tra: https://tunix.onrender.com/tintuc (hoặc /api/news).")
    finally:
        Src.close()
        Tgt.close()


if __name__ == "__main__":
    main()
