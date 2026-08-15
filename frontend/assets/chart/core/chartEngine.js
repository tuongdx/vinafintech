/**
 * ChartEngine — rendering core (bọc Lightweight Charts v5).
 * Chỉ lo RENDERING: tạo chart, series nến + volume, crosshair → legend,
 * đổi theme (applyOptions — KHÔNG recreate), đổi visible range, huỷ.
 * KHÔNG chứa logic nghiệp vụ / calculation.
 *
 * Lưu ý hiệu năng: Lightweight Charts render canvas — chỉ vẽ phần nhìn thấy,
 * không render lại toàn chart khi mouse move / zoom / pan.
 */
import { themeColors } from './chartConfig.js';

export class ChartEngine {
    /**
     * @param {HTMLElement} container vùng chứa chart
     * @param {object} options
     *   - theme: 'light' | 'dark'
     *   - onLegend: (bar|null, volume) => void  (crosshair / bar cuối)
     */
    constructor(container, options) {
        this.container = container;
        this.options = options || {};
        this.theme = this.options.theme === 'dark' ? 'dark' : 'light';
        this.onLegend = typeof this.options.onLegend === 'function' ? this.options.onLegend : null;
        this.chart = null;
        this.candleSeries = null;
        this.volumeSeries = null;
        this.candles = [];
        this.crosshairTime = null;
    }

    create() {
        const L = window.LightweightCharts;
        if (!L || typeof L.createChart !== 'function') {
            throw new Error('LightweightCharts chưa được nạp');
        }
        const colors = themeColors(this.theme);
        const container = this.container;
        container.innerHTML = '';

        const chart = L.createChart(container, {
            autoSize: true,
            layout: {
                background: { type: 'solid', color: colors.background },
                textColor: colors.text,
            },
            grid: {
                vertLines: { color: colors.grid },
                horzLines: { color: colors.grid },
            },
            rightPriceScale: { borderColor: colors.border },
            timeScale: { borderColor: colors.border, timeVisible: true, secondsVisible: false },
            crosshair: { mode: 0 },
            localization: {
                // Trục đứng (giá) hiển thị theo VND, có phân tách hàng nghìn
                priceFormatter: (price) => {
                    const p = Number(price);
                    if (!Number.isFinite(p)) return '--';
                    return Math.round(p).toLocaleString('en-US') + ' đ';
                },
            },
        });

        const candleSeries = chart.addSeries(L.CandlestickSeries, {
            upColor: colors.up,
            downColor: colors.down,
            borderUpColor: colors.up,
            borderDownColor: colors.down,
            wickUpColor: colors.up,
            wickDownColor: colors.down,
        });

        const volumeSeries = chart.addSeries(L.HistogramSeries, {
            priceFormat: { type: 'volume' },
            priceScaleId: '',
        });
        chart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

        chart.subscribeCrosshairMove((param) => this._onCrosshair(param, candleSeries));

        this.chart = chart;
        this.candleSeries = candleSeries;
        this.volumeSeries = volumeSeries;
        return this;
    }

    _onCrosshair(param, candleSeries) {
        if (!this.onLegend) return;
        let bar = null;
        let vol = 0;
        if (param && param.time && param.seriesData) {
            const cd = param.seriesData.get(candleSeries);
            if (cd) {
                bar = { time: param.time, open: cd.open, high: cd.high, low: cd.low, close: cd.close };
                this.crosshairTime = param.time;
                const idx = this._indexOfTime(param.time);
                if (idx !== -1) vol = this.candles[idx].volume;
            }
        }
        if (!bar) {
            const last = this.candles[this.candles.length - 1];
            if (last) { bar = last; vol = last.volume; }
        }
        this.onLegend(bar, vol);
    }

