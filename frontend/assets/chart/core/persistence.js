/**
 * Persistence (PHASE 6) — lưu/đọc trạng thái chart vào localStorage.
 * Phân tách:
 *   - Drawings: theo (symbol, timeframe) — lưu toàn bộ list dưới 1 key.
 *   - Indicators: cài đặt layout (global, áp cho mọi mã).
 *   - Analysis: bật/tắt S/R · VP · Confluence · Market Breadth (global).
 */

export const STORAGE_KEYS = {
    drawings: 'vinafintech.drawings.v1',
    indicators: 'vinafintech.indicators.v1',
    analysis: 'vinafintech.analysis.v1',
    compare: 'vinafintech.compare.v1',
    layout: 'vinafintech.layout.v1',
    settings: 'vinafintech.settings.v1',
};

export function storageGet(key, fallback = null) {
    try {
        const raw = localStorage.getItem(key);
        return raw == null ? fallback : JSON.parse(raw);
    } catch (e) {
        return fallback;
    }
}

export function storageSet(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
        /* ignore (quota / private mode) */
    }
}

export function storageRemove(key) {
    try {
        localStorage.removeItem(key);
    } catch (e) {
        /* ignore */
    }
}
