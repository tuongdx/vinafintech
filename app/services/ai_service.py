"""TuniAI — Trợ lý AI chứng khoán (Phase 1).

Chat với DeepSeek kèm **Tool Calling**. Các tool gọi trực tiếp hàm service của TuniX
(vnstock_service) + truy vấn DB tin tức — không đi qua HTTP để tránh overhead.

Kiến trúc (theo yêu cầu):
    User → TuniX Chat → TuniAI (DeepSeek) --Tool Calling--> Stock/News/Market API
        → Dữ liệu TuniX → DeepSeek phân tích → Câu trả lời
"""
import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

import httpx

from app.core.config import settings
from app.db import SessionLocal
from app.models import News, NewsStatusEnum
from app.services import vnstock_service as vns

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
TOOLS: list[dict[str, Any]] = []
_HANDLERS: dict[str, Any] = {}


def _tool(name: str, description: str, parameters: dict[str, Any]):
    def deco(fn):
        TOOLS.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
        _HANDLERS[name] = fn
        return fn

    return deco


def _cap(value: Any, max_str: int = 400, max_items: int = 25, depth: int = 0) -> Any:
    """Giới hạn kích thước dữ liệu trả về tool để kiểm soát token."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth > 4:
        return None
    if isinstance(value, str):
        return value if len(value) <= max_str else value[: max_str] + "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[: max_items]:
            out[k] = _cap(v, max_str, max_items, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_cap(v, max_str, max_items, depth + 1) for v in list(value)[: max_items]]
    return value


def _clean_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ===========================================================================
# Tools — Cổ phiếu
# ===========================================================================
@_tool(
    "search_stocks",
    "Tìm kiếm mã cổ phiếu theo mã hoặc tên công ty (ví dụ: 'HDB', 'Techcombank'). Trả về danh sách mã khớp kèm tên và sàn. Dùng khi không chắc chắn về mã.",
    {"type": "object", "properties": {"query": {"type": "string", "description": "Từ khóa tìm kiếm (mã hoặc tên công ty)"}}, "required": ["query"]},
)
def _search_stocks(query: str) -> dict[str, Any]:
    rows = vns.search_stocks(query)
    return {
        "count": len(rows),
        "results": [
            {"symbol": r.get("symbol"), "name": r.get("name"), "exchange": r.get("exchange")}
            for r in rows[:15]
        ],
    }


@_tool(
    "get_quote",
    "Lấy giá cổ phiếu REALTIME hiện tại kèm thay đổi, khối lượng, P/E, P/B, vốn hóa, EPS, ROE, biến động 1 tuần/1 tháng/từ đầu năm. Dùng khi hỏi giá hoặc phân tích nhanh một mã.",
    {"type": "object", "properties": {"symbol": {"type": "string", "description": "Mã cổ phiếu (VD: HDB)"}}, "required": ["symbol"]},
)
def _get_quote(symbol: str) -> dict[str, Any]:
    q = vns.get_quote(symbol)
    if not q.get("valid"):
        return {"error": q.get("message", f"Không tìm thấy mã {symbol}")}
    keys = [
        "symbol", "name", "exchange", "price", "change", "percent_change", "reference_price",
        "open", "high", "low", "volume", "value", "as_of", "source",
        "market_cap", "book_value", "pe", "pb", "eps", "roe", "roa",
        "avg_volume_10d", "pct_1w", "pct_1m", "pct_ytd",
    ]
    return {k: q.get(k) for k in keys}


@_tool(
    "get_history",
    "Lịch sử giá OHLCV của một cổ phiếu theo kỳ (period: 1m/3m/6m/1y/5y). Trả về tóm tắt biến động + các phiên gần nhất. Dùng để phân tích xu hướng giá.",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Mã cổ phiếu (VD: HDB)"},
        "period": {"type": "string", "description": "Kỳ xem lịch sử (mặc định 1y)", "enum": ["1m", "3m", "6m", "1y", "5y"]},
    }, "required": ["symbol"]},
)
def _get_history(symbol: str, period: str = "1y") -> dict[str, Any]:
    bars = vns.get_history(symbol, period=period)
    if not bars:
        return {"error": f"Không có dữ liệu lịch sử cho {symbol}"}
    closes = [b.get("close") for b in bars if isinstance(b.get("close"), (int, float))]
    vols = [b.get("volume") for b in bars if isinstance(b.get("volume"), (int, float))]
    change_pct = None
    if len(closes) >= 2 and closes[0]:
        change_pct = round((closes[-1] / closes[0] - 1) * 100, 2)
    return {
        "symbol": str(symbol).upper(),
        "period": period,
        "bars": len(bars),
        "first_close": closes[0] if closes else None,
        "last_close": closes[-1] if closes else None,
        "change_pct_over_period": change_pct,
        "high": max(closes) if closes else None,
        "low": min(closes) if closes else None,
        "avg_volume": round(sum(vols) / len(vols)) if vols else 0,
        "last_5_bars": bars[-5:],
    }


@_tool(
    "get_company_summary",
    "Tóm tắt hồ sơ công ty của một mã cổ phiếu (tên, ngành, sàn, mô tả hoạt động...). Dùng để giới thiệu công ty.",
    {"type": "object", "properties": {"symbol": {"type": "string", "description": "Mã cổ phiếu"}}, "required": ["symbol"]},
)
def _company_summary(symbol: str) -> dict[str, Any]:
    return _cap(vns.get_company_summary(symbol), max_str=600)


@_tool(
    "get_profit_analysis",
    "Lợi nhuận thực tế theo quý (BCTC kết quả kinh doanh): doanh thu, lợi nhuận gộp, lợi nhuận trước/sau thuế của một cổ phiếu trong các quý gần nhất. Dùng khi phân tích BCTC / tăng trưởng lợi nhuận.",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Mã cổ phiếu"},
        "quarters": {"type": "integer", "description": "Số quý muốn xem (4-12, mặc định 8)"},
    }, "required": ["symbol"]},
)
def _profit_analysis(symbol: str, quarters: int = 8) -> dict[str, Any]:
    return _cap(vns.get_profit_analysis(symbol, quarters=quarters), max_items=20)


@_tool(
    "get_company_reports",
    "Danh sách báo cáo/tài liệu của công ty: financial (báo cáo tài chính), analysis (báo cáo phân tích của công ty chứng khoán), annual (báo cáo thường niên), agm (nghị quyết ĐHĐCĐ), prospectus (bản cáo bạch).",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Mã cổ phiếu"},
        "category": {"type": "string", "enum": ["financial", "analysis", "annual", "agm", "prospectus"], "description": "Loại tài liệu (mặc định financial)"},
    }, "required": ["symbol"]},
)
def _company_reports(symbol: str, category: str = "financial") -> dict[str, Any]:
    return _cap(vns.get_company_reports(symbol, category=category), max_items=15)


@_tool(
    "get_shareholder_structure",
    "Cơ cấu cổ đông của một cổ phiếu (tỷ lệ Nhà nước / Nước ngoài / Khác, loại nhà đầu tư, danh sách cổ đông lớn).",
    {"type": "object", "properties": {"symbol": {"type": "string", "description": "Mã cổ phiếu"}}, "required": ["symbol"]},
)
def _shareholders(symbol: str) -> dict[str, Any]:
    return _cap(vns.get_shareholder_structure(symbol), max_items=20)


@_tool(
    "get_dividends",
    "Lịch sử trả cổ tức của một cổ phiếu (thời điểm, loại, tỷ lệ).",
    {"type": "object", "properties": {"symbol": {"type": "string", "description": "Mã cổ phiếu"}}, "required": ["symbol"]},
)
def _dividends(symbol: str) -> dict[str, Any]:
    return _cap(vns.get_dividends(symbol), max_items=15)


@_tool(
    "get_peer_comparison",
    "So sánh chỉ số (vốn hóa, P/E, biên lợi nhuận...) giữa một cổ phiếu với các cổ phiếu cùng ngành. Dùng khi hỏi 'so sánh mã'.",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Mã cổ phiếu"},
        "limit": {"type": "integer", "description": "Số công ty cùng ngành (mặc định 10)"},
    }, "required": ["symbol"]},
)
def _peers(symbol: str, limit: int = 10) -> dict[str, Any]:
    return _cap(vns.get_peer_comparison(symbol, limit=limit), max_items=15)


@_tool(
    "get_order_book",
    "Sổ lệnh (bên mua/bên bán, giá và khối lượng) của một cổ phiếu tại thời điểm hiện tại.",
    {"type": "object", "properties": {"symbol": {"type": "string", "description": "Mã cổ phiếu"}}, "required": ["symbol"]},
)
def _order_book(symbol: str) -> dict[str, Any]:
    return _cap(vns.get_order_book(symbol), max_items=12)


@_tool(
    "get_recent_trades",
    "Các giao dịch khớp lệnh gần nhất của một cổ phiếu (giá, khối lượng, thời gian).",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Mã cổ phiếu"},
        "limit": {"type": "integer", "description": "Số giao dịch (mặc định 20)"},
    }, "required": ["symbol"]},
)
def _recent_trades(symbol: str, limit: int = 20) -> dict[str, Any]:
    return _cap(vns.get_recent_trades(symbol, limit=limit), max_items=20)


# ===========================================================================
# Tools — Thị trường
# ===========================================================================
@_tool(
    "get_market_overview",
    "Tổng quan thị trường hiện tại (realtime): VNINDEX, VN30, Dow Jones, Bitcoin, giá vàng thế giới và trong nước.",
    {"type": "object", "properties": {}},
)
def _market_overview() -> dict[str, Any]:
    return _cap(vns.get_market_overview(), max_items=10)


@_tool(
    "get_top_stocks",
    "Top cổ phiếu nổi bật: breakout (đột biến khối lượng), foreign-buy (khối ngoại mua ròng), foreign-sell (khối ngoại bán ròng), break-high (vượt đỉnh), break-low (phá đáy). period: 1w/1M/3M/6M/1y.",
    {"type": "object", "properties": {
        "tab": {"type": "string", "enum": ["breakout", "foreign-buy", "foreign-sell", "break-high", "break-low"], "description": "Loại top (mặc định breakout)"},
        "period": {"type": "string", "description": "Kỳ (mặc định 1w)"},
    }},
)
def _top_stocks(tab: str = "breakout", period: str = "1w") -> dict[str, Any]:
    rows = vns.get_top_stocks(tab=tab, period=period)
    return {"count": len(rows), "results": [_cap(r, max_items=12) for r in rows[:15]]}


@_tool(
    "get_foreign_trading",
    "Dòng tiền khối ngoại (mua/bán ròng) theo mã hoặc toàn thị trường.",
    {"type": "object", "properties": {
        "type": {"type": "string", "description": "Loại: foreign (mặc định)"},
        "period": {"type": "string", "description": "Kỳ: 1D (mặc định)"},
    }},
)
def _foreign_trading(type: str = "foreign", period: str = "1D") -> dict[str, Any]:
    return _cap(vns.get_foreign_trading(type, period=period), max_items=15)


@_tool(
    "get_realtime_board",
    "Bảng giá realtime toàn thị trường (HOSE/HNX/UPCOM). Trả về thống kê số mã tăng/giảm + top tăng/giảm mạnh nhất. Dùng khi hỏi 'thị trường hôm nay thế nào'.",
    {"type": "object", "properties": {}},
)
def _realtime_board() -> dict[str, Any]:
    data = vns.get_realtime_exchange_board(["HOSE", "HNX", "UPCOM"])
    rows = data.get("data") or []

    def _pct(r: dict[str, Any]) -> float:
        v = r.get("percent_change")
        return v if isinstance(v, (int, float)) else 0.0

    ranked = sorted(rows, key=_pct, reverse=True)
    gainers = [
        {"symbol": r.get("symbol"), "price": r.get("price"), "percent_change": _pct(r)}
        for r in ranked[:10] if _pct(r) > 0
    ]
    losers = [
        {"symbol": r.get("symbol"), "price": r.get("price"), "percent_change": _pct(r)}
        for r in reversed(ranked[-10:]) if _pct(r) < 0
    ]
    return {
        "exchanges": data.get("exchanges"),
        "total_symbols": data.get("total_symbols"),
        "up": sum(1 for r in rows if _pct(r) > 0),
        "down": sum(1 for r in rows if _pct(r) < 0),
        "flat": sum(1 for r in rows if _pct(r) == 0),
        "top_gainers": gainers[:10],
        "top_losers": losers[:10],
        "as_of": next((r.get("as_of") for r in rows if r.get("as_of")), ""),
    }


@_tool(
    "filter_stocks",
    "Lọc/tìm cổ phiếu theo tiêu chí (AI Screener). Tham số: industry (slug ngành), basic (rat-tot|tot|trung-binh|chua-dat), valuation (du-bien-an-toan|chua-du-bien-an-toan), risk (thap|trung-binh|can-chu-y).",
    {"type": "object", "properties": {
        "industry": {"type": "string", "description": "Ngành (slug tiếng Việt, VD: tai-chinh, cong-nghe, bat-dong-san, tien-ich)"},
        "basic": {"type": "string", "enum": ["rat-tot", "tot", "trung-binh", "chua-dat"]},
        "valuation": {"type": "string", "enum": ["du-bien-an-toan", "chua-du-bien-an-toan"]},
        "risk": {"type": "string", "enum": ["thap", "trung-binh", "can-chu-y"]},
    }},
)
def _filter_stocks(**kw: Any) -> dict[str, Any]:
    allowed = {"industry", "basic", "valuation", "risk"}
    args = {k: v for k, v in kw.items() if k in allowed and v is not None}
    rows = vns.filter_stocks(**args)
    return {
        "count": len(rows),
        "results": [
            {
                "symbol": r.get("symbol") or r.get("ticker"),
                "name": r.get("name"),
                "sector": r.get("sectorName") or r.get("industryName") or "",
            }
            for r in rows[:25]
        ],
    }


# ===========================================================================
# Tools — Tin tức (trực tiếp từ DB TuniX)
# ===========================================================================
def _query_news(symbol: str = "", limit: int = 8) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 8), 20))
    sym = (symbol or "").strip().upper()
    db = SessionLocal()
    try:
        q = db.query(News).filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
        if sym:
            q = q.filter((News.tags.ilike(f"%{sym}%")) | (News.title.ilike(f"%{sym}%")))
        rows = q.order_by(News.id.desc()).limit(limit).all()
        return [
            {
                "id": n.id,
                "title": n.title,
                "summary": n.summary,
                "category": n.category or "tin-tuc",
                "tags": n.tags,
                "source": n.source,
                "date": str(n.created_at),
            }
            for n in rows
        ]
    finally:
        db.close()


def _fetch_news_by_id(news_id: int) -> News | None:
    db = SessionLocal()
    try:
        n = db.query(News).filter(News.id == int(news_id)).first()
        if not n or n.status not in (NewsStatusEnum.approved, NewsStatusEnum.published):
            return None
        return n
    finally:
        db.close()


@_tool(
    "get_news",
    "Tin tức thị trường mới nhất đã xuất bản trên TuniX. Có thể truyền symbol để lấy tin liên quan một mã cổ phiếu. Trả về tiêu đề, tóm tắt, thời gian.",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Mã cổ phiếu để lọc tin liên quan (tùy chọn)"},
        "limit": {"type": "integer", "description": "Số bài (mặc định 8)"},
    }},
)
def _news(symbol: str = "", limit: int = 8) -> dict[str, Any]:
    results = _query_news(symbol=symbol, limit=limit)
    return {"count": len(results), "results": results}


@_tool(
    "get_news_content",
    "Lấy nội dung chi tiết của một bài tin theo id (lấy id từ get_news). Dùng để đọc/tóm tắt bài viết.",
    {"type": "object", "properties": {"news_id": {"type": "integer", "description": "ID bài tin"}}, "required": ["news_id"]},
)
def _news_content(news_id: int) -> dict[str, Any]:
    n = _fetch_news_by_id(news_id)
    if not n:
        return {"error": "Không tìm thấy bài tin này"}
    return {
        "id": n.id,
        "title": n.title,
        "category": n.category or "tin-tuc",
        "date": str(n.created_at),
        "content": _clean_text(n.content)[:2500],
    }


# ===========================================================================
# DeepSeek client
# ===========================================================================
class AiConfigError(Exception):
    pass


class DeepSeekError(Exception):
    pass


SYSTEM_PROMPT = """Bạn là TuniAI — trợ lý AI phân tích chứng khoán Việt Nam của nền tảng TuniX.

