"""PortfolioPerformanceService — tính hiệu quả danh mục đầu tư cho trang Danh mục.

Dữ liệu hiện tại của hệ thống mới có bảng portfolio_holdings (symbol, quantity,
avg_price, created_at) — chưa có sổ lệnh mua/bán, cổ tức, phí giao dịch hay dòng
tiền nạp/rút. Do đó:
  - Tổng vốn đầu tư  = giá vốn của các vị thế đang nắm giữ (Σ số lượng × giá vốn).
  - Tổng lãi/lỗ      = unrealized (giá trị thị trường − giá vốn) của vị thế đang nắm giữ.
  - realized / dividends / fees = 0 (đã tách tham số `transactions` sẵn để nối sổ
    lệnh trong tương lai mà không phải sửa API).

Thiết kế dễ mở rộng sau này: XIRR, time-weighted return, so sánh VN-Index / VN30,
hiệu suất theo ngày/tháng/năm, benchmark theo ngành.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any, Iterable


def _f(x: Any) -> float:
    """Chuyển an toàn sang số."""
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_date(x: Any) -> date | None:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if x:
        try:
            return datetime.fromisoformat(str(x)).date()
        except (TypeError, ValueError):
            return None
    return None


def calculate_portfolio_value(holdings: Iterable[dict[str, Any]]) -> float:
    """Giá trị hiện tại = Σ (số lượng đang nắm giữ × giá thị trường)."""
    return round(sum(_f(h.get("market_value")) for h in holdings), 2)


def calculate_total_invested(
    holdings: Iterable[dict[str, Any]],
    transactions: Iterable[dict[str, Any]] | None = None,
) -> float:
    """Tổng vốn thực tế đã bỏ vào.

    Nếu có sổ lệnh (`transactions`): mua + phí − thu hồi khi bán − cổ tức − rút.
    Chưa có sổ lệnh: dùng giá vốn các vị thế đang nắm giữ.
    """
    if transactions:
        invested = 0.0
        for t in transactions:
            side = str(t.get("side") or "").lower()
            amount = _f(t.get("amount"))
            fee = _f(t.get("fee"))
            if side == "buy":
                invested += amount + fee
            elif side == "sell":
                invested -= amount
            elif side == "dividend":
                invested -= amount
            elif side == "deposit":
                invested += amount
            elif side == "withdraw":
                invested -= amount
        return round(invested, 2)
    return round(sum(_f(h.get("cost")) for h in holdings), 2)


def calculate_realized_profit(transactions: Iterable[dict[str, Any]] | None = None) -> float:
    """Lợi nhuận đã hiện thực (từ bán / cổ tức). Chưa có sổ lệnh → 0."""
    return 0.0


def calculate_dividends_received(transactions: Iterable[dict[str, Any]] | None = None) -> float:
    """Tổng cổ tức tiền mặt đã nhận. Chưa có sổ lệnh → 0."""
    return 0.0


def calculate_unrealized_profit(holdings: Iterable[dict[str, Any]]) -> float:
    """Lợi nhuận chưa hiện thực = giá trị hiện tại − vốn đầu tư vị thế đang nắm giữ."""
    return round(calculate_portfolio_value(holdings) - calculate_total_invested(holdings), 2)


def calculate_total_return(
    holdings: Iterable[dict[str, Any]],
    transactions: Iterable[dict[str, Any]] | None = None,
) -> float:
    """Tổng lãi/lỗ = realized + cổ tức + unrealized."""
    realized = calculate_realized_profit(transactions)
    dividends = calculate_dividends_received(transactions)
    unrealized = calculate_unrealized_profit(holdings)
    return round(realized + dividends + unrealized, 2)


def calculate_return_percentage(
    holdings: Iterable[dict[str, Any]],
    transactions: Iterable[dict[str, Any]] | None = None,
) -> float:
    """Tỷ suất lợi nhuận (%) = Tổng lãi/lỗ ÷ Tổng vốn đầu tư × 100."""
    invested = calculate_total_invested(holdings, transactions)
    if invested <= 0:
        return 0.0
    total = calculate_total_return(holdings, transactions)
    return round(total / invested * 100.0, 2)


def calculate_start_date(holdings: Iterable[dict[str, Any]]) -> date | None:
    """Ngày bắt đầu đầu tư = created_at sớm nhất trong các vị thế."""
    dates: list[date] = []
    for h in holdings:
        d = _as_date(h.get("created_at"))
        if d:
            dates.append(d)
    return min(dates) if dates else None


def format_duration(start: date | None, end: date | None = None) -> str:
    """Thời gian đầu tư dạng '5 tháng 8 ngày' (từ start đến end, mặc định hôm nay)."""
    if not start:
        return ""
    end = end or date.today()
    if end < start:
        end = start
    months = (end.year - start.year) * 12 + (end.month - start.month)
    days = end.day - start.day
    if days < 0:
        months -= 1
        prev = date(end.year, end.month, 1)
        days += calendar.monthrange(prev.year, prev.month)[1]
    parts: list[str] = []
    if months > 0:
        parts.append(f"{months} tháng")
    if days > 0 or not parts:
        parts.append(f"{days} ngày")
    return " ".join(parts)


def _best_worst(holdings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Mã sinh lời tốt nhất / giảm mạnh nhất (theo % so giá vốn)."""
    hlist = list(holdings)
    if not hlist:
        return {"best": None, "worst": None}
    best = max(hlist, key=lambda h: _f(h.get("pnl_pct")))
    worst = min(hlist, key=lambda h: _f(h.get("pnl_pct")))
    return {
        "best": {
            "symbol": str(best.get("symbol") or ""),
            "pnl_pct": round(_f(best.get("pnl_pct")), 2),
        },
        "worst": {
            "symbol": str(worst.get("symbol") or ""),
            "pnl_pct": round(_f(worst.get("pnl_pct")), 2),
        },
    }


def build_summary(
    holdings: Iterable[dict[str, Any]],
    transactions: Iterable[dict[str, Any]] | None = None,
    quotes_ok: int = 0,
    total_quotes: int = 0,
) -> dict[str, Any]:
    """Tổng hợp toàn bộ chỉ số hiệu quả danh mục cho block 'Tổng lãi/lỗ danh mục'."""
    holdings = list(holdings or [])
    value = calculate_portfolio_value(holdings)
    invested = calculate_total_invested(holdings, transactions)
    total_return = calculate_total_return(holdings, transactions)
    return_pct = calculate_return_percentage(holdings, transactions)
    start = calculate_start_date(holdings)
    bw = _best_worst(holdings)
    return {
        "has_data": bool(holdings),
        "total_invested": invested,
        "current_value": value,
        "total_pnl": total_return,
        "return_pct": return_pct,
        "start_date": start.isoformat() if start else None,
        "duration_text": format_duration(start) if start else "",
        "holding_count": len(holdings),
        "best": bw["best"],
        "worst": bw["worst"],
        "quotes_ok": quotes_ok,
        "total_quotes": total_quotes,
    }
