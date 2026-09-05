"""Đồng bộ schema database cho các môi trường đã có dữ liệu.

SQLAlchemy ``create_all`` chỉ tạo bảng mới, KHÔNG thêm cột mới vào bảng đã tồn tại.
Helper này dùng ``ALTER TABLE ... ADD COLUMN`` (an toàn với SQLite) khi cột chưa tồn tại,
đảm bảo KHÔNG mất dữ liệu user hiện tại. Khi chuyển sang DB khác (Postgres/MySQL),
nên thay bằng Alembic migration chính thức.
"""
from sqlalchemy import inspect, text

from app.core.config import settings
from app.db import Base, engine

# (table, column, ddl) — ddl tương thích CẢ SQLite lẫn PostgreSQL
# Lưu ý: PostgreSQL yêu cầu DEFAULT FALSE/TRUE cho cột BOOLEAN (không dùng 0/1).
_USER_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "phone", "VARCHAR(20)"),
    ("users", "avatar", "VARCHAR(500)"),
    ("users", "plan_expires_at", "DATETIME"),
    ("users", "membership_started_at", "DATETIME"),
    ("users", "membership_amount", "INTEGER"),
    ("users", "last_login", "DATETIME"),
    ("users", "terms_accepted", "BOOLEAN DEFAULT FALSE"),
    ("users", "email_verified", "BOOLEAN DEFAULT FALSE"),
    ("users", "notify_reports", "BOOLEAN DEFAULT TRUE"),
    ("users", "notify_news", "BOOLEAN DEFAULT TRUE"),
    ("users", "pref_theme", "VARCHAR(10) DEFAULT 'light'"),
]

_NEWS_COLUMNS: list[tuple[str, str, str]] = [
    ("news", "category", "VARCHAR(50)"),
    ("news", "image_url", "TEXT"),
    ("news", "author_name", "VARCHAR(255)"),
    ("news", "image_caption", "VARCHAR(500)"),
    ("news", "tags", "TEXT"),
    ("news", "source", "VARCHAR(255)"),
    ("news", "source_url", "VARCHAR(500)"),
    ("news", "views", "INTEGER DEFAULT 0"),
]

_FAQ_COLUMNS: list[tuple[str, str, str]] = [
    ("faqs", "category", "VARCHAR(50) DEFAULT ''"),
]


def ensure_schema() -> None:
    """Tạo bảng mới + thêm cột còn thiếu. Gọi 1 lần lúc startup."""
    Base.metadata.create_all(bind=engine)

    if settings.database_url.startswith("sqlite"):
        _ensure_sqlite_columns()
    else:
        # DB khác: khuyến nghị Alembic. Vẫn thử ALTER TABLE an toàn theo inspect.
        _ensure_generic_columns()


def _ensure_sqlite_columns() -> None:
    with engine.begin() as conn:
        for table, column, ddl in (_USER_COLUMNS + _NEWS_COLUMNS + _FAQ_COLUMNS):
            existing = {row["name"] for row in conn.execute(text(f"PRAGMA table_info({table})")).mappings()}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _ensure_generic_columns() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in (_USER_COLUMNS + _NEWS_COLUMNS + _FAQ_COLUMNS):
            try:
                existing = {c["name"] for c in insp.get_columns(table)}
            except Exception:
                continue
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
