"""TuniX Signal Engine — bộ engine phân tích tín hiệu định lượng cho chứng khoán VN.

Cấu trúc:
    config.py         ngưỡng/trọng số tập trung (spec §26)
    indicators.py     chỉ báo kỹ thuật thuần Python (Phase 4)
    wavelet.py        MODWT — phân tách đa tần số (Phase 3)
    features.py       Feature Engineering + kiểm tra chất lượng dữ liệu (Phase 3)
    ai/               interface + adapter model AI (Phase 5-6, hiện CHƯA train)
    scoring.py        Signal Score + Setup Strength (Phase 10)
    risk.py           Risk score + Entry/Stop/Trailing cho Admin (Phase 10)
    market_regime.py  Market Regime (spec §15)
    state_machine.py  Signal State Machine (spec §16)
    strategies/       Uptrend / Bottom Fishing / Swing (Phase 7-9)
    engine.py         orchestrator: analyze_symbol + scan batch (spec §29)
    serializers.py    payload Admin (đầy đủ) vs payload public (KHÔNG leak BUY/SELL)
    repository.py     lưu snapshot signal vào DB (spec §21, §24, §27)
    backtest.py       backtest chống look-ahead (spec §22-23)
    model_performance.py  thống kê chất lượng model AI (spec §25)
"""
