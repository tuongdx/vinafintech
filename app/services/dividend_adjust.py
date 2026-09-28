"""Điều chỉnh giá vốn / số lượng vị thế theo sự kiện cổ tức (quyền cho cổ đông).

VÌ SAO CẦN: ngày giao dịch không hưởng quyền, giá thị trường GIẢM cơ học đúng bằng
giá trị cổ tức tiền mặt (hoặc bị pha loãng khi trả cổ tức/thưởng bằng cổ phiếu).
Nếu vẫn giữ nguyên giá vốn & số lượng người dùng đã nhập thì danh mục sẽ hiện
"lỗ ảo" ngay sau ngày chốt quyền.

CÁCH TÍNH (giá vốn điều chỉnh — cách NĐT Việt Nam quen dùng):
    cost0 = số lượng × giá vốn (đúng số người dùng nhập)
    với mỗi sự kiện SAU ngày mua (created_at) và ĐẾN hôm nay:
        cổ tức tiền mặt   : tiền nhận += số lượng × tiền/cp ; cost -= số lượng × tiền/cp
        cổ tức/thưởng CP  : số lượng ×= (1 + tỷ lệ)          ; cost giữ nguyên
    giá vốn điều chỉnh = cost / số lượng mới

⇒ Tổng vốn (cost) giảm đúng phần cổ tức tiền mặt đã nhận, số lượng tăng đúng tỷ lệ
cổ phiếu thưởng, nên Lãi/Lỗ không còn bị sai lệch sau ngày chốt quyền.

KHÔNG sửa dữ liệu người dùng nhập: `portfolio_holdings.quantity/avg_price` vẫn là
nguồn sự thật (dùng khi sửa/xoá/bán vị thế) — hàm ở đây chỉ trả bản ĐÃ ĐIỀU CHỈNH
để hiển thị.

NGUỒN SỰ KIỆN (đã chia — dùng cho điều chỉnh giá vốn): cùng nguồn với tính năng cổ tức
của site — Simplize `dividendPayment`, fallback vnstock (VCI events), cache 24h — xem
`vnstock_service.get_dividends()`. Bỏ qua các đợt KHÔNG phải cổ tức/thưởng cho cổ đông
(quyền mua, bán ưu đãi, ESOP, phát hành riêng, trái phiếu chuyển đổi…) — giống logic
đang dùng ở StockDetail/Tiện ích.

NGUỒN SỰ KIỆN SẮP TỚI (nhãn "Sắp chia cổ tức" toàn site): Simplize/VCI chỉ có LỊCH SỬ
nên không dùng được. Dùng VNDirect finfo `/v4/events` — trả sự kiện quyền cho cổ đông
của TOÀN thị trường trong 1 request (xem khối _VD_* bên dưới).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import time
import re
import threading
from typing import Any

from app.services import news_time

# Từ khoá = đợt KHÔNG phải cổ tức/thưởng cho cổ đông (phải bỏ tiền ra mua / pha loãng khác).
_EXCLUDE_RE = re.compile(
    r"(quyền mua|mua cổ phiếu|bán ưu đãi|ưu đãi|chào bán|phát hành riêng|esop|cbc nv|"
    r"cán bộ công nhân|chuyển đổi|trái phiếu|tăng vốn|đấu giá)"
)
# Có "giá 20.000 đ/cp" ⇒ là đợt mua ưu đãi (phải nộp tiền), không phải cổ tức.
_HAS_ISSUE_PRICE_RE = re.compile(r"giá\s*[\d.,]+\s*(?:đ|vnd|vnđ|đồng|dong)")
_CASH_PER_SHARE_RE = re.compile(r"(\d[\d.,]*)\s*(?:đ|vnd|vnđ|đồng|dong)\s*/\s*(?:cp|cổ phiếu|co phieu)")
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_CASH_HINT_RE = re.compile(r"(tiền mặt|tien mat|bằng tiền|bang tien)")
_RATIO_AB_RE = re.compile(r"tỷ lệ\s*(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)")
_RATIO_CP_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*cp\s*[:/]\s*(\d+(?:[.,]\d+)?)\s*cp")
_STOCK_PCT_AFTER_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*%\s*(?:bằng cổ phiếu|cổ phiếu thưởng|cổ tức bằng cổ phiếu|thưởng phiếu)"
)
_STOCK_PCT_BEFORE_RE = re.compile(
    r"(?:bằng cổ phiếu|cổ phiếu thưởng|cổ tức bằng cổ phiếu|thưởng phiếu)[^%]*?(\d+(?:[.,]\d+)?)\s*%"
)
_STOCK_HINT_RE = re.compile(
    r"(cổ phiếu|co phieu|thưởng phiếu|thuong phieu|thưởng cp|thuong cp|cp thưởng)"
)

_MONEY_PER_PCT_OF_PAR = 100.0  # 1% mệnh giá 10.000đ = 100 đ/cp


def _vn_number(raw: Any) -> float:
    """Số kiểu Việt Nam → float. Dấu xuất hiện SAU CÙNG là dấu thập phân;
    chỉ 1 loại dấu mà khối cuối đúng 3 chữ số ⇒ phân cách nghìn ('1,000' → 1000)."""
    if raw is None:
        return 0.0
    text = str(raw).strip().replace(" ", "")
    if not text:
        return 0.0
    i_comma, i_dot = text.rfind(","), text.rfind(".")
    dec = ""
    if i_comma >= 0 and i_dot >= 0:
        dec = "," if i_comma > i_dot else "."
    else:
        sep = "," if i_comma >= 0 else ("." if i_dot >= 0 else "")
        if sep and (len(text) - text.rfind(sep) - 1) != 3:
            dec = sep
    if dec:
        other = "." if dec == "," else ","
        i = text.rfind(dec)
        head = text[:i].replace(other, "").replace(dec, "")
        text = f"{head}.{text[i + 1:]}"
    else:
        text = text.replace(",", "").replace(".", "")
    try:
        value = float(text)
    except (TypeError, ValueError):
        return 0.0
    return value if value == value else 0.0


def _as_date(raw: Any) -> date | None:
    """Ngày từ ISO ('2026-05-20', '2026-05-20T00:00:00') hoặc 'dd/mm/yyyy'."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def parse_event_part(part: str) -> tuple[float, float]:
    """(tiền mặt đ/cp, tỷ lệ cổ phiếu %) của MỘT phần nội dung sự kiện."""
    text = " " + re.sub(r"\s+", " ", str(part or "").lower()).strip() + " "
    if not text.strip():
        return 0.0, 0.0
    if not re.search(r"(cổ tức|thưởng)", text):
        return 0.0, 0.0
    if _EXCLUDE_RE.search(text):
        return 0.0, 0.0
    if _HAS_ISSUE_PRICE_RE.search(text):
        return 0.0, 0.0

    is_stock = bool(_STOCK_HINT_RE.search(text))
    is_cash = bool(_CASH_HINT_RE.search(text)) or bool(re.search(r"[đd]\s*/\s*(cp|cổ phiếu|co phieu)", text))

    cash = 0.0
    # 1) Tiền mặt ghi thẳng: "500đ/cp", "1,000 Đồng/CP"
    m = _CASH_PER_SHARE_RE.search(text)
    if m:
        cash = _vn_number(m.group(1))
    # 2) Tiền mặt theo % mệnh giá 10.000đ: "bằng tiền mặt 5%"
    if cash <= 0 and is_cash:
        m = _PCT_RE.search(text)
        if m:
            cash = _vn_number(m.group(1)) * _MONEY_PER_PCT_OF_PAR
    if cash < 0:
        cash = 0.0

    stock_pct = 0.0
    # 3) Cổ phiếu thưởng theo %: "bằng cổ phiếu 10%"
    if is_stock:
        m = _STOCK_PCT_AFTER_RE.search(text) or _STOCK_PCT_BEFORE_RE.search(text)
        if m:
            stock_pct = _vn_number(m.group(m.lastindex or 1))
    # 4) Tỷ lệ chia "A:B" (20:3 → 15%, 1:1 → 100%)
    if is_stock or re.search(r"tỷ lệ\s*\d+\s*[:/]\s*\d+", text):
        m = _RATIO_AB_RE.search(text) or _RATIO_CP_RE.search(text)
        if m:
            a, b = _vn_number(m.group(1)), _vn_number(m.group(2))
            if a > 0:
                stock_pct = b / a * 100.0
    if not (0 <= stock_pct <= 1000):
        stock_pct = 0.0
    return cash, stock_pct


