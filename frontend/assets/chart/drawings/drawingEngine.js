/**
 * DrawingEngine — tầng tương tác + render drawing.
 *
 * - Tạo SVG overlay phủ lên chart (lưu theo time/price, render bằng toạ độ pixel).
 * - Chế độ tool: click tạo điểm → preview → hoàn tất (add vào manager).
 * - Chế độ crosshair: click/bấm-kéo để chọn / kéo handle / kéo cả drawing.
 * - Magnet: snap giá về OHLC gần nhất (ưu tiên High/Low).
 * - Undo/Redo, ESC, Delete, lock/hide.
 *
 * KHÔNG render lại toàn chart khi mouse move — chỉ cập nhật SVG drawing
 * (rAF-throttle) khi pan/zoom/resize.
 */
import { TOOL_DEFS, renderDrawing, renderPreview, hitTestDrawing } from './shapes.js';

const DEFAULT_COLORS = {
    trendline: '#2962ff',
    horizontal: '#ff6d00',
    vertical: '#8e24aa',
    channel: '#00b8d4',
    fibonacci: '#f0b90b',
    fibextension: '#f0b90b',
    rectangle: '#008080',
    text: '#3949ab',
    marker: '#3949ab',
    measure: '#43a047',
};

function defaultStyle(type) {
    return { color: DEFAULT_COLORS[type] || '#2962ff', width: 1, opacity: 1 };
}

function nearestIndex(candles, time) {
    let lo = 0, hi = candles.length - 1;
    if (time <= candles[0].time) return 0;
    if (time >= candles[hi].time) return hi;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (candles[mid].time < time) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(candles[lo - 1].time - time) < Math.abs(candles[lo].time - time)) return lo - 1;
    return lo;
}