QUY TẮC BẮT BUỘC:
1. Luôn dùng TOOL để lấy dữ liệu thực tế từ TuniX trước khi trả lời. TUYỆT ĐỐI không tự bịa số liệu, giá, chỉ số.
2. Nếu câu hỏi nhắc mã chưa rõ, hãy dùng search_stocks để tìm đúng mã trước.
3. Trả lời bằng TIẾNG VIỆT, ngắn gọn, có cấu trúc (gạch đầu dòng), kèm số liệu cụ thể và ghi chú thời điểm dữ liệu.
4. Phân biệt rõ: dữ liệu realtime (giá/bảng giá) vs dữ liệu báo cáo quá khứ (BCTC, lịch sử giá).
5. Không đưa khuyến nghị mua/bán cụ thể; chỉ phân tích khách quan, nêu rủi ro và nhắc người dùng tự quyết định.
6. Nếu tool trả lỗi hoặc không có dữ liệu, hãy nói rõ là không tìm thấy thay vì bịa.
7. Khi phân tích cổ phiếu hãy nêu: giá & biến động, định giá (P/E, P/B), tăng trưởng (lợi nhuận theo quý), rủi ro, tin tức liên quan."""


def _deepseek_url() -> str:
    base = (settings.deepseek_base_url or "https://api.deepseek.com").rstrip("/")
    return base + "/chat/completions"


def _deepseek_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


def _ensure_configured() -> None:
    if not settings.deepseek_api_key:
        raise AiConfigError("TuniAI chưa được cấu hình API key DeepSeek (DEEPSEEK_API_KEY).")


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages[-20:]:
        role = str(m.get("role") or "user")
        content = str(m.get("content") or "")
        if role in ("system", "user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


async def _run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if not handler:
        return {"error": f"Tool '{name}' không tồn tại"}
    return await asyncio.to_thread(handler, **args)


# ---------------------------------------------------------------------------
# Chat không streaming
# ---------------------------------------------------------------------------
async def chat(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Chạy vòng lặp tool-calling, trả về câu trả lời cuối cùng."""
    _ensure_configured()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + _normalize_messages(messages)
    tool_log: list[dict[str, Any]] = []
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(settings.ai_chat_max_turns):
            payload = {
                "model": settings.deepseek_model,
                "messages": msgs,
                "tools": TOOLS,
                "temperature": 0.3,
                "max_tokens": settings.ai_chat_max_tokens,
                "stream": False,
            }
            try:
                resp = await client.post(_deepseek_url(), json=payload, headers=_deepseek_headers())
            except Exception as exc:
                raise DeepSeekError(f"Không kết nối được DeepSeek: {exc}") from exc
            if resp.status_code == 401:
                raise AiConfigError("API key DeepSeek không hợp lệ.")
            if resp.status_code != 200:
                raise DeepSeekError(f"DeepSeek trả lỗi {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                reply = msg.get("content") or ""
                if choice.get("finish_reason") == "length":
                    reply += "\n\n⚠️ (Câu trả lời bị cắt do giới hạn độ dài — bạn hãy hỏi ngắn gọn hơn.)"
                return {"reply": reply, "tool_calls": tool_log}

            msgs.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": [
                        {"id": tc["id"], "type": "function", "function": tc["function"]}
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                tool_log.append({"name": name, "arguments": args})
                try:
                    result = await _run_tool(name, args)
                    content = json.dumps(result, ensure_ascii=False, default=str)
                except Exception as exc:
                    content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": content})

    return {
        "reply": "Xin lỗi, yêu cầu cần quá nhiều bước tra cứu. Bạn hãy hỏi cụ thể hơn nhé.",
        "tool_calls": tool_log,
    }


# ---------------------------------------------------------------------------
# Chat streaming (SSE)
# ---------------------------------------------------------------------------
async def stream_chat(messages: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
    """Stream token + sự kiện tool qua generator. Mỗi event là dict:
    {'type':'delta'|'tool'|'done'|'error', ...}
    """
    _ensure_configured()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + _normalize_messages(messages)
    tool_log: list[dict[str, Any]] = []
    timeout = httpx.Timeout(120.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(settings.ai_chat_max_turns):
            payload = {
                "model": settings.deepseek_model,
                "messages": msgs,
                "tools": TOOLS,
                "temperature": 0.3,
                "max_tokens": settings.ai_chat_max_tokens,
                "stream": True,
            }
            collected_content = ""
            tool_acc: dict[int, dict[str, str]] = {}
            finish_reason: str | None = None

            try:
                async with client.stream("POST", _deepseek_url(), json=payload, headers=_deepseek_headers()) as resp:
                    if resp.status_code == 401:
                        raise AiConfigError("API key DeepSeek không hợp lệ.")
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise DeepSeekError(f"DeepSeek trả lỗi {resp.status_code}: {body[:200]}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except Exception:
                            continue
                        if not chunk.get("choices"):
                            continue
                        choice = chunk["choices"][0]
                        if choice.get("finish_reason"):
                            finish_reason = choice.get("finish_reason")
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            collected_content += delta["content"]
                            yield {"type": "delta", "content": delta["content"]}
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                acc["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                acc["name"] += fn["name"]
                            if fn.get("arguments"):
                                acc["arguments"] += fn["arguments"]
            except AiConfigError:
                raise
            except Exception as exc:
                yield {"type": "error", "content": f"Lỗi khi gọi DeepSeek: {str(exc)[:200]}"}
                return

            tool_calls: list[dict[str, Any]] = []
            for idx in sorted(tool_acc):
                acc = tool_acc[idx]
                if acc["name"]:
                    tool_calls.append(
                        {
                            "id": acc["id"] or f"call_{idx}",
                            "type": "function",
                            "function": {"name": acc["name"], "arguments": acc["arguments"]},
                        }
                    )

            if not tool_calls:
                yield {"type": "done", "content": collected_content, "tool_calls": tool_log, "truncated": finish_reason == "length"}
                return

            msgs.append(
                {
                    "role": "assistant",
                    "content": collected_content or None,
                    "tool_calls": [
                        {"id": tc["id"], "type": "function", "function": tc["function"]}
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                tool_log.append({"name": name, "arguments": args})
                yield {"type": "tool", "name": name, "arguments": args}
                try:
                    result = await _run_tool(name, args)
                    content = json.dumps(result, ensure_ascii=False, default=str)
                except Exception as exc:
                    content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": content})

    yield {"type": "error", "content": "Yêu cầu cần quá nhiều bước tra cứu."}


# ---------------------------------------------------------------------------
# Nhận định thị trường (ticker dưới header) — 1 câu tóm tắt dòng tiền VNINDEX
# ---------------------------------------------------------------------------
_MARKET_INSIGHT_TTL = 5 * 60           # làm mới nhận định mỗi 5 phút (cập nhật trong phiên)
_market_insight_cache: dict[str, Any] = {}


def _fmt_ti(v: Any) -> str:
    """Số VND → 'X.X tỷ' (1 số lẻ, dấu chấm nghìn, giữ dấu âm)."""
    try:
        num = float(v)
    except (TypeError, ValueError):
        return "0"
    ti = num / 1e9
    s = f"{abs(ti):,.1f}"
    return ("-" if ti < 0 else "") + s


def _trend_note(trend: str) -> str:
    """Lời nhắc nhở theo xu hướng VNINDEX (tăng/giảm/đi ngang)."""
    if trend == "tăng":
        return ("TuniAI đánh giá VNINDEX xu hướng tăng, các nhà đầu tư ngắn hạn chú ý "
                "chốt lời, các nhà đầu tư trung, dài hạn chú ý tỷ trọng.")
    if trend == "giảm":
        return "TuniAI đánh giá VNINDEX xu hướng giảm, các nhà đầu tư chú ý cắt hoặc giảm tỷ trọng."
    return "TuniAI đánh giá VNINDEX xu hướng đi ngang, các nhà đầu tư chú ý quan sát thêm."


def _collect_market_core(include_breadth: bool = True) -> dict[str, Any]:
    """Gom dữ liệu dòng tiền + VNINDEX — dùng chung cho chat tool và ticker.

    - VNINDEX: giá, % thay đổi, xu hướng (1W/1M/3M/6M), đỉnh/đáy 1 năm.
    - Khối ngoại: tổng mua/bán ròng + top mã mua/bán.
    - Thanh khoản: khối lượng phiên gần nhất vs TB 1 tháng.
    - Độ rộng (tuỳ chọn — tốn kém): số mã tăng/giảm.
    """
    flow: dict[str, Any] = {"vnindex": {}, "foreign": {}, "liquidity": {}, "breadth": {}}

    # VNINDEX realtime (đã cache)
    try:
        for item in vns.get_market_overview() or []:
            if str(item.get("symbol", "")).upper() == "VNINDEX":
                flow["vnindex"].update({
                    "value": item.get("value"),
                    "change": item.get("change"),
                    "percent_change": item.get("percent_change"),
                })
                break
    except Exception:
        pass

    # Xu hướng + đỉnh/đáy + thanh khoản từ lịch sử 1 năm
    try:
        detail = vns.get_index_detail("vnindex", "1y")
        hist = detail.get("history") or []
        closes = [float(b.get("close")) for b in hist if b.get("close") is not None]
        if len(closes) >= 2:
            latest = closes[-1]

            def _chg(back: int) -> float:
                base = closes[-(back + 1)] if len(closes) > back else closes[0]
                return ((latest / base) - 1.0) * 100.0 if base else 0.0

            changes = {k: round(_chg(n), 2) for k, n in (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126))}
            trend = "tăng" if (changes["1m"] > 0 and changes["3m"] > 0) else (
                "giảm" if (changes["1m"] < 0 and changes["3m"] < 0) else "đi ngang")
            flow["vnindex"]["trend"] = trend
            flow["vnindex"]["changes"] = changes
            flow["vnindex"]["high_1y"] = round(max(closes), 2)
            flow["vnindex"]["low_1y"] = round(min(closes), 2)

            vols = [float(b.get("volume") or 0) for b in hist if b.get("volume") is not None]
            if len(vols) >= 2:
                window = vols[-21:] or vols
                avg = sum(window) / len(window)
                flow["liquidity"] = {
                    "last_volume": int(vols[-1]),
                    "avg_volume_1m": int(round(avg)),
                    "volume_ratio": round(vols[-1] / avg, 2) if avg else 0,
                }
    except Exception:
        pass

    # Dòng tiền khối ngoại (realtime, cache 90s)
    try:
        ft = vns.get_foreign_trading("foreign", "1D")
        flow["foreign"] = {
            "totals": ft.get("totals") or {},
            "buy_top": (ft.get("buy_list") or [])[:5],
            "sell_top": (ft.get("sell_list") or [])[:5],
        }
    except Exception:
        pass

    # Độ rộng thị trường (nặng — best-effort)
    if include_breadth:
        try:
            board = vns.get_realtime_exchange_board(["HOSE", "HNX", "UPCOM"])
            rows = board.get("data") or []

            def _pct(r: dict[str, Any]) -> float:
                v = r.get("percent_change")
                return v if isinstance(v, (int, float)) else 0.0

            up = sum(1 for r in rows if _pct(r) > 0)
            down = sum(1 for r in rows if _pct(r) < 0)
            flow["breadth"] = {"up": up, "down": down, "flat": max(0, len(rows) - up - down)}
        except Exception:
            flow["breadth"] = {}

    return flow


@_tool(
    "get_market_flow",
    "Dòng tiền ra/vào thị trường + VNINDEX: khối ngoại mua/bán ròng (tổng + top mã), thanh khoản, "
    "độ rộng, xu hướng VNINDEX (1W/1M/3M/6M), đỉnh/đáy 1 năm. Dùng khi hỏi dòng tiền thị trường / xu hướng VNINDEX.",
    {"type": "object", "properties": {}},
)
def _market_flow_tool() -> dict[str, Any]:
    return _collect_market_core(include_breadth=True)


def _template_insight(snap: dict[str, Any]) -> str:
    """Dựng nhận định 1 câu từ dữ liệu (fallback khi DeepSeek lỗi/không cấu hình)."""
    vni = snap.get("vnindex") or {}
    totals = (snap.get("foreign") or {}).get("totals") or {}
    sell_top = (snap.get("foreign") or {}).get("sell_top") or []
    buy_top = (snap.get("foreign") or {}).get("buy_top") or []
    breadth = snap.get("breadth") or {}

    seg: list[str] = []
    net = totals.get("net")
    if net is not None:
        dong = "rút ròng mạnh" if net < 0 else "mua ròng"
        seg.append(f"dòng tiền khối ngoại đang {dong} {_fmt_ti(net)} tỷ đồng trong phiên")
    else:
        seg.append("dòng tiền khối ngoại chưa có số liệu đầy đủ")

    if sell_top:
        seg.append("tập trung bán tháo " + ", ".join(
            f"{r.get('symbol')} {_fmt_ti(r.get('net_value'))} tỷ" for r in sell_top[:3]
        ))
    if buy_top:
        seg.append("trong khi chỉ mua ròng nhẹ vào " + ", ".join(
            str(r.get("symbol")) for r in buy_top[:3]
        ))

    val = vni.get("value")
    pct = vni.get("percent_change")
    if val is not None:
        vstr = f"{float(val):,.0f}".replace(",", ".")
        pstr = f"{abs(float(pct or 0)):.2f}".replace(".", ",")
        dirw = "giảm" if (pct or 0) < 0 else "tăng"
        seg.append(f"khiến VNINDEX {dirw} {pstr}% về mốc {vstr} điểm")

    changes = vni.get("changes") or {}
    if changes:
        seg.append("xu hướng " + ", ".join(f"{k}: {v:+.2f}%" for k, v in list(changes.items())[:4]))
    high = vni.get("high_1y")
    low = vni.get("low_1y")
    if high:
        seg.append(f"đỉnh 1 năm {float(high):,.0f}".replace(",", "."))
    if low:
        seg.append(f"đáy 1 năm {float(low):,.0f}".replace(",", "."))

    up = int(breadth.get("up") or 0)
    down = int(breadth.get("down") or 0)
    if up or down:
        seg.append(f"độ rộng {'đỏ áp đảo' if down >= up else 'xanh'} ({down} giảm/{up} tăng)")

    return "Theo TuniAI nhận định, " + ", ".join(seg) + "."


async def _compose_insight_with_ai(flow: dict[str, Any]) -> str:
    """Nhờ DeepSeek viết nhận định dòng tiền + VNINDEX + lời khuyên NĐT (không bịa số liệu)."""
    _ensure_configured()
    system = (
        "Bạn là TuniAI - trợ lý phân tích chứng khoán Việt Nam của TuniX. "
        "Dựa ĐÚNG vào dữ liệu được cung cấp (dòng tiền khối ngoại, thanh khoản, độ rộng, "
        "VNINDEX giá/xu hướng/đỉnh đáy), viết GỌN trong 2-3 CÂU tiếng Việt cho dòng chữ chạy (ticker), gồm: "
        "1) dòng tiền ra/vào thị trường; "
        "2) VNINDEX xu hướng tăng/giảm + đỉnh/đáy gần nhất; "
        "3) lời khuyên ngắn cho nhà đầu tư ngắn hạn, trung hạn, dài hạn. "
        "Bắt đầu bằng 'Theo TuniAI nhận định,'. KHÔNG thêm số liệu không có trong dữ liệu, "
        "KHÔNG khuyến nghị mua/bán mã cụ thể."
    )
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "Dữ liệu hiện tại:\n" + json.dumps(flow, ensure_ascii=False, default=str)},
        ],
        "temperature": 0.4,
        "max_tokens": 260,
        "stream": False,
    }
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_deepseek_url(), json=payload, headers=_deepseek_headers())
    if resp.status_code != 200:
        raise DeepSeekError(f"DeepSeek trả lỗi {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


async def market_insight(force: bool = False) -> dict[str, Any]:
    """Nhận định thị trường 1 đoạn (ticker dưới header) — cache 5 phút, cập nhật trong phiên."""
    now = time.time()
    cached = _market_insight_cache.get("insight")
    if cached and not force and now < cached[0]:
        return cached[1]

    # Gom dữ liệu (độ rộng tốn kém → giới hạn thời gian; quá chậm thì bỏ độ rộng)
    try:
        flow = await asyncio.wait_for(asyncio.to_thread(_collect_market_core, True), timeout=14.0)
    except Exception:
        flow = await asyncio.to_thread(_collect_market_core, False)

    text = ""
    source = "template"
    try:
        text = await _compose_insight_with_ai(flow)
        source = "tuniai"
    except Exception:
        text = ""
    if not text:
        text = _template_insight(flow)
        source = "template"

    # Đảm bảo có lời khuyên theo xu hướng (chỉ append khi AI chưa tự đưa ra lời khuyên nào).
    trend = flow.get("vnindex", {}).get("trend")
    if trend and not re.search(r"ngắn hạn|trung hạn|dài hạn|chốt lời|tỷ trọng|cắt hoặc giảm", text):
        text = (text.rstrip().rstrip(".") + ". ") if text else ""
        text += _trend_note(trend)

    result: dict[str, Any] = {
        "text": text,
        "source": source,
        "generated_at": datetime.now(UTC).isoformat(),
        "data": flow,
    }
    _market_insight_cache["insight"] = (now + _MARKET_INSIGHT_TTL, result)
    return result
