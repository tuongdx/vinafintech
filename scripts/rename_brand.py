"""One-off: đổi tên thương hiệu trong dữ liệu DB.

- TuniX  → VinaFintech
- TuniAI → VinaF AI

Chạy local (SQLite):
    python scripts/rename_brand.py --dry-run
    python scripts/rename_brand.py

Chạy trên production (PostgreSQL Render):
    $env:DATABASE_URL="postgresql://..."
    python scripts/rename_brand.py --dry-run
    python scripts/rename_brand.py

Tùy chọn: `--include-history` cập nhật cả lịch sử chat AI (ai_conversations).
Mặc định KHÔNG sửa lịch sử chat và audit log.

Lưu ý: chỉ thay đúng chữ hoa "TuniX"/"TuniAI"; không đụng tới domain
"tunix.vn" hay email "support@tunix.vn".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text

from app.core.config import settings
from app.db import engine

DRY_RUN = "--dry-run" in sys.argv
INCLUDE_HISTORY = "--include-history" in sys.argv

# Thứ tự: TuniAI trước để không ảnh hưởng lẫn nhau (2 chuỗi không chồng nhau,
# nhưng giữ thứ tự rõ ràng).
UPDATES = [
    (
        "faqs",
        "UPDATE faqs SET question = REPLACE(question, 'TuniX', 'VinaFintech'), "
        "answer = REPLACE(answer, 'TuniX', 'VinaFintech') "
        "WHERE question LIKE '%TuniX%' OR answer LIKE '%TuniX%'",
    ),
    (
        "faqs",
        "UPDATE faqs SET question = REPLACE(question, 'TuniAI', 'VinaF AI'), "
        "answer = REPLACE(answer, 'TuniAI', 'VinaF AI') "
        "WHERE question LIKE '%TuniAI%' OR answer LIKE '%TuniAI%'",
    ),
    (
        "news",
        "UPDATE news SET author_name = REPLACE(author_name, 'TuniX', 'VinaFintech') "
        "WHERE author_name LIKE '%TuniX%'",
    ),
    (
        "features",
        "UPDATE features SET name = REPLACE(name, 'TuniAI', 'VinaF AI'), "
        "description = REPLACE(description, 'TuniAI', 'VinaF AI') "
        "WHERE name LIKE '%TuniAI%' OR description LIKE '%TuniAI%'",
    ),
    (
        "settings",
        "UPDATE settings SET value = 'VinaFintech' "
        "WHERE key = 'site_name' AND value = 'TuniX'",
    ),
]

if INCLUDE_HISTORY:
    UPDATES.extend([
        (
            "ai_conversations",
            "UPDATE ai_conversations SET title = REPLACE(title, 'TuniX', 'VinaFintech'), "
            "messages = REPLACE(messages, 'TuniX', 'VinaFintech') "
            "WHERE title LIKE '%TuniX%' OR messages LIKE '%TuniX%'",
        ),
        (
            "ai_conversations",
            "UPDATE ai_conversations SET title = REPLACE(title, 'TuniAI', 'VinaF AI'), "
            "messages = REPLACE(messages, 'TuniAI', 'VinaF AI') "
            "WHERE title LIKE '%TuniAI%' OR messages LIKE '%TuniAI%'",
        ),
    ])

CHECK_TABLES = {
    "faqs": ("question", "answer"),
    "news": ("author_name",),
    "features": ("name", "description"),
    "settings": ("key", "value"),
    "system_announcements": ("title", "body"),
    "notifications": ("title", "body"),
}
if INCLUDE_HISTORY:
    CHECK_TABLES["ai_conversations"] = ("title", "messages")


def mask_url(url: str) -> str:
    if "://" not in url:
        return url
    head, tail = url.split("://", 1)
    return f"{head}://***"


def exact_remaining(conn) -> list[tuple[str, int]]:
    result = []
    for table, cols in CHECK_TABLES.items():
        try:
            rows = conn.execute(text(f'SELECT {", ".join(cols)} FROM {table}')).fetchall()
        except Exception:
            continue
        count = 0
        for row in rows:
            for value in row:
                text_value = "" if value is None else str(value)
                if "TuniX" in text_value or "TuniAI" in text_value:
                    count += 1
                    break
        if count:
            result.append((table, count))
    return result


def main() -> None:
    print(f"DB: {mask_url(settings.database_url)}")
    print("MODE:", "DRY-RUN" if DRY_RUN else "APPLY")

    conn = engine.connect()
    trans = conn.begin()
    try:
        before = exact_remaining(conn)
        print("Trước:", before or "sạch")
        for name, sql in UPDATES:
            result = conn.execute(text(sql))
            print(f"  {name}: {result.rowcount} rows")
        after = exact_remaining(conn)
        print("Sau:", after or "sạch")
        if DRY_RUN:
            trans.rollback()
            print("Đã rollback (dry-run).")
        else:
            trans.commit()
            print("Đã commit.")
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