export class DrawingEngine {
    /**
     * @param {object} deps
     *   - chartEngine  (có getChart/getCandleSeries/getContainer)
     *   - manager      (DrawingManager)
     *   - getCandles   () => Candle[]
     *   - onToolChange (tool) => void
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.manager = deps.manager;
        this.getCandles = deps.getCandles || (() => []);
        this.onToolChange = deps.onToolChange || null;
        this.chart = null;
        this.series = null;
        this.container = null;
        this.svg = null;
        this.activeTool = 'crosshair';
        this.magnet = false;
        this.partial = null;      // drawing đang tạo dở
        this.drag = null;         // { mode, id, index, start, startTime, startPrice, origPoints }
        this._lastCursor = null;
        this._raf = null;
        this._ro = null;
        this._paneEl = null;
        this._paneRO = null;
        this._cleanup = [];
        this.inputActive = false;
        this._ctxMenu = null;
        this._ctxClose = null;
    }

    create() {
        const ce = this.chartEngine;
        this.chart = ce.getChart();
        this.series = ce.getCandleSeries();
        this.container = ce.getContainer();
        if (!this.chart || !this.container) return;

        // Idempotent: svg đã tồn tại & đang hiển thị → chỉ render lại
        if (this.svg && this.svg.isConnected) {
            this.render();
            return;
        }
        // Dọn svg cũ (nếu có) trước khi tạo mới
        if (this.svg) { try { this.svg.remove(); } catch (e) { /* ignore */ } this.svg = null; }

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'chart-overlay');
        svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:4;display:block;';
        this.container.appendChild(svg);
        this.svg = svg;

        // Pointer handlers (bubble từ drawing elements khi crosshair; từ svg khi tool active)
        svg.addEventListener('pointerdown', (e) => this._onDown(e));
        svg.addEventListener('pointermove', (e) => this._onMove(e));
        svg.addEventListener('pointerup', (e) => this._onUp(e));
        svg.addEventListener('wheel', (e) => { /* để bubble tới chart để zoom */ });

        // Menu chuột phải trên drawing (PHASE 14)
        svg.addEventListener('contextmenu', (e) => this._onContextMenu(e));

        // Click ngoài drawing (trên canvas) → bỏ chọn
        const deselect = (e) => {
            if (e.target && e.target.closest && e.target.closest('.co-drawing')) return;
            if (this.manager.selectedId) { this.manager.select(null); this.render(); }
        };
        this.container.addEventListener('pointerdown', deselect);

        // Redraw khi pan/zoom/resize
        try { this.chart.timeScale().subscribeVisibleTimeRangeChange(() => this.render()); } catch (e) { /* ignore */ }
        try { this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.render()); } catch (e) { /* ignore */ }
        if (typeof ResizeObserver !== 'undefined') {
            this._ro = new ResizeObserver(() => this.render());
            this._ro.observe(this.container);
        }

        this._cleanup.push(() => {
            svg.remove();
            this.container.removeEventListener('pointerdown', deselect);
            if (this._ro) this._ro.disconnect();
            if (this._paneRO) { this._paneRO.disconnect(); this._paneRO = null; this._paneEl = null; }
            this._closeCtxMenu();
        });

        // Keyboard
        this._keyHandler = (e) => this._onKey(e);
        window.addEventListener('keydown', this._keyHandler);
        this._cleanup.push(() => window.removeEventListener('keydown', this._keyHandler));

        this.render();
    }

    // ---------- coordinate mapping ----------
    xOf(time) { try { return this.chart.timeScale().timeToCoordinate(time); } catch (e) { return null; } }
    yOf(price) { try { return this.series.priceToCoordinate(price); } catch (e) { return null; } }
    xToTime(x) { try { return this.chart.timeScale().coordinateToTime(x); } catch (e) { return null; } }
    yToPrice(y) { try { return this.series.coordinateToPrice(y); } catch (e) { return null; } }

    env() {
        let W = 800, H = 400;
        try { W = this.chart.timeScale().width(); } catch (e) { /* ignore */ }
        try {
            const panes = this.chart.panes();
            if (panes && panes.length) {
                const h = panes[0].getHeight();
                H = h > 0 ? h : this.chart.timeScale().height();
            } else {
                H = this.chart.timeScale().height();
            }
        } catch (e) {
            try { H = this.chart.timeScale().height(); } catch (e2) { /* ignore */ }
        }
        const self = this;
        return { W, H, xOf: (t) => self.xOf(t), yOf: (p) => self.yOf(p) };
    }

    getPoint(e) {
        const rect = this.svg.getBoundingClientRect();
        const env = this.env();
        // Giới hạn trong pane giá chính (khi có sub-pane chỉ báo) → toạ độ chuẩn
        const x = Math.max(0, Math.min(env.W, e.clientX - rect.left));
        const y = Math.max(0, Math.min(env.H, e.clientY - rect.top));
        return { x, y };
    }

    toTimePrice(pt) {
        const time = this.xToTime(pt.x);
        const price = this.yToPrice(pt.y);
        if (time == null || price == null) return null;
        return { time, price };
    }

    magnetSnap(time, price) {
        if (!this.magnet) return { time, price };
        const candles = this.getCandles();
        if (!candles || !candles.length) return { time, price };
        const c = candles[nearestIndex(candles, time)];
        if (!c) return { time, price };
        const opts = [c.high, c.low, c.open, c.close];
        let best = c.close, bestD = Infinity;
        for (const o of opts) {
            const d = Math.abs(o - price);
            if (d < bestD) { bestD = d; best = o; }
        }
        return { time: c.time, price: best };
    }

    // ---------- tools / actions ----------
    setTool(tool) {
        this.activeTool = tool && TOOL_DEFS[tool] ? tool : 'crosshair';
        this.partial = null;
        this.closeTextInput();
        this.render();
        if (this.onToolChange) this.onToolChange(this.activeTool);
    }
    getTool() { return this.activeTool; }

    setMagnet(v) { this.magnet = !!v; }

    lockSelected() {
        const d = this.manager.getSelected();
        if (d) this.manager.setLocked(d.id, !d.locked);
        this.render();
    }
    hideSelected() {
        const d = this.manager.getSelected();
        if (d) { this.manager.setVisible(d.id, false); this.manager.select(null); }
        this.render();
    }
    deleteSelected() { this.manager.removeSelected(); this.render(); }
    undo() { this.manager.undo(); this.render(); }
    redo() { this.manager.redo(); this.render(); }

    // ---------- render ----------
    render() {
        if (this._raf) return;
        this._raf = requestAnimationFrame(() => {
            this._raf = null;
            this._doRender();
        });
    }

    // Theo dõi kích thước pane giá chính (khi thêm/xoá sub-pane chỉ báo).
    _watchMainPane() {
        if (typeof ResizeObserver === 'undefined') return;
        let el = null;
        try { el = this.chart.panes()[0].getHTMLElement(); } catch (e) { return; }
        if (!el || el === this._paneEl) return;
        if (this._paneRO) { try { this._paneRO.disconnect(); } catch (e) { /* ignore */ } }
        this._paneEl = el;
        this._paneRO = new ResizeObserver(() => this.render());
        this._paneRO.observe(el);
    }

    _doRender() {
        const svg = this.svg;
        if (!svg) return;
        this._watchMainPane();
        const env = this.env();
        const visible = this.manager.getVisible();
        const selId = this.manager.selectedId;
        let html = '';
        for (const d of visible) {
            html += renderDrawing(d, env, { selected: d.id === selId, symbol: this.manager.symbol, timeframe: this.manager.timeframe }, this.getCandles());
        }
        if (this.activeTool !== 'crosshair' && this.partial && this._cursorTP) {
            const previewD = {
                type: this.partial.type,
                style: this.partial.style,
                points: this.partial.points.concat([this._cursorTP]),
            };
            html += renderPreview(previewD, env);
        }
        svg.innerHTML = html;
        // Khi tool active → svg nhận pointer để VẼ; crosshair → svg trong suốt với
        // hit-test (chỉ shape vẽ có pointer-events:auto mới click được). PHẢI set
        // inline (KHÔNG chỉ class co-active) vì inline style pointer-events:none
        // (tạo lúc đầu) override rule CSS `.co-active{pointer-events:all}`.
        const active = this.activeTool !== 'crosshair';
        svg.classList.toggle('co-active', active);
        svg.style.pointerEvents = active ? 'all' : 'none';
    }

    hitTestTop(pt) {
        const visible = this.manager.getVisible();
        const env = this.env();
        for (let i = visible.length - 1; i >= 0; i--) {
            const d = visible[i];
            if (d.visible === false) continue;
            const hit = hitTestDrawing(d, env, pt.x, pt.y);
            if (hit) return { id: d.id, drawing: d, part: hit.part, index: hit.index };
        }
        return null;
    }

    // ---------- pointer ----------
    _onDown(e) {
        const pt = this.getPoint(e);
        const tool = this.activeTool;

        if (tool !== 'crosshair') {
            // tạo drawing
            e.stopPropagation();
            this._createPoint(pt, e);
            return;
        }

        // chế độ crosshair: hit-test drawing
        const hit = this.hitTestTop(pt);
        if (hit) {
            e.stopPropagation();
            this.manager.select(hit.id);
            if (!hit.drawing.locked) {
                if (hit.part === 'handle') {
                    this.manager.beginSnapshot();
                    this.drag = {
                        mode: 'handle', id: hit.id, index: hit.index,
                        start: pt,
                    };
                } else {
                    this.manager.beginSnapshot();
                    this.drag = {
                        mode: 'body', id: hit.id, start: pt,
                        startTime: this.toTimePrice(pt) ? this.toTimePrice(pt).time : 0,
                        startPrice: this.toTimePrice(pt) ? this.toTimePrice(pt).price : 0,
                        origPoints: hit.drawing.points.map((p) => ({ time: p.time, price: p.price })),
                    };
                }
                try { this.svg.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
            }
            this.render();
        }
        // click rỗng → deselect (xử lý ở listener container)
    }

    _createPoint(pt, e) {
        const tp = this.toTimePrice(pt);
        if (!tp) return;
        const snapped = this.magnetSnap(tp.time, tp.price);
        const def = TOOL_DEFS[this.activeTool];

        if (!this.partial) {
            this.partial = { type: this.activeTool, points: [], style: defaultStyle(this.activeTool), text: '' };
        }
        this.partial.points.push({ time: snapped.time, price: snapped.price });

        if (this.activeTool === 'text') {
            this.render();
            this._showTextInput(pt, snapped);
            return;
        }
        if (this.partial.points.length >= def.minPoints) {
            this.manager.add({
                type: this.partial.type,
                points: this.partial.points,
                style: this.partial.style,
            });
            this.partial = null;
        }
        this.render();
    }

    _onMove(e) {
        const pt = this.getPoint(e);
        this._lastCursor = pt;
        this._cursorTP = this.toTimePrice(pt);
        if (this.drag) {
            e.preventDefault();
            const d = this.manager.get(this.drag.id);
            if (d && !d.locked) {
                if (this.drag.mode === 'handle') {
                    const tp = this.toTimePrice(pt);
                    if (tp) {
                        const s = this.magnetSnap(tp.time, tp.price);
                        this.manager.applyPoints(d.id, d.points.map((pp, i) => i === this.drag.index ? { time: s.time, price: s.price } : { ...pp }));
                    }
                } else {
                    const tp = this.toTimePrice(pt);
                    if (tp) {
                        const dt = tp.time - this.drag.startTime;
                        const dp = tp.price - this.drag.startPrice;
                        this.manager.applyPoints(d.id, this.drag.origPoints.map((pp) => ({ time: pp.time + dt, price: pp.price + dp })));
                    }
                }
            }
            return;
        }
        if (this.activeTool !== 'crosshair' && this.partial) {
            this.render();
        }
    }

    _onUp(e) {
        if (this.drag) {
            try { this.svg.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
            this.drag = null;
        }
    }

    _onKey(e) {
        if (this.inputActive) return;
        const tag = e.target && e.target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;

        // Phím tắt đổi công cụ (PHASE 14): 1..0 + m
        const toolKeys = {
            '1': 'crosshair', '2': 'trendline', '3': 'horizontal', '4': 'vertical',
            '5': 'channel', '6': 'fibonacci', '7': 'fibextension', '8': 'rectangle',
            '9': 'text', '0': 'marker', 'm': 'measure',
        };
        if (toolKeys[e.key] && !e.ctrlKey && !e.metaKey && !e.altKey) {
            e.preventDefault();
            this.setTool(toolKeys[e.key]);
            return;
        }

        if (e.key === 'Escape') {
            if (this.partial) { this.partial = null; this.render(); return; }
            this.setTool('crosshair');
            this.manager.select(null);
            this.render();
        } else if (e.key === 'Delete' || e.key === 'Backspace') {
            const sel = this.manager.getSelected();
            if (sel && !sel.locked) { this.manager.removeSelected(); this.render(); }
        } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
            e.preventDefault();
            if (e.shiftKey) this.manager.redo(); else this.manager.undo();
            this.render();
        } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
            e.preventDefault();
            this.manager.redo();
            this.render();
        }
    }

    // ---------- text input ----------
    _showTextInput(pt, point) {
        this.closeTextInput();
        const wrap = document.createElement('div');
        wrap.className = 'co-text-input';
        const dark = document.body.classList.contains('dark-mode');
        wrap.style.cssText = 'position:absolute;left:' + pt.x + 'px;top:' + pt.y + 'px;z-index:6;' +
            'background:' + (dark ? '#161b22' : '#fff') + ';border:1px solid ' + (dark ? '#30363d' : '#dce1e9') + ';' +
            'border-radius:8px;padding:4px;box-shadow:0 4px 14px rgba(0,0,0,0.15);';
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Nhập nội dung...';
        input.style.cssText = 'width:180px;font-size:12px;padding:4px 6px;border:none;outline:none;background:transparent;color:' + (dark ? '#e6edf3' : '#1a1f36') + ';';
        wrap.appendChild(input);
        this.container.appendChild(wrap);

        this.inputActive = true;
        const finish = (commit) => {
            this.inputActive = false;
            wrap.remove();
            if (commit && this.partial && this.partial.points.length) {
                const d = this.partial;
                this.partial = null;
                this.manager.add({ type: d.type, points: d.points, style: d.style, text: input.value.trim() || 'Text' });
            } else {
                this.partial = null;
            }
            this.render();
        };
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); finish(true); }
            else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
        });
        input.addEventListener('blur', () => finish(input.value.trim() !== '' ? true : false));
        setTimeout(() => input.focus(), 0);
    }

    closeTextInput() {
        const el = this.container && this.container.querySelector('.co-text-input');
        if (el) el.remove();
        this.inputActive = false;
    }

    // ---------- menu chuột phải trên drawing (PHASE 14) ----------
    _onContextMenu(e) {
        e.preventDefault();
        e.stopPropagation();
        const pt = this.getPoint(e);
        const hit = this.hitTestTop(pt);
        if (!hit) { this._closeCtxMenu(); return; }
        this.manager.select(hit.id);
        this.render();
        const rect = this.container.getBoundingClientRect();
        const x = Math.max(0, Math.min(rect.width - 190, e.clientX - rect.left));
        const y = Math.max(0, Math.min(rect.height - 240, e.clientY - rect.top));
        this._openCtxMenu(hit.drawing, x, y);
    }

    _openCtxMenu(d, x, y) {
        this._closeCtxMenu();
        const menu = document.createElement('div');
        menu.className = 'co-ctx';

        // Màu
        const COLORS = ['#2962ff', '#f0b90b', '#26a69a', '#ef5350', '#8e44ad', '#e91e63', '#ff7043', '#3949ab', '#008080', '#43a047'];
        const cRow = document.createElement('div');
        cRow.className = 'co-ctx-row';
        for (const c of COLORS) {
            const sw = document.createElement('button');
            sw.type = 'button';
            sw.className = 'co-ctx-swatch';
            sw.style.background = c;
            sw.title = c;
            if (d.style && d.style.color === c) sw.classList.add('on');
            sw.addEventListener('click', (ev) => {
                ev.stopPropagation();
                this.manager.update(d.id, { style: { color: c } });
                this.render();
                this._closeCtxMenu();
            });
            cRow.appendChild(sw);
        }
        menu.appendChild(cRow);

        // Độ dày
        const wRow = document.createElement('div');
        wRow.className = 'co-ctx-row';
        for (const w of [1, 2, 3, 4]) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'co-ctx-w';
            b.textContent = String(w);
            b.title = 'Độ dày ' + w;
            if (d.style && d.style.width === w) b.classList.add('on');
            b.addEventListener('click', (ev) => {
                ev.stopPropagation();
                this.manager.update(d.id, { style: { width: w } });
                this.render();
                this._closeCtxMenu();
            });
            wRow.appendChild(b);
        }
        menu.appendChild(wRow);

        const addDivider = () => {
            const dv = document.createElement('div');
            dv.className = 'co-ctx-divider';
            menu.appendChild(dv);
        };
        const addAction = (label, icon, fn) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'co-ctx-item';
            b.innerHTML = '<i class="fa-solid ' + icon + '"></i> ' + label;
            b.addEventListener('click', (ev) => {
                ev.stopPropagation();
                fn();
                this._closeCtxMenu();
            });
            menu.appendChild(b);
        };

        addDivider();
        addAction(d.locked ? 'Mở khóa' : 'Khóa', 'fa-lock', () => this.manager.setLocked(d.id, !d.locked));
        addAction('Nhân bản', 'fa-copy', () => this._duplicate(d));
        addAction('Ẩn', 'fa-eye-slash', () => { this.manager.setVisible(d.id, false); this.manager.select(null); });
        addDivider();
        addAction('Xóa', 'fa-trash', () => this.manager.remove(d.id));

        menu.style.left = x + 'px';
        menu.style.top = y + 'px';
        this.container.appendChild(menu);
        this._ctxMenu = menu;

        this._ctxClose = (ev) => {
            if (this._ctxMenu && !this._ctxMenu.contains(ev.target)) this._closeCtxMenu();
        };
        document.addEventListener('pointerdown', this._ctxClose);
        document.addEventListener('contextmenu', this._ctxClose);
    }

    _closeCtxMenu() {
        if (this._ctxClose) {
            document.removeEventListener('pointerdown', this._ctxClose);
            document.removeEventListener('contextmenu', this._ctxClose);
            this._ctxClose = null;
        }
        if (this._ctxMenu) {
            try { this._ctxMenu.remove(); } catch (e) { /* ignore */ }
            this._ctxMenu = null;
        }
    }

    _duplicate(d) {
        const points = d.points.map((p) => ({ time: p.time + 86400, price: p.price }));
        this.manager.add({
            type: d.type,
            points,
            style: Object.assign({}, d.style),
            text: d.text,
        });
    }

    destroy() {
        if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
        if (this._paneRO) { try { this._paneRO.disconnect(); } catch (e) { /* ignore */ } this._paneRO = null; this._paneEl = null; }
        this.closeTextInput();
        for (const fn of this._cleanup) { try { fn(); } catch (e) { /* ignore */ } }
        this._cleanup = [];
        this.svg = null;
    }
}
