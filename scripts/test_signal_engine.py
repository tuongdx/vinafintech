"""Test VinaFintech Signal Engine (spec §30) — chạy: ``py scripts/test_signal_engine.py``

Bao gồm:
1. Feature: RSI (Wilder, đối chiếu ví dụ chuẩn), MA alignment, momentum, volume, CMF,
   relative strength, khoảng cách 52 tuần.
2. Wavelet MODWT: khai triển đa phân giải có dựng lại CHÍNH XÁC không mất mát.
3. Strategy: Uptrend / Bottom Fishing / Swing (dữ liệu tổng hợp có kiểm soát).
4. State machine: chuyển trạng thái + bad signal accumulation.
5. Permission + data leakage (gọi HTTP tới server local; tự SKIP nếu server không chạy).

Test HTTP cần: server đang chạy ở http://127.0.0.1:8000 và tài khoản admin/member.
Đặt biến môi trường để đổi tài khoản:
    TUNIX_TEST_ADMIN_EMAIL / TUNIX_TEST_ADMIN_PASSWORD
    TUNIX_TEST_MEMBER_EMAIL / TUNIX_TEST_MEMBER_PASSWORD
"""
from __future__ import annotations

import os
import sys
import traceback

# Console Windows mặc định cp1252 → in tiếng Việt sẽ lỗi UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — môi trường không hỗ trợ thì bỏ qua
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.signal_engine import features as feat  # noqa: E402
from app.services.signal_engine import indicators as ind  # noqa: E402
from app.services.signal_engine import serializers, state_machine, wavelet  # noqa: E402
from app.services.signal_engine.ai.ensemble import EnsembleModel  # noqa: E402
from app.services.signal_engine.strategies import bottom_fishing, swing, uptrend  # noqa: E402
from app.services.signal_engine import config as cfg_module  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  FAIL  {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# Dữ liệu tổng hợp
# ---------------------------------------------------------------------------
def make_bars(closes: list[float], volume: float = 1_000_000, spread: float = 0.01,
              start: str = "2023-01-02") -> list[dict]:
    """Sinh nến với NGÀY HỢP LỆ (dùng datetime — tránh sinh ngày 99 như bản cũ)."""
    from datetime import date, timedelta

    base = date.fromisoformat(start)
    bars = []
    for i, close in enumerate(closes):
        bars.append({
            "time": (base + timedelta(days=i)).isoformat() + "T00:00:00+00:00",
            "open": close,
            "high": close * (1 + spread),
            "low": close * (1 - spread),
            "close": close,
            "volume": volume,
        })
    return bars


UPTREND_CLOSES = [100 + i * 0.6 for i in range(200)]          # tăng đều 200 phiên
DOWNTREND_CLOSES = [200 - i * 0.5 for i in range(200)]        # giảm đều 200 phiên
DEEP_DROP_CLOSES = [200 - i * 0.7 for i in range(180)] + [74, 74.2, 74.1, 74.4, 74.3,
                                                          74.6, 74.5, 74.8, 74.7, 75.0,
                                                          75.1, 75.0, 75.3, 75.2, 75.5,
                                                          75.4, 75.7, 75.6, 75.9, 75.8]


def synthetic_features(kind: str) -> dict:
    """Feature tổng hợp mô phỏng 3 tình huống rõ ràng (không dùng dữ liệu thị trường)."""
    if kind == "uptrend":
        return {
            "price": 219.0,
            "trend": {"direction": "UP", "strength": 100.0, "label": "STRONG",
                      "ma": {"available": True, "score": 100.0, "ordered_up": True, "price_above_all": True,
                             "values": {"ma10": 216.6, "ma20": 213.6, "ma50": 204.6}}},
            "momentum": {"short_term": 4.0, "medium_term": 8.0, "long_term": 20.0, "composite": 8.7, "label": "STRONG"},
            "rsi": {"value": 66.0, "state": "BULLISH", "overbought": False, "oversold": False},
            "volume": {"volume_ratio": 1.9, "volume_surge": True, "volume_exhaustion": False, "label": "SURGE"},
            "money_flow": {"cmf": 0.15, "cmf_short": 0.2, "state": "STRONG_INFLOW", "rising": True},
            "relative_strength": {"available": True, "rs_score_raw": 4.5, "label": "STRONG",
                                  "periods": {"short": {"rs": 4.0}, "medium": {"rs": 5.0}}},
            "price_structure": {"available": True, "high_52w": 220.0, "low_52w": 100.0,
                                "distance_from_high_pct": -0.45, "distance_from_low_pct": 119.0,
                                "position_in_range_pct": 99.0, "consolidation_bars": 20,
                                "consolidation_ok": True, "breakout": True, "breakdown": False},
            "volatility": {"atr14": 3.0, "atr_pct": 1.37},
            "data_quality": {"score": 100},
        }
    if kind == "bottom":
        return {
            "price": 75.8,
            "trend": {"direction": "DOWN", "strength": 10.0, "label": "WEAK",
                      "ma": {"available": True, "score": 10.0, "ordered_up": False, "price_above_all": False,
                             "values": {"ma10": 77.0, "ma20": 82.0, "ma50": 110.0}}},
            "momentum": {"short_term": -1.0, "medium_term": -12.0, "long_term": -30.0, "composite": -10.5, "label": "NEGATIVE"},
            "rsi": {"value": 28.0, "state": "OVERSOLD", "overbought": False, "oversold": True},
            "volume": {"volume_ratio": 0.55, "volume_surge": False, "volume_exhaustion": True, "label": "EXHAUSTION"},
            "money_flow": {"cmf": -0.03, "cmf_short": -0.01, "state": "NEUTRAL", "rising": True},
            "relative_strength": {"available": True, "rs_score_raw": -6.0, "label": "VERY_WEAK", "periods": {}},
            "price_structure": {"available": True, "high_52w": 200.0, "low_52w": 74.0,
                                "distance_from_high_pct": -62.0, "distance_from_low_pct": 2.4,
                                "position_in_range_pct": 1.4, "consolidation_bars": 3,
                                "consolidation_ok": False, "breakout": False, "breakdown": False},
            "volatility": {"atr14": 1.6, "atr_pct": 2.1},
            "data_quality": {"score": 100},
        }
    # swing
    return {
        "price": 105.0,
        "trend": {"direction": "UP", "strength": 75.0, "label": "MODERATE",
                  "ma": {"available": True, "score": 75.0, "ordered_up": True, "price_above_all": True,
                         "values": {"ma10": 103.0, "ma20": 101.0, "ma50": 99.0}}},
        "momentum": {"short_term": 2.0, "medium_term": 3.0, "long_term": 6.0, "composite": 3.1, "label": "POSITIVE"},
        "rsi": {"value": 58.0, "state": "BULLISH", "overbought": False, "oversold": False},
        "volume": {"volume_ratio": 1.2, "volume_surge": False, "volume_exhaustion": False, "label": "NORMAL"},
        "money_flow": {"cmf": 0.16, "cmf_short": 0.2, "state": "STRONG_INFLOW", "rising": True},
        "relative_strength": {"available": True, "rs_score_raw": 2.0, "label": "POSITIVE", "periods": {}},
        "price_structure": {"available": True, "high_52w": 110.0, "low_52w": 80.0,
                            "distance_from_high_pct": -4.5, "distance_from_low_pct": 31.0,
                            "position_in_range_pct": 86.0, "consolidation_bars": 18,
                            "consolidation_ok": True, "breakout": False, "breakdown": False},
        "volatility": {"atr14": 2.2, "atr_pct": 2.1},
        "data_quality": {"score": 100},
    }


AI_UNTRAINED = EnsembleModel({}).predict({})


# ---------------------------------------------------------------------------
# 1) FEATURE TESTS
# ---------------------------------------------------------------------------
def test_indicators() -> None:
    section("1. Feature / Indicator")

    # RSI Wilder — ví dụ chuẩn của Wilder/StockCharts (RSI(14) ≈ 70.53)
    closes = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245,
              45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820]
    rsi = ind.rsi_wilder(closes, 14)
    check("RSI(14) khớp ví dụ chuẩn (~70.53)", rsi is not None and abs(rsi - 70.53) < 0.6, f"got {rsi}")
    check("RSI chuỗi tăng liên tục = 100", ind.rsi_wilder([100 + i for i in range(40)], 14) == 100.0)
    check("RSI chuỗi giảm liên tục = 0", ind.rsi_wilder([200 - i for i in range(40)], 14) == 0.0)
    check("RSI thiếu dữ liệu -> None", ind.rsi_wilder([100, 101, 102], 14) is None)

    # MA alignment
    ma = ind.ma_alignment([100 + i * 0.5 for i in range(120)], [10, 20, 50])
    check("MA alignment: uptrend ordered_up + score cao",
          ma["available"] and ma["ordered_up"] and ma["score"] >= 80, f"got {ma.get('score')}")
    ma_down = ind.ma_alignment([200 - i * 0.5 for i in range(120)], [10, 20, 50])
    check("MA alignment: downtrend ordered_down + score thấp",
          ma_down["available"] and ma_down["ordered_down"] and ma_down["score"] <= 25, f"got {ma_down.get('score')}")

    # Momentum
    mom = ind.composite_momentum([100] * 60 + [110, 120, 130], {"short": 3, "medium": 30, "long": 60})
    check("Momentum: short 30% khi +30% trong 3 phiên", mom["short_term"] is not None and abs(mom["short_term"] - 30.0) < 0.01,
          f"got {mom['short_term']}")
    check("Momentum composite tính được", mom["composite"] is not None)

    # Volume
    bars = make_bars([100, 101, 102, 103, 104] + [105] * 35, volume=1_000_000)
    for b in bars[-5:]:
        b["volume"] = 3_000_000
    vol = ind.volume_metrics(bars, 5, 40, 1.5, 0.6)
    check("Volume: surge nhận diện đúng", vol["volume_surge"] is True and vol["volume_ratio"] > 1.5, f"got {vol}")

    # CMF — toàn bộ nến đóng ở đỉnh -> CMF = 1.0
    bull_bars = []
    for i in range(30):
        bull_bars.append({"time": f"2024-01-{i + 1:02d}", "open": 100, "high": 101, "low": 100, "close": 101, "volume": 1000})
    cmf_bull = ind.cmf(bull_bars, 20)
    check("CMF: đóng ở đỉnh -> ~1.0", cmf_bull is not None and abs(cmf_bull - 1.0) < 0.001, f"got {cmf_bull}")
    bear_bars = [{**b, "close": 100} for b in bull_bars]
    cmf_bear = ind.cmf(bear_bars, 20)
    check("CMF: đóng ở đáy -> ~-1.0", cmf_bear is not None and abs(cmf_bear + 1.0) < 0.001, f"got {cmf_bear}")
    check("CMF: high == low -> không chia 0",
          ind.cmf([{"time": "x", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}] * 25, 20) == 0.0)

    # Relative strength
    rs = ind.relative_strength([100 + i for i in range(80)], [100] * 80, {"short": 21, "medium": 63})
    check("RS: cổ phiếu mạnh hơn benchmark -> rs > 0",
          rs["available"] and rs["rs_score_raw"] > 0 and rs["label"] in ("POSITIVE", "STRONG"), f"got {rs.get('rs_score_raw')}")
    check("RS: thiếu benchmark -> available False", ind.relative_strength([1, 2, 3], [], {"short": 21})["available"] is False)

    # Price structure — 52 tuần
    bars52 = make_bars([100] * 100 + [150] + [120] * 100)
    st = ind.price_structure(bars52, bars_for_52w=260, breakout_lookback=20, consolidation_min_bars=15)
    check("52w high/low đúng", st["high_52w"] == round(150 * 1.01, 2) and st["low_52w"] == round(100 * 0.99, 2),
          f"got {st['high_52w']} / {st['low_52w']}")
    dist = st["distance_from_high_pct"]
    check("Khoảng cách từ đỉnh 52 tuần âm khi dưới đỉnh", dist is not None and dist < 0, f"got {dist}")


