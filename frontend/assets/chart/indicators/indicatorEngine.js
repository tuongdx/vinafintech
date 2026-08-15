/**
 * IndicatorEngine — quản lý vòng đời các chỉ báo trên chart.
 *
 * - Mỗi chỉ báo enabled: tạo series (overlay lên giá hoặc sub-pane riêng),
 *   recompute từ candles mỗi khi có dữ liệu mới (setData).
 * - Sub-pane dùng `series.moveToPane(n)` (LWC v5.2.1 KHÔNG hỗ trợ option
 *   paneIndex trên addSeries — phải moveToPane; pane rỗng tự bị xoá).
 * - Pane index cấp động theo thứ tự bật: chỉ báo sub đầu → pane 1, tiếp → 2...
 *   khi tắt 1 cái, các pane còn lại tự dồn lên.
 */
import { INDICATOR_DEFS, SUB_REF_COLOR } from './indicatorConfig.js';
import { ma, ema, rsi, macd, atr, bollinger, stochastic, adx, cci, obv, ichimoku } from './calculations.js';

export class IndicatorEngine {
    /**
     * @param {object} deps
     *   - chartEngine (có getChart/createSeries/removeSeries)
     *   - onError (msg) => void
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.onError = deps.onError || null;
        this.onChange = deps.onChange || null;
        this.enabled = new Map(); // key -> { def, settings, series: [], paneAssigned }
        this.candles = [];
        // Ichimoku cloud (SVG overlay — LWC v5 không có fill-between gốc)
        this.cloudSvg = null;
        this._cloudRaf = null;
        this._cloudRO = null;
        this._cloudPaneEl = null;
        this._cloudPaneRO = null;
    }

    isEnabled(key) { return this.enabled.has(key); }
    getEnabled() {
        return [...this.enabled.values()].map((e) => ({ key: e.def.key, settings: Object.assign({}, e.settings) }));
    }

    toggle(key, settings) {
        if (this.enabled.has(key)) this.remove(key);
        else this.add(key, settings);
        return this.isEnabled(key);
    }

    add(key, settings) {
        const def = INDICATOR_DEFS[key];
        if (!def || this.enabled.has(key)) return null;
        const merged = {};
        for (const f of def.fields) {
            const v = settings && settings[f.key] != null ? settings[f.key] : f.def;
            merged[f.key] = Number.isFinite(v) ? v : f.def;
        }
        const entry = { def, settings: merged, series: [], paneAssigned: 0 };
        this.enabled.set(key, entry);
        this._createSeries(entry);
        this._layoutSubPanes();
        this._updateSeries(entry);
        this._emitChange();
        return entry;
    }

    remove(key) {
        const entry = this.enabled.get(key);
        if (!entry) return;
        for (const s of entry.series) this.chartEngine.removeSeries(s);
        entry.series = [];
        this.enabled.delete(key);
        this._layoutSubPanes();
        this._emitChange();
    }

    update(key, settings) {
        const entry = this.enabled.get(key);
        if (!entry) return;
        for (const f of entry.def.fields) {
            if (settings[f.key] != null && Number.isFinite(settings[f.key])) {
                const v = settings[f.key];
                entry.settings[f.key] = Math.max(f.min || 0, Math.min(f.max || 1e9, v));
            }
        }
        this._updateSeries(entry);
        this._emitChange();
    }

    /** Gọi khi có dữ liệu chart mới (load / đổi period) → recompute tất cả. */
    setData(candles) {
        this.candles = Array.isArray(candles) ? candles : [];
        for (const entry of this.enabled.values()) this._updateSeries(entry);
    }

