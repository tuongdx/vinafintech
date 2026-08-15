/**
 * ComparisonEngine (PHASE 19) — overlay so sánh NHIỀU mã / chỉ số.
 *
 * Hai chế độ hiển thị (mode):
 *   - 'pct' (mặc định): % thay đổi từ nến đầu → thang 'compare'.
 *   - 'abs': giá tuyệt đối (close) → thang 'compareAbs' riêng (không méo giá chính).
 * Mỗi mã 1 màu (palette), thêm/xoá từng mã, clear tất cả, đổi mode tất cả.
 *
 * Nguồn: /api/chart/history (cổ phiếu) → fallback /api/stocks/index-detail (chỉ số).
 */

const PALETTE = ['#f0b90b', '#26a69a', '#ef5350', '#8e44ad', '#2962ff', '#ff7043', '#e91e63'];

function toUnixTs(v) {
    if (typeof v === 'number') return v;
    const t = new Date(v).getTime() / 1000;
    return Number.isFinite(t) ? t : null;
}

function normalizeBars(bars) {
    const out = [];
    for (const b of bars) {
        const time = toUnixTs(b && b.time);
        const close = Number(b && b.close);
        if (time == null || !Number.isFinite(close)) continue;
        out.push({ time, close });
    }
    out.sort((a, b) => a.time - b.time);
    return out;
}

function toPctData(candles) {
    const base = candles.length ? candles[0].close : 0;
    return candles.map((c) => ({ time: c.time, value: base ? (c.close / base - 1) * 100 : 0 }));
}

function toAbsData(candles) {
    return candles.map((c) => ({ time: c.time, value: c.close }));
}

export class ComparisonEngine {
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.onError = deps.onError || null;
        this.mode = 'pct';           // 'pct' | 'abs'
        this.symbols = [];
        this.lines = new Map();      // symbol -> { series, color }
        this.lastVal = new Map();    // symbol -> number (hiển thị: % hoặc giá)
        this.lastUp = new Map();     // symbol -> boolean (tăng/giảm so nến đầu)
    }

    getSymbols() { return this.symbols.slice(); }
    getMode() { return this.mode; }
    getColor(symbol) { const l = this.lines.get(String(symbol).toUpperCase()); return l ? l.color : null; }

    getDisplay(symbol) {
        const key = String(symbol || '').toUpperCase();
        const v = this.lastVal.get(key);
        const up = this.lastUp.get(key);
        if (v == null) return { text: '--', cls: '' };
        if (this.mode === 'abs') {
            return { text: Math.round(v).toLocaleString('en-US'), cls: '' };
        }
        return { text: (up ? '+' : '') + v.toFixed(2) + '%', cls: up ? 'up' : 'down' };
    }

    async fetchCandles(symbol, interval) {
        const q = new URLSearchParams({ symbol: String(symbol).toUpperCase(), interval: interval || '1D', countback: '500' });
        try {
            const res = await fetch('/api/chart/history?' + q.toString(), { cache: 'no-store' });
            if (res.ok) {
                const payload = await res.json();
                if (payload && payload.valid && Array.isArray(payload.bars) && payload.bars.length) {
                    return normalizeBars(payload.bars);
                }
            }
        } catch (e) { /* thử đường chỉ số */ }
        try {
            const res2 = await fetch('/api/stocks/index-detail?key=' + encodeURIComponent(String(symbol).toLowerCase()) + '&period=1y');
            if (res2.ok) {
                const d = await res2.json();
                if (d && d.valid && Array.isArray(d.history) && d.history.length) {
                    return normalizeBars(d.history);
                }
            }
        } catch (e) { /* ignore */ }
        return null;
    }

    async addSymbol(symbol, interval) {
        const key = String(symbol || '').toUpperCase();
        if (!key || this.lines.has(key)) return false;
        const candles = await this.fetchCandles(key, interval);
        if (!candles || !candles.length) return false;
        const color = PALETTE[this.symbols.length % PALETTE.length];
        const data = this.mode === 'abs' ? toAbsData(candles) : toPctData(candles);
        const series = this._renderLine(data, color, key);
        this.lines.set(key, { series, color });
        this.symbols.push(key);
        this._setLast(key, candles);
        return true;
    }

    removeSymbol(symbol) {
        const key = String(symbol || '').toUpperCase();
        const line = this.lines.get(key);
        if (!line) return false;
        if (line.series) { try { this.chartEngine.removeSeries(line.series); } catch (e) { /* ignore */ } }
        this.lines.delete(key);
        this.symbols = this.symbols.filter((s) => s !== key);
        this.lastVal.delete(key);
        this.lastUp.delete(key);
        return true;
    }

    clear() { for (const s of this.symbols.slice()) this.removeSymbol(s); }

    /** Đổi mode → vẽ lại TẤT CẢ mã theo mode mới. */
    async setMode(mode, interval) {
        mode = mode === 'abs' ? 'abs' : 'pct';
        if (this.mode === mode) return true;
        this.mode = mode;
        const syms = this.symbols.slice();
        this.clear();
        for (const s of syms) await this.addSymbol(s, interval);
        return true;
    }

    /** Refetch tất cả theo timeframe mới (cập nhật data tại chỗ). */
    async refetch(interval) {
        for (const s of this.symbols.slice()) {
            const candles = await this.fetchCandles(s, interval);
            const line = this.lines.get(s);
            if (candles && candles.length && line && line.series) {
                line.series.setData(this.mode === 'abs' ? toAbsData(candles) : toPctData(candles));
                this._setLast(s, candles);
            }
        }
    }

    _setLast(key, candles) {
        const last = candles[candles.length - 1];
        const base = candles[0].close;
        this.lastUp.set(key, last.close >= base);
        this.lastVal.set(key, this.mode === 'abs' ? last.close : (last.close / base - 1) * 100);
    }

    _renderLine(data, color, title) {
        const chart = this.chartEngine.getChart();
        if (!chart) return null;
        const L = window.LightweightCharts;
        if (!L) return null;
        const scaleId = this.mode === 'abs' ? 'compareAbs' : 'compare';
        const series = chart.addSeries(L.LineSeries, {
            color,
            lineWidth: 2,
            priceScaleId: scaleId,
            priceLineVisible: false,
            lastValueVisible: true,
            crosshairMarkerVisible: true,
            title,
        });
        try {
            chart.priceScale(scaleId).applyOptions(
                this.mode === 'abs' ? { scaleMargins: { top: 0.2, bottom: 0.2 } } : { scaleMargins: { top: 0.15, bottom: 0.15 } }
            );
        } catch (e) { /* ignore */ }
        series.setData(data);
        return series;
    }

    destroy() { this.clear(); }
}
