from fastapi import APIRouter
from fastapi.responses import Response
import re

from app.services import dividend_adjust
from app.services.home_opportunity_service import get_home_opportunities
from app.services.opportunity_service import get_top_opportunities
from app.services.support_resistance import sr_service
from app.services.vnstock_service import (
    filter_stocks,
    get_company_summary,
    get_company_reports,
    get_dividends,
    get_exchange_chart,
    get_financials,
    get_foreign_trading,
    get_history,
    get_index_detail,
    get_indices,
    get_insider_trades,
    get_market_dashboard,
    get_market_map,
    get_market_overview,
    get_order_book,
    get_peer_comparison,
    get_profit_analysis,
    get_recent_trades,
    get_realtime_exchange_board,
    get_quote,
    get_shareholder_structure,
    get_stock_discover_page,
    get_stock_fundamentals,
    get_stock_lists,
    get_stock_rankings_live,
    get_stock_screener_page,
    get_top_stocks,
    get_watchlist,
    render_candlestick_svg,
    search_stocks,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search")
def search(q: str):
    return search_stocks(q)


@router.get("/{symbol}/chart-svg")
def chart_svg(symbol: str, period: str = "3m"):
    """Ảnh biểu đồ nến (SVG) của 1 cổ phiếu — để TuniAI nhúng vào câu trả lời."""
    svg = render_candlestick_svg(symbol, period=period)
    if not svg:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Không đủ dữ liệu để vẽ biểu đồ")
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/{symbol}/quote")
def quote(symbol: str):
    return get_quote(symbol)


@router.get("/{symbol}/history")
def history(symbol: str, interval: str = "1d", period: str = "1y"):
    return get_history(symbol, interval=interval, period=period)


@router.get("/{symbol}/financials")
def financials(symbol: str):
    return get_financials(symbol)


@router.get("/{symbol}/insider-trades")
def insider_trades(symbol: str):
    return get_insider_trades(symbol)


@router.get("/{symbol}/shareholders")
def shareholders(symbol: str):
    return get_shareholder_structure(symbol)


@router.get("/{symbol}/reports")
def reports(symbol: str, category: str = "financial"):
    return get_company_reports(symbol, category=category)


@router.get("/{symbol}/dividends")
def dividends(symbol: str):
    return get_dividends(symbol)


@router.get("/{symbol}/sr-zones")
def sr_zones(symbol: str, period: str = "1y"):
    """Các vùng Hỗ trợ/Kháng cự (dải giá + độ mạnh) tính bằng SupportResistanceService."""
    candles = get_history(symbol, interval="1d", period=period)
    quote = get_quote(symbol)
    price = None
    if quote and quote.get("valid"):
        try:
            price = float(quote.get("price") or 0) or None
        except (TypeError, ValueError):
            price = None
    return sr_service.detect_zones((symbol or "").upper().strip(), candles, current_price=price, timeframe="1D")


@router.get("/{symbol}/fundamentals")
def fundamentals(symbol: str):
    return get_stock_fundamentals(symbol)


@router.get("/{symbol}/company-summary")
def company_summary(symbol: str):
    return get_company_summary(symbol)


@router.get("/{symbol}/order-book")
def order_book(symbol: str):
    return get_order_book(symbol)


@router.get("/{symbol}/trades")
def trades(symbol: str, limit: int = 50):
    return get_recent_trades(symbol, limit=limit)


@router.get("/{symbol}/peers")
def peers(symbol: str, limit: int = 10):
    return get_peer_comparison(symbol, limit=limit)


@router.get("/{symbol}/profit-analysis")
def profit_analysis(symbol: str, quarters: int = 12):
    return get_profit_analysis(symbol, quarters=quarters)


@router.get("/indices")
def indices():
    return get_indices()


@router.get("/market-overview")
def market_overview():
    return get_market_overview()


@router.get("/index-detail")
def index_detail(key: str, period: str = "1y"):
    return get_index_detail(key, period=period)


@router.get("/market-map")
def market_map():
    return get_market_map()


@router.get("/market-dashboard")
def market_dashboard():
    """Toàn cảnh thị trường (dashboard trang 'Thị trường'): chỉ số, độ rộng, dòng tiền, ngành, radar."""
    return get_market_dashboard()


@router.get("/realtime-board")
def realtime_board(exchanges: str = "HOSE,HNX,UPCOM"):
    parsed = [item.strip() for item in exchanges.split(",") if item.strip()]
    return get_realtime_exchange_board(parsed)


@router.get("/watchlist")
def watchlist():
    return get_watchlist()


@router.get("/top-stocks")
def top_stocks(tab: str = "breakout", period: str = "1w"):
    return get_top_stocks(tab=tab, period=period)


@router.get("/foreign-trading")
def foreign_trading(type: str = "foreign", period: str = "1D"):
    return get_foreign_trading(type, period=period)


@router.get("/exchange-chart")
def exchange_chart(exchange: str = "HOSE", period: str = "5d"):
    return get_exchange_chart(exchange, period=period)


@router.get("/stock-lists")
def stock_lists():
    """Toàn bộ vũ trụ cổ phiếu kèm nhóm (VN30/HNX30/HOSE/HNX/UPCOM/Ngành) cho sidebar fullscreen."""
    return get_stock_lists()


@router.get("/stock-screener")
def stock_screener():
    """Toàn bộ screener kèm điểm TuniX + nhóm 'Cổ phiếu nổi bật' cho trang Cổ phiếu."""
    return get_stock_screener_page()


@router.get("/upcoming-dividends")
def upcoming_dividends(symbols: str = ""):
    """Cổ tức SẮP chia của danh sách mã — PUBLIC, không cần đăng nhập.

    Dùng cho nhãn "Sắp chia cổ tức" ở mọi bảng trong toàn site.
    Trả {MÃ: {date, ex_date, pay_date, cash, stock_pct, label}} — chỉ gồm mã đang trong
    cửa sổ hiển thị: **5 ngày trước ngày GDKHQ → hết ngày GDKHQ** (xem
    `dividend_adjust._in_upcoming_window`); hết ngày GDKHQ là tự mất.
    CHỈ đọc dữ liệu đã cache (RAM/.cache) để trả về nhanh; mã chưa có được nạp nền.
    """
    syms = [s for s in re.split(r"[,\s]+", symbols or "") if s][:150]
    return dividend_adjust.upcoming_for(syms)


@router.get("/stock-rankings")
def stock_rankings():
    """Bảng xếp hạng TuniX REALTIME (5 bảng × 8 dòng) cho block "Xếp hạng TuniX"
    ở trang Khám phá: giá/% thay đổi lấy từ bảng giá, endpoint nhẹ để poll định kỳ."""
    return get_stock_rankings_live()


@router.get("/stock-discover")
def stock_discover():
    """Trang 'Khám phá': cơ hội nổi bật hôm nay, chiến lược, bản đồ cơ hội, xếp hạng TuniX."""
    data = get_stock_discover_page()
    try:
        opp = get_top_opportunities()
        if opp.get("ok"):
            # Thay danh sách featured tĩnh (4 mã cố định) bằng engine động + trạng thái thị trường.
            data["featured"] = opp.get("items") or []
            data["market_status"] = opp.get("market")
    except Exception as exc:  # không làm hỏng trang nếu engine lỗi
        import logging
        logging.getLogger("tunix.opportunity").warning("stock-discover: bỏ qua engine opportunity: %s", exc)
    return data


@router.get("/opportunities")
def opportunities():
    """Cơ hội nổi bật hôm nay: trạng thái thị trường + tối đa 11 mã có Opportunity Score."""
    return get_top_opportunities()


@router.get("/new-opportunities")
def new_opportunities():
    """Block "Cơ hội mới" (trang chủ): 3 nhóm cổ phiếu (tăng trưởng / định giá / tài chính
    lành mạnh) tự cập nhật theo dữ liệu thị trường — snapshot dựng lại vào 12h trưa (giờ VN)
    hằng ngày. Trang Khám phá vẫn dùng `/stock-discover` (dữ liệu tươi)."""
    return get_home_opportunities()


@router.get("/filter")
def filter_api(
    industry: str | None = None,
    basic: str | None = None,
    valuation: str | None = None,
    risk: str | None = None,
):
    return filter_stocks(industry=industry, basic=basic, valuation=valuation, risk=risk)
