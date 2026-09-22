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

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
def default_universe(limit: int | None = None, cfg: dict | None = None) -> list[str]:
    """Danh sách mã để scan: ưu tiên thanh khoản (screener), fallback symbol directory."""
    cfg = cfg or cfg_module.get_config()
    engine_cfg = cfg.get("engine") or {}
    limit = int(limit or engine_cfg.get("universe_limit", 120))
    cached = vnstock_service.cache.get(_UNIVERSE_CACHE_KEY)
    if cached:
        return cached[:limit]

    symbols: list[str] = []
    try:
        page = vnstock_service.get_stock_screener_page() or {}
        rows = [r for r in (page.get("stocks") or []) if isinstance(r, dict)]
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
    vnstock_service.cache.set(_UNIVERSE_CACHE_KEY, symbols, ttl_seconds=1800)
    return symbols[:limit]


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
