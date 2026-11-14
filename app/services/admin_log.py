"""Nhật ký thao tác quản trị (menu "Nhật ký" trong QuanTri).

Ghi lại lịch sử người dùng của trang quản trị: AI làm (user/email/vai trò), LÚC NÀO,
THAO TÁC gì (thêm / sửa / xoá / duyệt / xuất bản...) và thuộc TÍNH NĂNG nào.

Cách ghi: middleware `cache_and_timing` trong `app/main.py` gọi `record_request()`
cho mọi request THAY ĐỔI dữ liệu (POST/PUT/PATCH/DELETE) trả về 2xx và trỏ tới
endpoint thuộc danh sách tính năng quản trị (`_FEATURE_MAP`). Nhờ vậy KHÔNG phải
sửa từng router mà vẫn phủ hết các tính năng (kể cả endpoint thêm sau này).

Mọi lỗi ghi log đều bị nuốt (chỉ log cảnh báo) — không được làm hỏng request chính.
"""
from __future__ import annotations

import enum
import json
import logging
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import decode_token
from app.db import SessionLocal
from app.models import (
    AdminLog,
    ContactMessage,
    FAQ,
    Feature,
    Feedback,
    News,
    Plan,
    StockSymbol,
    SupportTicket,
    User,
)

logger = logging.getLogger("vinafintech.admin_log")

# Vai trò được coi là "người dùng trang quản trị" (thao tác sẽ được ghi nhật ký)
_ADMIN_ROLES = {"super-admin", "admin", "content-manager", "editor", "support"}

# Thao tác mặc định suy từ HTTP method
_METHOD_ACTIONS = {
    "POST": ("create", "Thêm"),
    "PUT": ("update", "Sửa"),
    "PATCH": ("update", "Sửa"),
    "DELETE": ("delete", "Xoá"),
}

# Hậu tố đường dẫn chỉ thao tác nghiệp vụ (ưu tiên hơn HTTP method)
_SUFFIX_ACTIONS = {
    "approve": ("approve", "Duyệt"),
    "publish": ("publish", "Xuất bản"),
    "reject": ("reject", "Từ chối"),
    "reply": ("reply", "Trả lời"),
    "scan": ("scan", "Quét"),
}

# Đoạn đường dẫn KHÔNG phải id (tài nguyên con) — bỏ qua khi xác định bản ghi bị tác động,
# vd: /users/5/role, /users/5/plan, /news/12/approve
_NON_ID_SUFFIXES = set(_SUFFIX_ACTIONS) | {
    "role", "plan", "status", "password", "category", "activate", "deactivate",
    "read", "visible", "resolve", "close", "image", "feature", "settings",
    "signal-access",  # /users/5/signal-access → bản ghi bị tác động là user id 5
}

ACTION_LABELS = {
    "create": "Thêm",
    "update": "Sửa",
    "delete": "Xoá",
    "approve": "Duyệt",
    "publish": "Xuất bản",
    "reject": "Từ chối",
    "reply": "Trả lời",
    "scan": "Quét",
}

# ============================================================================
# Nhãn dễ đọc cho cột "Trước khi sửa" / "Sau khi sửa"
# ============================================================================
FIELD_LABELS: dict[str, str] = {
    "role": "Vai trò",
    "plan": "Gói hội viên",
    "signal_access": "Quyền Tín hiệu",
    "full_name": "Họ tên",
    "email": "Email",
    "phone": "Số điện thoại",
    "is_active": "Kích hoạt tài khoản",
    "email_verified": "Xác thực email",
    "password": "Mật khẩu",
    "title": "Tiêu đề",
    "summary": "Tóm tắt",
    "content": "Nội dung",
    "category": "Danh mục",
    "author_name": "Tác giả",
    "image_url": "Ảnh",
    "image_caption": "Chú thích ảnh",
    "tags": "Thẻ",
    "source": "Nguồn",
    "source_url": "Link nguồn",
    "status": "Trạng thái",
    "is_visible": "Hiển thị",
    "question": "Câu hỏi",
    "answer": "Câu trả lời",
    "order_index": "Thứ tự",
    "name": "Tên",
    "description": "Mô tả",
    "enabled": "Bật tính năng",
    "access_level": "Cấp truy cập",
    "key": "Mã",
    "price": "Giá",
    "duration_days": "Số ngày",
    "symbol": "Mã cổ phiếu",
    "exchange": "Sàn",
    "type": "Loại",
    "fee": "Phí",
    "reason": "Lý do",
    "priority": "Độ ưu tiên",
    "due_date": "Hạn xử lý",
    "message": "Nội dung",
    "subject": "Tiêu đề",
    "reply": "Phản hồi",
    "note": "Ghi chú",
    "amount": "Số tiền",
    "quantity": "Số lượng",
    "unit_price": "Đơn giá",
    "label": "Tên",
    "plan_expires_at": "Hết hạn gói",
    "membership_started_at": "Bắt đầu hội viên",
    "membership_amount": "Số tiền hội viên",
    "last_login": "Đăng nhập gần nhất",
    "published_at": "Ngày đăng",
    "views": "Lượt xem",
    "duration_days": "Số ngày",
    "expires_at": "Hết hạn",
    "ref_key": "Mã tham chiếu",
}

