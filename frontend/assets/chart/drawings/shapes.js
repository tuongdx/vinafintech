/**
 * Shapes — định nghĩa từng loại drawing (render SVG + hit-test).
 * Mọi điểm lưu theo (time, price); pixel chỉ dùng để render (xOf/yOf).
 *
 * env = { W, H, xOf(time), yOf(price) }   (W/H = chiều rộng/cao pane)
 */
import {
    lineYAt,
    pointSegDist,
    fibonacciRetracementLevels,
    fibonacciExtensionLevels,
    measureBetween,
} from './geometry.js';

export const TOOL_DEFS = {
    crosshair:  { label: 'Chuột (chọn / pan / zoom)', icon: 'fa-crosshairs' },
    trendline:  { label: 'Đường xu hướng', icon: 'fa-slash', minPoints: 2 },
    horizontal: { label: 'Đường ngang', icon: 'fa-minus', minPoints: 1 },
    vertical:   { label: 'Đường dọc', icon: 'fa-minus', rotate: 90, minPoints: 1 },
    channel:    { label: 'Kênh song song', icon: 'fa-bars', minPoints: 3 },
    fibonacci:  { label: 'Fibonacci Retracement', icon: 'text', text: 'Fib', minPoints: 2 },
    fibextension: { label: 'Fibonacci Extension', icon: 'text', text: 'FibX', minPoints: 3 },
    rectangle:  { label: 'Hình chữ nhật', icon: 'fa-square', minPoints: 2 },
    text:       { label: 'Chữ', icon: 'fa-font', minPoints: 1 },
    marker:     { label: 'Marker', icon: 'fa-map-pin', minPoints: 1 },
    measure:    { label: 'Thước đo', icon: 'fa-ruler', minPoints: 2, showPoints: false },
};

export const TOOL_TYPES = Object.keys(TOOL_DEFS).filter((t) => t !== 'crosshair');

// ---------- helpers ----------
function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function r2(n) { return Math.round(n * 100) / 100; }
function fmtPrice(n) { return Number(n).toLocaleString('en-US'); }
function fmtDate(t) {
    const d = new Date(t * 1000);
    if (Number.isNaN(d.getTime())) return '';
    return String(d.getDate()).padStart(2, '0') + '/' + String(d.getMonth() + 1).padStart(2, '0') + '/' + d.getFullYear();
}
function line(x1, y1, x2, y2, st, cls) {
    return '<line class="' + cls + '" x1="' + r2(x1) + '" y1="' + r2(y1) + '" x2="' + r2(x2) + '" y2="' + r2(y2) + '" ' +
        'stroke="' + st.color + '" stroke-width="' + st.width + '" opacity="' + st.opacity + '" />';
}
function ttext(x, y, str, attrs) {
    return '<text x="' + r2(x) + '" y="' + r2(y) + '" ' + (attrs || '') + '>' + esc(str) + '</text>';
}

function pointCoords(d, env) {
    return d.points.map((p) => ({ x: env.xOf(p.time), y: env.yOf(p.price) }));
}

function handles(d, env) {
    const st = d.style;
    let s = '';
    d.points.forEach((p, i) => {
        const x = env.xOf(p.time);
        const y = env.yOf(p.price);
        if (x == null || y == null) return;
        s += '<circle class="co-handle" data-pt="' + i + '" cx="' + r2(x) + '" cy="' + r2(y) + '" r="4" ' +
            'fill="#ffffff" stroke="' + st.color + '" stroke-width="1.5" />';
    });
    return s;
}

