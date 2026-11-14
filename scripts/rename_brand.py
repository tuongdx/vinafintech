"""One-off: đổi tên thương hiệu cũ còn sót trong dữ liệu DB.

- LEGACY_BRAND (brand cũ) → VinaFintech
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

Lưu ý: chỉ thay đúng chữ hoa của brand cũ và "TuniAI"; không đụng tới domain
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

# Brand cũ nằm trong DỮ LIỆU DB (không phải định danh trong source) — phải giữ
# nguyên chuỗi cũ thì script mới còn tác dụng với DB production chưa migrate.
LEGACY_BRAND = "TuniX"
BRAND = "VinaFintech"
LEGACY_AI = "TuniAI"
AI_BRAND = "VinaF AI"

# Thứ tự: TuniAI trước để không ảnh hưởng lẫn nhau (2 chuỗi không chồng nhau,
# nhưng giữ thứ tự rõ ràng).
UPDATES = [
    (
        "faqs",
        f"UPDATE faqs SET question = REPLACE(question, '{LEGACY_BRAND}', '{BRAND}'), "
        f"answer = REPLACE(answer, '{LEGACY_BRAND}', '{BRAND}') "
        f"WHERE question LIKE '%{LEGACY_BRAND}%' OR answer LIKE '%{LEGACY_BRAND}%'",
    ),
    (
        "faqs",
        f"UPDATE faqs SET question = REPLACE(question, '{LEGACY_AI}', '{AI_BRAND}'), "
        f"answer = REPLACE(answer, '{LEGACY_AI}', '{AI_BRAND}') "
        f"WHERE question LIKE '%{LEGACY_AI}%' OR answer LIKE '%{LEGACY_AI}%'",
    ),
    (
        "news",
        f"UPDATE news SET author_name = REPLACE(author_name, '{LEGACY_BRAND}', '{BRAND}') "
        f"WHERE author_name LIKE '%{LEGACY_BRAND}%'",
    ),
    (
        "features",
        f"UPDATE features SET name = REPLACE(name, '{LEGACY_AI}', '{AI_BRAND}'), "
        f"description = REPLACE(description, '{LEGACY_AI}', '{AI_BRAND}') "
        f"WHERE name LIKE '%{LEGACY_AI}%' OR description LIKE '%{LEGACY_AI}%'",
    ),
    (
        "settings",
        f"UPDATE settings SET value = '{BRAND}' "
        f"WHERE key = 'site_name' AND value = '{LEGACY_BRAND}'",
    ),
]

if INCLUDE_HISTORY:
    UPDATES.extend([
        (
            "ai_conversations",
            f"UPDATE ai_conversations SET title = REPLACE(title, '{LEGACY_BRAND}', '{BRAND}'), "
            f"messages = REPLACE(messages, '{LEGACY_BRAND}', '{BRAND}') "
            f"WHERE title LIKE '%{LEGACY_BRAND}%' OR messages LIKE '%{LEGACY_BRAND}%'",
        ),
        (
            "ai_conversations",
            f"UPDATE ai_conversations SET title = REPLACE(title, '{LEGACY_AI}', '{AI_BRAND}'), "
            f"messages = REPLACE(messages, '{LEGACY_AI}', '{AI_BRAND}') "
            f"WHERE title LIKE '%{LEGACY_AI}%' OR messages LIKE '%{LEGACY_AI}%'",
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
                if LEGACY_BRAND in text_value or LEGACY_AI in text_value:
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
