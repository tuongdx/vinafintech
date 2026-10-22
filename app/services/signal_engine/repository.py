"""Repository — lưu/đọc signal snapshot & thống kê (spec §21, §24, §25, §27, §28).

Nguyên tắc:
- Mỗi signal giữ **snapshot đầy đủ** tại thời điểm tạo (features + AI + conditions + reasons)
  để trả lời câu hỏi "lúc đó hệ thống thực sự thấy gì?".
- Không tạo số liệu thống kê giả: chỉ tính từ các bản ghi CÓ THẬT (signal đã đóng).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from app.models import (
    BacktestResult,
    Signal,
    SignalCondition,
    SignalFeatureSnapshot,
    SignalHistory,
    SignalPosition,
    SignalPrediction,
    SignalStrategy,
)

logger = logging.getLogger("tunix.signal.repository")

_ACTIVE_STATES = ("CONFIRMED", "ACTIVE", "WEAKENING")
_CLOSED_STATES = ("EXIT", "INVALIDATED")
# Chỉ các trạng thái "đang hiệu lực" (_ACTIVE_STATES) bị ĐÓNG khi quá hạn nắm giữ T+ —
# WATCH/SETUP_FORMING/NO_SETUP là trạng thái theo dõi (không phải lệnh đang mở) nên chỉ
# cần phân tích lại (xem engine.refresh_open_signals).

_STRATEGY_META = {
    "UPTREND": ("Uptrend", "Theo dòng tiền mạnh: MA xếp tầng, momentum dương, volume xác nhận, breakout."),
    "BOTTOM_FISHING": ("Bottom Fishing", "Bắt đáy có xác nhận: giảm sâu nhưng áp lực bán suy yếu, không rơi tự do."),
    "SWING": ("Swing Trading", "Lướt sóng theo Chaikin Money Flow + Relative Strength, rotation TOP N."),
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    """ISO CÓ múi giờ cho mốc lưu trong DB (DB lưu UTC naive — `datetime.utcnow`).

    Thiếu múi giờ thì trình duyệt sẽ hiểu mốc đó là GIỜ ĐỊA PHƯƠNG (VN: lệch 7 giờ) ⇒
    cột "Ngày tạo"/"Vào lệnh" hiển thị sai. Vì vậy mọi mốc trả ra API đều gắn tường minh.
    """
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return ""


def seed_strategies(db: Session) -> None:
    """Đăng ký 3 chiến thuật vào `signal_strategies` (idempotent)."""
    from app.services.signal_engine import config as cfg_module

    cfg = cfg_module.get_config()
    for name, (display, desc) in _STRATEGY_META.items():
        row = db.query(SignalStrategy).filter(SignalStrategy.name == name).first()
        strategy_cfg = (((cfg.get("strategies") or {}).get(name.lower())) or {})
        enabled = bool(strategy_cfg.get("enabled", True))
        payload = _dump(strategy_cfg)
        if row:
            row.display_name, row.description, row.enabled, row.config_json = display, desc, enabled, payload
        else:
            db.add(SignalStrategy(name=name, display_name=display, description=desc, enabled=enabled, config_json=payload))
    db.commit()


# ---------------------------------------------------------------------------
# Ghi snapshot
# ---------------------------------------------------------------------------
def save_snapshot(db: Session, snapshot: dict) -> dict[str, Any]:
    """Lưu feature snapshot + signal + conditions + history (idempotent theo mã/chiến thuật)."""
    symbol = (snapshot.get("symbol") or "").upper()
    if not symbol:
        return {"saved": 0}

    features = snapshot.get("features") or {}
    as_of = str(snapshot.get("as_of") or "")
    saved = 0

    # 1) Feature snapshot theo mã + thời điểm (spec §21)
    if as_of:
        existing = (
            db.query(SignalFeatureSnapshot)
            .filter(SignalFeatureSnapshot.symbol == symbol,
                    SignalFeatureSnapshot.interval == str((features.get("interval") or "1d")),
                    SignalFeatureSnapshot.as_of == as_of)
            .first()
        )
        payload = {
            "bars": int(features.get("bars") or 0),
            "status": str(features.get("status") or ""),
            "features_json": _dump(features),
            "data_quality_json": _dump(features.get("data_quality")),
            "config_version": str(snapshot.get("config_version") or ""),
        }
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(SignalFeatureSnapshot(symbol=symbol, interval="1d", as_of=as_of, **payload))
        saved += 1

    # 2) AI predictions: CHỈ ghi khi model thực sự chạy (trained) — tránh nhồi bảng rỗng
    for model in (snapshot.get("ai") or {}).get("models") or []:
        if not model.get("trained"):
            continue
        db.add(SignalPrediction(
            symbol=symbol,
            as_of=as_of,
            model=str(model.get("model") or ""),
            status=str(model.get("status") or ""),
            direction=str(model.get("direction") or ""),
            probability_up=model.get("probability_up"),
            confidence=model.get("confidence"),
            horizon_days=model.get("horizon_days"),
            detail_json=_dump(model.get("detail")),
        ))
        saved += 1

    # 3) Signals theo từng chiến thuật
    # Commit theo TỪNG chiến thuật: 1 chiến thuật lỗi không kéo đổ 2 chiến thuật còn lại.
    for name, strat in (snapshot.get("strategies") or {}).items():
        if not strat.get("evaluated"):
            continue
        try:
            saved += _save_strategy_signal(db, snapshot, symbol, name, strat, as_of)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning("lưu signal %s/%s lỗi: %s", symbol, name, exc)

    db.commit()
    return {"saved": saved}


def _save_strategy_signal(db: Session, snapshot: dict, symbol: str, strategy: str,
                          strat: dict, as_of: str) -> int:
    current = (
        db.query(Signal)
        .filter(Signal.symbol == symbol, Signal.strategy == strategy, Signal.is_active == True)  # noqa: E712
        .order_by(Signal.id.desc())
        .first()
    )
    created = False
    reopened = False
    if current is None:
        # Không có signal đang mở → dùng lại đúng dòng của CÙNG NẾN (as_of) nếu có.
        # Bắt buộc: bảng signals có unique (symbol, strategy, as_of) — nếu cứ INSERT mới sẽ
        # vi phạm ràng buộc, rollback, và DB giữ giá trị CŨ (bug đã gặp khi quét lại trong ngày).
        current = (
            db.query(Signal)
            .filter(Signal.symbol == symbol, Signal.strategy == strategy, Signal.as_of == as_of)
            .order_by(Signal.id.desc())
            .first()
        )
        if current is not None:
            reopened = True
            current.is_active = True
            current.closed_at = None
            current.exit_price = None
            current.exit_reason = ""
            current.result = ""
            current.return_pct = None
            current.holding_days = None
    levels = strat.get("levels") or {}
    state = str(strat.get("state") or "")
    signal_value = str(strat.get("signal") or "")
    setup = strat.get("setup_strength") or {}
    confidence = (strat.get("confidence") or {}).get("value")
    risk_payload = snapshot.get("risk") or {}
    reasons = strat.get("reasons") or []

    fields = {
        "state": state,
        "signal": signal_value,
        "score": strat.get("score"),
        "confidence": confidence,
        "setup_strength": setup.get("level") or "",
        "risk_score": risk_payload.get("risk_score"),
        "risk_level": risk_payload.get("level") or "",
        "market_regime": (((snapshot.get("market") or {}).get("regime") or {}).get("regime")) or "",
        "price": snapshot.get("price"),
        "confirmations": int(strat.get("confirmations") or 0),
        "total_conditions": int(strat.get("total_conditions") or 0),
        "bad_signal_count": int(strat.get("bad_signal_count") or 0),
        "exit_condition": bool(strat.get("exit_condition")),
        "config_version": str(snapshot.get("config_version") or ""),
        "features_json": _dump(snapshot.get("features")),
        "ai_json": _dump(snapshot.get("ai")),
        "conditions_json": _dump(strat.get("conditions")),
        "reasons_json": _dump(reasons),
        "audit_json": _dump({
            "as_of": as_of,
            "price": snapshot.get("price"),
            "market": snapshot.get("market"),
            "decision": snapshot.get("decision"),
            "strategy_score": strat.get("score"),
            "setup": setup,
            "generated_at": snapshot.get("generated_at"),
            "config_version": snapshot.get("config_version"),
        }),
        "updated_at": _now(),
    }

    created = False
    if current is None:
        current = Signal(symbol=symbol, strategy=strategy, as_of=as_of,
                         entry=levels.get("entry"), stop_loss=levels.get("stop_loss"),
                         take_profit=levels.get("take_profit"), trailing_stop=levels.get("trailing_stop"),
                         is_active=True, created_at=_now())
        db.add(current)
        db.flush()
        created = True
    for key, value in fields.items():
        setattr(current, key, value)
    # Entry/SL/TP/Trailing cập nhật khi bắt đầu hiệu lực (không đổi giữa chừng để audit được)
    if state in _ACTIVE_STATES and not current.entry:
        current.entry = levels.get("entry")
        current.stop_loss = levels.get("stop_loss")
        current.take_profit = levels.get("take_profit")
        current.trailing_stop = levels.get("trailing_stop")
    elif state in _ACTIVE_STATES and levels.get("trailing_stop"):
        current.trailing_stop = levels.get("trailing_stop")

    # Lịch sử trạng thái (spec §24/§27): chỉ ghi khi ĐỔI trạng thái hoặc tạo mới
    prev_state = ""
    if not created:
        prev = (
            db.query(SignalHistory)
            .filter(SignalHistory.signal_id == current.id)
            .order_by(SignalHistory.id.desc())
            .first()
        )
        prev_state = prev.to_state if prev else ""
    if created or reopened or prev_state != state:
        db.add(SignalHistory(
            signal_id=current.id, symbol=symbol, strategy=strategy,
            from_state=prev_state, to_state=state, signal=signal_value,
            reason=(f"Mở lại cùng nến: {strat.get('transition', {}).get('reason') or ''}" if reopened
                    else str((strat.get("transition") or {}).get("reason") or "")),
            price=snapshot.get("price"), score=strat.get("score"),
            detail_json=_dump({"bad_signal_count": fields["bad_signal_count"], "confirmations": fields["confirmations"],
                               "reopened": reopened}),
        ))

    # Conditions của lần phân tích mới nhất (refresh để bảng không phình)
    db.query(SignalCondition).filter(SignalCondition.signal_id == current.id).delete()
    for cond in strat.get("conditions") or []:
        db.add(SignalCondition(
            signal_id=current.id, symbol=symbol, strategy=strategy,
            key=str(cond.get("key") or ""), label=str(cond.get("label") or ""),
            group=str(cond.get("group") or "technical"), required=bool(cond.get("required")),
            state=str(cond.get("state") or ""), passed=cond.get("passed"),
            value_json=_dump(cond.get("value")), threshold=str(cond.get("threshold") or ""),
            reason=str(cond.get("reason") or "")[:300],
        ))

    # Đóng signal khi thoát/hết hiệu lực (spec §24)
    if state in _CLOSED_STATES:
        current.is_active = False
        current.closed_at = _now()
        current.exit_price = snapshot.get("price")
        current.exit_reason = str((strat.get("transition") or {}).get("reason") or state)[:120]
        entry = current.entry
        if entry and current.exit_price:
            current.return_pct = round((current.exit_price / entry - 1) * 100, 3)
            current.result = "WIN" if current.return_pct > 0 else "LOSS" if current.return_pct < 0 else "FLAT"
        else:
            current.result = "UNKNOWN"
        if current.created_at:
            current.holding_days = max(0, (_now() - current.created_at).days)
        db.add(SignalHistory(
            signal_id=current.id, symbol=symbol, strategy=strategy,
            from_state=state, to_state=state, signal=signal_value,
            reason=current.exit_reason, price=snapshot.get("price"), score=strat.get("score"),
            exit_price=current.exit_price, return_pct=current.return_pct,
            holding_days=current.holding_days, result=current.result,
        ))
    return 1


# ---------------------------------------------------------------------------
# Đọc trạng thái trước đó (cho state machine)
# ---------------------------------------------------------------------------
def previous_states_bulk(db: Session, symbols: list[str]) -> dict[str, dict[str, str]]:
    """{symbol: {strategy: state}} của các signal đang mở — 1 query duy nhất."""
    out: dict[str, dict[str, str]] = {}
    if not symbols:
        return out
    upper = [s.upper() for s in symbols]
    rows = (
        db.query(Signal.symbol, Signal.strategy, Signal.state)
        .filter(Signal.symbol.in_(upper), Signal.is_active == True)  # noqa: E712
        .all()
    )
    for symbol, strategy, state in rows:
        out.setdefault(str(symbol), {})[str(strategy)] = str(state)
    return out


def previous_states(db: Session, symbol: str) -> dict[str, str]:
    return previous_states_bulk(db, [symbol]).get(symbol.upper(), {})


# ---------------------------------------------------------------------------
# Truy vấn cho menu Admin
# ---------------------------------------------------------------------------
# Cột cho phép SẮP XẾP trên bảng tín hiệu (khoá URL → (cột DB, hướng mặc định)).
# Phải khớp nút sort ở header bảng — xem `frontend/assets/tin-hieu.js`.
# LƯU Ý: cột "Giá hiện tại" KHÔNG có ở đây vì là giá realtime (client tự sắp trong trang).
_SORT_COLUMNS: dict[str, tuple[Any, str]] = {
    "confidence": (Signal.confidence, "desc"),
    "score": (Signal.score, "desc"),
    "updated": (Signal.updated_at, "desc"),
    "created": (Signal.created_at, "desc"),
    "symbol": (Signal.symbol, "asc"),
    "strategy": (Signal.strategy, "asc"),
    "signal": (Signal.signal, "desc"),
    "state": (Signal.state, "asc"),
    "entry": (Signal.entry, "desc"),
    "stop_loss": (Signal.stop_loss, "desc"),
    "trailing_stop": (Signal.trailing_stop, "desc"),
    "take_profit": (Signal.take_profit, "desc"),
}
_SORT_ALIASES = {
    "diem": "score", "điểm": "score", "score_desc": "score",
    "moi": "updated", "mới": "updated", "updated_at": "updated",
    "created_at": "created", "ngay": "created", "ngày": "created",
    "trailing": "trailing_stop", "sl": "stop_loss", "tp": "take_profit",
}
# Trạng thái sắp theo VÒNG ĐỜI tín hiệu (không theo alphabet).
_STATE_SORT_ORDER = ["NO_SETUP", "WATCH", "SETUP_FORMING", "CONFIRMED", "ACTIVE",
                     "WEAKENING", "EXIT", "INVALIDATED"]


def sort_config(sort: str, direction: str = "") -> tuple[str, str]:
    """Chuẩn hoá ``(khoá cột, hướng)`` cho bảng tín hiệu — dùng chung repository + router."""
    key = (sort or "confidence").strip().lower()
    key = _SORT_ALIASES.get(key, key)
    if key not in _SORT_COLUMNS:
        key = "confidence"
    dir_ = (direction or "").strip().lower()
    if dir_ not in ("asc", "desc"):
        dir_ = _SORT_COLUMNS[key][1]
    return key, dir_


def _list_order(sort: str, direction: str = "") -> list[Any]:
    """Thứ tự bảng tín hiệu (spec §17) — phục vụ NÚT SORT ở đầu mọi cột (tin-hieu.js).

    MẶC ĐỊNH ``confidence``: **ĐỘ TIN CẬY cao nhất ở trên, thấp nhất ở dưới** — tín hiệu chưa
    đánh giá được độ tin cậy (hiển thị "—") luôn nằm CUỐI. Dùng `is_(None)` (thay vì
    `nullslast`) để chạy được trên cả SQLite lẫn Postgres.

    ``sort`` là KHOÁ cột trong ``_SORT_COLUMNS`` (khoá lạ → về ``confidence`` để không vỡ
    truy vấn); ``direction`` = ``asc``/``desc`` (rỗng → hướng mặc định của cột).
    """
    key, dir_ = sort_config(sort, direction)
    descending = dir_ == "desc"

    if key == "state":
        # Trạng thái sắp theo VÒNG ĐỜI (NO_SETUP → … → INVALIDATED), KHÔNG theo alphabet.
        expr: Any = case({name: idx for idx, name in enumerate(_STATE_SORT_ORDER)},
                         value=Signal.state, else_=len(_STATE_SORT_ORDER))
    else:
        expr = _SORT_COLUMNS[key][0]

    order: list[Any] = [expr.is_(None)]
    order.append(expr.desc() if descending else expr.asc())
    # Tiêu chí phụ: ĐỘ TIN CẬY giảm dần để phân trang ổn định (giữ đúng hành vi cũ).
    if key == "confidence":
        order.extend([Signal.score.is_(None), Signal.score.desc()])
    else:
        order.extend([Signal.confidence.is_(None), Signal.confidence.desc()])
    order.append(Signal.id.desc())
    return order


def list_signals(db: Session, *, strategy: str = "", signal: str = "", state: str = "",
                 symbol: str = "", min_score: float | None = None, min_confidence: float | None = None,
                 date_from: str = "", date_to: str = "", active_only: bool = False,
                 sort: str = "confidence", direction: str = "",
                 symbols: set[str] | None = None, one_per_symbol: bool = False,
                 limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db.query(Signal)
    if symbols:
        # Giới hạn theo TẬP MÃ (bộ lọc thanh khoản của trang Tín hiệu) — rỗng/None = KHÔNG lọc.
        query = query.filter(Signal.symbol.in_(sorted(symbols)))
    if strategy:
        query = query.filter(Signal.strategy == strategy.upper())
    if signal:
        query = query.filter(Signal.signal == signal.upper())
    if state:
        # Hỗ trợ NHIỀU trạng thái: "CONFIRMED,ACTIVE" (trang Tín hiệu chỉ hiện setup đã vào được lệnh).
        states = [s.strip().upper() for s in str(state).split(",") if s.strip()]
        if states:
            query = query.filter(Signal.state.in_(states))
    if symbol:
        query = query.filter(Signal.symbol == symbol.upper())
    if min_score is not None:
        query = query.filter(Signal.score >= min_score)
    if min_confidence is not None:
        query = query.filter(Signal.confidence >= min_confidence)
    if date_from:
        query = query.filter(Signal.created_at >= _parse_date(date_from))
    if date_to:
        query = query.filter(Signal.created_at <= _parse_date(date_to, end_of_day=True))
    if active_only:
        query = query.filter(Signal.is_active == True)  # noqa: E712

    order = _list_order(sort, direction)

    if one_per_symbol:
        # MỘT DÒNG CHO MỖI MÃ (yêu cầu trang Tín hiệu): một cổ phiếu có thể được 2–3 chiến thuật
        # (UPTREND/BOTTOM_FISHING/SWING) cùng phát tín hiệu ⇒ chỉ hiển thị 1 dòng, giữ dòng ĐẦU TIÊN
        # theo đúng thứ tự đang sắp xếp (đổi cột sắp xếp = đổi chiến thuật được chọn cho mã đó).
        # `total` đếm theo SỐ MÃ (distinct) để phân trang khớp đúng số dòng thực tế.
        # Dùng window function `ROW_NUMBER()` — chạy được trên cả SQLite (≥3.25) lẫn Postgres.
        rank = func.row_number().over(partition_by=Signal.symbol, order_by=order).label("rn")
        ranked = query.with_entities(Signal.id.label("signal_id"), rank).subquery()
        total = int(query.with_entities(func.count(distinct(Signal.symbol))).scalar() or 0)
        rows = (
            db.query(Signal)
            .join(ranked, Signal.id == ranked.c.signal_id)
            .filter(ranked.c.rn == 1)
            .order_by(*order)
            .offset(max(0, offset))
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return {"total": total, "items": [_row_out(r) for r in rows]}

    total = query.count()
    rows = (
        query.order_by(*order)
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return {"total": total, "items": [_row_out(r) for r in rows]}


def last_scan_at(db: Session) -> str | None:
    """Thời điểm dữ liệu tín hiệu được ghi/cập nhật gần nhất (ISO) — hiển thị "Quét gần nhất".

    Dùng `MAX(signals.updated_at)` (mọi lần quét — kể cả quét tự động theo lịch — đều set field này).
    """
    try:
        value = db.query(func.max(Signal.updated_at)).scalar()
    except Exception as exc:  # noqa: BLE001 — thiếu bảng/cột ⇒ không chặn trang
        logger.warning("không đọc được thời điểm quét gần nhất: %s", exc)
        return None
    return iso_utc(value)


# ---------------------------------------------------------------------------
# VỊ THẾ NGƯỜI DÙNG tự mở (nút ON/OFF ở cột "Vị thế" trang Tín hiệu)
# ---------------------------------------------------------------------------
POSITION_OPEN = "OPEN"
POSITION_CLOSED = "CLOSED"


def open_position_of(db: Session, user_id: int, symbol: str) -> SignalPosition | None:
    """Vị thế ĐANG MỞ của (user, mã) — mỗi mã chỉ có tối đa 1 vị thế mở."""
    return (
        db.query(SignalPosition)
        .filter(SignalPosition.user_id == user_id,
                SignalPosition.symbol == str(symbol or "").upper(),
                SignalPosition.status == POSITION_OPEN)
        .order_by(SignalPosition.id.desc())
        .first()
    )


def position_out(row: SignalPosition, live_price: float | None = None) -> dict[str, Any]:
    """Dòng vị thế trả cho UI. Vị thế ĐANG MỞ: `return_pct` = lãi/lỗ TẠM TÍNH theo giá hiện tại."""
    current = float(live_price) if live_price and float(live_price) > 0 else None
    entry = float(row.entry_price) if row.entry_price else None
    pnl_pct = round((current / entry - 1) * 100, 3) if (current and entry) else None
    is_open = row.status == POSITION_OPEN
    return {
        "id": row.id,
        "symbol": row.symbol,
        "strategy": row.strategy,
        "signal_id": row.signal_id,
        "status": row.status,
        "entry_price": row.entry_price,
        "entry_at": iso_utc(row.entry_at),
        "exit_price": row.exit_price,
        "exit_at": iso_utc(row.exit_at),
        "exit_reason": row.exit_reason,
        "current_price": current,
        "return_pct": pnl_pct if is_open else row.return_pct,
        "result": "" if is_open else row.result,
        "note": row.note,
    }


def list_positions(db: Session, user_id: int, *, limit: int = 300) -> list[SignalPosition]:
    """Vị thế của 1 user — ĐANG MỞ trước, rồi tới các vị thế đã đóng (mới nhất trước)."""
    return (
        db.query(SignalPosition)
        .filter(SignalPosition.user_id == user_id)
        .order_by(SignalPosition.status.desc(), SignalPosition.id.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )


def open_position(db: Session, user_id: int, symbol: str, *, price: float, strategy: str = "",
                  signal_id: int | None = None, note: str = "") -> tuple[SignalPosition, bool]:
    """Mở vị thế tại ``price`` (giá hiện tại lúc người dùng bật ON).

    Nếu mã đang có vị thế MỞ ⇒ trả lại vị thế cũ (created=False) để nút bật ON là idempotent.
    """
    symbol = str(symbol or "").upper()
    existing = open_position_of(db, user_id, symbol)
    if existing is not None:
        return existing, False
    row = SignalPosition(
        user_id=user_id, symbol=symbol, strategy=str(strategy or "").upper(), signal_id=signal_id,
        status=POSITION_OPEN, entry_price=round(float(price), 2), entry_at=_now(), note=str(note or "")[:255],
        created_at=_now(), updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True


def close_position(db: Session, row: SignalPosition, *, price: float, reason: str = "") -> SignalPosition:
    """Đóng vị thế tại ``price`` (giá hiện tại lúc người dùng tắt OFF) ⇒ chốt lãi/lỗ."""
    entry = float(row.entry_price) if row.entry_price else 0.0
    exit_price = round(float(price), 2)
    row.status = POSITION_CLOSED
    row.exit_price = exit_price
    row.exit_at = _now()
    row.exit_reason = str(reason or "Đóng vị thế trên trang Tín hiệu")[:160]
    if entry > 0:
        row.return_pct = round((exit_price / entry - 1) * 100, 3)
        row.result = "WIN" if row.return_pct > 0 else "LOSS" if row.return_pct < 0 else "FLAT"
    else:
        row.return_pct = None
        row.result = "UNKNOWN"
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def position_stats(db: Session, user_id: int) -> dict[str, Any]:
    """Thống kê VỊ THẾ của 1 user.

    ⚠️ Tỷ lệ thắng / profit factor / lãi-lỗ TB / drawdown CHỈ tính trên vị thế **ĐÃ ĐÓNG**
    (người dùng đã tắt OFF ⇒ đã chốt) — vị thế đang mở chỉ đếm số lượng, không tính vào tỷ lệ thắng.
    """
    rows = db.query(SignalPosition).filter(SignalPosition.user_id == user_id).all()
    open_rows = [r for r in rows if r.status == POSITION_OPEN]
    closed = [r for r in rows if r.status == POSITION_CLOSED and r.return_pct is not None]
    closed.sort(key=lambda r: r.exit_at or r.created_at)

    returns = [float(r.return_pct) for r in closed]
    wins = [v for v in returns if v > 0]
    losses = [v for v in returns if v < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = max_dd = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    holding = [(r.exit_at - r.entry_at).days for r in closed
               if r.exit_at is not None and r.entry_at is not None]

    return {
        "open": len(open_rows),
        "closed": len(closed),
        "total": len(rows),
        "win_rate": round(len(wins) / len(returns) * 100, 2) if returns else None,
        "average_gain_pct": round(gross_profit / len(wins), 3) if wins else None,
        "average_loss_pct": round(sum(losses) / len(losses), 3) if losses else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "total_return_pct": round(sum(returns), 3) if returns else None,
        "max_drawdown_pct": round(max_dd, 3) if returns else None,
        "average_holding_days": round(sum(holding) / len(holding), 1) if holding else None,
        "note": ("Vị thế ĐANG MỞ không tính vào tỷ lệ thắng; bật ON để mở vị thế tại giá hiện tại, "
                 "tắt OFF để chốt và ghi vào thống kê."),
    }


def _parse_date(value: str, end_of_day: bool = False) -> datetime:
    text = (value or "").strip().replace("/", "-")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = _now()
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _row_out(row: Signal) -> dict[str, Any]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "strategy": row.strategy,
        "as_of": row.as_of,
        "state": row.state,
        "signal": row.signal,
        "score": row.score,
        "confidence": row.confidence,
        "setup_strength": row.setup_strength,
        "risk_score": row.risk_score,
        "risk_level": row.risk_level,
        "market_regime": row.market_regime,
        "entry": row.entry,
        "stop_loss": row.stop_loss,
        "take_profit": row.take_profit,
        "trailing_stop": row.trailing_stop,
        "price": row.price,
        "confirmations": row.confirmations,
        "total_conditions": row.total_conditions,
        "bad_signal_count": row.bad_signal_count,
        "exit_condition": row.exit_condition,
        "is_active": row.is_active,
        "result": row.result,
        "return_pct": row.return_pct,
        "holding_days": row.holding_days,
        "exit_price": row.exit_price,
        "exit_reason": row.exit_reason,
        "created_at": iso_utc(row.created_at),
        "updated_at": iso_utc(row.updated_at),
        "closed_at": iso_utc(row.closed_at),
    }


def get_signal(db: Session, signal_id: int) -> dict[str, Any] | None:
    row = db.query(Signal).filter(Signal.id == signal_id).first()
    if not row:
        return None
    data = _row_out(row)
    data["conditions"] = [
        {
            "key": c.key, "label": c.label, "group": c.group, "required": c.required,
            "state": c.state, "passed": c.passed,
            "value": _load_json(c.value_json), "threshold": c.threshold, "reason": c.reason,
        }
        for c in db.query(SignalCondition).filter(SignalCondition.signal_id == row.id).order_by(SignalCondition.id.asc()).all()
    ]
    data["history"] = [
        {
            "from_state": h.from_state, "to_state": h.to_state, "signal": h.signal, "reason": h.reason,
            "price": h.price, "score": h.score, "exit_price": h.exit_price, "return_pct": h.return_pct,
            "holding_days": h.holding_days, "result": h.result,
            "created_at": iso_utc(h.created_at),
        }
        for h in db.query(SignalHistory).filter(SignalHistory.signal_id == row.id).order_by(SignalHistory.id.asc()).all()
    ]
    data["features"] = _load_json(row.features_json)
    data["ai"] = _load_json(row.ai_json)
    data["reasons"] = _load_json(row.reasons_json)
    data["audit"] = _load_json(row.audit_json)
    return data


def _load_json(text: str | None) -> Any:
    try:
        return json.loads(text) if text else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Vòng đời T+ — đóng tín hiệu QUÁ HẠN & liệt kê tín hiệu ĐANG MỞ (spec §16/§24 mở rộng)
# ---------------------------------------------------------------------------
def open_signals(db: Session) -> list[dict[str, Any]]:
    """Danh sách tín hiệu ĐANG MỞ (``is_active``) — dữ liệu tối thiểu cho vòng đời T+."""
    rows = (
        db.query(Signal.id, Signal.symbol, Signal.strategy, Signal.state,
                 Signal.as_of, Signal.created_at, Signal.updated_at)
        .filter(Signal.is_active == True)  # noqa: E712
        .all()
    )
    return [
        {
            "id": row[0],
            "symbol": str(row[1] or "").upper(),
            "strategy": str(row[2] or ""),
            "state": str(row[3] or ""),
            "as_of": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }
        for row in rows
    ]


def expire_overdue(db: Session, *, max_holding_sessions: int | None = None,
                   benchmark_bars: list[dict] | None = None,
                   cfg: dict | None = None) -> dict[str, Any]:
    """Đóng các tín hiệu ĐANG HIỆU LỰC đã QUÁ HẠN nắm giữ T+ (mặc định 10 phiên).

    Vì sao: tín hiệu TuniX là tín hiệu T+ (ngắn hạn) — quá hạn nắm giữ thì KHÔNG còn hiệu lực.
    Trước đây không có bước này nên tín hiệu từ nhiều tuần trước vẫn hiển thị "Đang hiệu lực"
    trên trang Tín hiệu (bug thật đã gặp).

    Số phiên đếm từ ``created_at`` (thời điểm tín hiệu mở) theo nến benchmark; nếu không có
    nến benchmark thì quy đổi sang ngày lịch (10 phiên ≈ 14 ngày).
    """
    from app.services.signal_engine import lifecycle

    conf = lifecycle.expiry_config(cfg)
    max_sessions = int(max_holding_sessions or conf["max_holding_sessions"])
    if not conf["enabled"]:
        return {"enabled": False, "expired": 0, "symbols": [], "checked": 0,
                "max_holding_sessions": max_sessions}

    rows = (
        db.query(Signal)
        .filter(Signal.is_active == True, Signal.state.in_(_ACTIVE_STATES))  # noqa: E712
        .all()
    )
    now = _now()
    expired: list[str] = []
    for row in rows:
        decision = lifecycle.expiry_decision(row.created_at, max_sessions, benchmark_bars, now=now)
        if not decision["expired"]:
            continue
        _close_expired(db, row, decision, max_sessions, now)
        expired.append(str(row.symbol or ""))
    if expired:
        db.commit()
    return {
        "enabled": True,
        "expired": len(expired),
        "symbols": sorted({s for s in expired if s}),
        "checked": len(rows),
        "max_holding_sessions": max_sessions,
    }


def _close_expired(db: Session, row: Signal, decision: dict, max_sessions: int,
                   now: datetime) -> None:
    """Đóng 1 tín hiệu quá hạn T+ (kèm lịch sử trạng thái để audit được)."""
    from_state = str(row.state or "")
    row.state = "EXIT"
    row.signal = "SELL"
    row.is_active = False
    row.closed_at = now
    row.exit_price = row.price
    row.exit_reason = (f"Hết thời hạn nắm giữ T+{max_sessions} ({decision.get('metric')}) "
                       f"— tín hiệu hết hiệu lực")[:120]
    if row.entry and row.exit_price:
        row.return_pct = round((row.exit_price / row.entry - 1) * 100, 3)
        row.result = "WIN" if row.return_pct > 0 else "LOSS" if row.return_pct < 0 else "FLAT"
    else:
        row.result = "UNKNOWN"
    holding = decision.get("sessions")
    row.holding_days = holding if holding is not None else decision.get("days")
    row.updated_at = now
    db.add(SignalHistory(
        signal_id=row.id, symbol=row.symbol, strategy=row.strategy,
        from_state=from_state, to_state="EXIT", signal="SELL",
        reason=row.exit_reason, price=row.price, score=row.score,
        exit_price=row.exit_price, return_pct=row.return_pct,
        holding_days=row.holding_days, result=row.result,
        detail_json=_dump({
            "expired": True, "max_holding_sessions": max_sessions,
            "sessions": decision.get("sessions"), "days": decision.get("days"),
            "basis": decision.get("basis"),
        }),
    ))


def expired_symbols_since(db: Session, since: datetime | None = None) -> list[str]:
    """Mã có tín hiệu bị ĐÓNG do HẾT HẠN T+ kể từ mốc ``since`` (thường là phiên hiện tại).

    Dùng cho ``engine.refresh_open_signals``: mã vừa hết hạn cần được phân tích lại theo nến
    mới nhất để tạo tín hiệu MỚI (nếu setup vẫn còn tốt) — nếu không, mã đó sẽ không bao giờ
    được cập nhật lại cho tới lần "Quét thị trường" tiếp theo (vì không còn tín hiệu đang mở).
    """
    query = db.query(Signal.symbol).filter(
        Signal.is_active == False,  # noqa: E712
        Signal.exit_reason.like("Hết thời hạn nắm giữ T+%"),
    )
    if since is not None:
        query = query.filter(Signal.closed_at >= since)
    return sorted({str(symbol or "").upper() for (symbol,) in query.all() if symbol})


def signal_history(db: Session, *, symbol: str = "", strategy: str = "", limit: int = 100,
                   offset: int = 0) -> dict[str, Any]:
    query = db.query(SignalHistory)
    if symbol:
        query = query.filter(SignalHistory.symbol == symbol.upper())
    if strategy:
        query = query.filter(SignalHistory.strategy == strategy.upper())
    total = query.count()
    rows = query.order_by(SignalHistory.id.desc()).offset(max(0, offset)).limit(max(1, min(limit, 300))).all()
    return {
        "total": total,
        "items": [
            {
                "id": h.id, "signal_id": h.signal_id, "symbol": h.symbol, "strategy": h.strategy,
                "from_state": h.from_state, "to_state": h.to_state, "signal": h.signal,
                "reason": h.reason, "price": h.price, "score": h.score,
                "exit_price": h.exit_price, "return_pct": h.return_pct,
                "holding_days": h.holding_days, "result": h.result,
                "created_at": iso_utc(h.created_at),
            }
            for h in rows
        ],
    }


# ---------------------------------------------------------------------------
# Thống kê tổng quan (spec §17) — chỉ tính từ dữ liệu CÓ THẬT
# ---------------------------------------------------------------------------
def overview_stats(db: Session, symbols: set[str] | None = None) -> dict[str, Any]:
    """Thống kê tổng quan. ``symbols`` = tập mã được phép tính (bộ lọc thanh khoản);
    ``None``/rỗng = tính trên TOÀN BỘ tín hiệu."""
    scope = [Signal.symbol.in_(sorted(symbols))] if symbols else []
    total = db.query(func.count(Signal.id)).filter(*scope).scalar() or 0
    by_strategy = dict(
        db.query(Signal.strategy, func.count(Signal.id)).filter(*scope).group_by(Signal.strategy).all()
    )
    by_state = dict(
        db.query(Signal.state, func.count(Signal.id)).filter(*scope).group_by(Signal.state).all()
    )
    by_signal = dict(
        db.query(Signal.signal, func.count(Signal.id)).filter(*scope).group_by(Signal.signal).all()
    )
    active = db.query(func.count(Signal.id)).filter(*scope).filter(Signal.is_active == True).scalar() or 0  # noqa: E712
    closed = db.query(Signal).filter(*scope).filter(Signal.is_active == False).all()  # noqa: E712

    returns = [float(r.return_pct) for r in closed if r.return_pct is not None]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "total_signals": total,
        "active_signals": active,
        "closed_signals": len(closed),
        "by_strategy": {k or "UNKNOWN": v for k, v in by_strategy.items()},
        "by_state": {k or "UNKNOWN": v for k, v in by_state.items()},
        "by_signal": {k or "NONE": v for k, v in by_signal.items()},
        "performance": {
            "closed_with_result": len(returns),
            "win_rate": round(len(wins) / len(returns) * 100, 2) if returns else None,
            "average_gain_pct": round(sum(wins) / len(wins), 3) if wins else None,
            "average_loss_pct": round(sum(losses) / len(losses), 3) if losses else None,
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
            "max_drawdown_pct": round(max_dd, 3) if returns else None,
            "average_holding_days": round(
                sum((r.holding_days or 0) for r in closed if r.holding_days is not None) /
                max(1, len([r for r in closed if r.holding_days is not None])), 1
            ) if closed else None,
        },
        "note": "Thống kê chỉ tính trên signal ĐÃ ĐÓNG có return_pct thật; rỗng nghĩa là chưa có dữ liệu.",
    }


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
def save_backtest(db: Session, result: dict[str, Any]) -> int:
    row = BacktestResult(
        symbol=str(result.get("symbol") or ""),
        strategy=str(result.get("strategy") or ""),
        start_date=str(result.get("start_date") or ""),
        end_date=str(result.get("end_date") or ""),
        status=str(result.get("status") or "OK"),
        params_json=_dump(result.get("params")),
        stats_json=_dump(result.get("stats")),
        trades_json=_dump(result.get("trades")),
    )
    db.add(row)
    db.commit()
    return row.id


def list_backtests(db: Session, *, symbol: str = "", strategy: str = "", limit: int = 20) -> dict[str, Any]:
    query = db.query(BacktestResult)
    if symbol:
        query = query.filter(BacktestResult.symbol == symbol.upper())
    if strategy:
        query = query.filter(BacktestResult.strategy == strategy.upper())
    rows = query.order_by(BacktestResult.id.desc()).limit(max(1, min(limit, 100))).all()
    return {
        "items": [
            {
                "id": r.id, "symbol": r.symbol, "strategy": r.strategy, "status": r.status,
                "start_date": r.start_date, "end_date": r.end_date,
                "stats": _load_json(r.stats_json), "params": _load_json(r.params_json),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
