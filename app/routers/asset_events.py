"""Sự kiện liên quan đến tài sản (Asset Events).

GET /api/asset-events — CHỈ trả sự kiện cho cổ phiếu ĐANG NẮM GIỮ + THEO DÕI của user,
từ dữ liệu THẬT (không tạo dữ liệu giả trong production). Các loại sự kiện hỗ trợ:
    EARNINGS              ← báo cáo tài chính / kết quả kinh doanh (cafef FileBCTC)
    DIVIDEND              ← trả/chia cổ tức (vnstock dividends)
    EX_DIVIDEND_DATE      ← ngày chốt quyền / không hưởng quyền (dividends)
    STOCK_SPLIT           ← chia tách cổ phiếu (dividends)
    RIGHTS_ISSUE          ← phát hành thêm / quyền mua / tăng vốn / ESOP (dividends)
    IMPORTANT_NEWS        ← tin quan trọng (news DB có mã trong tags/tiêu đề)
    UNUSUAL_PRICE_MOVEMENT← biến động giá bất thường (quote realtime)
Yêu cầu đăng nhập (JWT). Data model: ``AssetEvent`` (app/models.py) — bảng sẵn sàng
để lưu/persist nếu cần; API hiện tại tính trực tiếp từ nguồn thật để luôn mới & trung thực.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import News, NewsStatusEnum, User
from app.services import vnstock_service as vns
from app.services.alert_service import alert_service

router = APIRouter(prefix="/asset-events", tags=["asset-events"])


def _parse_date(ds: str) -> datetime | None:
    ds = (ds or "").strip()
    if not ds:
        return None
    # ISO datetime (có giờ:phút, VD: 2026-08-24T03:16:42.254016)
    try:
        return datetime.fromisoformat(ds.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(ds, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _period_end_date(label: str) -> str:
    """'Q2/2026' → '2026-06-30' (ngày cuối quý) để sắp xếp sự kiện kết quả kinh doanh.
    Trả '' nếu không nhận dạng được (không tạo dữ liệu, chỉ gán mốc cuối kỳ từ nhãn thật)."""
    import re
    m = re.match(r"\s*q([1-4])\s*/\s*(\d{4})", label or "", re.IGNORECASE)
    if not m:
        return ""
    q, year = int(m.group(1)), int(m.group(2))
    month_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
    return f"{year}-{month_end}"


def _mk(sym: str, etype: str, title: str, desc: str, date_s: str, meta: dict, **extra) -> dict:
    dt = _parse_date(date_s)
    out = {
        "symbol": sym,
        "event_type": etype,
        "title": title,
        "description": desc,
        "event_date": dt.isoformat() if dt else None,
        "sort_date": dt.isoformat() if dt else "",
        "metadata": meta,
    }
    out.update(extra)
    return out


def _dividend_events(sym: str, limit: int = 6) -> list[dict]:
    """DIVIDEND / EX_DIVIDEND_DATE / STOCK_SPLIT / RIGHTS_ISSUE từ lịch sử cổ tức
    (chỉ giữ `limit` sự kiện mới nhất/mã để list cân bằng, không lấn át loại khác)."""
    events: list[dict] = []
    try:
        rows = vns.get_dividends(sym)
    except Exception:
        rows = []
    rows = [r for r in rows if str(r.get("content") or "").strip()]
    rows.sort(key=lambda r: _parse_date(str(r.get("date") or "")) or datetime.min, reverse=True)
    for r in rows[:limit]:
        content = str(r.get("content") or "").strip()
        date_s = str(r.get("date") or "").strip()
        if not content:
            continue
        low = content.lower()
        if any(k in low for k in ("chia tách", "tách cổ phiếu", "tách 1")):
            etype, title = "STOCK_SPLIT", f"Chia tách cổ phiếu {sym}"
        elif any(k in low for k in ("phát hành", "quyền mua", "tăng vốn", "esop", "chào bán", "phương án phát hành")):
            etype, title = "RIGHTS_ISSUE", f"Phát hành thêm {sym}"
        elif any(k in low for k in ("chốt", "không hưởng quyền", "ngày đăng ký cuối", "ngày giao dịch không hưởng")):
            etype, title = "EX_DIVIDEND_DATE", f"Ngày chốt quyền {sym}"
        elif any(k in low for k in ("trả cổ tức", "chia cổ tức", "cổ tức")):
            etype, title = "DIVIDEND", f"Cổ tức {sym}"
        else:
            etype, title = "DIVIDEND", f"Sự kiện doanh nghiệp {sym}"
        events.append(_mk(sym, etype, title, content, date_s,
                          {"source": "dividend", "raw_date": date_s}))
    return events


def _earnings_events(sym: str, limit: int = 2) -> list[dict]:
    """EARNINGS — công bố báo cáo tài chính / kết quả kinh doanh (cafef FileBCTC)."""
    events: list[dict] = []
    try:
        rep = vns.get_company_reports(sym, "financial")
    except Exception:
        rep = None
    items = (rep or {}).get("items") or []
    for it in items:
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        low = title.lower()
        if not any(k in low for k in ("báo cáo tài chính", "báo cáo kết quả", "kết quả kinh doanh",
                                      "báo cáo quý", "bctc", "báo cáo năm")):
            continue
        period = str(it.get("date") or "")
        events.append(_mk(sym, "EARNINGS", f"Công bố {title}",
                          f"{sym} công bố {title}.", "",
                          {"source": "cafef-report", "title": title, "link": it.get("link") or "", "period": period},
                          sort_date=_period_end_date(period)))
        if len(events) >= limit:
            break
    return events


def _unusual_move_events(sym: str) -> list[dict]:
    """UNUSUAL_PRICE_MOVEMENT — biến động giá % >= ngưỡng cấu hình."""
    try:
        q = vns.get_quote(sym)
    except Exception:
        q = None
    if not q or not q.get("valid"):
        return []
    try:
        pct = float(q.get("percent_change") or 0)
        price = float(q.get("price") or 0)
    except (TypeError, ValueError):
        return []
    if abs(pct) < settings.alert_unusual_move_pct:
        return []
    direction = "tăng" if pct > 0 else "giảm"
    return [_mk(sym, "UNUSUAL_PRICE_MOVEMENT", f"{sym} {direction} {abs(pct):.1f}% trong phiên",
                f"Giá {sym} đang ở mức {price:,.0f}, {direction} {abs(pct):.1f}% so với hôm qua — biến động bất thường.",
                "", {"source": "quote", "percent_change": pct, "price": price})]


def _news_events(db: Session, sym: str, limit: int = 3) -> list[dict]:
    """IMPORTANT_NEWS — tin trong news DB có mã trong tags/tiêu đề."""
    matched = (
        db.query(News)
        .filter(
            News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]),
            (News.tags.ilike(f"%{sym}%")) | (News.title.ilike(f"%{sym}%")),
        )
        .order_by(News.id.desc())
        .limit(limit)
        .all()
    )
    events: list[dict] = []
    for n in matched:
        events.append(_mk(sym, "IMPORTANT_NEWS", n.title, n.summary or n.title,
                          n.created_at.isoformat() if n.created_at else "",
                          {"source": "news", "category": n.category or "tin-tuc", "slug": _slug(n.title)},
                          news_id=n.id))
    return events


def _slug(text: str) -> str:
    import re
    import unicodedata
    text = (text or "").replace("đ", "d").replace("Đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


@router.get("")
def asset_events(
    limit: int = 50,
    include_news: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sự kiện liên quan tài sản của user (CHỈ holdings + watchlist), mới nhất trước."""
    syms = alert_service.user_symbols(db, user)
    events: list[dict] = []

    if syms:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, evs in zip(syms, ex.map(_dividend_events, syms)):
                events.extend(evs)
        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, evs in zip(syms, ex.map(_earnings_events, syms)):
                events.extend(evs)
        for sym in syms:
            events.extend(_unusual_move_events(sym))
        if include_news:
            for sym in syms:
                events.extend(_news_events(db, sym, 3))

    # Chỉ giữ sự kiện SẮP DIỄN RA hoặc TRONG ~6 THÁNG GẦN NHẤT (bỏ sự kiện quá cũ).
    cutoff = (datetime.utcnow() - timedelta(days=settings.asset_event_window_days)).isoformat()
    events = [e for e in events if not e.get("sort_date") or e["sort_date"] >= cutoff]

    # Mới nhất trước (ISO string so sánh từ điển được); sự kiện không có ngày về cuối.
    events.sort(key=lambda e: e.get("sort_date") or "", reverse=True)
    return events[: max(1, min(limit, 200))]