    // Binary search trên candles đã sort tăng dần theo time (O(log n))
    _indexOfTime(time) {
        let lo = 0;
        let hi = this.candles.length - 1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const t = this.candles[mid].time;
            if (t === time) return mid;
            if (t < time) lo = mid + 1; else hi = mid - 1;
        }
        return -1;
    }

    setData(candles) {
        this.candles = Array.isArray(candles) ? candles : [];
        const colors = themeColors(this.theme);
        const candleData = [];
        const volumeData = [];
        for (const c of this.candles) {
            candleData.push({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close });
            volumeData.push({
                time: c.time,
                value: c.volume,
                color: c.close >= c.open ? colors.volumeUp : colors.volumeDown,
            });
        }
        if (this.candleSeries) this.candleSeries.setData(candleData);
        if (this.volumeSeries) this.volumeSeries.setData(volumeData);
        if (this.onLegend) {
            const last = this.candles[this.candles.length - 1] || null;
            this.onLegend(last, last ? last.volume : 0);
        }
    }

    setTheme(mode) {
        this.theme = mode === 'dark' ? 'dark' : 'light';
        if (!this.chart) return;
        const colors = themeColors(this.theme);
        this.chart.applyOptions({
            layout: {
                background: { type: 'solid', color: colors.background },
                textColor: colors.text,
            },
            grid: {
                vertLines: { color: colors.grid },
                horzLines: { color: colors.grid },
            },
            rightPriceScale: { borderColor: colors.border },
            timeScale: { borderColor: colors.border },
        });
        this.candleSeries.applyOptions({
            upColor: colors.up,
            downColor: colors.down,
            borderUpColor: colors.up,
            borderDownColor: colors.down,
            wickUpColor: colors.up,
            wickDownColor: colors.down,
        });
        // Re-đổ volume để cập nhật màu (chỉ khi đổi theme, hiếm)
        this.setData(this.candles);
    }

    setVisibleRange(days) {
        if (!this.chart) return;
        if (days && days > 0) {
            const now = Math.floor(Date.now() / 1000);
            this.chart.timeScale().setVisibleRange({ from: now - days * 86400, to: now });
        } else {
            this.chart.timeScale().fitContent();
        }
    }

    /**
     * Cập nhật dữ liệu (thường là prepend thêm lịch sử cũ) mà KHÔNG đánh mất
     * vị trí hiển thị hiện tại. Đã kiểm chứng (LWC v5.2.1): `setData` THUẦN
     * tự giữ nguyên visible TIME window khi data được prepend (mở rộng về trái)
     * — người dùng vẫn nhìn đúng cùng các nến. KHÔNG được tự ý setVisibleRange/
     * setVisibleLogicalRange sau setData: làm hỏng time scale của LWC (fitContent
     * không còn fit đúng, logical range bị lệch).
     *
     * LƯU Ý: logical range của LWC v5.2.1 là TỶ LỆ THỜI GIAN (khuếch đại theo gap
     * nến), không phải index bar — không dùng nó để dịch view khi prepend.
     */
    updateData(candles) {
        this.setData(candles);
    }

    // Getters cho drawing engine (PHASE 2)
    getChart() { return this.chart; }
    getCandleSeries() { return this.candleSeries; }
    getVolumeSeries() { return this.volumeSeries; }
    getContainer() { return this.container; }
    getBars() { return this.candles; }

    /** Thời điểm crosshair gần nhất (PHASE 24 — điều hướng từng nến). */
    getCrosshairTime() { return this.crosshairTime; }

    // Series helpers cho indicator engine (PHASE 3)
    createSeries(kind, opts) {
        const L = window.LightweightCharts;
        if (!this.chart || !L) return null;
        return this.chart.addSeries(kind === 'histogram' ? L.HistogramSeries : L.LineSeries, opts || {});
    }
    removeSeries(series) {
        if (!this.chart || !series) return;
        try { this.chart.removeSeries(series); } catch (e) { /* ignore */ }
    }

    destroy() {
        if (this.chart) {
            try { this.chart.remove(); } catch (e) { /* ignore */ }
            this.chart = null;
        }
        this.candleSeries = null;
        this.volumeSeries = null;
        this.candles = [];
    }
}
