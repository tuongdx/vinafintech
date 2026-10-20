"""Signal Engine orchestrator (spec §29) — phân tích 1 mã & scan hàng loạt.

Luồng 1 mã:
    get_history (đã cache ở vnstock_service)
    → Feature Engineering        (features.build_features — chống look-ahead)
    → AI Ensemble                (ai/ensemble — hiện MODEL_NOT_TRAINED)
    → Market Regime              (market_regime.detect)
    → Strategy Engine            (3 chiến thuật)
    → Signal Score / Setup / Risk (scoring + risk)
    → State Machine              (state_machine.next_state)
    → Snapshot (persist tuỳ chọn qua repository)

Hiệu năng: snapshot mỗi mã cache TTL (config.engine.cache_ttl_seconds) + scan song song
ThreadPool (config.engine.scan_workers) + dùng lại cache lịch sử giá của vnstock_service.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import vnstock_service
from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import features as feat_module
from app.services.signal_engine import indicators as ind
from app.services.signal_engine import market_regime, risk, scoring, state_machine
from app.services.signal_engine.ai.ensemble import EnsembleModel
from app.services.signal_engine.strategies import evaluate_all

logger = logging.getLogger("tunix.signal.engine")

_SNAPSHOT_CACHE_PREFIX = "signal-snapshot:"
_MARKET_CACHE_KEY = "signal-market-snapshot"
_UNIVERSE_CACHE_KEY = "signal-universe"

# --- Chống LỆCH danh sách khuyến nghị giữa các tài khoản ---
# Mọi tài khoản đọc CÙNG một bảng `signals` (không lọc theo user), nhưng trang Tín hiệu gọi
# POST /signals/refresh mỗi lần mở trang và refresh lại GHI vào bảng chung. Nếu tài khoản nào
# mở trang cũng refresh thì danh sách có thể đổi giữa 2 người mở lệch nhau vài giây.
# ⇒ Chỉ cho phép refresh TOÀN HỆ THỐNG tối đa 1 lần / REFRESH_COOLDOWN_SECONDS (mặc định 5 phút).
_REFRESH_STATE_KEY = "signal_engine_last_refresh"
_REFRESH_COOLDOWN_SECONDS = 300
_refresh_lock = threading.Lock()

# Chiến thuật dùng chế độ chấm điểm nào (bullish: thưởng xu hướng tăng; bottom: thưởng quá bán)
_SCORE_MODE = {"UPTREND": "bullish", "BOTTOM_FISHING": "bottom", "SWING": "bullish"}
_STRENGTH_ORDER = ["WEAK", "MODERATE", "STRONG", "VERY_STRONG"]


def _apply_signal_gate(signal: str | None, strength_level: str | None, cfg: dict) -> tuple[str | None, str | None]:
    """Cổng chất lượng (config.signal.require_min_setup_strength).

    CHỈ chặn BUY — SELL (thoát lệnh) KHÔNG bao giờ bị chặn. Dùng chung cho cả đường
    tính trực tiếp và đường đọc từ cache (nếu không, signal bị lọc sẽ "sống lại" khi cache hit).
    """
    min_level = str(((cfg.get("signal") or {}).get("require_min_setup_strength") or "")).upper()
    if not min_level or signal != "BUY":
        return signal, None
    level = str(strength_level or "WEAK").upper()
    if level in _STRENGTH_ORDER and min_level in _STRENGTH_ORDER and _STRENGTH_ORDER.index(level) < _STRENGTH_ORDER.index(min_level):
        return None, f"Setup Strength {level} < {min_level} — chưa đủ độ đồng thuận để phát signal"
    return signal, None


# ---------------------------------------------------------------------------
# Bối cảnh thị trường
# ---------------------------------------------------------------------------
def market_snapshot(cfg: dict | None = None, force: bool = False) -> dict[str, Any]:
    """Benchmark (VNINDEX) + độ rộng thị trường + Market Regime (cache ngắn)."""
    cfg = cfg or cfg_module.get_config()
    engine_cfg = cfg.get("engine") or {}
    ttl = int(engine_cfg.get("snapshot_ttl_seconds", 300))
    if not force:
        cached = vnstock_service.cache.get(_MARKET_CACHE_KEY)
        if cached is not None:
            return cached

    benchmark = str(engine_cfg.get("benchmark") or "VNINDEX")
    bars: list[dict] = []
    try:
        bars = vnstock_service.get_history(benchmark, "1d", "1y") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("benchmark history lỗi (%s): %s", benchmark, exc)

    breadth: dict | None = None
    try:
        dashboard = vnstock_service.get_market_dashboard() or {}
        breadth = dashboard.get("breadth") if isinstance(dashboard.get("breadth"), dict) else None
    except Exception:  # noqa: BLE001 — thiếu breadth vẫn tính được regime từ benchmark
        breadth = None

    regime = market_regime.detect(bars, breadth, benchmark, cfg)
    payload = {
        "benchmark": benchmark,
        "bars": len(bars),
        "benchmark_bars": bars,
        "breadth": breadth,
        "regime": regime,
        "as_of": bars[-1].get("time") if bars else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    vnstock_service.cache.set(_MARKET_CACHE_KEY, payload, ttl_seconds=ttl)
    return payload


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
def cached_benchmark_bars() -> list[dict[str, Any]]:
    """Nến benchmark (VNINDEX) lấy từ CACHE — KHÔNG gọi mạng.

    Dùng cho kiểm tra hết hạn T+ ở các endpoint GET (``/signals``, ``/signals/overview``):
    chỉ đóng tín hiệu quá hạn theo số phiên khi cache thị trường đã có sẵn, tránh để một
    request đọc dữ liệu phải chờ gọi nguồn ngoài.
    """
    snapshot = vnstock_service.cache.get(_MARKET_CACHE_KEY)
    bars = (snapshot or {}).get("benchmark_bars") or []
    return list(bars)


def liquidity_min_ty(cfg: dict | None = None) -> float:
    """Ngưỡng thanh khoản của hệ thống: GTGD TRUNG BÌNH 20 PHIÊN ≥ ? tỷ VND/phiên.

    Đọc từ ``engine.min_avg_liquidity_20d_ty`` (≤ 0 = TẮT bộ lọc). Đây là NGUỒN DUY NHẤT,
    dùng cho cả vũ trụ quét (`default_universe`) lẫn bộ lọc hiển thị ở trang Tín hiệu
    (router `signals_admin._liquidity_filter`).
    """
    cfg = cfg or cfg_module.get_config()
    try:
        return float((cfg.get("engine") or {}).get("min_avg_liquidity_20d_ty") or 0) or 0.0
    except (TypeError, ValueError):
        return 0.0


def liquid_universe_symbols(cfg: dict | None = None) -> set[str]:
    """Tập mã đạt ngưỡng THANH KHOẢN (`liquidity_min_ty`, tỷ VND/phiên).

    Một nguồn duy nhất cho cả 2 chỗ: (1) vũ trụ quét (`default_universe`) và (2) bộ lọc
    hiển thị ở trang Tín hiệu ⇒ KHÔNG bao giờ tạo ra tín hiệu mà trang lại không hiển thị.
    Trả ``set()`` khi tắt lọc hoặc khi nguồn dữ liệu lỗi (bên gọi PHẢI bỏ qua khi rỗng).
    """
    min_ty = liquidity_min_ty(cfg)
    return vnstock_service.liquid_symbols_20d(min_ty) if min_ty > 0 else set()


def default_universe(limit: int | None = None, cfg: dict | None = None) -> list[str]:
    """Danh sách mã để scan: ưu tiên thanh khoản (screener), fallback symbol directory.

    ``limit`` = None ⇒ trần mặc định của engine (``engine.universe_limit``);
    ``limit <= 0`` ⇒ KHÔNG giới hạn (dùng cho quét TỰ ĐỘNG toàn thị trường — xem ``scheduler``).
    """
    cfg = cfg or cfg_module.get_config()
    engine_cfg = cfg.get("engine") or {}
    cap = int(engine_cfg.get("universe_limit", 120) if limit is None else limit)
    # Thanh khoản TB20 là 1 phần của QUY TẮC VŨ TRỤ ⇒ đưa ngưỡng vào KEY cache để đổi cấu
    # hình có hiệu lực NGAY (không phải chờ cache cũ hết hạn 30 phút).
    min_ty = liquidity_min_ty(cfg)
    liquid = liquid_universe_symbols(cfg) if min_ty > 0 else set()
    cache_key = f"{_UNIVERSE_CACHE_KEY}:{min_ty:g}" if liquid else f"{_UNIVERSE_CACHE_KEY}:raw"
    cached = vnstock_service.cache.get(cache_key)
    if cached:
        return list(cached) if cap <= 0 else list(cached[:cap])

    symbols: list[str] = []
    try:
        page = vnstock_service.get_stock_screener_page() or {}
        rows = [r for r in (page.get("stocks") or []) if isinstance(r, dict)]
        # CHỈ quét cổ phiếu VỐN HOÁ LỚN + THANH KHOẢN đủ để giao dịch hằng ngày
        # (bộ lọc dùng chung với Market Radar / Khám phá theo chiến lược / Xếp hạng TuniX).
        rows = [r for r in rows if vnstock_service.is_tradable_stock(r)]
        # + Thanh khoản TRUNG BÌNH 20 PHIÊN >= ngưỡng (mặc định 5 tỷ/phiên) — cùng quy tắc
        # với bộ lọc hiển thị ở trang Tín hiệu (xem `liquid_universe_symbols`).
        if liquid:
            rows = [r for r in rows if str(r.get("symbol") or "").upper() in liquid]
        rows.sort(key=lambda r: (r.get("volume") or 0), reverse=True)
        symbols = [str(r.get("symbol") or "").upper() for r in rows if r.get("symbol")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("không lấy được screener cho universe: %s", exc)

    if not symbols:
        try:
            symbols = [str(r.get("symbol") or "").upper() for r in vnstock_service._get_combined_stock_universe()]
        except Exception:  # noqa: BLE001
            symbols = []

    symbols = [s for s in symbols if s]
    vnstock_service.cache.set(cache_key, symbols, ttl_seconds=1800)
    return symbols if cap <= 0 else symbols[:cap]


# ---------------------------------------------------------------------------
# Phân tích 1 mã
# ---------------------------------------------------------------------------
def analyze_symbol(symbol: str, cfg: dict | None = None, market: dict | None = None,
                   previous_states: dict[str, str] | None = None,
                   use_cache: bool = True, db=None) -> dict[str, Any]:
    """Snapshot phân tích đầy đủ cho 1 mã (chứa CẢ field admin — serializer sẽ lọc)."""
    symbol = (symbol or "").strip().upper()
    cfg = cfg or cfg_module.get_config()
    engine_cfg = cfg.get("engine") or {}
    ttl = int(engine_cfg.get("cache_ttl_seconds", 900))
    cache_key = f"{_SNAPSHOT_CACHE_PREFIX}{symbol}:{cfg_module.config_version()}"
    if use_cache:
        cached = vnstock_service.cache.get(cache_key)
        if cached is not None:
            # Vẫn cập nhật lại state theo DB hiện tại (không stale state) VÀ persist lại
            # (nếu bỏ persist ở đây, bảng signals sẽ giữ giá trị cũ khi quét bằng cache).
            restated = _restate(cached, previous_states, cfg)
            _persist(db, restated)
            return restated

    started = time.perf_counter()
    market = market or market_snapshot(cfg)
    interval = str(engine_cfg.get("interval") or "1d")
    period = str(engine_cfg.get("history_period") or "2y")

    bars: list[dict] = []
    data_error: str | None = None
    try:
        bars = vnstock_service.get_history(symbol, interval, period) or []
    except Exception as exc:  # noqa: BLE001
        data_error = f"DATA_UNAVAILABLE: {exc}"
        logger.warning("history lỗi %s: %s", symbol, exc)

    features = feat_module.build_features(bars, market.get("benchmark_bars"), cfg, symbol)
    if data_error:
        features.setdefault("errors", []).append(data_error)
        if features.get("status") == feat_module.STATUS_OK:
            features["status"] = feat_module.STATUS_DATA_UNAVAILABLE

    ai_payload = EnsembleModel(cfg).predict(features)
    regime = market.get("regime") or {}

    scores_bullish = scoring.signal_score(features, ai_payload, "bullish", cfg)
    scores_bottom = scoring.signal_score(features, ai_payload, "bottom", cfg)
    strategy_scores = {
        "UPTREND": scores_bullish.get("total"),
        "SWING": scores_bullish.get("total"),
        "BOTTOM_FISHING": scores_bottom.get("total"),
    }

    outcomes = evaluate_all(features, ai_payload, regime, previous_states or {}, strategy_scores, cfg)

    data_ok = features.get("status") == feat_module.STATUS_OK
    strategies: dict[str, Any] = {}
    for name, outcome in outcomes.items():
        if not outcome.get("evaluated"):
            strategies[name] = outcome
            continue
        prev = (previous_states or {}).get(name)
        transition = state_machine.next_state(
            prev,
            int(outcome.get("confirmations") or 0),
            int(outcome.get("total_conditions") or 0),
            bool(outcome.get("required_passed")),
            int(outcome.get("bad_signal_count") or 0),
            bool(outcome.get("exit_condition")),
            data_ok,
            cfg,
        )
        levels = risk.trade_levels(features, name, cfg)
        strength = scoring.setup_strength(
            {"total": strategy_scores.get(name)},
            int(outcome.get("confirmations") or 0),
            int(outcome.get("total_conditions") or 0),
            outcome.get("conditions") or [],
            ai_payload,
            cfg,
        )
        conf = scoring.confidence(
            {"total": strategy_scores.get(name)},
            float(strength.get("confirm_ratio") or 0.0),
            outcome.get("conditions") or [],
            features.get("data_quality"),
            ai_payload,
        )

        signal = outcome.get("signal") if transition["state"] in (state_machine.CONFIRMED, state_machine.ACTIVE) else None
        if transition["state"] == state_machine.EXIT and prev in (state_machine.CONFIRMED, state_machine.ACTIVE, state_machine.WEAKENING):
            signal = "SELL"

        # Cổng chất lượng: setup WEAK không đủ độ đồng thuận để phát signal MUA.
        signal, gate_note = _apply_signal_gate(signal, strength.get("level"), cfg)

        strategies[name] = {
            **outcome,
            "state": transition["state"],
            "transition": transition,
            "setup_strength": strength,
            "confidence": conf,
            "score": strategy_scores.get(name),
            "levels": levels,
            "signal": signal,
            "signal_gate": gate_note,
            "reasons": _why(outcome, features, ai_payload, regime) + ([gate_note] if gate_note else []),
        }

    snapshot = _assemble(symbol, features, ai_payload, regime, market, scores_bullish, scores_bottom, strategies, cfg)
    snapshot["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    if use_cache and data_ok:
        vnstock_service.cache.set(cache_key, snapshot, ttl_seconds=ttl)
    _persist(db, snapshot)
    return snapshot


def _persist(db, snapshot: dict) -> None:
    """Lưu snapshot vào DB (best-effort: lỗi lưu không được làm hỏng kết quả phân tích).

    Lưu ý: rollback khi lỗi để session của caller không bị "poisoned" (nếu không, mọi
    query sau đó sẽ báo "transaction has been rolled back due to a previous exception").
    """
    if db is None:
        return
    try:
        from app.services.signal_engine import repository

        repository.save_snapshot(db, snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.warning("lưu snapshot signal lỗi (%s): %s", snapshot.get("symbol"), exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _restate(cached: dict, previous_states: dict[str, str] | None, cfg: dict) -> dict:
    """Snapshot lấy từ cache vẫn phải tính lại state theo DB hiện tại (không stale state)."""
    if not previous_states:
        return cached
    clone = {**cached, "strategies": {k: {**v} for k, v in (cached.get("strategies") or {}).items()}}
    for name, strat in clone["strategies"].items():
        if not strat.get("evaluated"):
            continue
        prev = previous_states.get(name)
        transition = state_machine.next_state(
            prev,
            int(strat.get("confirmations") or 0),
            int(strat.get("total_conditions") or 0),
            bool(strat.get("required_passed")),
            int(strat.get("bad_signal_count") or 0),
            bool(strat.get("exit_condition")),
            True,
            cfg,
        )
        strat["state"] = transition["state"]
        strat["transition"] = transition
        signal = strat.get("signal")
        if signal == "SELL" and prev not in (state_machine.CONFIRMED, state_machine.ACTIVE, state_machine.WEAKENING):
            signal = None
        if transition["state"] not in (state_machine.CONFIRMED, state_machine.ACTIVE):
            signal = "SELL" if (transition["state"] == state_machine.EXIT and prev in (state_machine.CONFIRMED, state_machine.ACTIVE, state_machine.WEAKENING)) else None
        # BẮT BUỘC áp lại cổng chất lượng: nếu bỏ qua, signal đã bị lọc sẽ "sống lại" khi cache hit.
        signal, gate_note = _apply_signal_gate(signal, (strat.get("setup_strength") or {}).get("level"), cfg)
        strat["signal_gate"] = gate_note
        strat["signal"] = signal
    return clone


def _assemble(symbol: str, features: dict, ai_payload: dict, regime: dict, market: dict,
              scores_bullish: dict, scores_bottom: dict, strategies: dict[str, Any],
              cfg: dict) -> dict[str, Any]:
    """Chọn chiến thuật chính (primary) + gộp snapshot."""
    ranked = sorted(
        ((name, s) for name, s in strategies.items() if s.get("evaluated")),
        key=lambda kv: (
            1 if kv[1].get("signal") else 0,
            float((kv[1].get("setup_strength") or {}).get("score") or 0),
        ),
        reverse=True,
    )
    primary_name = ranked[0][0] if ranked else None
    primary = strategies.get(primary_name) if primary_name else None

    return {
        "symbol": symbol,
        "engine": "TuniX Signal Engine",
        "config_version": cfg_module.config_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": features.get("as_of"),
        "price": features.get("price"),
        "status": features.get("status"),
        "errors": features.get("errors") or [],
        "features": features,
        "ai": ai_payload,
        "market": {"regime": regime, "breadth": market.get("breadth"), "benchmark": market.get("benchmark")},
        "scores": {
            "bullish": scores_bullish,
            "bottom": scores_bottom,
            "primary_mode": _SCORE_MODE.get(primary_name or "", "bullish"),
        },
        "strategies": strategies,
        "primary_strategy": primary_name,
        "setup": (primary or {}).get("setup_strength") or {},
        "risk": risk.risk_score(features, regime, cfg),
        "decision": {
            "state": (primary or {}).get("state") or state_machine.NO_SETUP,
            "strategy": primary_name,
            "signal": (primary or {}).get("signal"),
            "levels": (primary or {}).get("levels") or {},
            "confidence": ((primary or {}).get("confidence") or {}).get("value"),
            "exit_condition": bool((primary or {}).get("exit_condition")),
            "bad_signal_count": int((primary or {}).get("bad_signal_count") or 0),
        },
    }


def _why(outcome: dict, features: dict, ai_payload: dict | None, regime: dict) -> list[str]:
    """"Why this signal was generated?" (spec §18) — diễn giải các điều kiện ĐÃ ĐẠT."""
    reasons: list[str] = []
    for cond in outcome.get("conditions") or []:
        if cond.get("state") == "PASS":
            reasons.append(f"{cond.get('label')} = {_short(cond.get('value'))}")
        elif cond.get("state") == "UNAVAILABLE" and cond.get("group") == "ai":
            reasons.append(f"AI ({cond.get('key')}): chưa có model train — bỏ khỏi xác nhận")
    failed = [c.get("label") for c in (outcome.get("conditions") or []) if c.get("state") == "FAIL"]
    if failed:
        reasons.append("Chưa đạt: " + "; ".join(str(f) for f in failed[:4]))
    if regime.get("regime"):
        reasons.append(f"Market Regime = {regime.get('regime')}")
    return reasons


def _short(value: Any, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Scan hàng loạt
# ---------------------------------------------------------------------------
def scan(symbols: list[str] | None = None, limit: int | None = None, cfg: dict | None = None,
         db=None, persist: bool = False, use_cache: bool = True) -> dict[str, Any]:
    """Scan nhiều mã song song → danh sách snapshot + xếp hạng/rotation."""
    cfg = cfg or cfg_module.get_config()
    engine_cfg = cfg.get("engine") or {}
    symbols = [s.strip().upper() for s in (symbols or []) if s and s.strip()]
    if not symbols:
        symbols = default_universe(limit, cfg)
    symbols = symbols[: int(limit or engine_cfg.get("universe_limit", 120))]

    market = market_snapshot(cfg)
    previous: dict[str, dict[str, str]] = {}
    if db is not None:
        try:
            from app.services.signal_engine import repository

            previous = repository.previous_states_bulk(db, symbols)
        except Exception as exc:  # noqa: BLE001
            logger.warning("không đọc được state cũ: %s", exc)
            previous = {}

    workers = max(1, int(engine_cfg.get("scan_workers", 8)))
    results: list[dict[str, Any]] = []

    def _one(sym: str) -> dict[str, Any]:
        try:
            return analyze_symbol(sym, cfg, market, previous.get(sym), use_cache=use_cache, db=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("analyze lỗi %s: %s", sym, exc)
            return {"symbol": sym, "status": "FEATURE_CALCULATION_ERROR", "errors": [str(exc)], "strategies": {}}

    if symbols:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_one, symbols))

    if persist and db is not None:
        try:
            from app.services.signal_engine import repository

            for snapshot in results:
                repository.save_snapshot(db, snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist scan lỗi: %s", exc)

    return {
        "as_of": market.get("as_of"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "market": market.get("regime"),
        "items": results,
        "rotation": rotation_plan(results, cfg),
    }


def rotation_plan(results: list[dict[str, Any]], cfg: dict | None = None) -> dict[str, Any]:
    """Portfolio rotation cho SWING (spec §12): TOP N theo score, mã yếu hơn bị đánh dấu thay thế."""
    cfg = cfg or cfg_module.get_config()
    size = int(((cfg.get("strategies") or {}).get("swing") or {}).get("portfolio_size", 5))
    ranked: list[tuple[str, float]] = []
    for item in results:
        strat = (item.get("strategies") or {}).get("SWING") or {}
        score = strat.get("score")
        if score is None or not strat.get("evaluated"):
            continue
        ranked.append((item.get("symbol"), float(score)))
    ranked.sort(key=lambda kv: kv[1], reverse=True)
    leaders = [s for s, _ in ranked[:size]]
    lagging = [s for s, _ in ranked[size:]]
    return {
        "portfolio_size": size,
        "ranked": [{"symbol": s, "score": sc} for s, sc in ranked],
        "leaders": leaders,
        "replaceable": lagging[: max(0, size)],
        "note": "Mã trong 'replaceable' có thứ hạng thấp hơn TOP — ứng viên thay thế nếu đang nắm giữ.",
    }


# ---------------------------------------------------------------------------
# Vòng đời T+ — cập nhật lại tín hiệu ĐANG MỞ (spec §16/§24 mở rộng)
# ---------------------------------------------------------------------------
def _signal_stamp(row: dict[str, Any]) -> Any:
    """Mốc mới nhất của 1 tín hiệu = max(as_of nến, updated_at lần phân tích cuối)."""
    from app.services.signal_engine import lifecycle

    stamps = [lifecycle.parse_time(row.get("as_of")), lifecycle.parse_time(row.get("updated_at"))]
    valid = [s for s in stamps if s is not None]
    return max(valid) if valid else None


def _last_refresh_at(db) -> datetime | None:
    """Mốc refresh TOÀN HỆ THỐNG gần nhất (lưu ở bảng `settings` để đúng cả khi nhiều process)."""
    try:
        from app.models import Setting

        row = db.query(Setting).filter(Setting.key == _REFRESH_STATE_KEY).first()
        if not row or not row.value:
            return None
        data = json.loads(row.value)
        raw = data.get("at") if isinstance(data, dict) else None
        return datetime.fromisoformat(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 — thiếu bảng/lỗi JSON ⇒ coi như chưa refresh
        logger.warning("không đọc được mốc refresh tín hiệu: %s", exc)
        return None


def _mark_refresh_at(db, at: datetime) -> None:
    try:
        from app.models import Setting

        payload = json.dumps({"at": at.isoformat()})
        row = db.query(Setting).filter(Setting.key == _REFRESH_STATE_KEY).first()
        if row:
            row.value = payload
        else:
            db.add(Setting(key=_REFRESH_STATE_KEY, value=payload))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — không ghi được mốc thì vẫn cho chạy refresh
        logger.warning("không ghi được mốc refresh tín hiệu: %s", exc)


def refresh_open_signals(db=None, cfg: dict | None = None, max_symbols: int | None = None,
                         use_cache: bool = True, force: bool = False) -> dict[str, Any]:
    """Cập nhật lại tín hiệu ĐANG MỞ của TuniX Signal Engine (tín hiệu T+).

    Gồm 3 bước:
    1. **Đóng tín hiệu QUÁ HẠN**: tín hiệu đang hiệu lực (CONFIRMED/ACTIVE/WEAKENING) đã quá
       ``signal.expiry.max_holding_sessions`` phiên kể từ khi mở → đóng (EXIT) kèm lý do.
    2. **Phân tích lại tín hiệu CŨ**: các mã còn tín hiệu đang mở (WATCH/SETUP_FORMING/…)
       nhưng dữ liệu đã cũ hơn phiên mới nhất → chạy lại engine với nến mới + trạng thái cũ
       (state machine tự chuyển sang WEAKENING/EXIT/INVALIDATED nếu setup đã hỏng).
    3. **Tạo lại tín hiệu cho mã vừa hết hạn**: nếu setup vẫn còn tốt với nến hôm nay thì
       engine tạo tín hiệu MỚI (created_at mới ⇒ lại có đủ hạn nắm giữ T+ tiếp theo) thay vì
       giữ mãi dòng cũ đã đóng.

    Chạy KHÔNG chặn UI (trang Tín hiệu gọi sau khi đã vẽ danh sách). Giới hạn
    ``refresh_max_symbols`` mã mỗi lần, ưu tiên tín hiệu đang hiệu lực + cũ nhất trước.
    """
    from app.services.signal_engine import lifecycle, repository

    cfg = cfg or cfg_module.get_config()
    conf = lifecycle.expiry_config(cfg)
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": None,
        "market": None,
        "enabled": conf["enabled"],
        "max_holding_sessions": conf["max_holding_sessions"],
        "open_signals": 0,
        "expired": 0,
        "expired_symbols": [],
        "refreshed": 0,
        "refreshed_symbols": [],
        "candidates": 0,
        "pending": 0,
        "up_to_date": 0,
    }
    if db is None:
        return result

    # Giới hạn refresh toàn hệ thống (xem _REFRESH_COOLDOWN_SECONDS): mọi tài khoản dùng chung
    # một lịch ⇒ danh sách khuyến nghị KHÔNG đổi liên tục theo người mở trang.
    # Kiểm tra TRƯỚC khi lấy market snapshot để lần bị bỏ qua không tốn gọi mạng.
    now = datetime.now(timezone.utc)
    if not force:
        with _refresh_lock:
            last = _last_refresh_at(db)
            if last is not None:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = (now - last).total_seconds()
                if 0 <= elapsed < _REFRESH_COOLDOWN_SECONDS:
                    result.update({
                        "skipped": True,
                        "cooldown_remaining": int(_REFRESH_COOLDOWN_SECONDS - elapsed),
                        "last_refresh_at": last.isoformat(),
                    })
                    return result
            _mark_refresh_at(db, now)
    else:
        _mark_refresh_at(db, now)

    market = market_snapshot(cfg)
    bars = list(market.get("benchmark_bars") or [])
    latest = bars[-1].get("time") if bars else None
    result["as_of"] = market.get("as_of")
    result["market"] = market.get("regime")

    closed = repository.expire_overdue(db, benchmark_bars=bars, cfg=cfg)
    result["expired"] = int(closed.get("expired") or 0)
    result["expired_symbols"] = closed.get("symbols") or []

    # Gom theo mã: ưu tiên (1) tín hiệu đang hiệu lực, (2) cũ nhất trước.
    # - Mã vừa bị ĐÓNG do quá hạn trong lần này VÀ mã hết hạn trong phiên hiện tại (do GET
    #   ``/signals`` đóng trước đó) đều phải phân tích lại: nếu setup vẫn còn tốt với nến hôm nay
    #   thì engine tạo tín hiệu MỚI (created_at mới ⇒ lại đủ hạn nắm giữ T+ tiếp theo).
    # - Bỏ qua mã đã có tín hiệu đang mở (nếu không sẽ phân tích lại vô ích mỗi lần mở trang).
    rows = repository.open_signals(db)
    result["open_signals"] = len(rows)
    open_symbols = {row["symbol"] for row in rows}

    candidates: dict[str, tuple[int, Any]] = {}
    since = lifecycle.parse_time(latest) or (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    try:
        recently_expired = repository.expired_symbols_since(db, since)
    except Exception as exc:  # noqa: BLE001
        logger.warning("không đọc được mã vừa hết hạn T+: %s", exc)
        recently_expired = []
    for symbol in list(closed.get("symbols") or []) + recently_expired:
        symbol = str(symbol or "").upper()
        if symbol and symbol not in open_symbols:
            candidates.setdefault(symbol, (0, datetime(1970, 1, 1)))

    for row in rows:
        if row["state"] not in lifecycle.TRACKED_STATES:
            continue
        stamp = _signal_stamp(row)
        if not lifecycle.is_stale(stamp, latest):
            result["up_to_date"] += 1
            continue
        actionable = row["state"] in lifecycle.ACTIVE_STATES
        order = (0 if actionable else 1, stamp or datetime(1970, 1, 1))
        current = candidates.get(row["symbol"])
        if current is None or order < current:
            candidates[row["symbol"]] = order

    ordered = [sym for sym, _ in sorted(candidates.items(), key=lambda kv: kv[1])]
    limit = int(max_symbols or conf["refresh_max_symbols"])
    symbols = ordered[:limit]
    result["candidates"] = len(ordered)
    result["pending"] = max(0, len(ordered) - len(symbols))
    if not symbols:
        return result

    previous = repository.previous_states_bulk(db, symbols)
    workers = max(1, int((cfg.get("engine") or {}).get("scan_workers", 8)))

    def _one(symbol: str) -> dict[str, Any]:
        try:
            return analyze_symbol(symbol, cfg, market, previous.get(symbol),
                                  use_cache=use_cache, db=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh tín hiệu lỗi %s: %s", symbol, exc)
            return {"symbol": symbol, "strategies": {}, "status": "FEATURE_CALCULATION_ERROR"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        snapshots = list(pool.map(_one, symbols))

    # Persist TUẦN TỰ (Session của SQLAlchemy không an toàn khi dùng đa luồng).
    refreshed: list[str] = []
    for snapshot in snapshots:
        if not (snapshot.get("strategies") or {}):
            continue
        try:
            repository.save_snapshot(db, snapshot)
            refreshed.append(str(snapshot.get("symbol") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh persist lỗi %s: %s", snapshot.get("symbol"), exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    result["refreshed"] = len(refreshed)
    result["refreshed_symbols"] = refreshed
    return result