# ---------------------------------------------------------------------------
# 2) WAVELET
# ---------------------------------------------------------------------------
def test_wavelet() -> None:
    section("2. Wavelet MODWT")
    series = [100 + i * 0.5 + (3 if i % 7 == 0 else 0) for i in range(120)]
    decomp = wavelet.modwt(series, levels=4)
    rebuilt = wavelet.reconstruct(decomp)
    max_err = max(abs(a - b) for a, b in zip(series, rebuilt))
    check("MODWT: nhánh detail giữ nguyên độ dài chuỗi", all(len(d) == len(series) for d in decomp["levels"]))
    check("MODWT: MRA dựng lại chính xác (<=1e-9)", max_err <= 1e-9, f"max_err={max_err}")
    feats = wavelet.wavelet_features(series, levels=4)
    check("Wavelet features: energy_share đủ tầng + tổng ~1",
          feats["available"] and len(feats["energy_share"]) == 4 and abs(sum(feats["energy_share"]) - 1) < 0.01,
          f"got {feats.get('energy_share')}")
    check("Wavelet: thiếu dữ liệu -> available False", wavelet.wavelet_features([1, 2, 3])["available"] is False)


# ---------------------------------------------------------------------------
# 3) STRATEGY TESTS
# ---------------------------------------------------------------------------
def test_strategies() -> None:
    section("3. Strategies")
    cfg = cfg_module.get_config()

    up = uptrend.run(synthetic_features("uptrend"), AI_UNTRAINED, {"regime": "UPTREND"}, cfg=cfg)
    check("UPTREND: tất cả điều kiện kỹ thuật đạt", up["required_passed"] is True and up["confirmations"] >= 8,
          f"confirmations={up['confirmations']} required={up['required_passed']}")
    check("UPTREND: phát signal BUY", up["signal"] == "BUY")
    check("UPTREND: điều kiện AI = UNAVAILABLE (chưa train), KHÔNG tính là đạt",
          all(c["state"] == "UNAVAILABLE" for c in up["conditions"] if c["group"] == "ai"))
    check("UPTREND: không có bad signal khi mọi thứ tốt", up["bad_signal_count"] == 0)

    weak = uptrend.run(synthetic_features("bottom"), AI_UNTRAINED, {"regime": "DOWNTREND"}, cfg=cfg)
    check("UPTREND: không phát signal trên mã downtrend", weak["signal"] is None)
    check("UPTREND: bad signals xuất hiện trên mã suy yếu", weak["bad_signal_count"] >= 2, f"got {weak['bad_signal_count']}")

    bf = bottom_fishing.run(synthetic_features("bottom"), AI_UNTRAINED, {"regime": "WEAK_MARKET"}, cfg=cfg)
    check("BOTTOM_FISHING: quy đổi ngưỡng minh bạch khi thiếu điều kiện AI",
          bf["minimum_confirmations"] == 7 and bf["effective_minimum_confirmation"] == 6,
          f"min={bf['minimum_confirmations']} eff={bf['effective_minimum_confirmation']}")
    check("BOTTOM_FISHING: xác nhận đủ + signal BUY", bf["confirmation_ok"] is True and bf["signal"] == "BUY",
          f"confirmations={bf['confirmations']}")
    check("BOTTOM_FISHING: phanh an toàn 'không rơi tự do' là điều kiện BẮT BUỘC",
          any(c["key"] == "not_free_falling" and c["required"] for c in bf["conditions"]))

    free_fall = synthetic_features("bottom")
    free_fall["price_structure"] = {**free_fall["price_structure"], "distance_from_low_pct": 40.0, "breakdown": True}
    ff = bottom_fishing.run(free_fall, AI_UNTRAINED, {"regime": "DOWNTREND"}, cfg=cfg)
    check("BOTTOM_FISHING: chặn bắt dao rơi (breakdown + rời đáy xa)", ff["signal"] is None and ff["bad_signal_count"] >= 1,
          f"signal={ff['signal']}")

    sw = swing.run(synthetic_features("swing"), AI_UNTRAINED, {"regime": "SIDEWAYS"}, cfg=cfg)
    check("SWING: CMF vượt ngưỡng + RS tốt -> BUY", sw["signal"] == "BUY" and sw["required_passed"] is True)
    check("SWING: có mức CMF thoát riêng", sw["cmf_exit_level"] == 0.05 and sw["cmf_exit_triggered"] is False)

    exit_features = synthetic_features("swing")
    exit_features["money_flow"] = {"cmf": 0.01, "cmf_short": -0.02, "state": "OUTFLOW", "rising": False}
    ex = swing.run(exit_features, AI_UNTRAINED, {"regime": "SIDEWAYS"}, cfg=cfg)
    check("SWING: CMF dưới ngưỡng thoát -> exit_condition", ex["exit_condition"] is True and ex["cmf_exit_triggered"] is True,
          f"bad={ex['bad_signal_count']}")