# Giá trị của một số trường → nhãn tiếng Việt
VALUE_LABELS: dict[str, dict[str, str]] = {
    "role": {
        "super-admin": "Super Admin",
        "admin": "Quản trị viên",
        "content-manager": "Quản trị nội dung",
        "editor": "Biên tập viên",
        "support": "Hỗ trợ",
        "member": "Thành viên",
    },
    "plan": {
        "free": "Miễn phí",
        "monthly": "Gói tháng",
        "yearly": "Gói năm",
        "basic": "Basic",
        "premium": "Premium",
        "pro": "Pro",
    },
    "access_level": {"free": "Miễn phí", "monthly": "Gói tháng", "yearly": "Gói năm"},
    "status": {
        "pending-approval": "Chờ duyệt",
        "approved": "Đã duyệt",
        "published": "Đã xuất bản",
        "rejected": "Từ chối",
        "open": "Mở",
        "in-progress": "Đang xử lý",
        "resolved": "Đã giải quyết",
        "closed": "Đã đóng",
    },
}

_BOOL_TRUE = "Có"
_BOOL_FALSE = "Không"
_VN_TZ = timedelta(hours=7)


def _fmt_datetime(value: str) -> str | None:
    """Chuỗi ISO 'YYYY-MM-DDTHH:MM:SS' (naive, giờ UTC) → 'dd/MM/yyyy HH:mm' (giờ VN)."""
    if len(value) < 16 or value[4] != "-" or value[7] != "-" or value[10] not in ("T", " "):
        return None
    try:
        d = datetime.strptime(value[:10] + " " + value[11:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            d = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return (d + _VN_TZ).strftime("%d/%m/%Y %H:%M")

# Trường không cần hiển thị khi tóm tắt
_SKIP_CHANGE_FIELDS = {"id", "created_at", "updated_at", "hashed_password", "ip", "method", "path"}
# Ưu tiên hiển thị các trường này trước (khi bản ghi bị xoá)
_PREFERRED_CHANGE_FIELDS = (
    "title", "question", "name", "full_name", "label", "symbol", "email",
    "role", "plan", "status", "content", "description", "message",
)
_MAX_CHANGES = 6


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " "))


