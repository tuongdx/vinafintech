"""Lãi ngân hàng — ĐỒNG BỘ dữ liệu thật từ 24HMoney (Giai đoạn 2).

Nguyên tắc tối ưu & tránh bị nguồn phát hiện:
  - CHỈ 1 request GET tới trang (SSR chứa sẵn 2 bảng HTML) — không gọi API phụ, không chạy JS.
  - Không tự động fetch khi request web; chỉ chạy nền sau startup (daemon) với TTL (mặc định 24h)
    ghi dấu qua cột updated_at của dòng source=24HMoney → nhiều nhất 1 lần mỗi TTL.
  - Lock single-flight trong tiến trình; nếu parse lỗi/hụt (< MIN_BANKS) → ROLLBACK, giữ dữ liệu cũ.
  - Header giống trình duyệt thường, 1 kết nối duy nhất, không retry vòng lặp.
"""
import os
import threading
import unicodedata
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import BankInterestRate
from app.services.bank_rate_service import DEFAULT_CUSTOMER, SRC_24H_NAME, SRC_24H_URL

URL = SRC_24H_URL

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://24hmoney.vn/",
}

# Lãi suất chỉ nên lấy tối đa 1 lần mỗi khoảng này (giờ).
TTL_HOURS = float(os.environ.get("BANK_RATE_SYNC_TTL_HOURS", "24"))
# Nếu parse ra ít hơn số ngân hàng này → coi là lỗi, không ghi đè dữ liệu đang có.
MIN_BANKS = 10
_TIMEOUT = 25.0

# Mã ổn định cho các ngân hàng trong bảng 24HMoney (đảm bảo unique + quen thuộc).
BANK_CODE_MAP = {
    "Vietcombank": "VCB", "BIDV": "BID", "VietinBank": "CTG", "Agribank": "AGB",
    "Techcombank": "TCB", "MBBank": "MBB", "ACB": "ACB", "VPBank": "VPB",
    "SHB": "SHB", "HDBank": "HDB", "MB Bank": "MBB",
    "Saigonbank": "SGB", "PGBank": "PGB", "LPBank": "LPB", "OCB": "OCB",
    "BVBank": "BVB", "CIMB Bank": "CIM", "MSB": "MSB", "VIB": "VIB",
    "NCB": "NCB", "Nam Á Bank": "NAB", "An Bình Bank": "ABB", "VietBank": "VBB",
    "Sacombank": "STB", "Shinhan Bank": "SHR", "Woori Bank": "WRB",
    "Bảo Việt Bank": "BAV", "Bắc Á Bank": "BAB", "UOB": "UOB", "TPBank": "TPB",
    "Kiên Long Bank": "KLB", "Việt Á Bank": "VAB", "PVCombank": "PVC",
    "GPBANK": "GPB", "Eximbank": "EIB", "SeABank": "SSB",
    "Standard Chartered": "STC", "SCB": "SCB", "HSBC": "HSB",
}

_LOCK = threading.Lock()


def _ascii_name(name: str) -> str:
    name = name.replace("Đ", "D").replace("đ", "d")
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c))


def bank_code_for(name: str) -> str:
    key = (name or "").strip()
    if key in BANK_CODE_MAP:
        return BANK_CODE_MAP[key]
    ascii_key = _ascii_name(key).upper()
    code = "".join(ch for ch in ascii_key if ch.isalnum())[:12]
    return code or "NH"


