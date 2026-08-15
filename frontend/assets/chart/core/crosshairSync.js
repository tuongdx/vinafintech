/**
 * CrosshairSync — đồng bộ crosshair giữa các sub-pane (PHASE 20).
 *
 * Lightweight Charts v5 đã tự vẽ ĐƯỜNG THẲNG ĐỨNG xuyên qua MỌI pane
 * (cùng trục thời gian), nhưng ĐƯỜNG NGANG + nhãn giá chỉ hiện ở pane đang
 * hover (nguồn v5: `horzLineVisible(pane) { return pane === this._pane && ... }`).
 *
 * Module này vẽ thêm một SVG overlay: đường ngang + chip giá trị cho TỪNG
 * sub-pane (chỉ báo) tại thời điểm crosshair — đúng kiểu TradingView:
 *   - hover pane chính  → hiện đường ngang + giá trị ở mọi sub-pane.
 *   - hover sub-pane    → hiện đường ngang + giá ở pane chính + sub-pane khác.
 * Không vẽ lại pane đang hover (đã có crosshair native).
 *
 * SVG: z-index thấp (1), pointer-events:none → không chặn tương tác, nằm dưới
 * ichimoku cloud (2) / analysis (3) / drawings (4).
 */
import { themeColors } from './chartConfig.js';

