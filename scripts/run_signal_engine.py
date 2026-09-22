"""CLI TuniX Signal Engine — chạy từ terminal:

    py scripts/run_signal_engine.py --symbol FPT              # phân tích 1 mã (in đầy đủ, có BUY/SELL)
    py scripts/run_signal_engine.py --scan --limit 30         # quét N mã (mặc định KHÔNG ghi DB)
    py scripts/run_signal_engine.py --scan --limit 30 --persist
    py scripts/run_signal_engine.py --backtest FPT --strategy UPTREND
    py scripts/run_signal_engine.py --models                  # trạng thái model AI
    py scripts/run_signal_engine.py --overview                # thống kê signal đã lưu

LƯU Ý: đây là công cụ ADMIN (in cả Entry/Stop Loss). Không dùng cho người dùng cuối.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from app.db import SessionLocal  # noqa: E402
from app.services.signal_engine import backtest, serializers  # noqa: E402
from app.services.signal_engine import engine as engine_module  # noqa: E402
from app.services.signal_engine import model_performance, repository  # noqa: E402
from app.services.signal_engine.ai.ensemble import EnsembleModel  # noqa: E402
from app.services.signal_engine import config as cfg_module  # noqa: E402


def _money(v) -> str:
    return "—" if v is None else f"{round(v):,}".replace(",", ".")


def cmd_symbol(symbol: str, persist: bool) -> None:
    db = SessionLocal() if persist else None
    try:
        snapshot = engine_module.analyze_symbol(symbol, db=db, use_cache=False)
        view = serializers.admin_view(snapshot)
        print(f"\n=== {view['symbol']} · {view['as_of']} · giá {_money(view['price'])} · dữ liệu: {view['status']} ===")
        print(f"Market Regime: {((view.get('market') or {}).get('regime') or {}).get('regime')} · Risk: "
              f"{(view.get('risk') or {}).get('risk_score')} ({(view.get('risk') or {}).get('level')})")
        ai = view.get("ai") or {}
        print(f"AI ensemble: {ai.get('status')} · model train: {ai.get('trained_models')}/{ai.get('total_models')}")
        for name, strat in (view.get("strategies") or {}).items():
            if not strat.get("evaluated"):
                print(f"\n-- {name}: không đánh giá ({strat.get('reason') or strat.get('error')})")
                continue
            levels = strat.get("levels") or {}
            print(f"\n-- {name}: signal={strat.get('signal') or '—'} · state={strat.get('state')} · "
                  f"score={strat.get('score')} · confidence={strat.get('confidence')} · setup={strat.get('setup_strength')}")
            print(f"   Xác nhận {strat.get('confirmations')}/{strat.get('total_conditions')} điều kiện "
                  f"· bad signals {strat.get('bad_signal_count')} · exit={strat.get('exit_condition')}")
            if strat.get("signal"):
                print(f"   Entry {_money(levels.get('entry'))} · SL {_money(levels.get('stop_loss'))} · "
                      f"TP {_money(levels.get('take_profit'))} · Trailing {_money(levels.get('trailing_stop'))} · R:R {levels.get('rr_ratio')}")
            if strat.get("signal_gate"):
                print(f"   ⚠ {strat['signal_gate']}")
            for cond in strat.get("conditions") or []:
                mark = {"PASS": "✓", "FAIL": "✗"}.get(cond.get("state"), "—")
                print(f"     {mark} {cond.get('label')} (giá trị {cond.get('value')} / ngưỡng {cond.get('threshold')})")
    finally:
        if db:
            db.close()


def cmd_scan(limit: int, symbols: list[str], persist: bool) -> None:
    db = SessionLocal() if persist else None
    try:
        result = engine_module.scan(symbols=symbols or None, limit=limit, db=db, persist=persist, use_cache=False)
        print(f"\n=== SCAN {result['count']} mã · regime {(result.get('market') or {}).get('regime')} · "
              f"{'ĐÃ lưu DB' if persist else 'KHÔNG lưu DB (thêm --persist để lưu)'} ===")
        rows = [serializers.admin_list_row(item) for item in result["items"]]
        rows.sort(key=lambda r: (r.get("signal") is not None, r.get("score") or 0), reverse=True)
        for row in rows:
            print(f"  {row['symbol']:<6} {str(row['strategy']):<15} {str(row['signal'] or '—'):<5} "
                  f"state={str(row['state']):<14} score={row['score']} setup={row['setup_strength']} "
                  f"xác nhận={row['confirmations']}/{row['total_conditions']}")
        rot = result.get("rotation") or {}
        print(f"\nRotation SWING (TOP {rot.get('portfolio_size')}): {', '.join(rot.get('leaders') or []) or '—'}")
    finally:
        if db:
            db.close()


def cmd_backtest(symbol: str, strategy: str, persist: bool) -> None:
    result = backtest.run(symbol, strategy)
    db = SessionLocal() if persist else None
    try:
        if persist and result.get("stats"):
            repository.save_backtest(db, result)
        print(f"\n=== BACKTEST {result['symbol']} · {result['strategy']} · {result.get('status')} ===")
        print(f"Khoảng: {result.get('start_date')} → {result.get('end_date')}")
        stats = result.get("stats") or {}
        if stats.get("available") is False:
            print(f"  {stats.get('reason') or 'Insufficient historical data'}")
        for key in ("total_return_pct", "win_rate", "average_win_pct", "average_loss_pct", "profit_factor",
                    "max_drawdown_pct", "number_of_trades", "average_holding_period_days", "expectancy_pct", "sharpe_ratio"):
            if key in stats:
                print(f"  {key}: {stats[key]}")
        print(f"  (số lệnh chi tiết: {len(result.get('trades') or [])})")
    finally:
        if db:
            db.close()


def cmd_models() -> None:
    ensemble = EnsembleModel(cfg_module.get_config())
    print(f"\n=== MODEL AI · ensemble status: {ensemble.status()} ===")
    print(json.dumps(ensemble.model_descriptions(), ensure_ascii=False, indent=2, default=str))


def cmd_overview() -> None:
    db = SessionLocal()
    try:
        stats = repository.overview_stats(db)
        print("\n=== TỔNG QUAN SIGNAL ĐÃ LƯU ===")
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        print("\n=== MODEL PERFORMANCE ===")
        print(json.dumps(model_performance.stats(db), ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="TuniX Signal Engine CLI (ADMIN)")
    parser.add_argument("--symbol", help="Phân tích 1 mã (in cả Entry/Stop Loss)")
    parser.add_argument("--scan", action="store_true", help="Quét nhiều mã")
    parser.add_argument("--limit", type=int, default=20, help="Số mã khi quét (mặc định 20)")
    parser.add_argument("--symbols", default="", help="Danh sách mã khi quét, cách nhau dấu phẩy")
    parser.add_argument("--backtest", metavar="SYMBOL", help="Backtest 1 mã")
    parser.add_argument("--strategy", default="UPTREND", help="UPTREND | BOTTOM_FISHING | SWING")
    parser.add_argument("--persist", action="store_true", help="Lưu kết quả vào DB")
    parser.add_argument("--models", action="store_true", help="Trạng thái model AI")
    parser.add_argument("--overview", action="store_true", help="Thống kê signal đã lưu")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.models:
        cmd_models()
    elif args.overview:
        cmd_overview()
    elif args.backtest:
        cmd_backtest(args.backtest.upper(), args.strategy.upper(), args.persist)
    elif args.scan:
        cmd_scan(args.limit, symbols, args.persist)
    elif args.symbol:
        cmd_symbol(args.symbol.upper(), args.persist)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