def parse_event_content(content: str) -> tuple[float, float]:
    """(tiền/cp, tỷ lệ cổ phiếu %) của 1 dòng lịch cổ tức.

    Một dòng có thể gồm nhiều phần (ngăn bởi <br>, ';', '+') → tiền mặt lấy phần LỚN NHẤT,
    tỷ lệ cổ phiếu NHÂN DỒN (tránh cộng trùng khi cùng đợt được mô tả 2 lần).
    """
    parts = re.split(r"<br\s*/?>|\n|;|\+", str(content or ""), flags=re.IGNORECASE)
    cash = 0.0
    stock_factor = 1.0
    for part in parts:
        c, pct = parse_event_part(part)
        if c > cash:
            cash = c
        if pct > 0:
            stock_factor *= 1 + pct / 100.0
    return cash, (stock_factor - 1) * 100.0 if stock_factor > 1 else 0.0


def _parse_rows(rows: Any) -> list[dict[str, Any]]:
    """Parse danh sách dòng lịch cổ tức thô → sự kiện đã cấu trúc (sắp xếp theo ngày)."""
    out: list[dict[str, Any]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        event_date = _as_date(item.get("date"))
        if not content or event_date is None:
            continue
        cash, stock_pct = parse_event_content(content)
        if cash <= 0 and stock_pct <= 0:
            continue
        out.append(
            {
                "date": event_date.isoformat(),
                "cash": round(cash, 2),
                "stock_pct": round(stock_pct, 4),
                "label": content,
            }
        )
    out.sort(key=lambda r: r["date"])
    return out


def _cache_key(sym: str) -> str:
    return f"div-events:{sym}"


def _cached_events(sym: str) -> list[dict[str, Any]] | None:
    """Sự kiện đã có sẵn trong cache RAM hoặc file `.cache/symbol_dividends.json`.

    Trả None nếu CHƯA có dữ liệu (caller quyết định có đi lấy mạng hay không).
    Mục đích: các bảng toàn site hiển thị nhãn "Sắp chia cổ tức" mà KHÔNG phải gọi
    nguồn ngoài cho hàng trăm mã mỗi lần render.
    """
    from app.services import vnstock_service as vns

    cached = vns.cache.get(_cache_key(sym))
    if cached is not None:
        return cached
    file_rows = (vns._read_dividend_cache_file() or {}).get(sym)
    if not file_rows:
        return None
    rows = _parse_rows(file_rows)
    vns.cache.set(_cache_key(sym), rows, ttl_seconds=43200)
    return rows


def symbol_events(symbol: str) -> list[dict[str, Any]]:
    """Sự kiện cổ tức/thưởng ĐÃ PARSE của 1 mã, sắp xếp theo ngày tăng dần.

    Phần tử: {date (ISO), cash (đ/cp), stock_pct (%), label (nội dung gốc)}.
    Ưu tiên cache RAM → file cache (không gọi mạng) → nguồn ngoài (Simplize/VCI).
    Cache RAM 12h (nguồn `get_dividends` đã cache 24h).
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []
    cached = _cached_events(sym)
    if cached is not None:
        return cached

    # import lazy: chỉ cần khi thật sự lấy dữ liệu (tránh phụ thuộc nặng lúc import module)
    from app.services import vnstock_service as vns

    try:
        rows = _parse_rows(vns.get_dividends(sym))
    except Exception:  # noqa: BLE001 — lỗi nguồn cổ tức không được làm hỏng danh mục
        rows = []
    vns.cache.set(_cache_key(sym), rows, ttl_seconds=43200)
    return rows


_warm_inflight: set[str] = set()
_warm_guard = threading.Lock()
_WARM_MAX_PER_CALL = 60


def _warm_missing(symbols: list[str]) -> None:
    """Nạp NỀN lịch cổ tức cho các mã chưa có trong cache (giới hạn tải nguồn ngoài).

    Nhờ vậy lần tải trang đầu trả về ngay dữ liệu đã có, các mã còn lại tự có ở lần sau.
    """
    todo: list[str] = []
    with _warm_guard:
        for sym in symbols:
            if sym in _warm_inflight or len(todo) >= _WARM_MAX_PER_CALL:
                continue
            _warm_inflight.add(sym)
            todo.append(sym)
    if not todo:
        return

    def _run() -> None:
        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(symbol_events, todo))
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _warm_guard:
                for sym in todo:
                    _warm_inflight.discard(sym)

    threading.Thread(target=_run, daemon=True).start()


# ══════════════════ ĐỢT CHIA SẮP TỚI — NGUỒN TOÀN THỊ TRƯỜNG (VNDirect finfo) ══════════════════
# Simplize/VCI chỉ trả cổ tức ĐÃ CHIA ⇒ không thể báo "SẮP chia". VNDirect finfo
# `/v4/events` trả sự kiện quyền cho cổ đông của TOÀN thị trường trong 1 request:
#     type=DIVIDEND  → cổ tức TIỀN MẶT  (`dividend` = đ/cp, `ratio` = % mệnh giá)
#     type=STOCKDIV  → cổ tức/thưởng bằng CỔ PHIẾU (`ratio` = % cổ phiếu)
#     `effectiveDate` = ngày GDKHQ (giá giảm cơ học) — `actualDate` = ngày trả cổ tức.
# Nhãn "Sắp chia cổ tức" CHỈ hiện trong cửa sổ: 5 NGÀY TRƯỚC ngày GDKHQ → HẾT ngày GDKHQ
# (xem `_VD_LEAD_DAYS` + `_in_upcoming_window`) — hết ngày GDKHQ là tự mất.
_VD_EVENTS_URL = "https://api-finfo.vndirect.com.vn/v4/events"
_VD_TYPES = ("DIVIDEND", "STOCKDIV")
_VD_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "upcoming_dividends.json"
_VD_RAM_KEY = "div-upcoming:market"
_VD_RAM_TTL = 6 * 3600            # RAM 6 giờ
_VD_FILE_TTL = 3 * 24 * 3600      # file: dùng tạm khi nguồn lỗi (3 ngày)
_VD_LEAD_DAYS = 5                 # hiện nhãn sớm 5 ngày trước ngày GDKHQ
_VD_LOOKBACK_DAYS = 30            # lịch giữ cả đợt vừa qua (để biết NGÀY TRẢ của đợt vừa chốt)
_VD_FORMAT = 3                    # phiên bản cấu trúc file cache (đổi ⇒ bỏ file cũ)
# Cổ tức TIỀN chỉ được tự cộng vào quỹ Tiền mặt cho đợt trả TỪ ngày này trở đi (không hồi tố).
_CASH_GO_LIVE = date(2026, 9, 26)
_VD_MAX_ROWS = 500
_VD_FAIL_BACKOFF = 600.0          # nguồn lỗi ⇒ nghỉ 10 phút mới thử lại
_VD_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "vi-VN,vi;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
_vd_lock = threading.Lock()
_vd_fail_until = 0.0


def _vd_fetch(kind: str, since: date) -> list[dict[str, Any]]:
    """1 request: mọi sự kiện `kind` của cả thị trường có hiệu lực từ `since`."""
    from app.services import vnstock_service as vns

    query = f"locale:VN~AND~type:{kind}~AND~effectiveDate:gte:{since.isoformat()}"
    response = vns._get_http_client().get(
        _VD_EVENTS_URL,
        params={"q": query, "size": _VD_MAX_ROWS},
        headers=_VD_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _in_upcoming_window(event_date: date | None, today: date) -> bool:
    """Nhãn "Sắp chia cổ tức" hiện từ 5 NGÀY TRƯỚC ngày GDKHQ đến HẾT ngày GDKHQ."""
    if event_date is None:
        return False
    return event_date - timedelta(days=_VD_LEAD_DAYS) <= today <= event_date


def _vd_event(row: dict[str, Any]) -> dict[str, Any] | None:
    """1 dòng sự kiện VNDirect → {sym, date, ex_date, pay_date, cash, stock_pct, label}.

    `date` = `ex_date` = ngày GDKHQ (ngày chốt quyền + ngày giá giảm cơ học);
    `pay_date` = ngày trả cổ tức (None nếu nguồn chưa công bố).
    """
    sym = str(row.get("code") or "").strip().upper()
    if not sym:
        return None
    ex_date = _as_date(row.get("effectiveDate"))
    if ex_date is None:
        return None  # không có ngày GDKHQ thì không dùng được
    pay_date = _as_date(row.get("actualDate"))

    kind = str(row.get("type") or "").upper()
    note = str(row.get("note") or "").strip()
    cash = 0.0
    stock_pct = 0.0
    if kind == "STOCKDIV":
        stock_pct = _vn_number(row.get("ratio"))
    else:
        cash = _vn_number(row.get("dividend"))
        if cash <= 0:  # nguồn chỉ ghi tỷ lệ % mệnh giá
            cash = round(_vn_number(row.get("ratio")) * _MONEY_PER_PCT_OF_PAR, 2)
    if cash <= 0 and stock_pct <= 0:  # dự phòng: đọc lại từ nội dung
        parsed_cash, parsed_stock = parse_event_content(note)
        cash, stock_pct = cash or parsed_cash, stock_pct or parsed_stock
    if cash <= 0 and stock_pct <= 0:
        return None

    return {
        "sym": sym,
        "date": ex_date.isoformat(),
        "ex_date": ex_date.isoformat(),
        "pay_date": pay_date.isoformat() if pay_date else None,
        "cash": round(cash, 2),
        "stock_pct": round(stock_pct, 2),
        "label": note,
    }


def _vd_build(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Gộp các dòng sự kiện → {MÃ: [đợt theo ngày GDKHQ tăng dần]} (đợt cùng ngày được gộp)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event = _vd_event(row)
        if not event:
            continue
        sym = event.pop("sym")
        bucket = out.setdefault(sym, [])
        same = next((e for e in bucket if e.get("date") == event.get("date")), None)
        if same is None:
            bucket.append(event)
            continue
        # Nguồn tách 2 dòng cùng ngày (DIVIDEND + STOCKDIV) → gộp tiền + cổ phiếu.
        same["cash"] = round(float(same.get("cash") or 0) + float(event.get("cash") or 0), 2)
        same["stock_pct"] = round(float(same.get("stock_pct") or 0) + float(event.get("stock_pct") or 0), 2)
        same["pay_date"] = same.get("pay_date") or event.get("pay_date")
        if not same.get("label"):
            same["label"] = event.get("label")
    for bucket in out.values():
        bucket.sort(key=lambda e: str(e.get("date") or ""))
    return out


def _vd_read_file() -> dict[str, list[dict[str, Any]]] | None:
    """Cache file (sống sót qua restart / dùng khi nguồn lỗi) — bỏ đợt quá cũ."""
    try:
        if not _VD_CACHE_PATH.exists():
            return None
        raw = json.loads(_VD_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict):
        return None
    if int(raw.get("format") or 0) != _VD_FORMAT:
        return None  # file do bản CŨ ghi (khác cấu trúc) → bỏ, chờ lần fetch mới
    try:
        age = time.time() - float(raw.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return None
    if age > _VD_FILE_TTL:
        return None
    oldest = news_time.now_vn().date() - timedelta(days=_VD_LOOKBACK_DAYS * 3)
    fresh: dict[str, list[dict[str, Any]]] = {}
    for sym, events in raw["items"].items():
        if not isinstance(events, list):
            continue
        keep = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_date = _as_date(event.get("date"))
            if event_date is not None and event_date >= oldest:
                keep.append(event)
        if keep:
            fresh[str(sym).upper()] = keep
    return fresh


def _vd_write_file(items: dict[str, list[dict[str, Any]]]) -> None:
    try:
        _VD_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _VD_CACHE_PATH.write_text(
            json.dumps({"format": _VD_FORMAT, "fetched_at": time.time(), "items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def calendar_events(force: bool = False) -> dict[str, list[dict[str, Any]]] | None:
    """Lịch sự kiện quyền TOÀN THỊ TRƯỜNG → {MÃ: [đợt…]} (cả đợt vừa qua + sắp tới).

    None = CHƯA lấy được nguồn (caller tự quyết định fallback). Cache RAM 6h + file
    `.cache/upcoming_dividends.json`; nguồn lỗi thì nghỉ 10 phút và dùng file cũ.
    """
    global _vd_fail_until
    from app.services import vnstock_service as vns

    if not force:
        cached = vns.cache.get(_VD_RAM_KEY)
        if isinstance(cached, dict):
            return cached

    with _vd_lock:
        if not force:
            cached = vns.cache.get(_VD_RAM_KEY)
            if isinstance(cached, dict):
                return cached
            if _vd_fail_until and time.time() < _vd_fail_until:
                return _vd_read_file()
        since = news_time.now_vn().date() - timedelta(days=_VD_LOOKBACK_DAYS)
        rows: list[dict[str, Any]] = []
        try:
            for kind in _VD_TYPES:
                rows.extend(_vd_fetch(kind, since))
        except Exception:  # noqa: BLE001 — nguồn ngoài lỗi không được làm hỏng trang
            rows = []
        if not rows:
            _vd_fail_until = time.time() + _VD_FAIL_BACKOFF
            return _vd_read_file()
        items = _vd_build(rows)
        vns.cache.set(_VD_RAM_KEY, items, ttl_seconds=_VD_RAM_TTL)
        _vd_write_file(items)
        _vd_fail_until = 0.0
        return items


def market_upcoming(force: bool = False) -> dict[str, dict[str, Any]] | None:
    """Nhãn "Sắp chia cổ tức": {MÃ: đợt} cho mã ĐANG trong cửa sổ hiển thị.

    Cửa sổ = 5 ngày trước ngày GDKHQ → hết ngày GDKHQ (`_in_upcoming_window`).
    """
    calendar = calendar_events(force=force)
    if calendar is None:
        return None
    today = news_time.now_vn().date()
    out: dict[str, dict[str, Any]] = {}
    for sym, events in calendar.items():
        for event in events:
            if _in_upcoming_window(_as_date(event.get("date")), today):
                out[sym] = dict(event)
                break
    return out


def market_events(symbol: str) -> list[dict[str, Any]]:
    """Mọi đợt cổ tức gần đây/sắp tới của 1 mã theo lịch thị trường (rỗng nếu chưa có)."""
    try:
        calendar = calendar_events()
    except Exception:  # noqa: BLE001
        return []
    return [dict(e) for e in (calendar or {}).get(str(symbol or "").strip().upper(), [])]


def upcoming_for(symbols: Any, warm: bool = True) -> dict[str, dict[str, Any]]:
    """Với 1 danh sách mã: mã nào có đợt cổ tức SẮP TỚI → đợt gần nhất.

    Nguồn chính: bảng sự kiện quyền TOÀN THỊ TRƯỜNG (VNDirect — 1 request cho mọi mã),
    nên hầu như không cần gọi mạng theo từng mã. Nếu nguồn đó không lấy được thì quay về
    cách cũ: chỉ dùng dữ liệu đã cache của từng mã + nạp nền (warm).
    Chỉ trả mã đang trong cửa sổ: 5 ngày trước ngày GDKHQ → hết ngày GDKHQ.
    """
    today = news_time.now_vn().date()
    out: dict[str, dict[str, Any]] = {}
    try:
        market = market_upcoming()
    except Exception:  # noqa: BLE001 — nguồn lỗi ⇒ quay về cách cũ (cache từng mã)
        market = None
    missing: list[str] = []
    seen: set[str] = set()
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        event = (market or {}).get(sym)
        if event:
            out[sym] = dict(event)
            continue
        if market is not None:
            continue  # đã có bảng toàn thị trường ⇒ mã này chắc chắn không có đợt sắp tới
        events = _cached_events(sym)
        if events is None:
            missing.append(sym)
            continue
        for item in events:
            event_date = _as_date(item.get("date"))
            if _in_upcoming_window(event_date, today):
                out[sym] = dict(item)
                break
    if market is None and warm and missing:
        _warm_missing(missing)
    return out


def adjust_position(symbol: str, quantity: float, avg_price: float, since: Any = None) -> dict[str, Any]:
    """Điều chỉnh 1 vị thế theo các đợt cổ tức SAU ngày mua.

    Trả về:
        adjusted       : có đợt nào được áp dụng hay không
        quantity       : số lượng sau điều chỉnh (cổ phiếu thưởng làm tăng)
        avg_price      : giá vốn bình quân sau điều chỉnh
        cash_per_share : tổng cổ tức tiền mặt / cổ phiếu đã nhận (đ)
        cash_received  : tổng tiền cổ tức đã nhận (đ)
        stock_pct      : tổng tỷ lệ cổ phiếu thưởng đã nhận (%)
        events         : danh sách đợt đã cộng vào vị thế
        upcoming       : đợt SẮP TỚI gần nhất hoặc None (nhãn "Sắp chia cổ tức") — CHỈ hiện
                         trong 5 ngày trước ngày GDKHQ và hết ngày GDKHQ.
        pending_cash   : tiền cổ tức ĐÃ CHỐT QUYỀN nhưng CHƯA ĐẾN NGÀY CHIA (đ) — chỉ hiển thị
        pending_shares : cổ phiếu thưởng chờ về (cp) — chỉ hiển thị, KHÔNG cộng vào `quantity`
        pending_events : chi tiết từng đợt chờ nhận (kèm cash_amount/shares)
        cash_events    : cổ tức TIỀN đã tới ngày chia (kèm pay_date) → dùng để tự cộng vào
                         quỹ Tiền mặt VND đúng ngày chia (xem `credit_dividend_cash`)

    Đến NGÀY CHIA mới cộng vào vị thế (số lượng/giá vốn); trước đó (đã qua ngày GDKHQ) chỉ
    hiển thị dạng "chờ nhận" để không bị lỗ ảo do giá giảm ở ngày GDKHQ.
    """
    today = news_time.now_vn().date()
    # Đợt sắp tới được xác định ĐỘC LẬP với phần điều chỉnh (vị thế chưa đủ dữ liệu vẫn hiện nhãn).
    upcoming: dict[str, Any] | None = None
    try:
        events = _merge_events(symbol)
    except Exception:  # noqa: BLE001 — nguồn lỗi không được làm hỏng danh mục
        events = []
    for event in events:
        event_date = _as_date(event.get("date"))
        if _in_upcoming_window(event_date, today):
            upcoming = dict(event)
            break

    qty = float(quantity or 0)
    result: dict[str, Any] = {
        "adjusted": False,
        "quantity": qty,
        "avg_price": float(avg_price or 0),
        "cash_per_share": 0.0,
        "cash_received": 0.0,
        "stock_pct": 0.0,
        "events": [],
        "upcoming": upcoming,
        # Cổ tức ĐÃ CHỐT QUYỀN nhưng CHƯA ĐẾN NGÀY CHIA → chỉ HIỂN THỊ (chờ nhận).
        "pending_cash": 0.0,
        "pending_shares": 0.0,
        "pending_events": [],
        # Cổ tức TIỀN đã nhận (có ngày trả) → dùng để tự cộng vào quỹ Tiền mặt đúng ngày chia.
        "cash_events": [],
    }
    cost = qty * result["avg_price"]
    since_date = _as_date(since)
    if qty <= 0 or cost <= 0 or since_date is None:
        # Không biết ngày mua → KHÔNG tự điều chỉnh (tránh trừ nhầm cổ tức của giai đoạn trước).
        return result

    # `basis` = số lượng dùng để tính quyền: số đã cộng + số CỔ PHIẾU đang chờ nhận của các đợt trước.
    basis = qty
    for event in events:
        event_date = _as_date(event.get("date"))
        if event_date is None or event_date > today:
            continue
        if event_date <= since_date:
            continue  # đợt trước khi mua → đã phản ánh trong giá mua
        cash = float(event.get("cash") or 0)
        ratio = float(event.get("stock_pct") or 0) / 100.0
        pay_date = _as_date(event.get("pay_date"))
        if pay_date is not None and pay_date > today:
            # ĐÃ CHỐT QUYỀN, CHƯA CHIA → tính theo công thức nhưng KHÔNG cộng vào vị thế.
            cash_amount = basis * cash
            shares = basis * ratio
            if cash_amount <= 0 and shares <= 0:
                continue
            result["pending_cash"] += cash_amount
            result["pending_shares"] += shares
            basis += shares
            item = dict(event)
            item["cash_amount"] = round(cash_amount, 2)
            item["shares"] = round(shares, 4)
            result["pending_events"].append(item)
            continue
        # ĐÃ ĐẾN NGÀY CHIA (hoặc nguồn không có ngày trả) → cộng vào vị thế.
        applied = False
        if cash > 0:
            received = qty * cash
            cost -= received
            result["cash_received"] += received
            result["cash_per_share"] += cash
            applied = True
            result["cash_events"].append(
                {
                    "ex_date": event_date.isoformat(),
                    "pay_date": pay_date.isoformat() if pay_date else None,
                    "cash_per_share": round(cash, 2),
                    "cash_amount": round(received, 2),
                    "label": event.get("label") or "",
                }
            )
        if ratio > 0:
            qty *= 1 + ratio
            basis = max(basis, qty)
            result["stock_pct"] = (1 + result["stock_pct"] / 100.0) * (1 + ratio) * 100.0 - 100.0
            applied = True
        if applied:
            result["events"].append(dict(event))

    result["pending_cash"] = round(result["pending_cash"], 2)
    result["pending_shares"] = round(result["pending_shares"], 4)
    result["cash_received"] = round(result["cash_received"], 2)
    if result["events"]:
        result["adjusted"] = True
        result["quantity"] = qty
        result["avg_price"] = round(max(0.0, cost / qty), 2) if qty > 0 else 0.0
    result["cash_per_share"] = round(result["cash_per_share"], 2)
    result["stock_pct"] = round(result["stock_pct"], 4)
    return result


def _merge_events(symbol: str) -> list[dict[str, Any]]:
    """Gộp LỊCH SỬ (Simplize) + LỊCH THỊ TRƯỜNG (VNDirect) thành 1 danh sách đợt theo ngày GDKHQ.

    Lịch thị trường bổ sung `pay_date` (ngày trả) cho đợt trùng ngày GDKHQ và thêm cả đợt
    chưa có trong lịch sử (nguồn lịch sử công bố chậm hơn). Gộp theo NGÀY GDKHQ để một đợt
    không bị tính 2 lần; số liệu lấy từ lịch thị trường nếu có (nguồn công bố chính thức).
    """
    merged: dict[str, dict[str, Any]] = {}
    for event in market_events(symbol):
        key = str(event.get("date") or "")
        if key:
            merged[key] = dict(event)
    for event in symbol_events(symbol):
        event_date = _as_date(event.get("date"))
        if event_date is None:
            continue
        key = event_date.isoformat()
        current = merged.get(key)
        cash = float(event.get("cash") or 0)
        stock_pct = float(event.get("stock_pct") or 0)
        if current is None:
            merged[key] = {
                "date": key,
                "ex_date": key,
                "pay_date": None,
                "cash": cash,
                "stock_pct": stock_pct,
                "label": event.get("label") or "",
            }
            continue
        if float(current.get("cash") or 0) <= 0 < cash:
            current["cash"] = cash
        if float(current.get("stock_pct") or 0) <= 0 < stock_pct:
            current["stock_pct"] = stock_pct
        if not current.get("label"):
            current["label"] = event.get("label") or ""
    return sorted(merged.values(), key=lambda e: str(e.get("date") or ""))


def adjustment_out(info: dict[str, Any]) -> dict[str, Any]:
    """Rút gọn thông tin điều chỉnh để trả ra API (bỏ field nội bộ)."""
    return {
        "adjusted": bool(info.get("adjusted")),
        "cash_received": float(info.get("cash_received") or 0),
        "cash_per_share": float(info.get("cash_per_share") or 0),
        "stock_pct": float(info.get("stock_pct") or 0),
        "events": info.get("events") or [],
        "upcoming": info.get("upcoming"),
        "pending_cash": float(info.get("pending_cash") or 0),
        "pending_shares": float(info.get("pending_shares") or 0),
        "pending_events": info.get("pending_events") or [],
        "cash_events": info.get("cash_events") or [],
    }


def credit_dividend_cash(db: Any, user_id: int) -> float:
    """Cổ tức TIỀN đã tới NGÀY CHIA → tự cộng vào quỹ Tiền mặt VND (idempotent).

    - Chỉ áp dụng cho đợt trả TỪ `_CASH_GO_LIVE` trở đi (KHÔNG hồi tố cổ tức cũ).
    - Chống cộng trùng bằng `cash_transactions.ref_key` = `div:{MÃ}:{ngày GDKHQ}`.
    - `since` của vị thế = lần cập nhật cuối (hoặc ngày tạo) — nếu người dùng đã tự ghi
      nhận cổ tức vào danh mục thì đợt đó KHÔNG bị cộng vào quỹ nữa (khớp số đang hiển thị).
    Trả tổng tiền vừa cộng (0 nếu không có gì mới).
    """
    from app.models import PortfolioHolding
    from app.services import cash_service

    rows = db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user_id).all()
    total = 0.0
    for row in rows:
        try:
            info = adjust_position(
                row.symbol, float(row.quantity or 0), float(row.avg_price or 0), row.updated_at or row.created_at
            )
        except Exception:  # noqa: BLE001
            continue
        for event in info.get("cash_events") or []:
            pay_date = _as_date(event.get("pay_date"))
            amount = float(event.get("cash_amount") or 0)
            if pay_date is None or pay_date < _CASH_GO_LIVE or amount <= 0:
                continue
            ref = f"div:{str(row.symbol).upper()}:{event.get('ex_date')}"
            if cash_service.has_ref(db, user_id, ref):
                continue
            pay_txt = f"{pay_date.day:02d}/{pay_date.month:02d}/{pay_date.year}"
            cash_service.credit(
                db,
                user_id,
                cash_service.CUR_VND,
                amount,
                kind="dividend",
                label=f"Cổ tức {str(row.symbol).upper()} (chia ngày {pay_txt})",
                ref_key=ref,
            )
            total += amount
    return round(total, 2)