# ---------------------------------------------------------------------------
# 4) SCORING / STATE MACHINE
# ---------------------------------------------------------------------------
def test_scoring_and_state() -> None:
    section("4. Scoring + State machine")
    from app.services.signal_engine import scoring

    cfg = cfg_module.get_config()
    score = scoring.signal_score(synthetic_features("uptrend"), AI_UNTRAINED, "bullish", cfg)
    check("Signal Score: AI bị loại khỏi tổng khi chưa train", score["ai_included"] is False)
    check("Signal Score: trọng số được renormalize (tổng ~1)",
          abs(sum(score["weights_used"].values()) - 1.0) < 0.01, f"got {sum(score['weights_used'].values())}")
    check("Signal Score: nằm trong 0..100", 0 <= score["total"] <= 100, f"got {score['total']}")
    check("Signal Score: uptrend > bottom", score["total"] > scoring.signal_score(synthetic_features("bottom"), AI_UNTRAINED, "bottom", cfg)["total"])

    strength = scoring.setup_strength({"total": 90}, 8, 9, [{"key": "a", "group": "technical", "passed": True}] * 8, AI_UNTRAINED, cfg)
    check("Setup Strength: VERY_STRONG khi điểm + xác nhận + đồng thuận cao", strength["level"] == "VERY_STRONG",
          f"got {strength['level']}")
    check("Setup Strength: không dùng AI khi chưa train", strength["ai_used"] is False)

    # State machine
    st = state_machine.next_state(None, 2, 10, False, 0, False, True, cfg)
    check("State: ít điều kiện -> NO_SETUP", st["state"] == state_machine.NO_SETUP, f"got {st['state']}")
    st = state_machine.next_state(None, 8, 10, False, 0, False, True, cfg)
    check("State: nhiều điều kiện -> SETUP_FORMING", st["state"] == state_machine.SETUP_FORMING, f"got {st['state']}")
    st = state_machine.next_state(None, 9, 9, True, 0, False, True, cfg)
    check("State: đủ điều kiện bắt buộc -> CONFIRMED", st["state"] == state_machine.CONFIRMED, f"got {st['state']}")
    st = state_machine.next_state(state_machine.CONFIRMED, 9, 9, True, 0, False, True, cfg)
    check("State: vẫn giữ -> ACTIVE", st["state"] == state_machine.ACTIVE, f"got {st['state']}")
    st = state_machine.next_state(state_machine.ACTIVE, 8, 9, True, 3, False, True, cfg)
    check("State: tín hiệu xấu tích luỹ -> WEAKENING", st["state"] == state_machine.WEAKENING, f"got {st['state']}")
    st = state_machine.next_state(state_machine.ACTIVE, 5, 9, False, 5, True, True, cfg)
    check("State: đủ tín hiệu xấu -> EXIT", st["state"] == state_machine.EXIT, f"got {st['state']}")
    st = state_machine.next_state(None, 0, 9, False, 0, False, False, cfg)
    check("State: dữ liệu không hợp lệ -> NO_SETUP (có lý do)", st["state"] == state_machine.NO_SETUP and "Dữ liệu" in st["reason"])