def describe_value(field: str, value) -> str:
    """Đổi giá trị thô → chuỗi dễ đọc (VD role 'member' → 'Thành viên')."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return _BOOL_TRUE if value else _BOOL_FALSE
    if isinstance(value, (list, tuple)):
        if not value:
            return "—"
        return ", ".join(describe_value(field, v) for v in value[:5])
    if isinstance(value, dict):
        return _clip(json.dumps(value, ensure_ascii=False, default=str), 120)
    key = str(value)
    # Ngày giờ dạng ISO (VD 2027-09-05T04:01:47) → hiển thị dd/MM/yyyy HH:mm (giờ VN)
    when = _fmt_datetime(key)
    if when:
        return when
    mapped = VALUE_LABELS.get(field, {}).get(key)
    if mapped:
        return mapped
    text = key if not isinstance(value, (int, float)) else f"{value:,}".replace(",", ".")
    return _clip(text, 160)


def _load_json(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def build_changes(before_json: str, after_json: str) -> list[dict]:
    """So sánh dữ liệu trước/sau → danh sách thay đổi dễ đọc cho UI.

    - Sửa: chỉ lấy các trường có trong dữ liệu gửi lên và có giá trị khác bản ghi cũ.
    - Thêm: lấy các trường của dữ liệu gửi lên.
    - Xoá: lấy vài trường "đáng chú ý" của bản ghi bị xoá.
    """
    before = _load_json(before_json)
    after = _load_json(after_json)
    changes: list[dict] = []

    if after:
        keys = [k for k in after.keys() if k not in _SKIP_CHANGE_FIELDS]
        changed_keys = [k for k in keys if before is None or str(before.get(k)) != str(after.get(k))]
        for field in (changed_keys or keys):
            b = describe_value(field, before.get(field)) if before else ""
            a = describe_value(field, after.get(field))
            changes.append({"field": field, "label": field_label(field), "before": b, "after": a})
    elif before:
        keys = [k for k in _PREFERRED_CHANGE_FIELDS if k in before and before.get(k) not in (None, "")]
        if not keys:
            keys = [
                k for k, v in before.items()
                if k not in _SKIP_CHANGE_FIELDS and isinstance(v, (str, int, float, bool)) and v not in (None, "")
            ][:3]
        for field in keys:
            changes.append({
                "field": field,
                "label": field_label(field),
                "before": describe_value(field, before.get(field)),
                "after": "",
            })

    return changes[:_MAX_CHANGES]

# (tiền tố đường dẫn sau API prefix, key tính năng, nhãn hiển thị) — khớp CỤ THỂ trước
_FEATURE_MAP: tuple[tuple[str, str, str], ...] = (
    ("/admin/tools/import-news", "writing-tool", "Tool viết bài"),
    ("/admin/tools/crawl-tool", "crawl-tool", "Tool crawl bài viết"),
    ("/admin/tools", "writing-tool", "Tool viết bài"),
    ("/admin/stocks", "stocks", "Cổ phiếu"),
    ("/users", "users", "Tài khoản"),
    ("/news", "news", "Tin tức"),
    ("/plans", "plans", "Gói hội viên"),
    ("/features", "features", "Tính năng khoá hội viên"),
    ("/faqs", "faqs", "Câu hỏi thường gặp"),
    ("/tickets", "tickets", "Góp ý"),
    ("/feedback", "feedback", "Góp ý"),
    ("/contact", "contact", "Liên hệ"),
)


def resolve_feature(rel_path: str) -> tuple[str, str]:
    """Từ đường dẫn (đã bỏ API prefix) → (key tính năng, nhãn). Không khớp → ("", "")."""
    for prefix, key, label in _FEATURE_MAP:
        if rel_path.startswith(prefix):
            return key, label
    return "", ""


def resolve_action(method: str, rel_path: str) -> tuple[str, str] | None:
    """Xác định thao tác: ưu tiên hậu tố nghiệp vụ, sau đó tới HTTP method."""
    last = rel_path.rstrip("/").rsplit("/", 1)[-1].split("?")[0].lower()
    if last in _SUFFIX_ACTIONS:
        return _SUFFIX_ACTIONS[last]
    return _METHOD_ACTIONS.get((method or "").upper())


# ============================================================================
# Dữ liệu TRƯỚC khi sửa / SAU khi sửa
# ============================================================================
# key tính năng → (model, tên cột khoá, số đoạn đường dẫn tạo nên "gốc" tính năng)
_MODEL_MAP: dict[str, tuple[type, str, int]] = {
    "users": (User, "id", 1),
    "news": (News, "id", 1),
    "plans": (Plan, "id", 1),
    "features": (Feature, "id", 1),
    "faqs": (FAQ, "id", 1),
    "tickets": (SupportTicket, "id", 1),
    "contact": (ContactMessage, "id", 1),
    "feedback": (Feedback, "id", 1),
    "stocks": (StockSymbol, "symbol", 2),  # /admin/stocks/{symbol}
}

# Không bao giờ ghi ra nhật ký
_SECRET_COLS = {"hashed_password"}
_SECRET_KEYS = {"password", "hashed_password", "token", "access_token", "refresh_token", "api_key", "secret"}

_MAX_BODY_BYTES = 64 * 1024  # chỉ đọc body JSON ≤ 64KB
_MAX_VALUE_CHARS = 2000  # cắt bớt giá trị dài (VD: content bài viết)


def _clip(value, limit: int = _MAX_VALUE_CHARS):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… <cắt {len(value) - limit} ký tự>"
    return value


def _mask(obj):
    """Che các trường nhạy cảm trong dữ liệu gửi lên."""
    if isinstance(obj, dict):
        return {k: ("***" if str(k).lower() in _SECRET_KEYS else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    return _clip(obj)


def _row_to_json(row) -> str:
    data = {}
    try:
        columns = row.__table__.columns
    except Exception:
        return ""
    for col in columns:
        name = col.name
        if name in _SECRET_COLS:
            continue
        value = getattr(row, name, None)
        if isinstance(value, enum.Enum):
            value = value.value
        elif isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, (bytes, bytearray)):
            value = f"<{len(value)} bytes>"
        data[name] = _clip(value)
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return ""


def _target_ref(rel_path: str, feature: str) -> tuple[type, str, object] | None:
    """Từ đường dẫn suy ra bản ghi bị tác động (model + khoá). Không chắc chắn → None."""
    spec = _MODEL_MAP.get(feature)
    if not spec:
        return None
    model, key_field, root_len = spec
    segments = [s for s in rel_path.strip("/").split("/") if s]
    # Bỏ các đoạn cuối KHÔNG phải id (vd /users/5/role → /users/5)
    while segments and segments[-1].lower() in _NON_ID_SUFFIXES:
        segments.pop()
    rest = segments[root_len:]
    if len(rest) != 1:
        return None
    key_value = rest[0]
    if key_field == "id":
        if not key_value.isdigit():
            return None
        return model, key_field, int(key_value)
    return model, key_field, key_value


def _snapshot_record(rel_path: str, feature: str) -> str:
    """Chụp dữ liệu bản ghi hiện tại trong DB (chạy trong thread riêng). Rỗng nếu không xác định được."""
    ref = _target_ref(rel_path, feature)
    if not ref:
        return ""
    model, key_field, key_value = ref
    try:
        with SessionLocal() as db:
            column = getattr(model, key_field)
            if key_field == "id":
                row = db.query(model).filter(column == key_value).first()
            else:
                row = db.query(model).filter(column.ilike(str(key_value))).first()
            return _row_to_json(row) if row is not None else ""
    except Exception:
        logger.warning("Không chụp được dữ liệu bản ghi: %s", rel_path, exc_info=False)
        return ""


async def _read_json_body(request: Request) -> str:
    """Đọc dữ liệu gửi lên (query params + body JSON) — dùng làm dữ liệu SAU khi thêm/sửa.

    Query params cần thiết cho các endpoint kiểu `PATCH /users/2/role?role=member`
    (giá trị nằm ở query string chứ không phải body).
    """
    data: dict = {}
    try:
        for key, value in request.query_params.items():
            if key in ("v",):  # tham số cache-bust của frontend
                continue
            data[key] = value
    except Exception:
        data = {}

    ct = (request.headers.get("content-type") or "").lower()
    if ct.startswith("application/json"):
        try:
            length = int(request.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= _MAX_BODY_BYTES:
            try:
                raw = await request.body()
            except Exception:
                raw = b""
            text_body = raw.decode("utf-8", "replace").strip()
            if text_body:
                try:
                    obj = json.loads(text_body)
                    if isinstance(obj, dict):
                        data.update(obj)
                except Exception:
                    data["_raw"] = _clip(text_body, 500)

    if not data:
        return ""
    try:
        return json.dumps(_mask(data), ensure_ascii=False, default=str)
    except Exception:
        return ""


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "") or ""


def _bearer_token(request: Request) -> str:
    raw = request.headers.get("authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _write_log(ctx: dict, status_code: int) -> None:
    """Ghi 1 dòng nhật ký (chạy trong thread riêng).

    Dữ liệu "SAU khi sửa" lấy từ BẢN GHI THẬT trong DB sau khi route chạy (chính xác hơn
    payload gửi lên — tránh báo khác biệt giả khi client gửi field rỗng/không đổi).
    Với endpoint không có id trên đường dẫn (VD tạo mới) thì dùng payload gửi lên.
    """
    rel = ctx.get("rel") or ""
    feature = ctx.get("feature") or ""
    after_data = ""
    if rel and _target_ref(rel, feature):
        after_data = _snapshot_record(rel, feature)
    if not after_data:
        after_data = ctx.get("payload") or ""

    with SessionLocal() as db:
        user = db.query(User).filter(User.id == ctx.get("user_id")).first() if ctx.get("user_id") else None
        row = AdminLog(
            user_id=user.id if user else ctx.get("user_id"),
            user_email=(user.email if user else ctx.get("user_email")) or "",
            user_name=(user.full_name if user else ctx.get("user_name")) or "",
            user_role=(user.role.value if user and user.role else ctx.get("user_role")) or "",
            action=ctx["action"],
            feature=feature,
            feature_label=ctx["feature_label"],
            target=ctx["path"],
            method=ctx["method"],
            path=ctx["path"],
            status_code=status_code,
            ip=ctx.get("ip") or "",
            before_data=ctx.get("before_data") or "",
            after_data=after_data,
        )
        db.add(row)
        db.commit()


async def record(ctx: dict, status_code: int) -> None:
    """Ghi nhật ký cho request quản trị thành công (2xx). KHÔNG bao giờ raise."""
    try:
        if not (200 <= status_code < 300):
            return
        await run_in_threadpool(_write_log, ctx, status_code)
    except Exception:  # noqa: BLE001 — nhật ký không được làm hỏng request chính
        logger.warning("Ghi nhật ký quản trị thất bại: %s %s", ctx.get("method"), ctx.get("path"), exc_info=False)


async def prepare(request: Request) -> dict | None:
    """Chuẩn bị ngữ cảnh ghi nhật ký TRƯỚC khi route chạy.

    - Chụp bản ghi hiện tại (dữ liệu TRƯỚC khi sửa/xoá) — phải lấy trước `call_next`
      vì route có thể thay đổi dữ liệu.
    - Đọc dữ liệu gửi lên (query params + body JSON, đã che field nhạy cảm) để dùng cho
      các endpoint KHÔNG có id trên đường dẫn (VD tạo mới).
    Trả None nếu request không thuộc phạm vi ghi nhật ký (method/path/quyền không khớp).
    """
    try:
        method = (request.method or "").upper()
        if method not in _METHOD_ACTIONS:
            return None
        path = request.url.path
        prefix = settings.api_prefix.rstrip("/")
        if not path.startswith(prefix + "/"):
            return None
        rel = path[len(prefix):]

        feature, feature_label = resolve_feature(rel)
        if not feature:
            return None
        resolved = resolve_action(method, rel)
        if not resolved:
            return None
        action, _label = resolved

        claims = decode_token(_bearer_token(request))
        if not claims or claims.get("type") != "access":
            return None
        role = str(claims.get("role") or "")
        if role not in _ADMIN_ROLES:
            return None

        try:
            user_id = int(claims.get("sub"))
        except (TypeError, ValueError):
            user_id = None

        # Không cần payload cho DELETE (không có body) — tránh đọc body vô ích.
        payload = "" if method == "DELETE" else await _read_json_body(request)
        # Dữ liệu TRƯỚC khi thay đổi: chụp bản ghi hiện tại (phải lấy trước khi route chạy).
        before_data = await run_in_threadpool(_snapshot_record, rel, feature)

        return {
            "feature": feature,
            "feature_label": feature_label,
            "action": action,
            "path": path,
            "rel": rel,
            "method": method,
            "ip": _client_ip(request),
            "user_id": user_id,
            "user_email": str(claims.get("email") or ""),
            "user_name": "",
            "user_role": role,
            "payload": payload,
            "before_data": before_data,
            "after_data": "",
        }
    except Exception:  # noqa: BLE001 — không được làm hỏng request chính
        logger.warning("Chuẩn bị nhật ký quản trị thất bại: %s %s", request.method, request.url.path)
        return None

