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
# Lưu ý: PostgreSQL KHÔNG có kiểu DATETIME — phải dùng TIMESTAMP (map sang DateTime của SQLAlchemy).
# PostgreSQL yêu cầu DEFAULT FALSE/TRUE cho cột BOOLEAN (không dùng 0/1).
_USER_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "phone", "VARCHAR(20)"),
    ("users", "avatar", "VARCHAR(500)"),
    ("users", "plan_expires_at", "TIMESTAMP"),
    ("users", "membership_started_at", "TIMESTAMP"),
    ("users", "membership_amount", "INTEGER"),
    ("users", "last_login", "TIMESTAMP"),
    ("users", "terms_accepted", "BOOLEAN DEFAULT FALSE"),
    ("users", "email_verified", "BOOLEAN DEFAULT FALSE"),
    ("users", "notify_reports", "BOOLEAN DEFAULT TRUE"),
    ("users", "notify_news", "BOOLEAN DEFAULT TRUE"),
    ("users", "pref_theme", "VARCHAR(10) DEFAULT 'light'"),
    # Quyền dùng tính năng "Tín hiệu" (admin cấp theo từng tài khoản) — PostgreSQL cần DEFAULT FALSE.
    ("users", "signal_access", "BOOLEAN DEFAULT FALSE"),
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
    # Ngày giờ đăng bài (giờ VN) — import bằng tool sẽ lấy từ file JSON
    ("news", "published_at", "TIMESTAMP"),
]

_FAQ_COLUMNS: list[tuple[str, str, str]] = [
    ("faqs", "category", "VARCHAR(50) DEFAULT ''"),
]

_CASH_COLUMNS: list[tuple[str, str, str]] = [
    ("cash_transactions", "currency", "VARCHAR(10) DEFAULT 'VND'"),
    # Khoá chống trùng bút toán tự động (cổ tức: `div:{MÃ}:{ngày GDKHQ}`)
    ("cash_transactions", "ref_key", "VARCHAR(120) DEFAULT ''"),
]

_ADMIN_LOG_COLUMNS: list[tuple[str, str, str]] = [
    # Dữ liệu trước/sau khi thao tác (JSON) — phục vụ cột "Trước khi sửa"/"Sau khi sửa" ở menu Nhật ký
    ("admin_logs", "before_data", "TEXT"),
    ("admin_logs", "after_data", "TEXT"),
]

_ALL_EXTRA_COLUMNS = _USER_COLUMNS + _NEWS_COLUMNS + _FAQ_COLUMNS + _CASH_COLUMNS + _ADMIN_LOG_COLUMNS


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
        for table, column, ddl in _ALL_EXTRA_COLUMNS:
            try:
                existing = {row["name"] for row in conn.execute(text(f"PRAGMA table_info({table})")).mappings()}
            except Exception:
                continue
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _ensure_generic_columns() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _ALL_EXTRA_COLUMNS:
            try:
                existing = {c["name"] for c in insp.get_columns(table)}
            except Exception:
                continue
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
