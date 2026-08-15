/**
 * DataNormalizer — chuẩn hoá dữ liệu API → Candle[].
 *
 * interface Candle {
 *   time: number;   // unix seconds
 *   open: number; high: number; low: number; close: number; volume: number;
 * }
 *
 * Quy tắc: bỏ bar lỗi (malformed), bỏ duplicate, sort tăng dần theo time.
 * Không sửa API backend chỉ để hợp format chart.
 */

function isFiniteNumber(v) {
    return typeof v === 'number' && Number.isFinite(v);
}

/** Một dòng bất kỳ (từ payload) → Candle hợp lệ hoặc null. */
export function toCandle(row) {
    if (!row || typeof row !== 'object') return null;
    const time = Number(row.time);
    const open = Number(row.open);
    const high = Number(row.high);
    const low = Number(row.low);
    const close = Number(row.close);
    const volume = Number(row.volume) || 0;
    if (!isFiniteNumber(time) || time <= 0 ||
        !isFiniteNumber(open) || !isFiniteNumber(high) ||
        !isFiniteNumber(low) || !isFiniteNumber(close) ||
        high < low) {
        return null;
    }
    return { time, open, high, low, close, volume };
}

/** Payload /api/chart/history → { candles, noData, nextTime }. */
export function normalizeChartHistory(payload) {
    const raw = (payload && Array.isArray(payload.bars)) ? payload.bars : [];
    const candles = [];
    const seen = new Set();
    for (const row of raw) {
        const c = toCandle(row);
        if (!c) continue;
        if (seen.has(c.time)) continue; // không chấp nhận duplicate candle
        seen.add(c.time);
        candles.push(c);
    }
    candles.sort((a, b) => a.time - b.time);
    return {
        candles,
        noData: payload ? payload.noData === true : candles.length === 0,
        nextTime: payload && payload.nextTime ? Number(payload.nextTime) : null,
        earliestTime: payload && payload.earliestTime ? Number(payload.earliestTime) : null,
    };
}

/** Dạng ISO string (vd get_history /stocks/{sym}/history) → Candle[]. */
export function normalizeIsoHistory(list) {
    const candles = [];
    const seen = new Set();
    for (const row of (Array.isArray(list) ? list : [])) {
        let time = 0;
        try {
            time = Math.floor(new Date(String(row && row.time).replace('Z', '+00:00')).getTime() / 1000);
        } catch (e) {
            continue;
        }
        const c = toCandle({ time, ...row });
        if (!c) continue;
        if (seen.has(c.time)) continue;
        seen.add(c.time);
        candles.push(c);
    }
    candles.sort((a, b) => a.time - b.time);
    return candles;
}
