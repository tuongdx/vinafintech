"""Lãi ngân hàng — API công khai (Giai đoạn 1, dữ liệu tham khảo tổng hợp).

Các endpoint chỉ ĐỌC (meta/rates/estimate/compare) — không cần đăng nhập.
Giai đoạn 2: backend sẽ tự import từ nguồn chính thức vào DB, API giữ nguyên.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import bank_rate_service as svc

router = APIRouter(prefix="/bank-rates", tags=["bank-rates"])


@router.get("/meta")
def get_meta(db: Session = Depends(get_db)):
    """Danh mục: nguồn dữ liệu, lần cập nhật cuối, kỳ hạn, hình thức, danh sách ngân hàng."""
    return svc.meta(db)


@router.get("/rates")
def get_rates(
    bank: str | None = Query(default=None, description="bank_code, VD: VCB"),
    channel: str | None = Query(default=None, description="online | counter"),
    term: int | None = Query(default=None, description="kỳ hạn tháng, VD: 12"),
    customer: str | None = Query(default=None, description="đối tượng khách hàng"),
    sort: str = Query(default="bank", description="bank | rate_desc | rate_asc"),
    db: Session = Depends(get_db),
):
    """Danh sách lãi suất theo bộ lọc & sắp xếp."""
    return svc.list_rates(db, bank_code=bank, channel=channel, term=term, customer_type=customer, sort=sort)


@router.get("/estimate")
def get_estimate(
    amount: float = Query(..., gt=0, description="Số tiền gửi (VNĐ)"),
    bank: str = Query(..., description="bank_code"),
    term: int = Query(..., description="kỳ hạn tháng"),
    channel: str = Query(default="online", description="online | counter"),
    db: Session = Depends(get_db),
):
    """Tính tiền lãi dự kiến & tổng nhận khi đáo hạn (lãi đơn, trả lãi cuối kỳ)."""
    return svc.estimate(db, amount, bank, term, channel)


@router.get("/compare")
def get_compare(
    amount: float = Query(..., gt=0, description="Số tiền gửi (VNĐ)"),
    term: int = Query(..., description="kỳ hạn tháng"),
    channel: str = Query(default="online", description="online | counter"),
    db: Session = Depends(get_db),
):
    """So sánh lãi suất & tiền lãi giữa các ngân hàng cùng kỳ hạn/hình thức (sắp theo lãi giảm dần)."""
    return svc.compare(db, amount, term, channel)