    // ---------- internal ----------
    _createSeries(entry) {
        const def = entry.def;
        const opts = (color, extra) => Object.assign({
            color, lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: true,
            crosshairMarkerVisible: true,
        }, extra || {});

        if (def.type === 'line') {
            const s = this.chartEngine.createSeries('line', opts(def.color));
            if (s) {
                entry.series.push(s);
                if (def.refs) {
                    for (const p of def.refs) {
                        try {
                            s.createPriceLine({
                                price: p, color: SUB_REF_COLOR, lineWidth: 1,
                                lineStyle: 2, axisLabelVisible: false, title: '',
                            });
                        } catch (e) { /* ignore */ }
                    }
                }
            }
        } else if (def.type === 'boll') {
            const u = this.chartEngine.createSeries('line', opts(def.color));
            const m = this.chartEngine.createSeries('line', opts('#2962ff'));
            const l = this.chartEngine.createSeries('line', opts(def.color));
            for (const s of [u, m, l]) if (s) entry.series.push(s);
        } else if (def.type === 'macd') {
            const h = this.chartEngine.createSeries('histogram', opts('rgba(38,166,154,0.9)', { priceLineVisible: false }));
            const mc = this.chartEngine.createSeries('line', opts('#2962ff'));
            const sg = this.chartEngine.createSeries('line', opts('#f0b90b'));
            for (const s of [h, mc, sg]) if (s) entry.series.push(s);
        } else if (def.type === 'stoch') {
            const k = this.chartEngine.createSeries('line', opts(def.color));
            const d = this.chartEngine.createSeries('line', opts('#f0b90b'));
            if (k && def.refs) {
                for (const p of def.refs) {
                    try {
                        k.createPriceLine({ price: p, color: SUB_REF_COLOR, lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
                    } catch (e) { /* ignore */ }
                }
            }
            for (const s of [k, d]) if (s) entry.series.push(s);
        } else if (def.type === 'adx') {
            const a = this.chartEngine.createSeries('line', opts(def.color));
            const p = this.chartEngine.createSeries('line', opts('#26a69a'));
            const m = this.chartEngine.createSeries('line', opts('#ef5350'));
            for (const s of [a, p, m]) if (s) entry.series.push(s);
        } else if (def.type === 'ichimoku') {
            const t = this.chartEngine.createSeries('line', opts('#2962ff'));
            const k = this.chartEngine.createSeries('line', opts('#ef5350'));
            const sa = this.chartEngine.createSeries('line', opts('#26a69a'));
            const sb = this.chartEngine.createSeries('line', opts('#e91e63'));
            const ch = this.chartEngine.createSeries('line', opts('#f0b90b'));
            for (const s of [t, k, sa, sb, ch]) if (s) entry.series.push(s);
            this._ensureCloudSvg();
        }
    }

    _layoutSubPanes() {
        let pane = 1;
        for (const entry of this.enabled.values()) {
            if (entry.def.pane === 'sub') {
                for (const s of entry.series) {
                    try { s.moveToPane(pane); } catch (e) { /* ignore */ }
                }
                entry.paneAssigned = pane;
                pane++;
            }
        }
    }

    _updateSeries(entry) {
        if (!entry.series.length || !this.candles.length) return;
        const def = entry.def;
        const c = this.candles;
        try {
            if (def.type === 'line') {
                const fn = def.key === 'MA' ? ma
                    : def.key === 'EMA' ? ema
                    : def.key === 'RSI' ? rsi
                    : def.key === 'CCI' ? cci
                    : def.key === 'OBV' ? obv
                    : atr;
                const data = fn(c, entry.settings.period);
                entry.data = data;
                entry.series[0].setData(data);
            } else if (def.type === 'boll') {
                const b = bollinger(c, entry.settings.period, entry.settings.mult);
                entry.data = b;
                entry.series[0].setData(b.upper);
                entry.series[1].setData(b.mid);
                entry.series[2].setData(b.lower);
            } else if (def.type === 'macd') {
                const m = macd(c, entry.settings.fast, entry.settings.slow, entry.settings.signal);
                entry.data = m;
                entry.series[0].setData(m.hist);
                entry.series[1].setData(m.macd);
                entry.series[2].setData(m.signal);
            } else if (def.type === 'stoch') {
                const s = stochastic(c, entry.settings.k, entry.settings.d, entry.settings.smooth);
                entry.data = s;
                entry.series[0].setData(s.k);
                entry.series[1].setData(s.d);
            } else if (def.type === 'adx') {
                const a = adx(c, entry.settings.period);
                entry.data = a;
                entry.series[0].setData(a.adx);
                entry.series[1].setData(a.plus);
                entry.series[2].setData(a.minus);
            } else if (def.type === 'ichimoku') {
                const ik = ichimoku(c, entry.settings.tenkan, entry.settings.kijun, entry.settings.senkou, entry.settings.kijun);
                entry.series[0].setData(ik.tenkan);
                entry.series[1].setData(ik.kijun);
                entry.series[2].setData(ik.spanA);
                entry.series[3].setData(ik.spanB);
                entry.series[4].setData(ik.chikou);
                entry.data = ik;
                this._renderCloud();
            }
        } catch (e) {
            if (this.onError) this.onError('Lỗi tính chỉ báo ' + def.label + ': ' + (e && e.message ? e.message : e));
        }
    }

    _emitChange() {
        if (this.onChange) { try { this.onChange(this.getEnabled()); } catch (e) { /* ignore */ } }
    }

    // ---------- giá trị tại một thời điểm (crosshair legend, PHASE 15) ----------
    _binSearch(arr, time) {
        if (!arr || !arr.length) return null;
        let lo = 0, hi = arr.length - 1, best = null;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (arr[mid].time <= time) { best = arr[mid]; lo = mid + 1; } else { hi = mid - 1; }
        }
        return best;
    }

    /** Trả về [{ key, label, value, color }] của mọi chỉ báo bật tại time. */
    valuesAtTime(time) {
        const out = [];
        for (const entry of this.enabled.values()) {
            const def = entry.def;
            const d = entry.data;
            if (!d) continue;
            let p = null;
            if (def.type === 'line') p = this._binSearch(d, time);
            else if (def.type === 'boll') p = this._binSearch(d.mid, time);
            else if (def.type === 'macd') p = this._binSearch(d.macd, time);
            else if (def.type === 'stoch') p = this._binSearch(d.k, time);
            else if (def.type === 'adx') p = this._binSearch(d.adx, time);
            else if (def.type === 'ichimoku') p = this._binSearch(d.tenkan, time);
            if (p && p.value != null) {
                out.push({ key: def.key, label: def.label, value: p.value, color: def.color });
            }
        }
        return out;
    }

    // ---------- Ichimoku cloud (SVG overlay) ----------
    _ensureCloudSvg() {
        if (this.cloudSvg && this.cloudSvg.isConnected) return;
        if (this.cloudSvg) { try { this.cloudSvg.remove(); } catch (e) { /* ignore */ } this.cloudSvg = null; }
        const container = this.chartEngine.getContainer();
        const chart = this.chartEngine.getChart();
        if (!container || !chart) return;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'ichimoku-cloud');
        svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2;display:block;';
        container.appendChild(svg);
        this.cloudSvg = svg;
        try { chart.timeScale().subscribeVisibleTimeRangeChange(() => this._renderCloud()); } catch (e) { /* ignore */ }
        try { chart.timeScale().subscribeVisibleLogicalRangeChange(() => this._renderCloud()); } catch (e) { /* ignore */ }
        if (typeof ResizeObserver !== 'undefined') {
            this._cloudRO = new ResizeObserver(() => this._renderCloud());
            this._cloudRO.observe(container);
        }
        this._watchCloudPane();
    }

