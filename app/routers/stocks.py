from fastapi import APIRouter

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
    get_market_map,
    get_market_overview,
    get_order_book,
    get_peer_comparison,
    get_profit_analysis,
    get_recent_trades,
    get_realtime_exchange_board,
    get_quote,
    get_shareholder_structure,
    get_stock_fundamentals,
    get_stock_lists,
    get_top_stocks,
    get_watchlist,
    search_stocks,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search")
def search(q: str):
    return search_stocks(q)


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


@router.get("/filter")
def filter_api(
    industry: str | None = None,
    basic: str | None = None,
    valuation: str | None = None,
    risk: str | None = None,
):
    return filter_stocks(industry=industry, basic=basic, valuation=valuation, risk=risk)
