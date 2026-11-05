/**
 * DataProvider — lấy dữ liệu OHLCV từ VinaFintech (backend /api/chart/history).
 * Chỉ gọi qua VinaFintech backend; KHÔNG gọi thẳng nguồn ngoài, KHÔNG tạo dữ liệu giả.
 *
 * Hỗ trợ toàn bộ dataset theo (symbol, timeframe):
 *   - fetchInitial(symbol, interval, countback): tải cửa sổ ban đầu.
 *   - fetchRange(symbol, interval, fromTs, toTs): tải thêm dải lịch sử cũ hơn.
 * Cache theo (symbol, interval) — mỗi mã/timeframe có cache riêng, không lẫn.
 */
import { DataCache } from './dataCache.js';
import { normalizeChartHistory } from './dataNormalizer.js';

export class DataProvider {
    constructor(cache) {
        this.cache = cache || new DataCache();
    }

    _query(params) {
        const q = new URLSearchParams({
            symbol: String(params.symbol || '').toUpperCase(),
            interval: params.interval || '1D',
        });
        if (params.countback) q.set('countback', String(params.countback));
        if (params.from) q.set('from', String(params.from));
        if (params.to) q.set('to', String(params.to));
        return '/api/chart/history?' + q.toString();
    }

    async _request(params) {
        const res = await fetch(this._query(params), { cache: 'no-store' });
        if (!res.ok) {
            throw new Error('Không tải được dữ liệu biểu đồ (HTTP ' + res.status + ')');
        }
        const payload = await res.json();
        return normalizeChartHistory(payload);
    }

    /**
     * Tải cửa sổ ban đầu cho (symbol, interval) — `countback` nến gần nhất.
     * Trả { candles, noData, nextTime, earliestTime } và gộp vào cache.
     */
    async fetchInitial(symbol, interval, countback) {
        const normalized = await this._request({ symbol, interval, countback: countback || 500 });
        this.cache.upsert(symbol, interval, normalized.candles, normalized.earliestTime);
        return normalized;
    }

    /**
     * Tải dải dữ liệu [fromTs, toTs] (cũ hơn) và gộp vào cache.
     * Trả { candles, noData, nextTime, earliestTime }.
     */
    async fetchRange(symbol, interval, fromTs, toTs) {
        const normalized = await this._request({ symbol, interval, from: fromTs, to: toTs });
        this.cache.upsert(symbol, interval, normalized.candles, normalized.earliestTime);
        return normalized;
    }

    /** Toàn bộ nến đã cache cho (symbol, interval) — trả entry hoặc null. */
    getCached(symbol, interval) {
        return this.cache.get(symbol, interval);
    }

    /** Bỏ cache một (symbol, interval). */
    clearCache(symbol, interval) {
        if (symbol && interval) this.cache.remove(symbol, interval);
        else this.cache.clear();
    }
}