export class CrosshairSync {
    /**
     * @param {object} deps
     *   - chartEngine (có getChart/getContainer/getBars/getCandleSeries/theme)
     *   - indicatorEngine (có valuesAtTime(time) + enabled Map)
     *   - onError (msg) => void
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.indicatorEngine = deps.indicatorEngine || null;
        this.onError = deps.onError || null;
        this.enabled = true;
        this.svg = null;
        this._raf = null;
        this._time = null;
        this._hoverPane = -1;
        this._unsubs = [];
        this._ro = null;
    }

    create() {
        const container = this.chartEngine.getContainer();
        if (!container || this.svg) return this;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'crosshair-sync');
        svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;' +
            'pointer-events:none;z-index:1;display:block;';
        container.appendChild(svg);
        this.svg = svg;

        const chart = this.chartEngine.getChart();
        if (chart) {
            try {
                const un = chart.subscribeCrosshairMove((p) => this._onMove(p));
                this._unsubs.push(un);
            } catch (e) { /* ignore */ }
            // Khi zoom/pan/thay đổi kích thước → toạ độ dịch chuyển → vẽ lại.
            try { chart.timeScale().subscribeVisibleTimeRangeChange(() => this._schedule()); } catch (e) { /* ignore */ }
            try { chart.timeScale().subscribeVisibleLogicalRangeChange(() => this._schedule()); } catch (e) { /* ignore */ }
        }
        if (typeof ResizeObserver !== 'undefined') {
            this._ro = new ResizeObserver(() => this._schedule());
            this._ro.observe(container);
        }
        return this;
    }

    _onMove(param) {
        this._hoverPane = (param && typeof param.paneIndex === 'number') ? param.paneIndex : -1;
        if (!param || param.time == null) {
            this._time = null;
            this._schedule();
            return;
        }
        this._time = param.time;
        this._schedule();
    }

    /** Bật/tắt đồng bộ (PHASE 23) — tắt thì xoá overlay, không vẽ. */
    setEnabled(on) {
        this.enabled = !!on;
        if (!this.enabled) {
            this._time = null;
            if (this.svg) this.svg.innerHTML = '';
        } else {
            this._schedule();
        }
    }

    /** Vẽ tại thời điểm cụ thể (PHASE 24 — điều hướng từng nến). */
    setProgrammaticTime(time) {
        if (!this.enabled) return;
        if (time == null) {
            this._time = null;
            if (this.svg) this.svg.innerHTML = '';
            return;
        }
        this._time = time;
        this._hoverPane = -1; // vẽ mọi pane (crosshair tổng hợp không có pane hover)
        this._schedule();
    }

    _schedule() {
        if (this._raf) return;
        this._raf = requestAnimationFrame(() => {
            this._raf = null;
            this._render();
        });
    }

    _render() {
        const svg = this.svg;
        if (!svg || !this.enabled) return;
        const chart = this.chartEngine.getChart();
        const container = this.chartEngine.getContainer();
        const time = this._time;
        if (!chart || !container || time == null) { svg.innerHTML = ''; return; }

        const cr = container.getBoundingClientRect();
        let panes = [];
        try { panes = chart.panes(); } catch (e) { /* ignore */ }
        if (!panes.length) { svg.innerHTML = ''; return; }

        const theme = themeColors(this.chartEngine.theme || 'dark');
        const lineColor = theme.text;
        let html = '';

        // ---- pane chính: đường ngang tại close (chỉ khi hover sub-pane) ----
        if (this._hoverPane !== 0) {
            const close = this._closeAt(time);
            if (close != null) {
                const candle = this.chartEngine.getCandleSeries();
                let y = null;
                try { y = candle.priceToCoordinate(close); } catch (e) { /* ignore */ }
                const pr = panes[0].getHTMLElement().getBoundingClientRect();
                if (y != null && pr) {
                    const yc = pr.top - cr.top + y;
                    html += this._lineHtml(pr.width, yc, lineColor);
                    html += this._chipHtml(pr.right - cr.left, yc, theme.text, this._fmtPrice(close), theme);
                }
            }
        }

        // ---- sub-pane: đường ngang + chip theo giá trị chỉ báo tại time ----
        const inds = this.indicatorEngine ? this.indicatorEngine.valuesAtTime(time) : [];
        const enabled = this.indicatorEngine ? this.indicatorEngine.enabled : null;
        for (const ind of inds) {
            if (!enabled) break;
            const entry = enabled.get(ind.key);
            if (!entry || !entry.series || !entry.series.length) continue;
            if (entry.def.pane !== 'sub') continue; // chỉ sub-pane
            const paneIdx = entry.paneAssigned;
            if (paneIdx === this._hoverPane) continue; // pane đang hover đã có native
            const pane = panes[paneIdx];
            if (!pane) continue;
            const pr = pane.getHTMLElement().getBoundingClientRect();
            const series = entry.series[0];
            let y = null;
            try { y = series.priceToCoordinate(ind.value); } catch (e) { /* ignore */ }
            if (y == null) continue;
            const yc = pr.top - cr.top + y;
            const c = ind.color || lineColor;
            html += this._lineHtml(pr.width, yc, c);
            html += this._chipHtml(pr.right - cr.left, yc, c, ind.label + ' ' + this._fmtVal(ind.value), theme);
        }

        if (svg.innerHTML !== html) svg.innerHTML = html;
    }

    // Binary search close tại time (candles sort tăng dần).
    _closeAt(time) {
        const bars = this.chartEngine.getBars ? this.chartEngine.getBars() : [];
        let lo = 0, hi = bars.length - 1, best = null;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (bars[mid].time <= time) { best = bars[mid]; lo = mid + 1; } else { hi = mid - 1; }
        }
        return best ? best.close : null;
    }

    _lineHtml(width, y, color) {
        return '<line x1="0" y1="' + y.toFixed(1) + '" x2="' + width.toFixed(1) + '" y2="' + y.toFixed(1) + '" ' +
            'stroke="' + color + '" stroke-width="1" stroke-dasharray="5,4" opacity="0.65" />';
    }

    // Chip giá trị: text có halo (stroke) để đọc được trên trục giá.
    _chipHtml(rightX, y, color, text, theme) {
        const halo = theme.background === '#0d1117' ? 'rgba(13,17,23,0.85)' : 'rgba(255,255,255,0.92)';
        return '<text x="' + (rightX - 6).toFixed(1) + '" y="' + (y + 3.5).toFixed(1) + '" ' +
            'font-size="10" font-weight="600" text-anchor="end" ' +
            'paint-order="stroke" stroke="' + halo + '" stroke-width="4" stroke-linejoin="round" ' +
            'fill="' + color + '">' + escapeXml(text) + '</text>';
    }

    _fmtPrice(v) { return Math.round(Number(v)).toLocaleString('en-US'); }
    _fmtVal(v) { return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }); }

    /** Vẽ lại với trạng thái hiện tại (dùng sau khi đổi theme). */
    refresh() { this._schedule(); }

    destroy() {
        if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
        for (const un of this._unsubs) { try { un(); } catch (e) { /* ignore */ } }
        this._unsubs = [];
        if (this._ro) { try { this._ro.disconnect(); } catch (e) { /* ignore */ } this._ro = null; }
        if (this.svg) { try { this.svg.remove(); } catch (e) { /* ignore */ } this.svg = null; }
        this._time = null;
    }
}

function escapeXml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
}
