/**
 * ChartState — nguồn sự thật (single source of truth) cho chart.
 * PHASE 1: symbol, timeframe, theme, bars, loading, error.
 * Các module khác đọc state; state phát sự kiện 'change' khi có thay đổi.
 */
import { ChartEvents } from './chartEvents.js';

export class ChartState {
    constructor(initial = {}) {
        this._events = new ChartEvents();
        this._data = {
            symbol: initial.symbol || '',
            timeframe: initial.timeframe || '1D',
            theme: initial.theme === 'dark' ? 'dark' : 'light',
            bars: [],
            loading: false,
            error: null,
        };
    }

    get() {
        return this._data;
    }

    set(patch) {
        const changed = Object.keys(patch).some((k) => this._data[k] !== patch[k]);
        if (!changed) return;
        Object.assign(this._data, patch);
        this._events.emit('change', { ...this._data });
    }

    onChange(fn) {
        return this._events.on('change', fn);
    }

    getSymbol() { return this._data.symbol; }
    getTimeframe() { return this._data.timeframe; }
    getTheme() { return this._data.theme; }
    getBars() { return this._data.bars; }
}
