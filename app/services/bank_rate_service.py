"""Lãi ngân hàng — service (Giai đoạn 1).

Tách tầng để chuẩn bị Giai đoạn 2:
  [Ngân hàng / nguồn tự động] → TuniX Backend (ghi bảng bank_interest_rates) → API → UI
Logic tính toán tách hàm thuần (calc_interest...) để dễ kiểm thử.

LƯU Ý DỮ LIỆU: Giai đoạn 1 chưa có nguồn cập nhật tự động → dùng bộ dữ liệu TỔNG HỢP THAM KHẢO
seed vào DB, hiển thị rõ "không realtime", không tự cho là lãi suất chính thức của ngân hàng.
Khi có nguồn chính thức (Giai đoạn 2) chỉ cần thay seed/import — không đổi API/UI.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import BankInterestRate

TERMS = [1, 3, 6, 12, 18, 24]
CHANNEL_LABELS = {"online": "Gửi Online", "counter": "Gửi tại quầy"}
DEFAULT_CUSTOMER = "Khách hàng cá nhân"
SOURCE = "Tổng hợp tham khảo (Giai đoạn 1)"

# Dữ liệu tham khảo: (code, tên, online %, counter %) cho các kỳ hạn TERMS.
# Bộ này CHỈ LÀ MẪU TỔNG HỢP để demo; khi có nguồn chính thức sẽ thay bằng dữ liệu import.
_SEED = [
    # bank_code, bank_name,        online rates,                          counter rates
    ("VCB", "Vietcombank",         [3.0, 3.6, 4.3, 4.8, 4.9, 5.0],       [2.8, 3.4, 4.1, 4.6, 4.7, 4.8]),
    ("BID", "BIDV",                [3.1, 3.7, 4.4, 4.8, 5.0, 5.1],       [2.9, 3.5, 4.2, 4.6, 4.8, 4.9]),
    ("CTG", "VietinBank",          [3.2, 3.8, 4.4, 4.9, 5.0, 5.1],       [3.0, 3.6, 4.2, 4.7, 4.8, 4.9]),
    ("AGB", "Agribank",            [2.9, 3.5, 4.2, 4.7, 4.8, 4.9],       [2.9, 3.5, 4.2, 4.7, 4.8, 4.9]),
    ("TCB", "Techcombank",         [3.4, 4.0, 4.7, 5.2, 5.3, 5.4],       [3.1, 3.7, 4.3, 4.8, 5.0, 5.1]),
    ("MBB", "MB Bank",             [3.3, 3.9, 4.6, 5.1, 5.3, 5.4],       [3.0, 3.6, 4.2, 4.8, 5.0, 5.1]),
    ("ACB", "ACB",                 [3.2, 3.8, 4.5, 5.0, 5.2, 5.3],       [3.0, 3.6, 4.3, 4.8, 5.0, 5.1]),
    ("VPB", "VPBank",              [3.5, 4.1, 4.8, 5.3, 5.4, 5.5],       [3.1, 3.7, 4.4, 4.9, 5.1, 5.2]),
    ("HDB", "HDBank",              [3.6, 4.2, 4.9, 5.4, 5.6, 5.7],       [3.2, 3.8, 4.5, 5.0, 5.2, 5.3]),
    ("SHB", "SHB",                 [3.4, 4.0, 4.7, 5.2, 5.4, 5.5],       [3.0, 3.6, 4.3, 4.8, 5.0, 5.1]),
]


def _build_seed_rows() -> list[BankInterestRate]:
    rows: list[BankInterestRate] = []
    for code, name, online, counter in _SEED:
        for i, term in enumerate(TERMS):
            for channel, arr in (("online", online), ("counter", counter)):
                rows.append(
                    BankInterestRate(
                        bank_code=code,
                        bank_name=name,
                        term_months=term,
                        interest_rate=arr[i],
                        channel=channel,
                        customer_type=DEFAULT_CUSTOMER,
                        min_amount=None,
                        conditions=None,
                        source=SOURCE,
                    )
                )
    return rows


def seed_rates(db: Session) -> int:
    """Seed dữ liệu mẫu nếu bảng đang trống (idempotent). Trả về số dòng đã thêm."""
    if db.query(BankInterestRate).count() > 0:
        return 0
    rows = _build_seed_rows()
    db.add_all(rows)
    db.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Tính toán (thuần — dễ kiểm thử)
# ---------------------------------------------------------------------------
def calc_interest(amount: float, rate_pct: float, months: int) -> dict:
    """Tiền lãi DỰ KIẾN theo lãi đơn (trả lãi cuối kỳ).

    lãi = gốc × lãi suất%/năm × (số tháng/12)
    """
    amount = float(amount or 0)
    rate = float(rate_pct or 0)
    months = max(0, int(months or 0))
    if amount <= 0 or rate < 0 or months <= 0:
        return {"interest": 0, "total": int(round(amount)), "rate_pct": rate, "months": months}
    interest = amount * rate / 100.0 * (months / 12.0)
    interest = int(round(interest))
    return {
        "principal": int(round(amount)),
        "interest": interest,
        "total": int(round(amount)) + interest,
        "rate_pct": rate,
        "months": months,
    }


# ---------------------------------------------------------------------------
# Truy vấn DB
# ---------------------------------------------------------------------------
def _row_out(r: BankInterestRate) -> dict:
    return {
        "id": r.id,
        "bank_code": r.bank_code,
        "bank_name": r.bank_name,
        "term": r.term_months,
        "interest_rate": r.interest_rate,
        "channel": r.channel,
        "channel_label": CHANNEL_LABELS.get(r.channel, r.channel),
        "customer_type": r.customer_type,
        "min_amount": r.min_amount,
        "conditions": r.conditions,
        "source": r.source,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def meta(db: Session) -> dict:
    rows = db.query(BankInterestRate).all()
    banks: list[dict] = []
    seen: set[str] = set()
    channels: set[str] = set()
    last = None
    for r in rows:
        channels.add(r.channel)
        if r.bank_code not in seen:
            seen.add(r.bank_code)
            banks.append({"code": r.bank_code, "name": r.bank_name})
        if r.updated_at and (last is None or r.updated_at > last):
            last = r.updated_at
    banks.sort(key=lambda b: b["name"])
    return {
        "source": SOURCE,
        "updated_at": last.isoformat() if last else None,
        "terms": list(TERMS),
        "channels": sorted(channels),
        "customer_types": [DEFAULT_CUSTOMER],
        "banks": banks,
    }


def list_rates(
    db: Session,
    bank_code: str | None = None,
    channel: str | None = None,
    term: int | None = None,
    customer_type: str | None = None,
    sort: str = "bank",
) -> list[dict]:
    q = db.query(BankInterestRate)
    if bank_code:
        q = q.filter(BankInterestRate.bank_code == bank_code)
    if channel:
        q = q.filter(BankInterestRate.channel == channel)
    if term:
        q = q.filter(BankInterestRate.term_months == int(term))
    if customer_type:
        q = q.filter(BankInterestRate.customer_type == customer_type)
    rows = q.all()
    out = [_row_out(r) for r in rows]
    # sort: bank (tên A→Z) | rate_desc | rate_asc
    if sort == "rate_desc":
        out.sort(key=lambda x: (-x["interest_rate"], x["bank_name"]))
    elif sort == "rate_asc":
        out.sort(key=lambda x: (x["interest_rate"], x["bank_name"]))
    else:
        out.sort(key=lambda x: (x["bank_name"], x["term"]))
    return out


def estimate(
    db: Session,
    amount: float,
    bank_code: str,
    term: int,
    channel: str = "online",
    customer_type: str | None = None,
) -> dict:
    q = db.query(BankInterestRate).filter(
        BankInterestRate.bank_code == bank_code,
        BankInterestRate.term_months == int(term),
        BankInterestRate.channel == channel,
    )
    if customer_type:
        q = q.filter(BankInterestRate.customer_type == customer_type)
    row = q.first()
    if not row:
        return {"found": False, "error": "Chưa có lãi suất cho lựa chọn này."}
    calc = calc_interest(amount, row.interest_rate, int(term))
    result = _row_out(row)
    result.update(
        {
            "found": True,
            "amount": int(round(float(amount or 0))),
            "expected_interest": calc["interest"],
            "total_receive": calc["total"],
        }
    )
    return result


def compare(db: Session, amount: float, term: int, channel: str = "online") -> dict:
    rows = (
        db.query(BankInterestRate)
        .filter(BankInterestRate.term_months == int(term), BankInterestRate.channel == channel)
        .all()
    )
    items = []
    for r in rows:
        calc = calc_interest(amount, r.interest_rate, int(term))
        items.append(
            {
                "bank_code": r.bank_code,
                "bank_name": r.bank_name,
                "channel": r.channel,
                "channel_label": CHANNEL_LABELS.get(r.channel, r.channel),
                "term": r.term_months,
                "interest_rate": r.interest_rate,
                "expected_interest": calc["interest"],
                "total_receive": calc["total"],
            }
        )
    items.sort(key=lambda x: (-x["interest_rate"], x["bank_name"]))
    return {
        "amount": int(round(float(amount or 0))),
        "term": int(term),
        "channel": channel,
        "items": items,
        "count": len(items),
    }
