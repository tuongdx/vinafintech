"""Giá Vàng/Bạc — API công khai.

Dữ liệu qua backend (giavang.org cho vàng trong nước, Yahoo Finance cho vàng/bạc thế
giới) — frontend không gọi nguồn ngoài trực tiếp. Chỉ HIỂN THỊ: bảng giá trong nước,
giá thế giới, biểu đồ, so sánh. Không mua/bán, không công cụ tính.
"""
from fastapi import APIRouter, Query

from app.services import gold_service as svc

router = APIRouter(prefix="/gold", tags=["gold"])


@router.get("/domestic")
def get_domestic():
    """Bảng giá vàng trong nước (SJC, DOJI, PNJ, Bảo Tín Minh Châu, ...) — Miếng/Nhẫn mua-bán."""
    return svc.get_domestic()


@router.get("/world")
def get_world():
    """Vàng (XAU) & Bạc (XAG) thế giới — giá USD/ounce + thay đổi 24h/1W/1M/1Y."""
    return svc.get_world()


@router.get("/chart")
def get_chart(
    period: str = Query(default="1W", description="1D/1W/1M/3M/1Y/5Y"),
    metal: str = Query(default="xau", description="xau (mặc định) hoặc xag"),
):
    """Chuỗi giá cho biểu đồ (mặc định: vàng thế giới GC=F)."""
    return svc.get_chart(period=period, metal=metal)