    _watchCloudPane() {
        const chart = this.chartEngine.getChart();
        if (!chart || typeof ResizeObserver === 'undefined') return;
        let el = null;
        try { el = chart.panes()[0].getHTMLElement(); } catch (e) { return; }
        if (!el || el === this._cloudPaneEl) return;
        if (this._cloudPaneRO) { try { this._cloudPaneRO.disconnect(); } catch (e) { /* ignore */ } }
        this._cloudPaneEl = el;
        this._cloudPaneRO = new ResizeObserver(() => this._renderCloud());
        this._cloudPaneRO.observe(el);
    }

    _renderCloud() {
        if (this._cloudRaf) return;
        this._cloudRaf = requestAnimationFrame(() => {
            this._cloudRaf = null;
            this._doRenderCloud();
        });
    }

    _doRenderCloud() {
        const svg = this.cloudSvg;
        const ich = this.enabled.get('ICHIMOKU');
        if (!svg) { if (ich) this._ensureCloudSvg(); if (!this.cloudSvg) return; }
        if (!ich || !ich.data || !this.candles.length) { if (svg) svg.innerHTML = ''; return; }
        this._watchCloudPane();
        const chart = this.chartEngine.getChart();
        const series = this.chartEngine.getCandleSeries();
        if (!chart || !series) return;
        const A = ich.data.spanA;
        const B = ich.data.spanB;
        // Ghép Span A/B theo THỜI GIAN (spanB warmup 52 > spanA 26 nên dài khác nhau)
        const bByTime = new Map(B.map((p) => [p.time, p.value]));
        const pairs = [];
        for (const a of A) {
            const bv = bByTime.get(a.time);
            if (bv != null) pairs.push({ time: a.time, a: a.value, b: bv });
        }
        const n = pairs.length;
        if (n < 2) { this.cloudSvg.innerHTML = ''; return; }
        const pts = [];
        for (let k = 0; k < n; k++) {
            const xA = chart.timeScale().timeToCoordinate(pairs[k].time);
            const yA = series.priceToCoordinate(pairs[k].a);
            const xB = chart.timeScale().timeToCoordinate(pairs[k].time);
            const yB = series.priceToCoordinate(pairs[k].b);
            pts.push(xA == null || yA == null || xB == null || yB == null ? null : { xA, yA, xB, yB });
        }
        let html = '';
        for (let k = 0; k + 1 < n; k++) {
            const p0 = pts[k], p1 = pts[k + 1];
            if (!p0 || !p1) continue;
            const bull = pairs[k].a >= pairs[k].b;
            const fill = bull ? 'rgba(38,166,154,0.25)' : 'rgba(239,83,80,0.25)';
            html += '<polygon points="' +
                p0.xA.toFixed(1) + ',' + p0.yA.toFixed(1) + ' ' +
                p1.xA.toFixed(1) + ',' + p1.yA.toFixed(1) + ' ' +
                p1.xB.toFixed(1) + ',' + p1.yB.toFixed(1) + ' ' +
                p0.xB.toFixed(1) + ',' + p0.yB.toFixed(1) + '" fill="' + fill + '" />';
        }
        this.cloudSvg.innerHTML = html;
    }

    _disposeCloud() {
        if (this._cloudRaf) { cancelAnimationFrame(this._cloudRaf); this._cloudRaf = null; }
        if (this._cloudRO) { try { this._cloudRO.disconnect(); } catch (e) { /* ignore */ } this._cloudRO = null; }
        if (this._cloudPaneRO) { try { this._cloudPaneRO.disconnect(); } catch (e) { /* ignore */ } this._cloudPaneRO = null; this._cloudPaneEl = null; }
        if (this.cloudSvg) { try { this.cloudSvg.remove(); } catch (e) { /* ignore */ } this.cloudSvg = null; }
    }

    destroy() {
        this._disposeCloud();
        for (const entry of this.enabled.values()) {
            for (const s of entry.series) this.chartEngine.removeSeries(s);
            entry.series = [];
        }
        this.enabled.clear();
        this.candles = [];
    }
}
