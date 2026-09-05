"""Dịch vụ phát hiện cảnh báo (Alert Detection Service).

Quét các cổ phiếu trong danh mục nắm giữ + theo dõi của user, dùng dữ liệu THẬT
(history/quote từ vnstock) để phát hiện tín hiệu:
    - Gần vùng Hỗ trợ / Kháng cự (SupportResistanceService)
    - Phá vỡ kháng cự (BREAKOUT) / thủng hỗ trợ (BREAKDOWN)
    - Phá vỡ MA50 lên/xuống
    - RSI quá mua / quá bán

Mỗi alert có ``metadata`` = WHY_THIS_ALERT (JSON) giải thích lý do tạo cảnh báo,
và ``dedup_key`` để chống spam (cooldown cấu hình qua settings, mặc định 24h).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Alert, AlertCategory, AlertImportance, AlertType, PortfolioHolding, User, Watchlist
from app.services import vnstock_service as vns
from app.services.support_resistance import sr_service

# Ánh xạ loại alert → (category, importance)
ALERT_META: dict[str, tuple[AlertCategory, AlertImportance]] = {
    AlertType.support_near: (AlertCategory.opportunity, AlertImportance.medium),
    AlertType.resistance_near: (AlertCategory.warning, AlertImportance.medium),
    AlertType.breakout: (AlertCategory.opportunity, AlertImportance.high),
    AlertType.breakdown: (AlertCategory.warning, AlertImportance.high),
    AlertType.ma50_break_up: (AlertCategory.trend, AlertImportance.medium),
    AlertType.ma50_break_down: (AlertCategory.trend, AlertImportance.high),
    AlertType.rsi_overbought: (AlertCategory.warning, AlertImportance.medium),
    AlertType.rsi_oversold: (AlertCategory.opportunity, AlertImportance.medium),
}

ALERT_LABEL: dict[str, str] = {
    AlertType.support_near: "Gần hỗ trợ",
    AlertType.resistance_near: "Gần kháng cự",
    AlertType.breakout: "Phá vỡ kháng cự",
    AlertType.breakdown: "Thủng hỗ trợ",
    AlertType.ma50_break_up: "Phá vỡ MA50",
    AlertType.ma50_break_down: "Rơi khỏi MA50",
    AlertType.rsi_overbought: "RSI quá mua",
    AlertType.rsi_oversold: "RSI quá bán",
}

# Ưu tiên tín hiệu khi 1 mã xung đột Cơ hội vs Cảnh báo (cao hơn = được giữ).
# Quy tắc: 1 mã cổ phiếu CHỈ có Cơ hội HOẶC Cảnh báo (không cả hai); Xu hướng độc lập.
ALERT_PRIORITY: dict[str, int] = {
    AlertType.breakout: 5,
    AlertType.breakdown: 5,
    AlertType.support_near: 4,
    AlertType.resistance_near: 4,
    AlertType.rsi_oversold: 2,
    AlertType.rsi_overbought: 2,
}


def _alert_priority(alert_type: Any) -> int:
    return ALERT_PRIORITY.get(alert_type, 1)


def _alert_strength(obj: Any) -> float:
    """Lấy strength_score từ metadata (WHY_THIS_ALERT) nếu có."""
    try:
        if isinstance(obj, dict):
            meta = obj.get("why") or {}
        else:
            meta = json.loads(obj.alert_metadata) if getattr(obj, "alert_metadata", None) else {}
        return float(meta.get("strength_score") or 0)
    except Exception:
        return 0.0


def _obj_type(obj: Any) -> Any:
    return obj["alert_type"] if isinstance(obj, dict) else obj.alert_type


def _best_score(items: list[Any]) -> tuple[int, float]:
    """Điểm mạnh nhất của nhóm = (priority, strength_score) — so Cơ hội vs Cảnh báo."""
    best = (-1, -1.0)
    for it in items:
        score = (_alert_priority(_obj_type(it)), _alert_strength(it))
        if score > best:
            best = score
    return best


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI Wilder smoothing."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class AlertDetectionService:
    """Duy trì state cho từng phiên quét — dễ mở rộng thêm loại cảnh báo."""

    def __init__(self) -> None:
        self.enabled_handlers: list[str] = [
            "support_near", "resistance_near", "breakout", "breakdown",
            "ma50_break", "rsi",
        ]

    # ---------- Indicators ----------
    def _indicators(self, candles: list[dict[str, Any]]) -> dict[str, Any]:
        closes = [_to_float(c.get("close")) for c in candles]
        closes = [c for c in closes if c > 0]
        ma50 = _sma(closes, 50)
        ma200 = _sma(closes, 200)
        rsi = _rsi(closes, 14)
        return {
            "closes": closes,
            "ma50": ma50,
            "ma200": ma200,
            "rsi": rsi,
        }

    # ---------- Handlers (mỗi loại trả 1 candidate alert dict hoặc None) ----------
    def _near_zone_alert(self, symbol: str, kind: str, price: float, zone: dict[str, Any],
                         threshold: float, alert_type: AlertType) -> dict[str, Any] | None:
        dist = _to_float(zone.get("distance_percent"))
        if dist is None or dist > threshold * 100:
            return None
        why = {
            "current_price": round(price, 2),
            "zone": {"lower": zone["lower_bound"], "upper": zone["upper_bound"], "pivot": zone["pivot_price"]},
            "distance_percent": dist,
            "touch_count": zone["touch_count"],
            "strength_score": zone["strength_score"],
            "confidence": zone["confidence"],
            "volume_confirmation": zone["volume_confirmation"],
            "timeframe": zone["timeframe"],
        }
        dedup_key = f"{symbol}:{alert_type.value}:{zone['lower_bound']}:{zone['upper_bound']}"
        if kind == "support":
            title = f"{symbol} đang tiến gần vùng hỗ trợ"
            desc = f"Giá {price:,.0f} chỉ còn cách vùng hỗ trợ {zone['lower_bound']:,.0f}–{zone['upper_bound']:,.0f} khoảng {dist:.1f}%."
        else:
            title = f"{symbol} đang tiến gần vùng kháng cự"
            desc = f"Giá {price:,.0f} cách vùng kháng cự {zone['lower_bound']:,.0f}–{zone['upper_bound']:,.0f} khoảng {dist:.1f}%."
        return {
            "alert_type": alert_type, "dedup_key": dedup_key, "title": title,
            "description": desc, "why": why,
        }

    def _break_alert(self, symbol: str, kind: str, prev_close: float, price: float,
                     zone: dict[str, Any], alert_type: AlertType) -> dict[str, Any] | None:
        """Phá vỡ kháng cự / thủng hỗ trợ hôm nay (hôm qua chưa qua, hôm nay đã qua)."""
        if kind == "resistance":
            crossed = prev_close < zone["lower_bound"] <= price
        else:
            crossed = prev_close > zone["upper_bound"] >= price
        if not crossed:
            return None
        why = {
            "current_price": round(price, 2),
            "prev_close": round(prev_close, 2),
            "zone": {"lower": zone["lower_bound"], "upper": zone["upper_bound"], "pivot": zone["pivot_price"]},
            "touch_count": zone["touch_count"],
            "strength_score": zone["strength_score"],
            "confidence": zone["confidence"],
            "timeframe": zone["timeframe"],
        }
        dedup_key = f"{symbol}:{alert_type.value}:{zone['lower_bound']}:{zone['upper_bound']}"
        if kind == "resistance":
            title = f"{symbol} phá vỡ vùng kháng cự"
            desc = f"Giá {price:,.0f} vượt lên trên kháng cự {zone['lower_bound']:,.0f}–{zone['upper_bound']:,.0f} (đóng cửa trước {prev_close:,.0f})."
        else:
            title = f"{symbol} thủng vùng hỗ trợ"
            desc = f"Giá {price:,.0f} rơi xuống dưới hỗ trợ {zone['lower_bound']:,.0f}–{zone['upper_bound']:,.0f} (đóng cửa trước {prev_close:,.0f})."
        return {
            "alert_type": alert_type, "dedup_key": dedup_key, "title": title,
            "description": desc, "why": why,
        }

    def _ma50_alert(self, symbol: str, closes: list[float], alert_type: AlertType) -> dict[str, Any] | None:
        if len(closes) < 51:
            return None
        ma50_today = _sma(closes, 50)
        ma50_prev = _sma(closes[:-1], 50)
        if ma50_today is None or ma50_prev is None:
            return None
        price = closes[-1]
        prev = closes[-2]
        if alert_type == AlertType.ma50_break_up:
            crossed = prev <= ma50_prev and price > ma50_today
            label = "vượt lên trên"
        else:
            crossed = prev >= ma50_prev and price < ma50_today
            label = "rơi xuống dưới"
        if not crossed:
            return None
        why = {"current_price": round(price, 2), "prev_close": round(prev, 2),
               "ma50": round(ma50_today, 2), "prev_ma50": round(ma50_prev, 2)}
        dedup_key = f"{symbol}:{alert_type.value}:{round(ma50_today, 2)}"
        title = f"{symbol} {label} MA50"
        desc = f"Giá {price:,.0f} {label} đường MA50 ({ma50_today:,.0f}) — xu hướng có thể đổi chiều."
        return {
            "alert_type": alert_type, "dedup_key": dedup_key, "title": title,
            "description": desc, "why": why,
        }

    def _rsi_alert(self, symbol: str, rsi: float, price: float, alert_type: AlertType) -> dict[str, Any] | None:
        if rsi is None:
            return None
        if alert_type == AlertType.rsi_overbought:
            if rsi < settings.alert_rsi_overbought:
                return None
            title = f"{symbol} RSI quá mua"
            desc = f"RSI(14) đạt {rsi:.1f} — vùng quá mua, rủi ro điều chỉnh tăng."
        else:
            if rsi > settings.alert_rsi_oversold:
                return None
            title = f"{symbol} RSI quá bán"
            desc = f"RSI(14) ở mức {rsi:.1f} — vùng quá bán, có thể phục hồi kỹ thuật."
        why = {"current_price": round(price, 2), "rsi14": round(rsi, 2),
               "overbought": settings.alert_rsi_overbought, "oversold": settings.alert_rsi_oversold}
        dedup_key = f"{symbol}:{alert_type.value}:{int(rsi // 5)}"
        return {
            "alert_type": alert_type, "dedup_key": dedup_key, "title": title,
            "description": desc, "why": why,
        }

    # ---------- Xung đột Cơ hội / Cảnh báo ----------
    def _resolve_opp_warning(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ràng buộc: 1 mã CHỈ có Cơ hội HOẶC Cảnh báo (không cả hai).

        Xu hướng (trend) độc lập, luôn giữ. Nếu có cả Cơ hội lẫn Cảnh báo →
        giữ nhóm có tín hiệu mạnh hơn (priority, rồi strength_score); hoà → Cảnh báo thắng.
        """
        opp = [c for c in candidates if ALERT_META.get(c["alert_type"], (AlertCategory.trend, AlertImportance.medium))[0] == AlertCategory.opportunity]
        warn = [c for c in candidates if ALERT_META.get(c["alert_type"], (AlertCategory.trend, AlertImportance.medium))[0] == AlertCategory.warning]
        trend = [c for c in candidates if ALERT_META.get(c["alert_type"], (AlertCategory.trend, AlertImportance.medium))[0] == AlertCategory.trend]
        if opp and warn:
            if _best_score(warn) >= _best_score(opp):
                opp = []  # giữ Cảnh báo
            else:
                warn = []  # giữ Cơ hội
        return opp + warn + trend

    def _cleanup_conflict(self, db: Session, user_id: int, symbol: str) -> None:
        """Dọn dữ liệu cũ: nếu user vẫn có cả Cơ hội lẫn Cảnh báo cho cùng mã (từ scan trước)
        → xoá nhóm thua, giữ đúng quy tắc 1 mã chỉ 1 nhóm."""
        opp = db.query(Alert).filter(
            Alert.user_id == user_id, Alert.symbol == symbol,
            Alert.category == AlertCategory.opportunity).all()
        warn = db.query(Alert).filter(
            Alert.user_id == user_id, Alert.symbol == symbol,
            Alert.category == AlertCategory.warning).all()
        if not (opp and warn):
            return
        if _best_score(warn) >= _best_score(opp):
            for r in opp:
                db.delete(r)
        else:
            for r in warn:
                db.delete(r)

    # ---------- Scan 1 mã ----------
    def scan_symbol(self, db: Session, user_id: int, symbol: str) -> int:
        symbol = (symbol or "").upper().strip()
        if not symbol:
            return 0
        try:
            candles = vns.get_history(symbol, "1d", settings.alert_history_period)
            quote = vns.get_quote(symbol)
        except Exception:
            return 0
        if not candles:
            return 0

        price = _to_float(quote.get("price")) if quote and quote.get("valid") else 0.0
        closes = [_to_float(c.get("close")) for c in candles]
        closes = [c for c in closes if c > 0]
        if not closes:
            return 0
        if price <= 0:
            price = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]

        ind = self._indicators(candles)
        zones = sr_service.detect_zones(symbol, candles, current_price=price, timeframe="1D")
        support = [z for z in zones if z["kind"] == "support"]
        resistance = [z for z in zones if z["kind"] == "resistance"]

        candidates: list[dict[str, Any]] = []
        for z in support:
            a = self._near_zone_alert(symbol, "support", price, z, settings.alert_near_support_threshold, AlertType.support_near)
            if a:
                candidates.append(a)
            b = self._break_alert(symbol, "support", prev_close, price, z, AlertType.breakdown)
            if b:
                candidates.append(b)
        for z in resistance:
            a = self._near_zone_alert(symbol, "resistance", price, z, settings.alert_near_resistance_threshold, AlertType.resistance_near)
            if a:
                candidates.append(a)
            b = self._break_alert(symbol, "resistance", prev_close, price, z, AlertType.breakout)
            if b:
                candidates.append(b)

        if "ma50_break" in self.enabled_handlers:
            for at in (AlertType.ma50_break_up, AlertType.ma50_break_down):
                a = self._ma50_alert(symbol, closes, at)
                if a:
                    candidates.append(a)
        if "rsi" in self.enabled_handlers and ind.get("rsi") is not None:
            for at in (AlertType.rsi_overbought, AlertType.rsi_oversold):
                a = self._rsi_alert(symbol, ind["rsi"], price, at)
                if a:
                    candidates.append(a)

        # Ràng buộc: 1 mã chỉ có CƠ HỘI hoặc CẢNH BÁO (không cả hai); Xu hướng độc lập.
        candidates = self._resolve_opp_warning(candidates)

        # Chống spam: bỏ qua candidate đã có alert cùng dedup_key trong cooldown window.
        cooldown = timedelta(hours=settings.alert_cooldown_hours)
        cutoff = datetime.utcnow() - cooldown
        created = 0
        for cand in candidates:
            exists = db.query(Alert.id).filter(
                Alert.user_id == user_id,
                Alert.dedup_key == cand["dedup_key"],
                Alert.created_at >= cutoff,
            ).first()
            if exists:
                continue
            category, importance = ALERT_META.get(cand["alert_type"], (AlertCategory.trend, AlertImportance.medium))
            db.add(Alert(
                user_id=user_id,
                symbol=symbol,
                alert_type=cand["alert_type"],
                category=category,
                importance=importance,
                title=cand["title"],
                description=cand["description"],
                alert_metadata=json.dumps(cand.get("why") or {}, ensure_ascii=False),
                dedup_key=cand["dedup_key"],
            ))
            created += 1

        # Dọn dữ liệu cũ còn xung đột (từ các phiên quét trước).
        self._cleanup_conflict(db, user_id, symbol)
        return created

    # ---------- Scan toàn bộ user ----------
    def user_symbols(self, db: Session, user: User) -> list[str]:
        syms: list[str] = []
        for row in db.query(PortfolioHolding.symbol).filter(PortfolioHolding.user_id == user.id).all():
            s = (row.symbol or "").upper().strip()
            if s and s not in syms:
                syms.append(s)
        for row in db.query(Watchlist.symbol).filter(Watchlist.user_id == user.id).all():
            s = (row.symbol or "").upper().strip()
            if s and s not in syms:
                syms.append(s)
        return syms

    def prune_untracked(self, db: Session, user_id: int, commit: bool = True) -> int:
        """Xoá 'cảnh báo mồ côi': cảnh báo của mã KHÔNG còn trong holdings ∪ watchlist.

        Gọi sau khi: quét cảnh báo, xoá/bán hết 1 vị thế, hoặc bỏ theo dõi 1 mã.
        """
        tracked: set[str] = set()
        for row in db.query(PortfolioHolding.symbol).filter(PortfolioHolding.user_id == user_id).all():
            s = (row.symbol or "").upper().strip()
            if s:
                tracked.add(s)
        for row in db.query(Watchlist.symbol).filter(Watchlist.user_id == user_id).all():
            s = (row.symbol or "").upper().strip()
            if s:
                tracked.add(s)
        q = db.query(Alert).filter(Alert.user_id == user_id)
        if tracked:
            q = q.filter(~Alert.symbol.in_(sorted(tracked)))
        rows = q.all()
        for a in rows:
            db.delete(a)
        n = len(rows)
        if commit and n:
            db.commit()
        return n

    def scan_user(self, db: Session, user: User) -> dict[str, Any]:
        syms = self.user_symbols(db, user)
        created = 0
        for s in syms:
            created += self.scan_symbol(db, user.id, s)
        # Dọn cảnh báo của mã không còn trong holdings ∪ watchlist (cảnh báo mồ côi).
        pruned = self.prune_untracked(db, user.id, commit=False)
        db.commit()
        return {"created": created, "scanned": len(syms), "symbols": syms, "pruned": pruned}


alert_service = AlertDetectionService()
