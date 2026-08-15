/**
 * PaneResizer (PHASE 16) — kéo vách ngăn giữa các pane để chỉnh độ cao.
 *
 * Đặt handle tại biên mỗi pane (trừ pane cuối). Kéo lên/xuống → điều chỉnh
 * stretch factor của pane trên/dưới sao cho tổng không đổi (đổi px thành
 * factor qua K = chiều cao chung / tổng factor). Handles tự cập nhật vị trí
 * khi pan/zoom/resize/đổi pane.
 */
export class PaneResizer {
    /**
     * @param {object} deps
     *   - chartEngine
     *   - onChange () => void  (sau mỗi lần kéo)
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.onChange = deps.onChange || null;
        this.container = null;
        this.el = null;
        this.handles = [];
        this._drag = null;
        this._mv = null;
        this._up = null;
        this._ro = null;
        this._paneEl = null;
        this._paneRO = null;
        this._raf = null;
        this._cleanup = [];
    }

    create() {
        const container = this.chartEngine.getContainer();
        const chart = this.chartEngine.getChart();
        if (!container || !chart) return;
        this.container = container;
        if (this.el && this.el.isConnected) return;

        const el = document.createElement('div');
        el.className = 'pane-seps';
        el.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:3;display:block;';
        container.appendChild(el);
        this.el = el;

        if (typeof ResizeObserver !== 'undefined') {
            this._ro = new ResizeObserver(() => this.place());
            this._ro.observe(container);
        }
        this._watchPane();
        this._cleanup.push(() => {
            el.remove();
            if (this._ro) { this._ro.disconnect(); this._ro = null; }
            if (this._paneRO) { this._paneRO.disconnect(); this._paneRO = null; this._paneEl = null; }
        });
        this.place();
    }

    _watchPane() {
        const chart = this.chartEngine.getChart();
        if (!chart || typeof ResizeObserver === 'undefined') return;
        let pel = null;
        try { pel = chart.panes()[0].getHTMLElement(); } catch (e) { return; }
        if (!pel || pel === this._paneEl) return;
        if (this._paneRO) { try { this._paneRO.disconnect(); } catch (e) { /* ignore */ } }
        this._paneEl = pel;
        this._paneRO = new ResizeObserver(() => this.place());
        this._paneRO.observe(pel);
    }

    place() {
        if (this._raf) return;
        this._raf = requestAnimationFrame(() => {
            this._raf = null;
            this._doPlace();
        });
    }

    _doPlace() {
        const chart = this.chartEngine.getChart();
        const el = this.el;
        if (!chart || !el || !this.container) return;
        this._watchPane();
        const panes = chart.panes();
        const count = panes.length;
        const rect = this.container.getBoundingClientRect();
        el.innerHTML = '';
        this.handles = [];
        if (count < 2) return;
        for (let i = 0; i < count - 1; i++) {
            const pel = panes[i].getHTMLElement ? panes[i].getHTMLElement() : null;
            if (!pel) continue;
            const pr = pel.getBoundingClientRect();
            const y = Math.max(0, pr.bottom - rect.top);
            const h = document.createElement('div');
            h.className = 'pane-sep';
            h.style.top = (y - 3) + 'px';
            h.dataset.i = String(i);
            h.addEventListener('pointerdown', (e) => this._startDrag(e, i));
            el.appendChild(h);
            this.handles.push(h);
        }
    }

    _startDrag(e, i) {
        if (this._drag) return;
        e.preventDefault();
        e.stopPropagation();
        const chart = this.chartEngine.getChart();
        const panes = chart.panes();
        const above = panes[i];
        const below = panes[i + 1];
        if (!above || !below) return;
        let Fa = 1, Fb = 1, Ha = 100, Hb = 100;
        try { Fa = above.getStretchFactor(); } catch (err) { /* ignore */ }
        try { Fb = below.getStretchFactor(); } catch (err) { /* ignore */ }
        try { Ha = above.getHeight(); } catch (err) { /* ignore */ }
        try { Hb = below.getHeight(); } catch (err) { /* ignore */ }
        const K = (Fa + Fb) > 0 ? (Ha + Hb) / (Fa + Fb) : Ha;
        this._drag = { above, below, startY: e.clientY, Fa, Fb, K, minH: 40 };
        this._mv = (ev) => this._dragMove(ev);
        this._up = (ev) => this._dragEnd(ev);
        window.addEventListener('pointermove', this._mv);
        window.addEventListener('pointerup', this._up);
    }

    _dragMove(e) {
        const d = this._drag;
        if (!d) return;
        const delta = e.clientY - d.startY;
        let nh = d.Fa * d.K + delta;
        let nbelow = d.Fb * d.K - delta;
        if (nh < d.minH) { nbelow -= (nh - d.minH); nh = d.minH; }
        if (nbelow < d.minH) { nh -= (nbelow - d.minH); nbelow = d.minH; }
        if (d.K > 0) {
            try { d.above.setStretchFactor(Math.max(0.1, nh / d.K)); } catch (err) { /* ignore */ }
            try { d.below.setStretchFactor(Math.max(0.1, nbelow / d.K)); } catch (err) { /* ignore */ }
        }
        this.place();
        if (this.onChange) { try { this.onChange(); } catch (err) { /* ignore */ } }
    }

    _dragEnd() {
        if (this._mv) window.removeEventListener('pointermove', this._mv);
        if (this._up) window.removeEventListener('pointerup', this._up);
        this._mv = null;
        this._up = null;
        this._drag = null;
    }

    destroy() {
        if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
        this._dragEnd();
        for (const fn of this._cleanup) { try { fn(); } catch (e) { /* ignore */ } }
        this._cleanup = [];
        this.el = null;
    }
}