# ---------------------------------------------------------------------------
# 5) SERIALIZER / LEAKAGE
# ---------------------------------------------------------------------------
def _fake_snapshot() -> dict:
    cfg = cfg_module.get_config()
    from app.services.signal_engine import engine as engine_module

    strat = uptrend.run(synthetic_features("uptrend"), AI_UNTRAINED, {"regime": "UPTREND"}, cfg=cfg)
    strat.update({"state": "CONFIRMED", "level": None, "levels": {"entry": 219000, "stop_loss": 213000, "take_profit": 231000, "trailing_stop": 210000},
                  "setup_strength": {"level": "STRONG", "score": 88, "confirmations": 8, "total_conditions": 9, "confirm_ratio": 0.9, "technical_agreement": 0.9},
                  "confidence": {"value": 85}, "score": 88})
    features = feat.build_features(make_bars(UPTREND_CLOSES), make_bars([100] * 200), cfg, "TEST")
    return {
        "symbol": "TEST", "engine": "VinaFintech Signal Engine", "as_of": "2026-01-01", "price": 219.0,
        "status": "OK", "errors": [], "config_version": cfg_module.config_version(),
        "features": features,
        "ai": AI_UNTRAINED,
        "market": {"regime": {"regime": "UPTREND"}, "breadth": None, "benchmark": "VNINDEX"},
        "scores": {"bullish": {"total": 88, "components": {"ai": None, "trend": 90}, "label": "VERY_STRONG",
                               "ai_included": False, "components_missing": ["ai"]},
                   "bottom": {"total": 30, "components": {"ai": None}, "label": "WEAK", "ai_included": False},
                   "primary_mode": "bullish"},
        "strategies": {"UPTREND": strat},
        "primary_strategy": "UPTREND",
        "setup": strat["setup_strength"],
        "risk": {"risk_score": 30, "level": "LOW", "reasons": []},
        "decision": {"state": "CONFIRMED", "strategy": "UPTREND", "signal": "BUY",
                     "levels": strat["levels"], "confidence": 85, "exit_condition": False, "bad_signal_count": 0},
    }


def test_serializers() -> None:
    section("5. Serializer / Data leakage")
    snapshot = _fake_snapshot()
    admin = serializers.admin_view(snapshot)
    public = serializers.public_view(snapshot)

    check("Admin: có đủ field quyết định", all(k in admin for k in ("decision", "strategies", "ai", "features")))
    check("Admin: có BUY + Entry + SL + TP + Trailing",
          admin["decision"]["signal"] == "BUY"
          and admin["strategies"]["UPTREND"]["levels"]["stop_loss"] == 213000
          and admin["strategies"]["UPTREND"]["levels"]["trailing_stop"] == 210000)

    check("Public: signal_score + risk + setup có mặt",
          public["scores"]["signal_score"] == 88 and public["risk"]["risk_score"] == 30 and public["setup"]["strength"] == "STRONG")
    check("Public: AI chưa train được nêu rõ", public["ai"]["used_in_scoring"] is False and public["ai"]["trained_models"] == 0)

    # Quét toàn cây payload public tìm field cấm
    found: list[str] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in serializers.FORBIDDEN_PUBLIC_KEYS:
                    found.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for idx, item in enumerate(node):
                walk(item, f"{path}[{idx}]")

    walk(public)
    check("Public: KHÔNG chứa signal/entry/stop_loss/take_profit/trailing_stop/confidence",
          not found, f"leaked={found}")

    # Test khẳng định assert_public_clean thực sự bắt được rò rỉ
    try:
        serializers.assert_public_clean({"a": {"entry": 1}})
        check("assert_public_clean bắt được field lồng nhau", False, "không raise")
    except ValueError:
        check("assert_public_clean bắt được field lồng nhau", True)

    try:
        serializers.public_view({**snapshot, "decision": {"signal": "BUY"}})
        check("public_view không dựng lại decision", "decision" not in serializers.public_view(snapshot))
    except Exception as exc:  # noqa: BLE001
        check("public_view không dựng lại decision", False, str(exc))


# ---------------------------------------------------------------------------
# 6) HTTP: PERMISSION + LEAKAGE
# ---------------------------------------------------------------------------
def _ensure_member_credentials(base: str) -> tuple[str, str] | None:
    """Trả (email, password) của 1 tài khoản member để test 403.

    Ưu tiên ENV; nếu không có thì tạo tài khoản TẠM trong DB local rồi DỌN SẠCH sau test.
    """
    email = os.environ.get("TUNIX_TEST_MEMBER_EMAIL", "")
    password = os.environ.get("TUNIX_TEST_MEMBER_PASSWORD", "")
    if email and password:
        return email, password
    if "127.0.0.1" not in base and "localhost" not in base:
        return None  # không tạo user trên môi trường không phải local
    try:
        from app.core.security import get_password_hash
        from app.db import SessionLocal
        from app.models import RoleEnum, User

        temp_email = "signal.permission.test@tunix.vn"
        temp_pass = "Test@123456"
        db = SessionLocal()
        try:
            existing = db.query(User).filter(User.email == temp_email).first()
            if existing:
                db.delete(existing)
                db.commit()
            db.add(User(
                email=temp_email,
                full_name="Signal Permission Test",
                hashed_password=get_password_hash(temp_pass),
                role=RoleEnum.member,
                is_active=True,
                email_verified=True,
                terms_accepted=True,
                pref_theme="light",
                notify_reports=True,
                notify_news=True,
            ))
            db.commit()
        finally:
            db.close()
        return temp_email, temp_pass
    except Exception as exc:  # noqa: BLE001
        print(f"  SKIP  Không tạo được tài khoản member tạm: {exc}")
        return None


def _cleanup_temp_member() -> None:
    try:
        from app.db import SessionLocal
        from app.models import User

        db = SessionLocal()
        try:
            row = db.query(User).filter(User.email == "signal.permission.test@tunix.vn").first()
            if row:
                db.delete(row)
                db.commit()
                print("  (đã dọn tài khoản member tạm)")
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass


