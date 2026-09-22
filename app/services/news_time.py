"""Chuẩn hoá NGÀY GIỜ ĐĂNG của bài viết (dùng chung cho tool import + API tin tức).

Định dạng ngày giờ trong file input (MarketTimes) — giữ nguyên khi trả ra API:

    "published_at": "2026-09-12T15:50:01+07:00",
    "published_date": "2026-09-12",
    "published_time": "15:50",
    "published_at_raw": "2026-09-12T15:50:01+07:00"

Quy ước lưu DB: ``News.published_at`` là datetime NAIVE theo GIỜ VIỆT NAM (UTC+7) —
tức đúng "ngày giờ trên bài viết" trong file, không quy đổi sang UTC. Nhờ vậy:

* Sắp xếp bài viết (``ORDER BY published_at DESC``) = đúng thứ tự ngày giờ file input.
* Bài cũ chưa có ``published_at`` thì suy ra từ ``created_at`` (đang lưu UTC) + 7 giờ.
* Khi trả API luôn gắn lại offset ``+07:00`` để frontend hiển thị đúng giờ VN.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

VN_OFFSET_HOURS = 7
VN_DELTA = timedelta(hours=VN_OFFSET_HOURS)
VN_TZ = timezone(VN_DELTA)

# Các format ngày giờ chấp nhận thêm ngoài ISO (dữ liệu crawl không đồng nhất)
_FALLBACK_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)


def now_vn() -> datetime:
    """Thời điểm hiện tại theo giờ Việt Nam (naive) — dùng cho bài đăng không có ngày giờ."""
    return datetime.now(VN_TZ).replace(tzinfo=None, microsecond=0)


def _to_vn_naive(value: datetime) -> datetime:
    """Đưa datetime về NAIVE giờ VN (aware → đổi múi giờ; naive → coi như đã là giờ VN)."""
    if value.tzinfo is None:
        return value.replace(microsecond=0)
    return value.astimezone(VN_TZ).replace(tzinfo=None, microsecond=0)


def parse_datetime(value: Any) -> datetime | None:
    """Chuỗi ngày giờ (ISO có/không offset, hoặc vài format phổ biến) → datetime giờ VN."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_vn_naive(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return _to_vn_naive(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        pass
    for fmt in _FALLBACK_FORMATS:
        try:
            return _to_vn_naive(datetime.strptime(text, fmt))
        except (ValueError, TypeError):
            continue
    return None


def parse_published_at(item: Mapping[str, Any]) -> datetime | None:
    """Đọc ngày giờ đăng của 1 bài trong file JSON (nhiều tên trường, có fallback).

    Ưu tiên: published_at → published_at_raw → published_date + published_time.
    Trả None nếu file không có ngày giờ (khi đó bài dùng thời điểm import).
    """
    for key in ("published_at", "published_at_raw"):
        dt = parse_datetime(item.get(key))
        if dt is not None:
            return dt

    date_part = parse_datetime(item.get("published_date"))
    if date_part is None:
        return None
    time_part = parse_datetime(item.get("published_time"))
    if time_part is None:
        return date_part.replace(hour=0, minute=0, second=0)
    return date_part.replace(hour=time_part.hour, minute=time_part.minute, second=time_part.second)


def resolve(published_at: datetime | None, created_at: datetime | None) -> datetime | None:
    """Ngày giờ đăng đã chuẩn hoá: ưu tiên published_at; bài cũ suy ra từ created_at (+7h)."""
    if published_at is not None:
        return _to_vn_naive(published_at)
    if created_at is None:
        return None
    if created_at.tzinfo is not None:
        return _to_vn_naive(created_at)
    # created_at của hệ thống lưu UTC (datetime.utcnow) → đổi sang giờ VN
    return _to_vn_naive(created_at + VN_DELTA)


def from_row(news: Any) -> datetime | None:
    """Ngày giờ đăng của 1 bản ghi News (ORM)."""
    return resolve(getattr(news, "published_at", None), getattr(news, "created_at", None))


def iso_vn(dt: datetime) -> str:
    """datetime (naive giờ VN) → chuỗi ISO kèm offset, đúng định dạng file input."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "+07:00"


def to_fields(dt: datetime | None) -> dict[str, str | None]:
    """datetime → 4 trường ngày giờ dùng cho API/frontend (như file input)."""
    if dt is None:
        return {
            "published_at": None,
            "published_date": None,
            "published_time": None,
            "published_at_raw": None,
        }
    iso = iso_vn(dt)
    return {
        "published_at": iso,
        "published_date": dt.strftime("%Y-%m-%d"),
        "published_time": dt.strftime("%H:%M"),
        "published_at_raw": iso,
    }


def fields_for_row(news: Any) -> dict[str, str | None]:
    """4 trường ngày giờ của 1 bản ghi News (dùng cho NewsOut)."""
    return to_fields(from_row(news))


def latest_first(query):
    """Áp ORDER BY: bài CÓ ngày giờ đăng trước, mới nhất trước (theo ngày giờ file input).

    Bài cũ chưa có ``published_at`` (NULL) xuống cuối và giữ thứ tự id giảm dần như trước.
    Dùng biểu thức boolean (thay vì NULLS LAST) để chạy được cả SQLite lẫn PostgreSQL.
    """
    from app.models import News

    return query.order_by(News.published_at.is_(None), News.published_at.desc(), News.id.desc())
