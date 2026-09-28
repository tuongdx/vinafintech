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

GET /api/asset-events/upcoming — Lịch sự kiện SẮP DIỄN RA của TOÀN BỘ cổ phiếu trên 3 sàn
(HOSE/HNX/UPCOM) — PUBLIC, KHÔNG cần đăng nhập — dùng cho block "Lịch sự kiện thị trường" ở
trang Thị trường. Nguồn: bảng sự kiện quyền toàn thị trường VNDirect (`dividend_adjust`),
snapshot dựng lại vào **12h trưa (giờ VN) hằng ngày** (thread nền `start_daily_refresh`).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import re
import threading
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import News, NewsStatusEnum, User
from app.services import dividend_adjust
from app.services import news_time
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
        news_time.latest_first(
            db.query(News).filter(
                News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]),
                (News.tags.ilike(f"%{sym}%")) | (News.title.ilike(f"%{sym}%")),
            )
        )
        .limit(limit)
        .all()
    )
    events: list[dict] = []
    for n in matched:
        published = news_time.from_row(n)
        events.append(_mk(sym, "IMPORTANT_NEWS", n.title, n.summary or n.title,
                          published.isoformat() if published else "",
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


def _market_universe(limit: int) -> list[str]:
    """Nhóm mã để quét lịch sự kiện: VN30 + top vốn hoá từ screener (đã cache)."""
    syms: list[str] = []
    for t in vns._VN30_TICKERS:
        if t not in syms:
            syms.append(t)
    try:
        sd = vns.get_stock_screener_page()
        stocks = sorted((sd.get("stocks") or []), key=lambda s: -(s.get("market_cap") or 0))
    except Exception:  # noqa: BLE001 — screener lỗi vẫn dùng VN30
        stocks = []
    for s in stocks:
        t = str(s.get("symbol") or "").strip().upper()
        if t and t not in syms:
            syms.append(t)
        if len(syms) >= limit:
            break
    return syms[:limit]


# ---------------------------------------------------------------------------
# LỊCH SỰ KIỆN THỊ TRƯỜNG — TOÀN BỘ CỔ PHIẾU 3 SÀN, LÀM MỚI 12H TRƯA HẰNG NGÀY
# ---------------------------------------------------------------------------
# Nguồn: bảng sự kiện quyền TOÀN THỊ TRƯỜNG của VNDirect finfo
# (`dividend_adjust.calendar_events` — 2 request cho mọi mã, có file cache + backoff khi
# nguồn lỗi) ⇒ phủ đủ HOSE/HNX/UPCOM mà KHÔNG phải quét từng mã (800+ mã là bất khả thi).
# Snapshot được dựng lại vào 12h trưa (giờ VN) mỗi ngày: trước 12h dùng bản 12h HÔM QUA,
# từ 12h dùng bản mới. Xem `_snapshot_day()` + `start_daily_refresh()`.
_EVENTS_SNAPSHOT_KEY = "asset-events-upcoming:market"
_EVENTS_FALLBACK_TTL = 600          # nguồn lỗi ⇒ thử lại sau 10 phút (dùng danh sách dự phòng)
_EVENTS_MAX_LIMIT = 60              # số sự kiện tối đa trả về cho block
_EVENTS_REFRESH_LOCK = threading.Lock()
_EVENTS_REFRESH_STARTED = False


def _snapshot_day(now: datetime | None = None) -> str:
    """Mốc snapshot lịch sự kiện: 12h trưa hôm nay — trước 12h vẫn tính mốc 12h HÔM QUA."""
    now = now or news_time.now_vn()
    day = now.date() if now.hour >= settings.asset_event_refresh_hour else now.date() - timedelta(days=1)
    return day.isoformat()


def _vn_dmy(iso: str) -> str:
    """'2026-10-05' → '05/10/2026' (rỗng nếu không phải ISO ngày)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def _market_event(sym: str, raw: dict) -> dict:
    """1 đợt sự kiện quyền (VNDirect) → sự kiện cho block "Lịch sự kiện thị trường"."""
    cash = float(raw.get("cash") or 0)
    stock_pct = float(raw.get("stock_pct") or 0)
    ex_date = str(raw.get("date") or "")[:10]        # ngày GDKHQ = mốc sự kiện
    pay_date = str(raw.get("pay_date") or "")[:10]
    parts: list[str] = []
    if cash > 0:
        parts.append("Cổ tức tiền mặt " + f"{cash:,.0f}".replace(",", ".") + " đ/CP")
    if stock_pct > 0:
        parts.append(f"Cổ tức/thưởng cổ phiếu {stock_pct:g}%")
    desc = " · ".join(parts)
    if ex_date:
        desc += (" · " if desc else "") + "Ngày GDKHQ: " + _vn_dmy(ex_date)
    if pay_date:
        desc += (" · " if desc else "") + "Ngày trả: " + _vn_dmy(pay_date)
    if stock_pct > 0 and cash <= 0:
        etype, title = "STOCK_SPLIT", f"Chia tách cổ phiếu {sym}"
    else:
        etype, title = "DIVIDEND", f"Cổ tức {sym}"
    return _mk(
        sym, etype, title, desc, ex_date,
        {"source": "vndirect-events", "ex_date": ex_date, "pay_date": pay_date or None,
         "cash": cash, "stock_pct": stock_pct},
        sort_date=ex_date,
    )


def _build_market_events() -> list[dict] | None:
    """Mọi sự kiện quyền SẮP TỚI của TOÀN BỘ cổ phiếu HOSE/HNX/UPCOM (nguồn VNDirect).

    None = CHƯA lấy được nguồn (caller tự quyết định fallback); [] = nguồn OK nhưng không
    có sự kiện nào từ hôm nay trở đi.
    """
    try:
        calendar = dividend_adjust.calendar_events(force=True)
    except Exception:  # noqa: BLE001 — nguồn ngoài lỗi không được làm hỏng trang
        return None
    if calendar is None:
        return None
    today = news_time.now_vn().date().isoformat()
    events: list[dict] = []
    for sym, rows in calendar.items():
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            ex_date = str(raw.get("date") or "")[:10]
            if not ex_date or ex_date < today:
                continue  # chỉ giữ sự kiện từ hôm nay trở đi
            events.append(_market_event(str(sym).upper(), raw))
    events.sort(key=lambda e: (e.get("event_date") or "", e["symbol"]))
    return events


def _legacy_market_events(universe: int) -> list[dict]:
    """Dự phòng khi nguồn toàn thị trường lỗi: quét cổ tức VN30 + top vốn hoá (cách cũ)."""
    events: list[dict] = []
    syms = _market_universe(universe)
    if not syms:
        return events
    with ThreadPoolExecutor(max_workers=12) as ex:
        for _sym, evs in zip(syms, ex.map(_dividend_events, syms)):
            events.extend(evs)
    today = news_time.now_vn().date().isoformat()
    upcoming = [e for e in events if (e.get("event_date") or "")[:10] >= today]
    upcoming.sort(key=lambda e: e.get("event_date") or "")
    return upcoming


def _upcoming_snapshot(universe: int = 0) -> list[dict]:
    """Snapshot (cache) lịch sự kiện sắp tới toàn thị trường theo mốc 12h trưa."""
    n = universe or settings.asset_event_upcoming_universe
    cache_key = f"{_EVENTS_SNAPSHOT_KEY}:{_snapshot_day()}"
    cached = vns.cache.get(cache_key)
    if cached is None:
        market = _build_market_events()
        if market is None:  # nguồn lỗi → dùng dự phòng, thử lại sau 10 phút
            cached = _legacy_market_events(n)
            vns.cache.set(cache_key, cached, ttl_seconds=_EVENTS_FALLBACK_TTL)
        else:
            cached = market
            vns.cache.set(cache_key, cached, ttl_seconds=settings.asset_event_upcoming_ttl)
    return cached


def get_upcoming_events(limit: int = 5, universe: int = 0) -> list[dict]:
    """Sự kiện SẮP DIỄN RA của TOÀN BỘ cổ phiếu trên 3 sàn (HOSE/HNX/UPCOM).

    Nguồn THẬT: bảng sự kiện quyền toàn thị trường VNDirect — chốt quyền / cổ tức tiền mặt /
    cổ tức-thưởng bằng cổ phiếu (xem `_build_market_events`). Một snapshot dùng chung cho mọi
    mã, thay cho việc quét từng mã.

    Snapshot dựng lại vào **12h trưa (giờ VN) hằng ngày** (`refresh_upcoming_events` chạy
    trong thread nền + `_snapshot_day()` đổi mốc đúng 12h) ⇒ trước 12h dùng bản của 12h hôm
    qua, từ 12h trở đi là dữ liệu mới. Nguồn lỗi ⇒ tạm dùng danh sách dự phòng VN30 + top
    vốn hoá và thử lại sau 10 phút.
    """
    return _upcoming_snapshot(universe)[: max(1, min(limit, _EVENTS_MAX_LIMIT))]


def refresh_upcoming_events() -> int:
    """Dựng lại snapshot lịch sự kiện toàn thị trường NGAY (bỏ qua cache).

    Gọi bởi scheduler 12h trưa hằng ngày và lúc warm startup. Trả số sự kiện sắp tới;
    trả 0 nếu nguồn lỗi (KHÔNG ghi đè snapshot cũ để block vẫn còn dữ liệu).
    """
    events = _build_market_events()
    if events is None:
        return 0
    vns.cache.set(
        f"{_EVENTS_SNAPSHOT_KEY}:{_snapshot_day()}",
        events,
        ttl_seconds=settings.asset_event_upcoming_ttl,
    )
    return len(events)


def _seconds_until_refresh() -> float:
    """Số giây tới mốc làm mới kế tiếp (12h trưa giờ VN)."""
    now = news_time.now_vn()
    target = now.replace(hour=settings.asset_event_refresh_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _refresh_loop() -> None:
    """Thread nền: ngủ tới 12h trưa rồi dựng lại snapshot, lặp lại mỗi ngày."""
    while True:
        try:
            time.sleep(_seconds_until_refresh())
            refresh_upcoming_events()
        except Exception:  # noqa: BLE001 — không để thread chết vì lỗi nguồn
            time.sleep(300)


def start_daily_refresh() -> None:
    """Bật làm mới lịch sự kiện toàn thị trường vào 12h trưa (giờ VN) mỗi ngày (idempotent)."""
    global _EVENTS_REFRESH_STARTED
    with _EVENTS_REFRESH_LOCK:
        if _EVENTS_REFRESH_STARTED:
            return
        _EVENTS_REFRESH_STARTED = True
    threading.Thread(target=_refresh_loop, daemon=True, name="asset-events-daily").start()


@router.get("/upcoming")
def upcoming_events(limit: int = 5, universe: int = 0):
    """Lịch sự kiện SẮP DIỄN RA của TOÀN BỘ cổ phiếu 3 sàn HOSE/HNX/UPCOM (PUBLIC).

    Dùng cho block "Lịch sự kiện thị trường" ở trang Thị trường — snapshot toàn thị trường
    được dựng lại vào 12h trưa (giờ VN) hằng ngày.
    """
    return get_upcoming_events(limit, universe)


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
