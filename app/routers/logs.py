"""Nhật ký hoạt động quản trị (menu "Nhật ký" trong trang QuanTri).

- `GET /logs`                 : nhật ký hệ thống dạng văn bản (bảng `logs`) — giữ để tương thích.
- `GET /logs/activity`        : lịch sử thao tác của người dùng trang quản trị (thêm/sửa/xoá...)
- `GET /logs/activity/meta`   : các thao tác + tính năng đã từng ghi (để dựng bộ lọc ở UI).
- `GET /logs/activity/export` : xuất Excel (.xlsx) theo bộ lọc hiện tại (hoặc toàn bộ).
- `DELETE /logs/activity/{id}`: xoá 1 dòng nhật ký.
- `DELETE /logs/activity`     : xoá theo bộ lọc (cần `all=true` nếu không có bộ lọc nào).

Tất cả yêu cầu quyền Super Admin.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import AdminLog, Log, RoleEnum, User
from app.services import admin_log
from app.services.admin_log import ACTION_LABELS, describe_value

try:  # openpyxl để tạo file .xlsx thật (có kiểu dáng, bộ lọc cột)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    _HAS_OPENPYXL = True
except Exception:  # noqa: BLE001 — thiếu thư viện thì fallback CSV (Excel vẫn mở được)
    _HAS_OPENPYXL = False

router = APIRouter(prefix="/logs", tags=["logs"])

_ADMIN_ONLY = require_roles(RoleEnum.super_admin)
_VN_TZ = timedelta(hours=7)
_VN_TZ = timedelta(hours=7)


def _activity_query(
    db: Session,
    action: str = "",
    feature: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """Query nhật ký đã áp bộ lọc — dùng chung cho xem / xuất Excel / xoá."""
    query = db.query(AdminLog)
    if (action or "").strip():
        query = query.filter(AdminLog.action == action.strip())
    if (feature or "").strip():
        query = query.filter(AdminLog.feature == feature.strip())
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                AdminLog.user_email.ilike(like),
                AdminLog.user_name.ilike(like),
                AdminLog.feature_label.ilike(like),
                AdminLog.target.ilike(like),
                AdminLog.path.ilike(like),
            )
        )
    start, end = _vn_bounds(date_from, date_to)
    if start is not None:
        query = query.filter(AdminLog.created_at >= start)
    if end is not None:
        query = query.filter(AdminLog.created_at <= end)
    return query


def _row_out(row: AdminLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "user_id": row.user_id,
        "user_email": row.user_email or "",
        "user_name": row.user_name or "",
        "user_role": row.user_role or "",
        "user_role_label": describe_value("role", row.user_role) if row.user_role else "",
        "action": row.action or "",
        "action_label": ACTION_LABELS.get(row.action or "", row.action or ""),
        "feature": row.feature or "",
        "feature_label": row.feature_label or "",
        "target": row.target or "",
        "method": row.method or "",
        "path": row.path or "",
        "status_code": row.status_code or 0,
        "ip": row.ip or "",
        "before_data": row.before_data or "",
        "after_data": row.after_data or "",
        # Danh sách thay đổi đã dịch sang tiếng Việt (hiển thị ở 2 cột Trước/Sau khi sửa)
        "changes": admin_log.build_changes(row.before_data or "", row.after_data or ""),
    }


@router.get("")
def list_logs(
    db: Session = Depends(get_db),
    _user: User = Depends(_ADMIN_ONLY),
):
    rows = db.query(Log).order_by(Log.id.desc()).limit(200).all()
    return [
        {"id": row.id, "level": row.level, "message": row.message, "created_at": row.created_at.isoformat()}
        for row in rows
    ]


@router.get("/activity/meta")
def activity_meta(
    db: Session = Depends(get_db),
    _user: User = Depends(_ADMIN_ONLY),
):
    """Thao tác + tính năng đã từng xuất hiện trong nhật ký (dựng bộ lọc ở UI)."""
    action_rows = (
        db.query(AdminLog.action, func.count(AdminLog.id))
        .group_by(AdminLog.action)
        .order_by(func.count(AdminLog.id).desc())
        .all()
    )
    feature_rows = (
        db.query(AdminLog.feature, AdminLog.feature_label, func.count(AdminLog.id))
        .group_by(AdminLog.feature, AdminLog.feature_label)
        .order_by(func.count(AdminLog.id).desc())
        .all()
    )
    return {
        "actions": [
            {"key": key, "label": ACTION_LABELS.get(key, key), "count": count}
            for key, count in action_rows
            if key
        ],
        "features": [
            {"key": key, "label": label or key, "count": count}
            for key, label, count in feature_rows
            if key
        ],
    }


@router.get("/activity")
def list_activity(
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str = Query("", description="Lọc theo thao tác: create/update/delete/approve..."),
    feature: str = Query("", description="Lọc theo tính năng: news/users/stocks..."),
    q: str = Query("", description="Tìm theo người dùng / đường dẫn / tính năng"),
    date_from: str = Query("", description="Từ ngày (YYYY-MM-DD, giờ VN)"),
    date_to: str = Query("", description="Đến ngày (YYYY-MM-DD, giờ VN)"),
    db: Session = Depends(get_db),
    _user: User = Depends(_ADMIN_ONLY),
):
    """Lịch sử thao tác của người dùng trang quản trị (mới nhất trước)."""
    query = _activity_query(db, action, feature, q, date_from, date_to)

    total = query.count()
    rows = query.order_by(AdminLog.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row_out(row) for row in rows],
    }


def _vn_bounds(date_from: str, date_to: str) -> tuple[datetime | None, datetime | None]:
    """Đổi ngày (theo giờ VN) → mốc UTC để lọc `created_at` (đang lưu UTC)."""

    def _parse(value: str, end: bool) -> datetime | None:
        raw = (value or "").strip()
        if not raw:
            return None
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
        if end:
            d = d.replace(hour=23, minute=59, second=59, microsecond=999999)
        return d - _VN_TZ

    return _parse(date_from, False), _parse(date_to, True)


# Cột xuất Excel: (tiêu đề, độ rộng)
_EXPORT_COLUMNS: list[tuple[str, int]] = [
    ("Thời gian", 20),
    ("Người thao tác", 26),
    ("Email", 28),
    ("Vai trò", 18),
    ("Thao tác", 12),
    ("Tính năng", 24),
    ("Trước khi sửa", 44),
    ("Sau khi sửa", 44),
    ("Đường dẫn", 30),
    ("IP", 16),
]


def _vn_time_str(value: datetime | None) -> str:
    if not value:
        return ""
    return (value + _VN_TZ).strftime("%d/%m/%Y %H:%M:%S")


def _changes_text(changes: list[dict], kind: str) -> str:
    lines = []
    for c in changes:
        val = c.get(kind) or ""
        if val:
            lines.append(f"{c.get('label')}: {val}")
    return "\n".join(lines)


def _export_rows(rows: list[AdminLog]) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        changes = admin_log.build_changes(row.before_data or "", row.after_data or "")
        out.append([
            _vn_time_str(row.created_at),
            row.user_name or row.user_email or "",
            row.user_email or "",
            describe_value("role", row.user_role) if row.user_role else "",
            ACTION_LABELS.get(row.action or "", row.action or ""),
            row.feature_label or row.feature or "",
            _changes_text(changes, "before"),
            _changes_text(changes, "after"),
            row.path or "",
            row.ip or "",
        ])
    return out


@router.get("/activity/export")
def export_activity(
    action: str = Query(""),
    feature: str = Query(""),
    q: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    db: Session = Depends(get_db),
    _user: User = Depends(_ADMIN_ONLY),
):
    """Xuất Excel (.xlsx) nhật ký theo bộ lọc hiện tại (không lọc → xuất tất cả)."""
    rows = _activity_query(db, action, feature, q, date_from, date_to).order_by(AdminLog.id.desc()).all()
    if not rows:
        raise HTTPException(status_code=400, detail="Không có dữ liệu để xuất theo bộ lọc hiện tại.")

    headers = _EXPORT_COLUMNS
    data = _export_rows(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")

    if _HAS_OPENPYXL:
        wb = Workbook()
        ws = wb.active
        ws.title = "Nhat ky hoat dong"
        head_font = Font(bold=True, color="FFFFFF")
        head_fill = PatternFill("solid", start_color="FF008080")
        for idx, (title, width) in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=idx, value=title)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = width
        for r, values in enumerate(data, start=2):
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                cell.alignment = Alignment(vertical="top", wrap_text=("\n" in str(value)))
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(headers)).column_letter}{len(data) + 1}"
        buf = io.BytesIO()
        wb.save(buf)
        payload = buf.getvalue()
        filename = f"nhat-ky-hoat-dong-{stamp}.xlsx"
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        # Fallback: CSV UTF-8 (BOM) — Excel mở được khi thiếu openpyxl
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow([h[0] for h in headers])
        writer.writerows(data)
        payload = ("\ufeff" + text.getvalue()).encode("utf-8")
        filename = f"nhat-ky-hoat-dong-{stamp}.csv"
        media = "text/csv; charset=utf-8"

    return Response(
        content=payload,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/activity/{log_id}")
def delete_activity(
    log_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(_ADMIN_ONLY),
):
    """Xoá 1 dòng nhật ký."""
    row = db.query(AdminLog).filter(AdminLog.id == log_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy dòng nhật ký.")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": 1}


@router.delete("/activity")
def clear_activity(
    action: str = Query(""),
    feature: str = Query(""),
    q: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    all: bool = Query(False, description="Bắt buộc true nếu muốn xoá khi KHÔNG có bộ lọc nào"),
    db: Session = Depends(get_db),
    _user: User = Depends(_ADMIN_ONLY),
):
    """Xoá nhật ký theo bộ lọc (thao tác, tính năng, từ khoá, khoảng thời gian)."""
    has_filter = bool((action or "").strip() or (feature or "").strip() or (q or "").strip()
                      or (date_from or "").strip() or (date_to or "").strip())
    if not has_filter and not all:
        raise HTTPException(status_code=400, detail="Vui lòng chọn bộ lọc trước khi xoá (hoặc xác nhận xoá toàn bộ).")
    deleted = _activity_query(db, action, feature, q, date_from, date_to).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": deleted}

