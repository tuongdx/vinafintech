"""Backtest Engine (spec §22-§23) — mô phỏng ĐÚNG điều kiện dữ liệu tại thời điểm T.

CHỐNG LOOK-AHEAD:
- Tại nến i, feature chỉ được tính trên ``bars[:i+1]`` (và benchmark cắt theo thời gian
  ``<= bars[i].time``) — không có giá/volume/indicator tương lai.
- Vào lệnh tại GIÁ ĐÓNG CỬA nến tín hiệu; thoát lệnh chỉ dùng dữ liệu các nến SAU đó
  (mô phỏng thực tế: biết tín hiệu ở cuối phiên, khớp lệnh phiên sau/trong phiên sau).
- Nếu cùng 1 nến chạm cả SL và TP ⇒ giả định **SL khớp trước** (kịch bản xấu nhất).

Không tạo số liệu giả: thiếu dữ liệu ⇒ ``status = INSUFFICIENT_DATA`` và các chỉ số = None.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.signal_engine import config as cfg_module
from app.services.signal_engine import features as feat_module
from app.services.signal_engine import indicators as ind
from app.services.signal_engine.ai.ensemble import EnsembleModel
from app.services.signal_engine.strategies import STRATEGIES

logger = logging.getLogger("tunix.signal.backtest")

_STRATEGY_BY_NAME = {m.NAME: m for m in STRATEGIES}


def _bench_slice(bench_bars: list[dict], cutoff: str) -> list[dict]:
    """Cắt benchmark theo thời gian <= cutoff (không dùng dữ liệu tương lai)."""
    if not bench_bars or not cutoff:
        return []
    out: list[dict] = []
    for bar in bench_bars:
        if str(bar.get("time") or "") <= cutoff:
            out.append(bar)
        else:
            break
    return out


def run(symbol: str, strategy: str = "UPTREND", bars: list[dict] | None = None,
        bench_bars: list[dict] | None = None, cfg: dict | None = None,
        max_bars: int = 400) -> dict[str, Any]:
    """Chạy backtest 1 mã + 1 chiến thuật. Trả stats (None khi thiếu dữ liệu)."""
    cfg = cfg or cfg_module.get_config()
    bt_cfg = cfg.get("backtest") or {}
    engine_cfg = cfg.get("engine") or {}
    strategy = strategy.upper()
    module = _STRATEGY_BY_NAME.get(strategy)
    min_bars = int(engine_cfg.get("min_bars", 200))

    result: dict[str, Any] = {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "status": "OK",
        "params": {
            "max_bars": max_bars,
            "min_bars": min_bars,
            "fee_pct": bt_cfg.get("fee_pct", 0.15),
            "config_version": cfg_module.config_version(),
        },
        "stats": None,
        "trades": [],
    }
    if module is None:
        result["status"] = "UNKNOWN_STRATEGY"
        return result

    if bars is None:
        from app.services import vnstock_service

        try:
            bars = vnstock_service.get_history(symbol, str(engine_cfg.get("interval") or "1d"), str(engine_cfg.get("history_period") or "2y"))
        except Exception as exc:  # noqa: BLE001
            result["status"] = "DATA_UNAVAILABLE"
            result["error"] = str(exc)
            return result

    bars = feat_module.normalize_bars(bars)
    if len(bars) < min_bars + 10:
        result["status"] = "INSUFFICIENT_DATA"
        result["stats"] = {
            "available": False,
            "reason": f"Insufficient historical data (cần >= {min_bars + 10} nến, có {len(bars)})",
        }
        return result

    window = bars[-max_bars:] if len(bars) > max_bars else bars
    bench_bars = bench_bars or []
    fee_pct = float(bt_cfg.get("fee_pct", 0.15))
    max_trades = int(bt_cfg.get("max_trades", 40))
    ensemble = EnsembleModel(cfg)

    trades: list[dict] = []
    open_trade: dict[str, Any] | None = None

    for i in range(min_bars - 1, len(window)):
        history = window[: i + 1]
        cutoff = str(window[i].get("time") or "")
        features = feat_module.build_features(history, _bench_slice(bench_bars, cutoff), cfg, symbol)
        if features.get("status") != feat_module.STATUS_OK:
            continue
        ai_payload = ensemble.predict(features)
        score = None
        try:
            from app.services.signal_engine import scoring as scoring_module

            mode = "bottom" if strategy == "BOTTOM_FISHING" else "bullish"
            score = scoring_module.signal_score(features, ai_payload, mode, cfg).get("total")
        except Exception:  # noqa: BLE001
            score = None

        outcome = module.run(features, ai_payload, {}, cfg=cfg, score=score)
        close = float(window[i]["close"])
        high = float(window[i]["high"])
        low = float(window[i]["low"])

        if open_trade is None:
            if outcome.get("signal") == "BUY" and len(trades) < max_trades:
                atr = (features.get("volatility") or {}).get("atr14")
                risk_cfg = cfg.get("risk") or {}
                if atr:
                    open_trade = {
                        "symbol": symbol.upper(),
                        "strategy": strategy,
                        "entry_index": i,
                        "entry_date": cutoff,
                        "entry": close,
                        "stop_loss": close - float(risk_cfg.get("atr_stop_multiple", 2.0)) * atr,
                        "take_profit": close + float(risk_cfg.get("atr_target_multiple", 4.0)) * atr,
                        "score": score,
                    }
            continue

        # Đang có lệnh: kiểm tra thoát bằng dữ liệu nến HIỆN TẠI (không dùng tương lai)
        exit_reason = None
        exit_price = close
        if low <= open_trade["stop_loss"]:
            exit_reason, exit_price = "STOP_LOSS", open_trade["stop_loss"]
        elif high >= open_trade["take_profit"]:
            exit_reason, exit_price = "TAKE_PROFIT", open_trade["take_profit"]
        elif outcome.get("exit_condition"):
            exit_reason, exit_price = "EXIT_CONDITION", close

        if exit_reason:
            gross = exit_price / open_trade["entry"] - 1
            net = gross - 2 * fee_pct / 100
            trades.append({
                **open_trade,
                "exit_date": cutoff,
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "holding_days": i - open_trade["entry_index"],
                "return_pct": round(net * 100, 3),
            })
            open_trade = None

    stats = compute_stats(trades, cfg, still_open=open_trade is not None)
    result["trades"] = trades
    result["stats"] = stats
    result["start_date"] = str(window[min_bars - 1].get("time") or "")
    result["end_date"] = str(window[-1].get("time") or "")
    if stats.get("available") is False:
        result["status"] = stats.get("status") or "INSUFFICIENT_DATA"
    return result


def compute_stats(trades: list[dict], cfg: dict | None = None, still_open: bool = False) -> dict[str, Any]:
    """Thống kê hiệu quả (spec §23). Không đủ số lệnh ⇒ trả available=False + lý do."""
    cfg = cfg or cfg_module.get_config()
    bt_cfg = cfg.get("backtest") or {}
    min_trades = int(bt_cfg.get("min_trades_for_stats", 5))
    if len(trades) < min_trades:
        return {
            "available": False,
            "status": "INSUFFICIENT_DATA",
            "reason": f"Insufficient historical data ({len(trades)} lệnh < {min_trades} lệnh tối thiểu)",
            "number_of_trades": len(trades),
        }

    returns = [float(t["return_pct"]) for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    curve: list[float] = []
    for r in returns:
        equity *= 1 + r / 100
        curve.append(round(equity, 5))
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    mean = sum(returns) / len(returns)
    if len(returns) > 1:
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = var ** 0.5
    else:
        std = 0.0
    avg_holding = sum(t.get("holding_days") or 0 for t in trades) / len(trades)
    sharpe = None
    if std > 0 and avg_holding > 0:
        sharpe = round(mean / std * (252 / max(1.0, avg_holding)) ** 0.5, 3)

    win_rate = len(wins) / len(returns) * 100
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "available": True,
        "total_return_pct": round((equity - 1) * 100, 3),
        "win_rate": round(win_rate, 2),
        "average_win_pct": round(avg_win, 3) if avg_win is not None else None,
        "average_loss_pct": round(avg_loss, 3) if avg_loss is not None else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "max_drawdown_pct": round(max_dd, 3),
        "number_of_trades": len(trades),
        "average_holding_period_days": round(avg_holding, 1),
        "expectancy_pct": round((win_rate / 100) * (avg_win or 0) + (1 - win_rate / 100) * (avg_loss or 0), 3),
        "sharpe_ratio": sharpe,
        "equity_curve": curve,
        "still_open_position": still_open,
        "note": "Vào lệnh tại giá đóng cửa nến tín hiệu; phí 2 chiều theo config.backtest.fee_pct; "
                "nếu 1 nến chạm cả SL và TP thì tính SL trước (kịch bản xấu nhất).",
    }