// ---------- per-shape render ----------
function renderTrendline(d, env, ui) {
    const p = pointCoords(d, env);
    if (p.length < 2 || p[0].x == null || p[1].x == null) return '';
    const A = p[0], B = p[1];
    const st = d.style;
    const extL = !!st.extendLeft, extR = !!st.extendRight;
    let x1 = A.x, y1 = A.y, x2 = B.x, y2 = B.y;
    if (extL && B.x !== A.x) { x1 = 0; y1 = lineYAt(A.x, A.y, B.x, B.y, 0); }
    if (extR && B.x !== A.x) { x2 = env.W; y2 = lineYAt(A.x, A.y, B.x, B.y, env.W); }
    let s = line(x1, y1, x2, y2, st, 'co-body');
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderHorizontal(d, env, ui) {
    const p = pointCoords(d, env);
    const pt = p[0];
    if (!pt || pt.y == null) return '';
    const st = d.style;
    let s = line(0, pt.y, env.W, pt.y, st, 'co-body');
    s += ttext(env.W - 6, pt.y - 4, fmtPrice(d.points[0].price), 'text-anchor="end" class="co-label" fill="' + st.color + '"');
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderVertical(d, env, ui) {
    const p = pointCoords(d, env);
    const pt = p[0];
    if (!pt || pt.x == null) return '';
    const st = d.style;
    let s = line(pt.x, 0, pt.x, env.H, st, 'co-body');
    s += ttext(pt.x, 14, fmtDate(d.points[0].time), 'text-anchor="middle" class="co-label" fill="' + st.color + '"');
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderChannel(d, env, ui) {
    const p = pointCoords(d, env);
    if (p.length < 3) return '';
    const A = p[0], B = p[1], C = p[2];
    if (A.x == null || B.x == null || C.x == null) return '';
    const st = d.style;
    const dx = B.x - A.x;
    const dy = B.y - A.y;
    const y1L = lineYAt(A.x, A.y, B.x, B.y, 0);
    const y1R = lineYAt(A.x, A.y, B.x, B.y, env.W);
    const kL = dx !== 0 ? (0 - C.x) / dx : 0;
    const kR = dx !== 0 ? (env.W - C.x) / dx : 0;
    const y2L = C.y + kL * dy;
    const y2R = C.y + kR * dy;
    let s = line(0, y1L, env.W, y1R, st, 'co-body') + line(0, y2L, env.W, y2R, st, 'co-body');
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderFibonacci(d, env, ui, extension) {
    const p = pointCoords(d, env);
    const need = extension ? 3 : 2;
    if (p.length < need) return '';
    const a = d.points[0].price, b = d.points[1].price;
    const levels = extension
        ? fibonacciExtensionLevels(a, b, d.points[2].price)
        : fibonacciRetracementLevels(a, b);
    const st = d.style;
    let s = '';
    levels.forEach((lv) => {
        const y = env.yOf(lv.price);
        if (y == null) return;
        s += line(0, y, env.W, y, { color: st.color, width: st.width, opacity: st.opacity * 0.9 }, 'co-body');
        const pct = (lv.level * 100).toFixed(1).replace(/\.0$/, '');
        s += ttext(4, y - 3, pct + '%  ' + fmtPrice(lv.price), 'class="co-label" fill="' + st.color + '"');
    });
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderRectangle(d, env, ui) {
    const p = pointCoords(d, env);
    if (p.length < 2) return '';
    const A = p[0], B = p[1];
    if (A.x == null || B.x == null) return '';
    const x = Math.min(A.x, B.x), y = Math.min(A.y, B.y);
    const w = Math.abs(B.x - A.x), h = Math.abs(B.y - A.y);
    const st = d.style;
    let s = '<rect class="co-body" x="' + r2(x) + '" y="' + r2(y) + '" width="' + r2(w) + '" height="' + r2(h) + '" ' +
        'fill="' + st.color + '" fill-opacity="0.12" stroke="' + st.color + '" stroke-width="' + st.width + '" opacity="' + st.opacity + '" />';
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderText(d, env, ui) {
    const p = pointCoords(d, env);
    const pt = p[0];
    if (!pt || pt.x == null) return '';
    const st = d.style;
    const label = d.text || 'Text';
    let s = ttext(pt.x, pt.y - 6, label, 'class="co-text co-body" font-size="13" fill="' + st.color + '"');
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderMarker(d, env, ui) {
    const p = pointCoords(d, env);
    const pt = p[0];
    if (!pt || pt.x == null) return '';
    const st = d.style;
    let s = '<g class="co-body">' +
        '<circle cx="' + r2(pt.x) + '" cy="' + r2(pt.y) + '" r="6" fill="' + st.color + '" fill-opacity="0.85" stroke="#fff" stroke-width="1" />' +
        '<circle cx="' + r2(pt.x) + '" cy="' + r2(pt.y) + '" r="2.4" fill="#fff" />' +
        '</g>';
    if (d.text) s += ttext(pt.x + 9, pt.y + 4, d.text, 'class="co-label" font-size="12" fill="' + st.color + '"');
    if (ui.selected) s += handles(d, env);
    return s;
}

function renderMeasure(d, env, ui, candles) {
    const p = pointCoords(d, env);
    if (p.length < 2 || p[0].x == null || p[1].x == null) return '';
    const A = p[0], B = p[1];
    const st = d.style;
    const m = measureBetween(d.points[0], d.points[1], candles || []);
    let s = line(A.x, A.y, B.x, B.y, st, 'co-body');
    // Đo theo CẢ 2 TRỤC (không phải khoảng cách đường chéo): nét đứt thể hiện phần
    // OX (ngang = thời gian) và OY (dọc = giá) — kẻ lệch vẫn đo chính xác từng trục.
    s += '<path class="co-measure-guide" pointer-events="none" fill="none" stroke="' + st.color + '" stroke-width="1" stroke-dasharray="3 3" opacity="0.55" ' +
        'd="M' + r2(A.x) + ',' + r2(A.y) + ' H' + r2(B.x) + ' V' + r2(B.y) +
        ' M' + r2(A.x) + ',' + r2(A.y) + ' V' + r2(B.y) + ' H' + r2(B.x) + '"/>';
    const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
    const sign = m.diff >= 0 ? '+' : '';
    const rows = [
        ['Trục X', Math.round(m.days) + ' ngày · ' + m.candleCount + ' nến'],
        ['Trục Y', sign + fmtPrice(m.diff) + ' (' + sign + m.pct.toFixed(2) + '%)'],
    ];
    let inner = '';
    rows.forEach((r, i) => {
        const y = i * 16 - 5;
        inner += '<text x="-80" y="' + r2(y) + '" style="fill:#4fb3b3;font-weight:600" font-size="10">' + esc(r[0]) + '</text>' +
                 '<text x="-30" y="' + r2(y) + '" style="fill:#e6edf3" font-size="11">' + esc(r[1]) + '</text>';
    });
    s += '<g class="co-measure" transform="translate(' + r2(mx) + ',' + r2(my - 10) + ')">' +
        '<rect x="-88" y="-27" width="176" height="42" rx="6" fill="#1e2b41" opacity="0.92"/>' +
        inner +
        '</g>';
    if (ui.selected) s += handles(d, env);
    return s;
}

/** Render một drawing (đã hoàn thành) → SVG markup. */
export function renderDrawing(d, env, ui, candles) {
    const shown = d.visible !== false && d.symbol === (ui.symbol || d.symbol) && d.timeframe === (ui.timeframe || d.timeframe);
    if (!shown) return '';
    let inner = '';
    switch (d.type) {
        case 'trendline': inner = renderTrendline(d, env, ui); break;
        case 'horizontal': inner = renderHorizontal(d, env, ui); break;
        case 'vertical': inner = renderVertical(d, env, ui); break;
        case 'channel': inner = renderChannel(d, env, ui); break;
        case 'fibonacci': inner = renderFibonacci(d, env, ui, false); break;
        case 'fibextension': inner = renderFibonacci(d, env, ui, true); break;
        case 'rectangle': inner = renderRectangle(d, env, ui); break;
        case 'text': inner = renderText(d, env, ui); break;
        case 'marker': inner = renderMarker(d, env, ui); break;
        case 'measure': inner = renderMeasure(d, env, ui, candles); break;
        default: return '';
    }
    if (!inner) return '';
    const lockedCls = d.locked ? ' co-locked' : '';
    const selCls = ui.selected ? ' co-selected' : '';
    return '<g class="co-drawing' + lockedCls + selCls + '" data-id="' + d.id + '">' + inner + '</g>';
}

/** Preview drawing đang tạo (điểm đã click + điểm con trỏ ở cuối). */
export function renderPreview(d, env) {
    // d = drawing giả: points gồm điểm đã click + điểm con trỏ (time/price)
    const inner = renderByType(d.type, d, env, { selected: false });
    if (!inner) return '';
    return '<g class="co-drawing co-preview" data-id="__preview">' + inner + '</g>';
}

function renderByType(type, d, env, ui) {
    switch (type) {
        case 'trendline': return renderTrendline(d, env, ui);
        case 'horizontal': return renderHorizontal(d, env, ui);
        case 'vertical': return renderVertical(d, env, ui);
        case 'channel': return renderChannel(d, env, ui);
        case 'fibonacci': return renderFibonacci(d, env, ui, false);
        case 'fibextension': return renderFibonacci(d, env, ui, true);
        case 'rectangle': return renderRectangle(d, env, ui);
        case 'text': return renderText(d, env, ui);
        case 'marker': return renderMarker(d, env, ui);
        case 'measure': return renderMeasure(d, env, ui, []);
        default: return '';
    }
}

// ---------- hit-test ----------
function handlesHit(d, env, px, py) {
    for (let i = 0; i < d.points.length; i++) {
        const x = env.xOf(d.points[i].time);
        const y = env.yOf(d.points[i].price);
        if (x == null || y == null) continue;
        if (Math.hypot(px - x, py - y) <= 7) return { part: 'handle', index: i };
    }
    return null;
}

function bodyHit(d, env, px, py) {
    const p = pointCoords(d, env);
    switch (d.type) {
        case 'trendline': {
            if (p.length < 2) return false;
            const A = p[0], B = p[1];
            if (A.x == null || B.x == null) return false;
            return pointSegDist(px, py, A.x, A.y, B.x, B.y) <= 7;
        }
        case 'horizontal': {
            const pt = p[0];
            if (!pt || pt.y == null) return false;
            return Math.abs(py - pt.y) <= 7;
        }
        case 'vertical': {
            const pt = p[0];
            if (!pt || pt.x == null) return false;
            return Math.abs(px - pt.x) <= 7;
        }
        case 'channel': {
            if (p.length < 3) return false;
            const A = p[0], B = p[1], C = p[2];
            if (A.x == null || B.x == null || C.x == null) return false;
            const dx = B.x - A.x, dy = B.y - A.y;
            const k = dx !== 0 ? (px - C.x) / dx : 0;
            const y2 = C.y + k * dy;
            const d1 = pointSegDist(px, py, A.x, A.y, B.x, B.y);
            const d2 = Math.abs(py - y2);
            return Math.min(d1, d2) <= 7;
        }
        case 'fibonacci':
        case 'fibextension': {
            const a = d.points[0].price, b = d.points[1].price;
            const levels = d.type === 'fibextension'
                ? fibonacciExtensionLevels(a, b, d.points[2] ? d.points[2].price : 0)
                : fibonacciRetracementLevels(a, b);
            for (const lv of levels) {
                const y = env.yOf(lv.price);
                if (y != null && Math.abs(py - y) <= 7) return true;
            }
            return false;
        }
        case 'rectangle': {
            if (p.length < 2) return false;
            const A = p[0], B = p[1];
            if (A.x == null || B.x == null) return false;
            const x0 = Math.min(A.x, B.x), x1 = Math.max(A.x, B.x);
            const y0 = Math.min(A.y, B.y), y1 = Math.max(A.y, B.y);
            if (px >= x0 && px <= x1 && py >= y0 && py <= y1) return true;
            return pointSegDist(px, py, x0, y0, x1, y0) <= 7 || pointSegDist(px, py, x1, y0, x1, y1) <= 7 ||
                   pointSegDist(px, py, x1, y1, x0, y1) <= 7 || pointSegDist(px, py, x0, y1, x0, y0) <= 7;
        }
        case 'text':
        case 'marker': {
            const pt = p[0];
            if (!pt || pt.x == null) return false;
            return Math.hypot(px - pt.x, py - pt.y) <= 12;
        }
        case 'measure': {
            if (p.length < 2) return false;
            const A = p[0], B = p[1];
            if (A.x == null || B.x == null) return false;
            return pointSegDist(px, py, A.x, A.y, B.x, B.y) <= 7;
        }
        default: return false;
    }
}

/** Trả về { part: 'handle'|'body', index } hoặc null. */
export function hitTestDrawing(d, env, px, py) {
    const h = handlesHit(d, env, px, py);
    if (h) return h;
    if (bodyHit(d, env, px, py)) return { part: 'body', index: -1 };
    return null;
}