def _find_user_id(base: str, h_admin: dict, email: str) -> int | None:
    """Tra user id theo email qua API admin (dùng để test cấp/thu quyền Tín hiệu)."""
    import httpx

    try:
        res = httpx.get(f"{base}/api/users", headers=h_admin, timeout=20.0)
        if res.status_code == 200:
            for row in res.json() or []:
                if (row.get("email") or "").lower() == email.lower():
                    return row.get("id")
    except Exception:  # noqa: BLE001
        return None
    return None


def test_http() -> None:
    section("6. HTTP — Permission (ADMIN 200 / USER 403) + leakage")
    import httpx

    base = os.environ.get("TUNIX_BASE_URL", "http://127.0.0.1:8000")
    admin_email = os.environ.get("TUNIX_TEST_ADMIN_EMAIL", "admin@tunix.vn")
    admin_pass = os.environ.get("TUNIX_TEST_ADMIN_PASSWORD", "Taniku@1710")
    member_creds = _ensure_member_credentials(base)
    member_email, member_pass = member_creds if member_creds else ("", "")

    try:
        httpx.get(f"{base}/openapi.json", timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        SKIPPED.append(f"HTTP tests (server không chạy: {exc})")
        print(f"  SKIP  Server không chạy ({exc}) — bỏ qua test HTTP")
        return

    def login(email: str, password: str) -> str | None:
        try:
            res = httpx.post(f"{base}/api/auth/login", json={"email": email, "password": password}, timeout=20.0)
            if res.status_code == 200:
                return res.json().get("access_token")
        except Exception:  # noqa: BLE001
            return None
        return None

    admin_token = login(admin_email, admin_pass)
    if not admin_token:
        SKIPPED.append("HTTP tests (không đăng nhập được admin)")
        print("  SKIP  Không đăng nhập được tài khoản admin — bỏ qua test HTTP")
        return

    h_admin = {"Authorization": f"Bearer {admin_token}"}
    res = httpx.get(f"{base}/api/signals?limit=5", headers=h_admin, timeout=30.0)
    check("ADMIN GET /api/signals -> 200", res.status_code == 200, f"got {res.status_code}")

    res = httpx.get(f"{base}/api/signals", timeout=30.0)
    check("Không có token -> 401", res.status_code == 401, f"got {res.status_code}")

    member_token = login(member_email, member_pass) if (member_email and member_pass) else None
    member_id = _find_user_id(base, h_admin, member_email) if member_token else None
    if member_token:
        h_member = {"Authorization": f"Bearer {member_token}"}
        # CHƯA được admin cấp quyền -> 403 (kể cả dùng đúng endpoint người dùng)
        res = httpx.get(f"{base}/api/signals", headers=h_member, timeout=30.0)
        check("MEMBER chưa được cấp quyền Tín hiệu -> 403", res.status_code == 403, f"got {res.status_code}")
        res = httpx.get(f"{base}/api/signals/overview", headers=h_member, timeout=30.0)
        check("MEMBER chưa được cấp quyền (overview) -> 403", res.status_code == 403, f"got {res.status_code}")
        check("403 có nêu rõ quyền Tín hiệu", "Tín hiệu" in (res.text or ""), (res.text or "")[:120])
    else:
        SKIPPED.append("Test MEMBER 403 (chưa cấu hình/chưa đăng nhập được member)")
        print("  SKIP  Chưa có tài khoản member để test 403")

    # Public endpoint không được leak
    res = httpx.get(f"{base}/api/stocks/FPT/analysis", timeout=120.0)
    if res.status_code == 200:
        payload = res.json()
        leaked: list[str] = []

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    if str(key).lower() in serializers.FORBIDDEN_PUBLIC_KEYS:
                        leaked.append(f"{path}.{key}")
                    walk(value, f"{path}.{key}")
            elif isinstance(node, (list, tuple)):
                for idx, item in enumerate(node):
                    walk(item, f"{path}[{idx}]")

        walk(payload)
        check("PUBLIC /api/stocks/FPT/analysis không leak field khuyến nghị", not leaked, f"leaked={leaked}")
        check("PUBLIC analysis có scores + setup + ai status",
              "scores" in payload and "setup" in payload and "status" in payload.get("ai", {}))
    else:
        SKIPPED.append(f"Public analysis FPT (status {res.status_code})")
        print(f"  SKIP  GET /api/stocks/FPT/analysis trả {res.status_code}")

    res = httpx.post(f"{base}/api/signals/analyze/FPT", headers=h_admin, timeout=180.0)
    if res.status_code == 200:
        data = res.json()
        check("ADMIN analyze có signal/entry/stop_loss trong decision",
              "decision" in data and "levels" in data["decision"])
    else:
        SKIPPED.append(f"Admin analyze FPT (status {res.status_code})")
        print(f"  SKIP  POST /api/signals/analyze/FPT trả {res.status_code}")

    # ------------------------------------------------------------------
    # Admin CẤP / THU quyền Tín hiệu cho member (users.signal_access)
    # ------------------------------------------------------------------
    if member_token and member_id:
        h_member = {"Authorization": f"Bearer {member_token}"}
        res = httpx.patch(f"{base}/api/users/{member_id}/signal-access?enabled=true", headers=h_admin, timeout=20.0)
        check("ADMIN cấp quyền Tín hiệu cho member -> 200", res.status_code == 200, f"got {res.status_code}")
        if res.status_code == 200:
            check("UserOut trả signal_access=true", res.json().get("signal_access") is True, str(res.json().get("signal_access")))

        res = httpx.get(f"{base}/api/signals?limit=5", headers=h_member, timeout=30.0)
        check("MEMBER đã được cấp quyền -> 200", res.status_code == 200, f"got {res.status_code}")
        if res.status_code == 200:
            items = (res.json() or {}).get("items") or []
            first = items[0] if items else {}
            check("Payload cho member có signal/entry/stop_loss",
                  all(k in first for k in ("signal", "entry", "stop_loss")), f"keys={sorted(first)[:10]}")
        else:
            SKIPPED.append("Payload member (không lấy được danh sách signal)")

        # Member KHÔNG được tự cấp quyền cho chính mình
        res = httpx.patch(f"{base}/api/users/{member_id}/signal-access?enabled=true", headers=h_member, timeout=20.0)
        check("MEMBER không được tự cấp quyền -> 403", res.status_code == 403, f"got {res.status_code}")

        res = httpx.patch(f"{base}/api/users/{member_id}/signal-access?enabled=false", headers=h_admin, timeout=20.0)
        check("ADMIN thu hồi quyền Tín hiệu -> 200", res.status_code == 200, f"got {res.status_code}")
        res = httpx.get(f"{base}/api/signals", headers=h_member, timeout=30.0)
        check("MEMBER sau khi bị thu hồi -> 403", res.status_code == 403, f"got {res.status_code}")
    else:
        SKIPPED.append("Test cấp/thu quyền Tín hiệu (không có member hoặc không tìm thấy user id)")
        print("  SKIP  Không đủ dữ liệu member để test cấp/thu quyền")


def test_cache_gate_regression() -> None:
    """REGRESSION (bug đã gặp khi test UI): snapshot đọc từ CACHE phải áp LẠI cổng chất lượng,
    nếu không signal đã bị lọc sẽ 'sống lại' ở lần quét dùng cache."""
    section("7. Regression — cổng chất lượng phải áp cho cả đường cache")
    from app.services.signal_engine import engine as engine_module

    cfg = cfg_module.get_config()
    snapshot = _fake_snapshot()
    strat = snapshot["strategies"]["UPTREND"]
    strat["state"] = "CONFIRMED"
    strat["signal"] = "BUY"
    strat["setup_strength"] = {"level": "WEAK", "score": 30}

    weak_out = engine_module._restate(snapshot, {"UPTREND": "CONFIRMED"}, cfg)  # noqa: SLF001
    check("Cache: BUY với Setup Strength WEAK bị chặn",
          weak_out["strategies"]["UPTREND"]["signal"] is None and weak_out["strategies"]["UPTREND"]["signal_gate"],
          f"got {weak_out['strategies']['UPTREND']['signal']}")

    snapshot["strategies"]["UPTREND"]["setup_strength"] = {"level": "MODERATE", "score": 60}
    ok_out = engine_module._restate(snapshot, {"UPTREND": "CONFIRMED"}, cfg)  # noqa: SLF001
    check("Cache: BUY với Setup Strength MODERATE được giữ",
          ok_out["strategies"]["UPTREND"]["signal"] == "BUY",
          f"got {ok_out['strategies']['UPTREND']['signal']}")


def test_cache_persist_regression() -> None:
    """REGRESSION (bug đã gặp khi test UI): snapshot lấy từ CACHE trước đây return sớm nên
    KHÔNG persist → bảng `signals` giữ giá trị cũ (signal đã bị lọc vẫn hiện BUY).

    Test dùng dữ liệu giá giả lập (không phụ thuộc mạng) + DB thật, có dọn dẹp sau khi xong.
    """
    section("8. Regression — snapshot từ cache vẫn phải persist")
    import time as _time

    from app.db import SessionLocal
    from app.models import Signal, SignalCondition, SignalFeatureSnapshot, SignalHistory
    from app.services import vnstock_service
    from app.services.signal_engine import engine as engine_module

    bars = make_bars([100 + i * 0.4 for i in range(260)], volume=1_500_000)
    bench = make_bars([1000 + i * 0.5 for i in range(260)], volume=1_000_000)
    original = vnstock_service.get_history
    vnstock_service.get_history = lambda sym, interval="1d", period="1y": (
        bench if "VNINDEX" in str(sym).upper() else bars
    )
    symbol = "ZZTEST"
    db = SessionLocal()
    try:
        # Dọn dẹp trước (phòng khi lần chạy trước bị lỗi giữa chừng)
        db.query(SignalCondition).filter(SignalCondition.symbol == symbol).delete()
        db.query(SignalHistory).filter(SignalHistory.symbol == symbol).delete()
        db.query(Signal).filter(Signal.symbol == symbol).delete()
        db.query(SignalFeatureSnapshot).filter(SignalFeatureSnapshot.symbol == symbol).delete()
        db.commit()
        engine_module.market_snapshot(force=True)
        engine_module.analyze_symbol(symbol, db=db, use_cache=True)
        rows1 = db.query(Signal).filter(Signal.symbol == symbol).all()
        stamps1 = {r.strategy: r.updated_at for r in rows1}
        count1 = len(rows1)
        _time.sleep(1.1)
        engine_module.analyze_symbol(symbol, db=db, use_cache=True)      # lần 2: cache hit
        db.expire_all()
        rows2 = db.query(Signal).filter(Signal.symbol == symbol).all()
        stamps2 = {r.strategy: r.updated_at for r in rows2}
        check("Cache hit vẫn persist (updated_at thay đổi)",
              bool(stamps1) and stamps1 != stamps2,
              f"{stamps1} -> {stamps2}")
        check("Cache hit không tạo thêm signal trùng (số dòng không đổi)",
              len(rows2) == count1 and count1 > 0,
              f"{count1} -> {len(rows2)}")
    finally:
        try:
            db.query(SignalCondition).filter(SignalCondition.symbol == symbol).delete()
            db.query(SignalHistory).filter(SignalHistory.symbol == symbol).delete()
            db.query(Signal).filter(Signal.symbol == symbol).delete()
            db.query(SignalFeatureSnapshot).filter(SignalFeatureSnapshot.symbol == symbol).delete()
            db.commit()
            print("  (đã dọn dữ liệu test ZZTEST)")
        except Exception as exc:  # noqa: BLE001
            print(f"  (không dọn được dữ liệu test: {exc})")
        db.close()
        vnstock_service.get_history = original


def test_tplus_lifecycle() -> None:
    """Vòng đời T+ (REGRESSION bug "tín hiệu lâu rồi vẫn hiện Đang hiệu lực"):

    - Tín hiệu ĐANG HIỆU LỰC quá hạn nắm giữ (``signal.expiry.max_holding_sessions``) phải tự
      ĐÓNG (EXIT) kèm lý do + số phiên.
    - Tín hiệu ĐANG MỞ nhưng dữ liệu đã CŨ (nến mới nhất đã có phiên mới) phải được PHÂN TÍCH LẠI.

    Dùng DB in-memory riêng ⇒ KHÔNG đụng dữ liệu thật của người dùng.
    """
    section("10. Vòng đời T+ — đóng tín hiệu quá hạn & cập nhật lại tín hiệu đang mở")
    from datetime import datetime as _dt, timedelta as _td

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import Signal, SignalHistory
    from app.services.signal_engine import engine as engine_module, lifecycle, repository  # noqa: F401

    # (a) Hàm thuần: đếm phiên & quyết định quá hạn
    bench = make_bars([1000] * 20, start="2026-01-01")
    check("lifecycle: đếm đúng số phiên kể từ mốc",
          lifecycle.sessions_since(bench[-11]["time"], bench) == 10,
          f"got {lifecycle.sessions_since(bench[-11]['time'], bench)}")
    check("lifecycle: đủ 10 phiên ⇒ QUÁ HẠN T+10",
          lifecycle.expiry_decision(bench[-11]["time"], 10, bench)["expired"] is True)
    check("lifecycle: 9 phiên ⇒ còn hiệu lực",
          lifecycle.expiry_decision(bench[-10]["time"], 10, bench)["expired"] is False)
    check("lifecycle: ngưỡng mặc định T+10 lấy từ config",
          lifecycle.expiry_config({})["max_holding_sessions"] == 10,
          f"got {lifecycle.expiry_config({})}")
    old_day = (_dt.utcnow() - _td(days=14)).isoformat()
    young_day = (_dt.utcnow() - _td(days=13)).isoformat()
    check("lifecycle: không có nến benchmark ⇒ quy đổi 10 phiên ≈ 14 ngày",
          lifecycle.expiry_decision(old_day, 10, None)["expired"] is True
          and lifecycle.expiry_decision(young_day, 10, None)["expired"] is False)
    check("lifecycle: phát hiện tín hiệu CŨ so với phiên mới nhất",
          lifecycle.is_stale(bench[-2]["time"], bench[-1]["time"]) is True
          and lifecycle.is_stale(bench[-1]["time"], bench[-1]["time"]) is False)

    # (b) Hành vi thật trên DB tách riêng (in-memory)
    tmp_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
    Base.metadata.create_all(tmp_engine)
    db = sessionmaker(bind=tmp_engine)()
    today = _dt.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    bench20 = [{"time": (today - _td(days=19 - i)).isoformat() + "+00:00"} for i in range(20)]

    old_created = _dt.utcnow() - _td(days=40)      # 40 ngày > 10 phiên ⇒ quá hạn
    db.add(Signal(symbol="ZZEXPIRED", strategy="UPTREND", as_of=bench20[0]["time"],
                  state="ACTIVE", signal="BUY", is_active=True, entry=100.0, price=110.0,
                  score=70.0, created_at=old_created, updated_at=old_created))
    db.add(Signal(symbol="ZZSTALE", strategy="UPTREND", as_of="2020-01-02T00:00:00+00:00",
                  state="ACTIVE", signal="BUY", is_active=True, entry=100.0, price=101.0,
                  score=60.0, created_at=_dt.utcnow() - _td(days=1), updated_at=_dt(2020, 1, 2)))
    # Mã đã bị đóng do hết hạn ở LẦN GỌI TRƯỚC (GET /signals đóng trước khi POST /refresh)
    db.add(Signal(symbol="ZZCLOSED", strategy="UPTREND", as_of="2026-09-20T00:00:00+00:00",
                  state="EXIT", signal="SELL", is_active=False, entry=100.0, price=95.0,
                  score=50.0, created_at=_dt.utcnow() - _td(days=30), updated_at=_dt.utcnow(),
                  closed_at=_dt.utcnow(), exit_reason="Hết thời hạn nắm giữ T+10 (15 phiên) "
                                                      "— tín hiệu hết hiệu lực"))
    db.commit()

    calls: list[str] = []
    original_market = engine_module.market_snapshot
    original_analyze = engine_module.analyze_symbol
    engine_module.market_snapshot = lambda *a, **k: {
        "benchmark_bars": bench20, "as_of": bench20[-1]["time"], "regime": {"regime": "UPTREND"}}

    def _fake_analyze(symbol, cfg=None, market=None, previous_states=None, use_cache=True, db=None):  # noqa: ANN001
        calls.append(symbol)
        return {"symbol": symbol, "strategies": {}}

    engine_module.analyze_symbol = _fake_analyze
    try:
        result = engine_module.refresh_open_signals(db=db, max_symbols=3)
        row = db.query(Signal).filter(Signal.symbol == "ZZEXPIRED").first()
        check("T+ quá hạn: tự ĐÓNG tín hiệu (is_active=False, state=EXIT, signal=SELL)",
              bool(row) and row.is_active is False and row.state == "EXIT" and row.signal == "SELL",
              f"state={row.state if row else None} active={row.is_active if row else None}")
        check("T+ quá hạn: ghi lý do + số phiên nắm giữ",
              bool(row) and "T+10" in (row.exit_reason or "") and row.holding_days == 20,
              f"reason={row.exit_reason if row else None} holding={row.holding_days if row else None}")
        check("T+ quá hạn: tính kết quả WIN + return_pct",
              bool(row) and row.result == "WIN" and row.return_pct == 10.0,
              f"result={row.result if row else None} ret={row.return_pct if row else None}")
        hist = db.query(SignalHistory).filter(SignalHistory.symbol == "ZZEXPIRED").all()
        check("T+ quá hạn: có dòng lịch sử trạng thái (audit)",
              len(hist) == 1 and hist[0].to_state == "EXIT", f"hist={len(hist)}")
        check("T+ quá hạn: số tín hiệu đã đóng trả về đúng", result.get("expired") == 1,
              f"expired={result.get('expired')}")
        check("Cập nhật lại: tín hiệu đang mở nhưng CŨ được PHÂN TÍCH LẠI",
              "ZZSTALE" in calls, f"calls={calls}")
        check("Cập nhật lại: mã VỪA HẾT HẠN cũng được phân tích lại (tạo tín hiệu mới nếu còn setup)",
              calls[:2] == ["ZZEXPIRED", "ZZCLOSED"],
              f"calls={calls}")
        check("Cập nhật lại: tôn trọng trần max_symbols", len(calls) <= 3, f"len={len(calls)}")
        fresh = db.query(Signal).filter(Signal.symbol == "ZZSTALE").first()
        check("Cập nhật lại: tín hiệu còn hạn KHÔNG bị đóng",
              bool(fresh) and fresh.is_active is True,
              f"active={fresh.is_active if fresh else None}")

        # (c) Thứ tự bảng tín hiệu: ĐỘ TIN CẬY cao nhất ở trên, "—" (None) ở dưới
        now = _dt.utcnow()
        for sym, conf, score in (("ZZCONF_HIGH", 88.0, 40.0), ("ZZCONF_MID", 42.0, 95.0),
                                 ("ZZCONF_NONE", None, 99.0)):
            db.add(Signal(symbol=sym, strategy="SWING", as_of="2026-01-0" + str(len(sym) % 9 + 1) + "T00:00:00+00:00",
                          state="WATCH", is_active=True, confidence=conf, score=score,
                          created_at=now, updated_at=now))
        db.commit()
        items = repository.list_signals(db, limit=20)["items"]
        values = [row["confidence"] for row in items]
        first_none = values.index(None) if None in values else len(values)
        check("Bảng tín hiệu: ĐỘ TIN CẬY cao nhất ở TRÊN cùng", values[:1] == [88.0], f"values={values}")
        check("Bảng tín hiệu: độ tin cậy GIẢM DẦN",
              values[:first_none] == sorted(values[:first_none], reverse=True), f"values={values}")
        check("Bảng tín hiệu: tín hiệu không có độ tin cậy (—) nằm CUỐI",
              all(v is None for v in values[first_none:]), f"values={values}")
    finally:
        engine_module.market_snapshot = original_market
        engine_module.analyze_symbol = original_analyze
        db.close()
        tmp_engine.dispose()


def test_backtest() -> None:
    """Backtest (spec §22-§23): chống look-ahead + không tạo số liệu giả."""
    section("9. Backtest — chống look-ahead & không bịa số liệu")
    from app.services.signal_engine import backtest as bt

    # (a) Cắt benchmark theo thời gian: không dùng dữ liệu tương lai
    bench = make_bars([1000] * 10, start="2024-01-01")
    sliced = bt._bench_slice(bench, bench[4]["time"])  # noqa: SLF001
    check("Backtest: benchmark bị cắt đúng cutoff (không dùng dữ liệu tương lai)",
          len(sliced) == 5 and sliced[-1]["time"] == bench[4]["time"], f"len={len(sliced)}")

    # (b) Thiếu dữ liệu -> INSUFFICIENT_DATA + stats.available False (KHÔNG tạo số giả)
    short = make_bars([100 + i for i in range(30)])
    res = bt.run("ZZBT", "UPTREND", bars=short, bench_bars=[], max_bars=400)
    check("Backtest: thiếu dữ liệu -> INSUFFICIENT_DATA",
          res["status"] == "INSUFFICIENT_DATA" and (res["stats"] or {}).get("available") is False,
          f"status={res['status']}")

    # (c) Cơ chế khớp lệnh — ép chiến thuật phát BUY để kiểm tra entry/exit/SL-priority/phí
    #     (test riêng phần MECHANICS; điều kiện thật của chiến thuật đã test ở mục 3)
    from app.services.signal_engine.strategies import uptrend as uptrend_module

    original_run = uptrend_module.run

    def _outcome(signal: str | None, exit_condition: bool = False) -> dict:
        return {
            "strategy": "UPTREND", "enabled": True, "evaluated": True, "signal": signal,
            "exit_condition": exit_condition, "bad_signal_count": 0, "conditions": [],
            "confirmations": 5, "total_conditions": 5, "required_passed": True, "levels": {},
        }

    try:
        uptrend_module.run = lambda *a, **k: _outcome("BUY")
        closes = [100] * 220 + [100 + i for i in range(1, 16)]
        bars_up = make_bars(closes)
        res_up = bt.run("ZZBT1", "UPTREND", bars=bars_up, bench_bars=make_bars([1000] * len(closes)))
        trades = res_up.get("trades") or []
        check("Backtest: signal BUY → mở lệnh", len(trades) >= 1, f"trades={len(trades)}")
        if trades:
            first = trades[0]
            check("Backtest: vào lệnh tại giá ĐÓNG CỬA nến tín hiệu",
                  abs(first["entry"] - 100) < 1e-9, f"entry={first['entry']}")
            check("Backtest: lệnh đầu thoát bằng TAKE_PROFIT khi giá tăng mạnh",
                  first["exit_reason"] == "TAKE_PROFIT", f"reason={first['exit_reason']}")
            check("Backtest: phí 2 chiều bị trừ khỏi return_pct",
                  first["return_pct"] < (first["exit_price"] / first["entry"] - 1) * 100 + 1e-9,
                  f"return={first['return_pct']}")
            check("Backtest: nến vào lệnh nằm trong vùng đủ dữ liệu (không look-ahead)",
                  first["entry_index"] >= 199, f"entry_index={first['entry_index']}")

        # Nến chạm CẢ SL và TP trong cùng 1 phiên ⇒ phải tính STOP_LOSS (kịch bản xấu nhất)
        bars2 = make_bars([100] * 215)
        bars2[-1] = {**bars2[-1], "time": "2024-08-10T00:00:00+00:00", "high": 130.0, "low": 70.0, "close": 100.0}
        res_sl = bt.run("ZZBT2", "UPTREND", bars=bars2, bench_bars=make_bars([1000] * 215))
        trades_sl = res_sl.get("trades") or []
        check("Backtest: 1 nến chạm cả SL và TP ⇒ tính STOP_LOSS",
              bool(trades_sl) and trades_sl[0]["exit_reason"] == "STOP_LOSS",
              f"trades={len(trades_sl)} reason={trades_sl[0]['exit_reason'] if trades_sl else None}")

        # Thoát theo exit_condition (bad signal accumulation) khi không chạm SL/TP
        uptrend_module.run = lambda *a, **k: _outcome(None, exit_condition=True)
        res_exit = bt.run("ZZBT3", "UPTREND", bars=bars_up, bench_bars=make_bars([1000] * len(closes)))
        # Khi exit_condition bật ngay từ đầu thì không có lệnh nào được mở (không vào mới) — chỉ cần không crash
        check("Backtest: exit_condition xử lý an toàn khi không mở được lệnh",
              isinstance(res_exit.get("trades"), list), f"trades={len(res_exit.get('trades') or [])}")
    finally:
        uptrend_module.run = original_run


def main() -> int:
    print("=" * 78)
    print("VinaFintech Signal Engine — test suite")
    print("=" * 78)
    for fn in (test_indicators, test_wavelet, test_strategies, test_scoring_and_state,
               test_serializers, test_cache_gate_regression, test_cache_persist_regression,
               test_backtest, test_tplus_lifecycle, test_http):
        try:
            fn()
        except Exception:  # noqa: BLE001
            FAILED.append(f"{fn.__name__} crashed")
            print(f"  FAIL  {fn.__name__} crashed:\n{traceback.format_exc()}")
    _cleanup_temp_member()

    print("\n" + "=" * 78)
    print(f"PASSED: {len(PASSED)}   FAILED: {len(FAILED)}   SKIPPED: {len(SKIPPED)}")
    for item in FAILED:
        print(f"  FAIL: {item}")
    for item in SKIPPED:
        print(f"  SKIP: {item}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
