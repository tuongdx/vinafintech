from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_IS_SQLITE = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _IS_SQLITE else {}

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)

if _IS_SQLITE:
    # SQLite: WAL + busy_timeout + synchronous=NORMAL giúp tránh "database is
    # locked" và tăng tốc ghi khi có nhiều thao tác thêm/sửa/xoá đồng thời.
    # (Chỉ áp dụng cho SQLite; PostgreSQL/MySQL không dùng PRAGMA này.)
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            # Tăng tốc đọc SQLite: bộ đệm lớn hơn + memory-map + temp trên RAM
            cursor.execute("PRAGMA cache_size=-20000")      # ~20MB cache
            cursor.execute("PRAGMA mmap_size=268435456")     # 256MB memory-map
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
