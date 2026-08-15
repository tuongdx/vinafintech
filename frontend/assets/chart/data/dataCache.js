/**
 * DataCache — cache nến theo (symbol, timeframe/interval) cho toàn bộ dataset.
 *
 * Mỗi (symbol, interval) giữ một mảng candles duy nhất, sắp xếp tăng dần theo
 * time. `upsert` gộp dữ liệu mới vào cache hiện tại:
 *   - Remove duplicate candle (theo time).
 *   - Sort tăng dần.
 *   - Prepend (dữ liệu cũ hơn) / Append (dữ liệu mới hơn) đều hoạt động.
 * Theo dõi:
 *   - earliest / latest : nến cũ nhất / mới nhất đã cache.
 *   - earliestBound     : mốc lịch sử xa nhất có sẵn (từ backend `earliestTime`).
 *   - complete          : đã tải đủ tới biên lịch sử (earliest <= earliestBound).
 *
 * KHÔNG làm sai dữ liệu: chỉ lưu nến thực từ TuniX.
 */
export class DataCache {
    constructor() {
        this._store = new Map();
    }

    _key(symbol, interval) {
        return String(symbol).toUpperCase() + ':' + String(interval).toUpperCase();
    }

    get(symbol, interval) {
        return this._store.get(this._key(symbol, interval)) || null;
    }

    has(symbol, interval) {
        return !!this._store.get(this._key(symbol, interval));
    }

    /** Gộp `candles` vào cache (symbol, interval). earliestTime = mốc backend. */
    upsert(symbol, interval, candles, earliestTime) {
        const key = this._key(symbol, interval);
        const prev = this._store.get(key) || { candles: [], earliest: null, latest: null, earliestBound: null };
        const map = new Map();
        for (const c of prev.candles) if (c && c.time) map.set(c.time, c);
        for (const c of candles) if (c && c.time) map.set(c.time, c);
        const list = [...map.values()].sort((a, b) => a.time - b.time);

        const entry = {
            candles: list,
            earliest: list.length ? list[0].time : prev.earliest,
            latest: list.length ? list[list.length - 1].time : prev.latest,
            earliestBound: earliestTime || prev.earliestBound || null,
        };
        // Đã tới biên lịch sử (không còn dữ liệu cũ hơn).
        entry.complete = !!(entry.earliestBound && entry.earliest && entry.earliest <= entry.earliestBound);
        this._store.set(key, entry);
        return entry;
    }

    remove(symbol, interval) {
        this._store.delete(this._key(symbol, interval));
    }

    clear() {
        this._store.clear();
    }
}
