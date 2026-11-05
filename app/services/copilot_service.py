"""AI Investment Copilot — quét danh mục người dùng, phát hiện thay đổi đáng chú ý
(BCTC, định giá, dòng tiền ngoại, kỹ thuật, tin tức) và đưa ra lý do ngắn gọn.

Dashboard (Home) gọi GET /api/ai/copilot để hiển thị thẻ "AI phát hiện N thay đổi".
Người dùng bấm "Tại sao?" → mở VinaF AI với prompt lấy từ change['prompt'] → AI tự
lấy dữ liệu và giải thích.
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.db import SessionLocal
from app.models import News, NewsStatusEnum, PortfolioHolding
from app.services import vnstock_service as vns

_MAX_CHANGES = 5
_EARNINGS_THRESHOLD_PCT = 10.0
_BIG_MOVE_PCT = 3.0
_FOREIGN_MIN_VND = 5e9


def _f(v: Any) -> float:
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


def _fmt_money(v: Any) -> str:
    try:
        n = float(v)
    except Exception:
        return "0"
    if not n:
        return "0"
    return f"{round(n):,}"


def _qoq(last: dict[str, Any], prev: dict[str, Any]) -> float | None:
    a, b = last.get("net_profit"), prev.get("net_profit")
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100, 1)


def _qoq_yoy(qs: list[dict[str, Any]]) -> float | None:
    """So với cùng quý năm trước (YoY) từ danh sách quý đã sắp xếp tăng dần."""
    if len(qs) < 5:
        return None
    last = qs[-1]
    q = last.get("quarter")
    y = last.get("year")
    if q is None or y is None:
        return None
    for item in qs:
        if item.get("quarter") == q and item.get("year") == y - 1:
            a, b = last.get("net_profit"), item.get("net_profit")
            if a is None or b is None or b == 0:
                return None
            return round((a / b - 1) * 100, 1)
    return None


def _ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 2)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _foreign_net_for(data: Any, sym: str) -> float | None:
    if not isinstance(data, dict):
        return None
    for item in data.get("buy_list") or []:
        if str(item.get("symbol") or "").upper() == sym:
            return _f(item.get("net_value"))
    for item in data.get("sell_list") or []:
        if str(item.get("symbol") or "").upper() == sym:
            return _f(item.get("net_value"))
    return None


def _news_for(sym: str, limit: int = 1) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(News)
            .filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
            .filter((News.tags.ilike(f"%{sym}%")) | (News.title.ilike(f"%{sym}%")))
            .order_by(News.id.desc())
            .limit(limit)
            .all()
        )
        return [{"title": n.title, "summary": (n.summary or "")[:140]} for n in rows]
    finally:
        db.close()


def _sector_pe_map() -> dict[str, float]:
    """P/E trung bình theo ngành (từ screener dataset đã cache)."""
    m: dict[str, float] = {}
    try:
        data = vns.get_stock_screener_page()
    except Exception:
        return m
    groups: dict[str, list[float]] = {}
    for s in data.get("stocks") or []:
        sec = str(s.get("sectorName") or "Khác")
        pe = s.get("pe_ratio")
        if isinstance(pe, (int, float)) and pe > 0:
            groups.setdefault(sec, []).append(pe)
    for sec, pes in groups.items():
        if pes:
            m[sec] = round(sum(pes) / len(pes), 1)
    return m


class CopilotService:
    @staticmethod
    def scan(user_id: int) -> dict[str, Any]:
        db = SessionLocal()
        try:
            rows = db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user_id).all()
        finally:
            db.close()
        if not rows:
            return {
                "empty": True,
                "summary": "Bạn chưa có cổ phiếu trong danh mục để AI theo dõi. Hãy thêm cổ phiếu đang nắm giữ ở trang Quản lý tài sản.",
                "changes": [],
            }
        symbols = sorted({str(h.symbol or "").upper().strip() for h in rows if str(h.symbol or "").strip()})
        sector_pe = _sector_pe_map()
        all_changes: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(CopilotService._scan_symbol, s, sector_pe) for s in symbols]
            for fut in futures:
                try:
                    all_changes.extend(fut.result() or [])
                except Exception:  # noqa: BLE001
                    pass
        all_changes.sort(key=lambda c: float(c.get("severity") or 0), reverse=True)
        changes = all_changes[:_MAX_CHANGES]
        summary = (
            f"🔔 VinaFintech AI phát hiện {len(changes)} thay đổi đáng chú ý trong danh mục của bạn."
            if changes
            else "Không phát hiện thay đổi đáng chú ý nào trong danh mục hiện tại."
        )
        return {"empty": False, "summary": summary, "changes": changes}

    # ------------------------------------------------------------------
    @staticmethod
    def _scan_symbol(sym: str, sector_pe: dict[str, float]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        # ---------- Giá hiện tại ----------
        try:
            quote = vns.get_quote(sym)
        except Exception:  # noqa: BLE001
            quote = {}
        price = _f(quote.get("price"))
        pct_day = _f(quote.get("percent_change"))
        if abs(pct_day) >= _BIG_MOVE_PCT:
            direction = "tích cực" if pct_day > 0 else "tiêu cực"
            out.append(
                {
                    "symbol": sym,
                    "type": "price",
                    "title": f"{sym} {pct_day:+.2f}% hôm nay",
                    "reason": (
                        f"Giá {sym} thay đổi {pct_day:+.2f}% trong phiên hôm nay "
                        f"(giá {_fmt_money(price)} đ) — thuộc nhóm biến động mạnh. Dữ liệu realtime từ VinaFintech."
                    ),
                    "prompt": (
                        f"Giải thích vì sao {sym} biến động {pct_day:+.2f}% hôm nay: nguyên nhân, "
                        "dòng tiền, tin tức liên quan và nhận định ngắn hạn."
                    ),
                    "severity": 2,
                    "direction": direction,
                }
            )

        # ---------- Lợi nhuận quý (BCTC) ----------
        try:
            profit = vns.get_profit_analysis(sym, quarters=5)
        except Exception:  # noqa: BLE001
            profit = {}
        qs = profit.get("quarters") or []
        if len(qs) >= 2:
            last, prev = qs[-1], qs[-2]
            yoy = _qoq_yoy(qs)
            qoq = _qoq(last, prev)
            if yoy is not None and abs(yoy) >= _EARNINGS_THRESHOLD_PCT:
                direction = "tích cực" if yoy > 0 else "tiêu cực"
                extra = (
                    f"; so với quý trước {'tăng' if qoq and qoq > 0 else 'giảm'} "
                    f"{abs(qoq or 0):.1f}%" if qoq is not None else ""
                )
                out.append(
                    {
                        "symbol": sym,
                        "type": "earnings",
                        "title": f"{sym}: lợi nhuận quý {last.get('label')} {'tăng' if yoy > 0 else 'giảm'} {abs(yoy):.1f}%",
                        "reason": (
                            f"Lợi nhuận sau thuế quý {last.get('label')} của {sym} là "
                            f"{_fmt_money(last.get('net_profit'))} đ, {'tăng' if yoy > 0 else 'giảm'} "
                            f"{abs(yoy):.1f}% so với cùng kỳ năm trước{extra}. Nguồn: BCTC (VCI iQ)."
                        ),
                        "prompt": (
                            f"Giải thích vì sao lợi nhuận quý {last.get('label')} của {sym} "
                            f"{'tăng' if yoy > 0 else 'giảm'} {abs(yoy):.1f}% so với cùng kỳ. "
                            "Phân tích BCTC: doanh thu, biên lợi nhuận, chi phí và yếu tố chính."
                        ),
                        "severity": 3,
                        "direction": direction,
                    }
                )

        # ---------- Dòng tiền ngoại đảo chiều ----------
        try:
            f1 = vns.get_foreign_trading("foreign", "1D")
            f30 = vns.get_foreign_trading("foreign", "1M")
        except Exception:  # noqa: BLE001
            f1, f30 = {}, {}
        net1 = _foreign_net_for(f1, sym)
        net30 = _foreign_net_for(f30, sym)
        if net1 is not None and net30 is not None and abs(net1) >= _FOREIGN_MIN_VND and (net1 > 0) != (net30 > 0):
            direction = "tích cực" if net1 > 0 else "tiêu cực"
            out.append(
                {
                    "symbol": sym,
                    "type": "foreign_flow",
                    "title": f"{sym}: dòng tiền ngoại đảo chiều",
                    "reason": (
                        f"Khối ngoại 30 ngày qua {('bán' if net30 < 0 else 'mua')} ròng "
                        f"{_fmt_money(abs(net30))} đ nhưng hôm nay {('mua' if net1 > 0 else 'bán')} ròng "
                        f"{_fmt_money(abs(net1))} đ — dòng tiền đảo chiều{' tích cực' if net1 > 0 else ' tiêu cực'}."
                    ),
                    "prompt": (
                        f"Giải thích vì sao dòng tiền khối ngoại của {sym} đảo chiều: hôm nay "
                        f"{'mua' if net1 > 0 else 'bán'} ròng {_fmt_money(abs(net1))} đ sau khi 30 ngày "
                        f"{'bán' if net30 < 0 else 'mua'} ròng {_fmt_money(abs(net30))} đ. Phân tích ý nghĩa."
                    ),
                    "severity": 3,
                    "direction": direction,
                }
            )

        # ---------- Kỹ thuật / chart ----------
        try:
            bars = vns.get_history(sym, period="1y") or []
        except Exception:  # noqa: BLE001
            bars = []
        closes = [b.get("close") for b in bars if isinstance(b.get("close"), (int, float))]
        if len(closes) >= 50:
            ma50 = _ma(closes, 50)
            rsi = _rsi(closes)
            if ma50 and len(closes) >= 3 and closes[-1] > ma50 and closes[-2] <= ma50:
                out.append(
                    {
                        "symbol": sym,
                        "type": "trend",
                        "title": f"{sym}: giá vượt MA50",
                        "reason": (
                            f"Giá {sym} ({_fmt_money(closes[-1])} đ) vừa cắt lên trên đường MA50 "
                            f"({_fmt_money(ma50)} đ) — tín hiệu xu hướng ngắn/trung hạn cải thiện."
                        ),
                        "prompt": (
                            f"Phân tích kỹ thuật {sym}: giá vừa vượt MA50, đánh giá xu hướng ngắn hạn, "
                            "hỗ trợ/kháng cự và rủi ro."
                        ),
                        "severity": 2,
                        "direction": "tích cực",
                    }
                )
            if rsi is not None and rsi >= 70:
                out.append(
                    {
                        "symbol": sym,
                        "type": "rsi",
                        "title": f"{sym}: RSI vào vùng quá mua",
                        "reason": (
                            f"RSI(14) của {sym} đạt {rsi:.1f} — vùng quá mua (>70), giá có thể "
                            f"điều chỉnh ngắn hạn. Giá {_fmt_money(closes[-1])} đ."
                        ),
                        "prompt": (
                            f"Phân tích chỉ báo RSI của {sym} ở mức {rsi:.1f} (quá mua): ý nghĩa "
                            "và rủi ro điều chỉnh."
                        ),
                        "severity": 2,
                        "direction": "tiêu cực",
                    }
                )
            elif rsi is not None and rsi <= 30:
                out.append(
                    {
                        "symbol": sym,
                        "type": "rsi",
                        "title": f"{sym}: RSI vào vùng quá bán",
                        "reason": (
                            f"RSI(14) của {sym} đạt {rsi:.1f} — vùng quá bán (<30), có thể xuất hiện "
                            "nhịp hồi phục kỹ thuật."
                        ),
                        "prompt": (
                            f"Phân tích chỉ báo RSI của {sym} ở mức {rsi:.1f} (quá bán): khả năng hồi phục."
                        ),
                        "severity": 2,
                        "direction": "tích cực",
                    }
                )

        # ---------- Định giá so với ngành ----------
        pe = _f(quote.get("pe"))
        sector_name = ""
        try:
            sector_name = str((vns.get_company_summary(sym).get("sector") or "")).strip()
        except Exception:  # noqa: BLE001
            pass
        sector_avg = sector_pe.get(sector_name) if sector_name else None
        if pe > 0 and sector_avg:
            if pe <= sector_avg * 0.7:
                out.append(
                    {
                        "symbol": sym,
                        "type": "valuation",
                        "title": f"{sym}: định giá rẻ hơn ngành",
                        "reason": (
                            f"P/E của {sym} là {pe:.1f}, thấp hơn đáng kể so với trung bình ngành "
                            f"{sector_name} ({sector_avg:.1f}) — định giá hấp dẫn tương đối."
                        ),
                        "prompt": (
                            f"Phân tích định giá {sym}: P/E {pe:.1f} thấp hơn trung bình ngành "
                            f"{sector_name} ({sector_avg:.1f}). Đánh giá xem có thực sự rẻ hay rủi ro tăng trưởng."
                        ),
                        "severity": 2,
                        "direction": "tích cực",
                    }
                )
            elif pe >= sector_avg * 1.5:
                out.append(
                    {
                        "symbol": sym,
                        "type": "valuation",
                        "title": f"{sym}: định giá cao hơn ngành",
                        "reason": (
                            f"P/E của {sym} là {pe:.1f}, cao hơn đáng kể so với trung bình ngành "
                            f"{sector_name} ({sector_avg:.1f}) — định giá tương đối đắt."
                        ),
                        "prompt": (
                            f"Phân tích định giá {sym}: P/E {pe:.1f} cao hơn trung bình ngành "
                            f"{sector_name} ({sector_avg:.1f}). Giải thích vì sao và mức độ rủi ro."
                        ),
                        "severity": 2,
                        "direction": "tiêu cực",
                    }
                )

        # ---------- Tin tức ----------
        try:
            news = _news_for(sym, 1)
        except Exception:  # noqa: BLE001
            news = []
        if news:
            n = news[0]
            out.append(
                {
                    "symbol": sym,
                    "type": "news",
                    "title": f"{sym}: có tin mới",
                    "reason": f"Bài viết mới: \"{n['title']}\" — {n.get('summary') or 'Xem chi tiết trên VinaFintech.'}",
                    "prompt": f"Tóm tắt và phân tích tin mới nhất của {sym}: \"{n['title']}\". Ảnh hưởng đến giá và định giá.",
                    "severity": 1,
                    "direction": "trung lập",
                }
            )

        return out
