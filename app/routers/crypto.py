"""Crypto — API công khai (dữ liệu Binance qua backend, frontend không gọi Binance trực tiếp)."""
from fastapi import APIRouter, Query

from app.services import crypto_service as svc

router = APIRouter(prefix="/crypto", tags=["crypto"])


@router.get("/markets")
def get_markets():
    """TOÀN BỘ coin spot (cặp USDT) — giá live Binance (1 request tổng, cache 6s).

    Trả hết trong 1 lần (~698 mã, ~22 KB gzip); frontend hiện dần 20 mã và nạp thêm
    10 mã mỗi lần cuộn tới đáy nên không cần tham số phân trang.
    """
    return svc.get_markets()


@router.get("/coin/{symbol}")
def get_coin(symbol: str):
    """Thông tin cơ bản của 1 coin: TÊN đầy đủ + đoạn GIỚI THIỆU TIẾNG VIỆT.

    Nguồn gốc: CoinGecko (Binance không có tên/giới thiệu); bản tiếng Việt do AI dịch
    (DeepSeek) — dịch ở luồng nền nên lần xem ĐẦU trả `translating=true`, client chờ
    vài giây rồi hỏi lại (bản dịch được lưu RAM + DB nên các lần sau trả ngay).
    """
    return svc.get_coin_intro(symbol)


@router.get("/klines")
def get_klines(
    symbol: str = Query(..., description="Cặp giao dịch, VD: BTCUSDT (hoặc BTC)"),
    interval: str = Query(default="1d", description="Binance interval: 1m/5m/15m/1h/4h/1d/1w/1M"),
    limit: int = Query(default=90, ge=1, le=1000),
    start_time: int | None = Query(default=None, description="Mốc bắt đầu (ms) — tuỳ chọn"),
    end_time: int | None = Query(default=None, description="Mốc kết thúc (ms) — dùng để tải thêm lịch sử cũ"),
):
    """Nến giá Binance cho biểu đồ (kèm start_time/end_time để lazy-load lịch sử cũ)."""
    return svc.get_klines(symbol, interval, limit, start_time, end_time)
