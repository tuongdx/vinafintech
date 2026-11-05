"""Router cung cấp dữ liệu cho TradingView Advanced Charts.

Tất cả dữ liệu đều lấy từ VinaFintech (qua chart_service -> vnstock_service -> Simplize).
Frontend TradingView Datafeed gọi các endpoint này; KHÔNG gọi thẳng Simplize.

Endpoints:
    GET /api/chart/config    -> cấu hình (onReady)
    GET /api/chart/symbols   -> resolveSymbol
    GET /api/chart/history   -> getBars (phân trang theo from/to)
    GET /api/chart/search    -> searchSymbols
    GET /api/chart/quote     -> quote realtime (cập nhật candle hiện tại)
    GET /api/chart/time      -> getServerTime
"""
from fastapi import APIRouter, Query

from app.services.chart_service import (
    get_chart_config,
    get_chart_history,
    get_chart_library_status,
    get_chart_quote,
    get_server_time,
    resolve_chart_symbol,
    search_chart_symbols,
)

router = APIRouter(prefix="/chart", tags=["chart"])


@router.get("/config")
def chart_config():
    return get_chart_config()


@router.get("/symbols")
def chart_symbols(symbol: str):
    return resolve_chart_symbol(symbol)


@router.get("/history")
def chart_history(
    symbol: str,
    interval: str = "1D",
    from_: int = Query(0, alias="from"),
    to: int = 0,
    limit: int = 0,
    countback: int = 0,
):
    # TradingView gửi tham số `from` (từ khóa Python) -> dùng alias "from"
    from_ts = from_ if from_ else 0
    return get_chart_history(
        symbol,
        interval=interval,
        from_ts=from_ts,
        to_ts=to,
        limit=limit,
        countback=countback,
    )


@router.get("/search")
def chart_search(query: str, limit: int = 20):
    return search_chart_symbols(query, limit=limit)


@router.get("/quote")
def chart_quote(symbol: str):
    return get_chart_quote(symbol)


@router.get("/library-status")
def chart_library_status():
    return get_chart_library_status()


@router.get("/time")
def chart_time():
    return {"serverTime": get_server_time()}
