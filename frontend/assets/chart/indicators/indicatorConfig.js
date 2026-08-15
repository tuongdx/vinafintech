/**
 * IndicatorConfig — đăng ký các chỉ báo (PHASE 3).
 * Mỗi chỉ báo: pane 'overlay' (lên giá) hoặc 'sub' (pane riêng bên dưới),
 * type: 'line' | 'boll' | 'macd', fields (input số), color mặc định.
 */

export const INDICATOR_DEFS = {
    MA: {
        key: 'MA', label: 'MA — Trung bình',
        pane: 'overlay', type: 'line',
        fields: [{ key: 'period', label: 'Chu kỳ', def: 20, min: 2, max: 500 }],
        color: '#2962ff',
    },
    EMA: {
        key: 'EMA', label: 'EMA — Hàm mũ',
        pane: 'overlay', type: 'line',
        fields: [{ key: 'period', label: 'Chu kỳ', def: 20, min: 2, max: 500 }],
        color: '#f0b90b',
    },
    BOLL: {
        key: 'BOLL', label: 'Bollinger Bands',
        pane: 'overlay', type: 'boll',
        fields: [
            { key: 'period', label: 'Chu kỳ', def: 20, min: 2, max: 500 },
            { key: 'mult', label: 'Độ lệch', def: 2, min: 0.5, max: 5, step: 0.1 },
        ],
        color: '#8e44ad',
    },
    RSI: {
        key: 'RSI', label: 'RSI',
        pane: 'sub', type: 'line',
        fields: [{ key: 'period', label: 'Chu kỳ', def: 14, min: 2, max: 100 }],
        color: '#f0b90b', refs: [30, 70],
    },
    MACD: {
        key: 'MACD', label: 'MACD',
        pane: 'sub', type: 'macd',
        fields: [
            { key: 'fast', label: 'Fast', def: 12, min: 2, max: 100 },
            { key: 'slow', label: 'Slow', def: 26, min: 2, max: 200 },
            { key: 'signal', label: 'Signal', def: 9, min: 2, max: 100 },
        ],
        color: '#2962ff',
    },
    ATR: {
        key: 'ATR', label: 'ATR — Biến động',
        pane: 'sub', type: 'line',
        fields: [{ key: 'period', label: 'Chu kỳ', def: 14, min: 2, max: 200 }],
        color: '#8e44ad',
    },
    STOCH: {
        key: 'STOCH', label: 'Stochastic',
        pane: 'sub', type: 'stoch',
        fields: [
            { key: 'k', label: '%K', def: 14, min: 2, max: 100 },
            { key: 'd', label: '%D', def: 3, min: 2, max: 50 },
            { key: 'smooth', label: 'Smooth', def: 3, min: 1, max: 20 },
        ],
        color: '#2962ff', refs: [20, 80],
    },
    ADX: {
        key: 'ADX', label: 'ADX',
        pane: 'sub', type: 'adx',
        fields: [{ key: 'period', label: 'Chu kỳ', def: 14, min: 2, max: 100 }],
        color: '#8e44ad',
    },
    CCI: {
        key: 'CCI', label: 'CCI',
        pane: 'sub', type: 'line',
        fields: [{ key: 'period', label: 'Chu kỳ', def: 20, min: 2, max: 200 }],
        color: '#26a69a', refs: [-100, 100],
    },
    OBV: {
        key: 'OBV', label: 'OBV — Khối lượng',
        pane: 'sub', type: 'line',
        fields: [],
        color: '#2962ff',
    },
    ICHIMOKU: {
        key: 'ICHIMOKU', label: 'Ichimoku',
        pane: 'overlay', type: 'ichimoku',
        fields: [
            { key: 'tenkan', label: 'Tenkan', def: 9, min: 2, max: 100 },
            { key: 'kijun', label: 'Kijun', def: 26, min: 2, max: 200 },
            { key: 'senkou', label: 'Senkou', def: 52, min: 2, max: 300 },
        ],
        color: '#2962ff',
    },
};

export const INDICATOR_KEYS = Object.keys(INDICATOR_DEFS);

/** Màu tham chiếu (đường 30/70 RSI, ...) trên pane phụ. */
export const SUB_REF_COLOR = '#8b949e';