def _parse_rate(raw: str | None):
    if raw is None:
        return None
    s = str(raw).strip().replace(",", ".").replace("%", "").replace("\u2212", "-")
    if not s or s in ("-", "--", "–", "N/A", "na"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if v <= 0 or v > 100:
        return None
    return v


def _nearest_channel(table) -> str | None:
    """Kênh gửi của bảng = heading gần nhất trước nó chứa 'Quầy' / 'Trực tuyến'/'Online'."""
    for node in table.find_all_previous(string=True):
        s = str(node).strip()
        if not s:
            continue
        if "Quầy" in s or "quầy" in s.lower():
            return "counter"
        low = s.lower()
        if "trực tuyến" in low or "online" in low:
            return "online"
    return None


def parse_html(html: str) -> list[dict]:
    """Trích 2 bảng (Quầy + Online): mỗi dòng = 1 (ngân hàng, kỳ hạn, kênh) có lãi suất."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for tbl in soup.find_all("table"):
        trs = tbl.find_all("tr")
        if not trs:
            continue
        head = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
        if not head or head[0].strip() != "Ngân hàng":
            continue
        channel = _nearest_channel(tbl)
        if channel not in ("online", "counter"):
            continue
        terms: list[int | None] = []
        for h in head[1:]:
            digits = "".join(ch for ch in h if ch.isdigit())
            terms.append(int(digits) if digits else None)
        for tr in trs[1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if not cells or not cells[0]:
                continue
            name = cells[0].strip()
            for j, t in enumerate(terms):
                if not t:
                    continue
                raw = cells[j + 1] if j + 1 < len(cells) else None
                rate = _parse_rate(raw)
                if rate is None:
                    continue
                out.append(
                    {
                        "bank_code": bank_code_for(name),
                        "bank_name": name,
                        "term_months": int(t),
                        "interest_rate": rate,
                        "channel": channel,
                    }
                )
    return out


def fetch_html() -> str:
    with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
        r = client.get(URL)
        r.raise_for_status()
        return r.text


def last_sync_at(db: Session):
    return db.query(func.max(BankInterestRate.updated_at)).filter(
        BankInterestRate.source == SRC_24H_NAME
    ).scalar()


def is_stale(db: Session, ttl_hours: float | None = None) -> bool:
    ttl_hours = TTL_HOURS if ttl_hours is None else ttl_hours
    last = last_sync_at(db)
    if last is None:
        return True
    return datetime.utcnow() - last > timedelta(hours=ttl_hours)


def sync(db: Session, force: bool = False) -> dict:
    """Đồng bộ (fetch+parse+thay dữ liệu) nếu quá hạn TTL. Có single-flight; không spam nguồn.

    Thành công → XOÁ toàn bộ dòng cũ rồi chèn dữ liệu mới (giao dịch), updated_at = thời điểm đồng bộ.
    Lỗi → rollback, GIỮ NGUYÊN dữ liệu hiện có.
    """
    with _LOCK:
        if not force and not is_stale(db):
            last = last_sync_at(db)
            return {"ok": True, "synced": False, "reason": "fresh", "updated_at": last.isoformat() if last else None}
        try:
            html = fetch_html()
            rows = parse_html(html)
            banks = {r["bank_code"] for r in rows}
            if len(banks) < MIN_BANKS:
                db.rollback()
                return {"ok": False, "synced": False, "error": f"parse thiếu dữ liệu ({len(banks)} ngân hàng)"}
            now = datetime.utcnow()
            new_rows = [
                BankInterestRate(
                    bank_code=r["bank_code"],
                    bank_name=r["bank_name"],
                    term_months=r["term_months"],
                    interest_rate=r["interest_rate"],
                    channel=r["channel"],
                    customer_type=DEFAULT_CUSTOMER,
                    min_amount=None,
                    conditions=None,
                    source=SRC_24H_NAME,
                    updated_at=now,
                )
                for r in rows
            ]
            db.query(BankInterestRate).delete()
            db.add_all(new_rows)
            db.commit()
            return {
                "ok": True,
                "synced": True,
                "banks": len(banks),
                "rows": len(new_rows),
                "updated_at": now.isoformat(),
            }
        except Exception as exc:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "synced": False, "error": str(exc)[:200]}


def run_auto_sync(delay_seconds: float = 4.0) -> None:
    """Chạy nền sau startup: chờ một chút rồi đồng bộ nếu quá hạn (không chặn boot)."""

    def _job():
        try:
            import time

            time.sleep(delay_seconds)
            from app.db import SessionLocal

            session = SessionLocal()
            try:
                sync(session)
            finally:
                session.close()
        except Exception:  # noqa: BLE001 — nền, không làm hỏng app
            pass

    threading.Thread(target=_job, daemon=True).start()
