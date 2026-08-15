/**
 * AnalysisEngine — hiển thị S/R + Volume Profile dưới dạng SVG overlay
 * (chỉ đọc, pointer-events none, nằm dưới lớp drawing).
 *
 * Mọi thứ neo theo GIÁ (y = priceToCoordinate) — không phụ thuộc thời gian,
 * nhưng phải vẽ lại khi pan/zoom/resize vì price scale tự co giãn theo
 * vùng nến đang thấy. Chỉ render trong pane giá chính (panes()[0]).
 */
import { detectSR, computeVolumeProfile } from './analysis.js';
import { computeConfluence } from './confluence.js';

function fmtPrice(n) { return Math.round(n).toLocaleString('en-US'); }

export class AnalysisEngine {
    /**
     * @param {object} deps
     *   - chartEngine (có getChart/getCandleSeries/getContainer)
     *   - onError (msg) => void
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.onError = deps.onError || null;
        this.onChange = deps.onChange || null;
        this.enabled = { sr: false, vp: false, cf: false };
        this.candles = [];
        this.svg = null;
        this._raf = null;
        this._ro = null;
        this._paneEl = null;
        this._paneRO = null;
        this._cleanup = [];
    }

    toggle(key) {
        if (key === 'sr' || key === 'vp' || key === 'cf') {
            this.enabled[key] = !this.enabled[key];
            this.render();
            this._emitChange();
        }
        return this.enabled[key];
    }
    isEnabled(key) { return !!this.enabled[key]; }

    _emitChange() {
        if (this.onChange) { try { this.onChange(Object.assign({}, this.enabled)); } catch (e) { /* ignore */ } }
    }

    setData(candles) {
        this.candles = Array.isArray(candles) ? candles : [];
        this.render();
    }

    create() {
        const chart = this.chartEngine.getChart();
        const container = this.chartEngine.getContainer();
        if (!chart || !container) return;
        if (this.svg && this.svg.isConnected) { this.render(); return; }
        if (this.svg) { try { this.svg.remove(); } catch (e) { /* ignore */ } this.svg = null; }

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'analysis-overlay');
        svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:3;display:block;';
        container.appendChild(svg);
        this.svg = svg;

        try { chart.timeScale().subscribeVisibleTimeRangeChange(() => this.render()); } catch (e) { /* ignore */ }
        try { chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.render()); } catch (e) { /* ignore */ }
        if (typeof ResizeObserver !== 'undefined') {
            this._ro = new ResizeObserver(() => this.render());
            this._ro.observe(container);
        }
        this._cleanup.push(() => {
            svg.remove();
            if (this._ro) this._ro.disconnect();
            if (this._paneRO) { this._paneRO.disconnect(); this._paneRO = null; this._paneEl = null; }
        });
        this.render();
    }

    // Theo dõi kích thước pane giá chính — khi thêm/xoá sub-pane (chỉ báo),
    // pane 0 đổi chiều cao → cần render lại (RO trên container KHÔNG bắt được).
    _watchMainPane() {
        const chart = this.chartEngine.getChart();
        if (!chart || typeof ResizeObserver === 'undefined') return;
        let el = null;
        try { el = chart.panes()[0].getHTMLElement(); } catch (e) { return; }
        if (!el || el === this._paneEl) return;
        if (this._paneRO) { try { this._paneRO.disconnect(); } catch (e) { /* ignore */ } }
        this._paneEl = el;
        this._paneRO = new ResizeObserver(() => this.render());
        this._paneRO.observe(el);
    }

    _geom() {
        const chart = this.chartEngine.getChart();
        const series = this.chartEngine.getCandleSeries();
        let W = 800, H = 400;
        try { W = chart.timeScale().width(); } catch (e) { /* ignore */ }
        try {
            const panes = chart.panes();
            if (panes && panes.length) {
                const h = panes[0].getHeight();
                H = h > 0 ? h : chart.timeScale().height();
            } else {
                H = chart.timeScale().height();
            }
        } catch (e) { /* ignore */ }
        const yOf = (price) => { try { return series.priceToCoordinate(price); } catch (e) { return null; } };
        return { W, H, yOf };
    }

    render() {
        if (this._raf) return;
        this._raf = requestAnimationFrame(() => {
            this._raf = null;
            this._doRender();
        });
    }

    _doRender() {
        const svg = this.svg;
        if (!svg || !this.candles.length) return;
        this._watchMainPane();
        const { W, yOf } = this._geom();
        const current = this.candles[this.candles.length - 1].close;
        let html = '';

        // ----- S/R -----
        if (this.enabled.sr) {
            const levels = detectSR(this.candles, { currentPrice: current });
            for (const lv of levels) {
                const y = yOf(lv.price);
                if (y == null) continue;
                const color = lv.kind === 'support' ? '#26a69a' : '#ef5350';
                html += '<line class="an-sr" x1="0" y1="' + y.toFixed(1) + '" x2="' + W + '" y2="' + y.toFixed(1) + '" ' +
                    'stroke="' + color + '" stroke-width="1" opacity="0.7" stroke-dasharray="5 4" />';
                html += '<text class="an-label" x="' + (W - 4) + '" y="' + (y - 3) + '" text-anchor="end" fill="' + color + '">' +
                    fmtPrice(lv.price) + ' · ' + lv.touches + '</text>';
            }
        }

        // ----- Volume Profile -----
        if (this.enabled.vp) {
            const vp = computeVolumeProfile(this.candles, { buckets: 26 });
            const maxW = Math.max(60, Math.min(130, W * 0.18));
            for (const b of vp.buckets) {
                if (!b.volume) continue;
                const yT = yOf(b.priceHigh);
                const yB = yOf(b.priceLow);
                if (yT == null || yB == null) continue;
                const w = (b.volume / vp.maxVol) * maxW;
                const fill = b.isPoc ? 'rgba(0,128,128,0.55)' : b.inVA ? 'rgba(0,128,128,0.30)' : 'rgba(0,128,128,0.15)';
                html += '<rect class="an-vp" x="' + (W - w).toFixed(1) + '" y="' + yT.toFixed(1) + '" ' +
                    'width="' + w.toFixed(1) + '" height="' + Math.max(1, (yB - yT)).toFixed(1) + '" fill="' + fill + '" />';
            }
            const py = yOf(vp.poc);
            if (py != null) {
                html += '<text class="an-label" x="' + (W - 4) + '" y="' + (py - 3) + '" text-anchor="end" fill="#008080" font-weight="700">' +
                    'POC ' + fmtPrice(vp.poc) + '</text>';
            }
            for (const [txt, price] of [['VAH', vp.vah], ['VAL', vp.val]]) {
                const y = yOf(price);
                if (y == null) continue;
                html += '<line x1="0" y1="' + y.toFixed(1) + '" x2="' + W + '" y2="' + y.toFixed(1) + '" ' +
                    'stroke="#f0b90b" stroke-width="1" opacity="0.55" stroke-dasharray="2 3" />';
                html += '<text class="an-label" x="6" y="' + (y - 3) + '" fill="#f0b90b">' + txt + ' ' + fmtPrice(price) + '</text>';
            }
        }

        // ----- Confluence (PHASE 5) -----
        if (this.enabled.cf) {
            const zones = computeConfluence(this.candles);
            for (const z of zones) {
                const yTop = yOf(z.high);
                const yBot = yOf(z.low);
                if (yTop == null || yBot == null) continue;
                const color = z.kind === 'support' ? '#26a69a' : '#ef5350';
                const op = Math.min(0.16, 0.05 + z.count * 0.03);
                const h = Math.max(2, yBot - yTop);
                html += '<rect class="an-cf" x="0" y="' + yTop.toFixed(1) + '" width="' + W + '" height="' + h.toFixed(1) + '" ' +
                    'fill="' + color + '" opacity="' + op + '" />';
                html += '<line x1="0" y1="' + yTop.toFixed(1) + '" x2="' + W + '" y2="' + yTop.toFixed(1) + '" ' +
                    'stroke="' + color + '" stroke-width="1" opacity="0.5" />';
                html += '<line x1="0" y1="' + yBot.toFixed(1) + '" x2="' + W + '" y2="' + yBot.toFixed(1) + '" ' +
                    'stroke="' + color + '" stroke-width="1" opacity="0.5" />';
                html += '<text class="an-label" x="6" y="' + (yTop + 11) + '" fill="' + color + '">' +
                    'Hội tụ·' + z.count + ' · ' + z.factors.join('+') + '</text>';
            }
        }

        svg.innerHTML = html;
    }

    destroy() {
        if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
        if (this._paneRO) { try { this._paneRO.disconnect(); } catch (e) { /* ignore */ } this._paneRO = null; this._paneEl = null; }
        for (const fn of this._cleanup) { try { fn(); } catch (e) { /* ignore */ } }
        this._cleanup = [];
        this.svg = null;
    }
}
