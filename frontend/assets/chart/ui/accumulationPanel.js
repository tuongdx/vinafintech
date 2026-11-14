/**
 * AccumulationPanel — "Giả định tích sản" cho Bottom Dashboard fullscreen (màn hình
 * "Biểu đồ phân tích kỹ thuật" toàn màn hình).
 *
 * ⚠️ THUẬT TOÁN KHÔNG NẰM Ở ĐÂY. Panel dùng CHUNG đúng một nguồn với block
 * "Giả định tích sản <MÃ> 5 năm" của trang chi tiết cổ phiếu:
 *     window.VinaFintechAccum  ←  /static/accumulationCalc.js
 *       - buildMonthlyPrices(history)                 (dữ liệu: GET /api/stocks/{sym}/history?period=5y)
 *       - computeSeries(months, dividends, monthly)   (dữ liệu: GET /api/stocks/{sym}/dividends)
 * Nhờ vậy 2 màn hình luôn ra CÙNG kết quả (trước đây panel tự parse cổ tức bản rút gọn
 * nên tính sai: cộng cả đợt quyền mua/ESOP và đếm trùng đợt cổ tức).
 *
 * File này CHỈ lo phần HIỂN THỊ trong panel (chart SVG nhỏ + summary 3 dòng).
 */
const ACC = (typeof window !== 'undefined') ? window.VinaFintechAccum : null;

const LINES = [
    { key: 'capital', label: 'Vốn góp', color: '#94a3b8' },
    { key: 'bank', label: 'Gửi NH 7%', color: '#f59e0b' },
    { key: 'stock', label: 'Cổ phiếu + cổ tức', color: '#008080' },
];

// Bước chia "đẹp" cho trục tiền: 0, step, 2*step, ... >= maxVal.
function niceTicks(maxVal, count) {
    if (!(maxVal > 0)) return [0];
    const raw = maxVal / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    let step;
    if (norm < 1.5) step = 1;
    else if (norm < 3) step = 2;
    else if (norm < 7) step = 5;
    else step = 10;
    step *= mag;
    const ticks = [];
    for (let v = 0; v <= maxVal + step * 1e-6; v += step) ticks.push(Math.round(v));
    if (ticks[ticks.length - 1] < maxVal) ticks.push(Math.round(ticks[ticks.length - 1] + step));
    return ticks;
}

// Định dạng giá trị tiền cho trục Y (luôn đơn vị "tr" để khớp summary).
function fmtAxisMoney(v) {
    if (v <= 0) return '0';
    const t = v / 1e6;
    return (t >= 100 ? Math.round(t).toLocaleString('en-US') : t.toFixed(1)) + ' tr';
}

// Các mã KHÔNG phải cổ phiếu (chỉ số / thị trường) → không áp dụng tích sản.
const INDEX_SYMBOLS = new Set([
    'VNINDEX', 'VN30', 'VN50', 'VN100', 'VNXALL', 'VNX50',
    'HNX', 'HNX30', 'HNXINDEX', 'HNXIC', 'HNXLG', 'HNXMD', 'HNXCON', 'HNXFIN', 'HNXSME',
    'UPCOM', 'UPINDEX', 'UPCOMINDEX',
]);
export function isIndexSymbol(sym) {
    const s = String(sym || '').trim().toUpperCase();
    if (!s) return false;
    if (INDEX_SYMBOLS.has(s)) return true;
    return /INDEX/.test(s);
}

export class AccumulationPanel {
    /**
     * @param {object} deps - { el (container), getSymbol () => string }
     */
    constructor(deps) {
        this.el = deps.el;
        this.getSymbol = deps.getSymbol || (() => '');
        this.isIndex = deps.isIndex || (() => isIndexSymbol(this.symbol));
        this.symbol = '';
        this.monthly = 5000000; // 5 triệu VND
        this._lastInput = null;
        this._data = null;
        this._loading = false;
        this._destroyed = false;
    }

    create() {
        if (!this.el) return this;
        this.el.innerHTML =
            '<div class="fsa-controls">' +
            '  <label for="fsaMonthly">Góp mỗi tháng</label>' +
            '  <input type="number" id="fsaMonthly" value="5" min="0" max="100" step="0.5" inputmode="decimal" />' +
            '  <span>triệu</span>' +
            '</div>' +
            '<div class="fsa-main">' +
            '  <div class="fsa-chart" id="fsaChart"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '  <div class="fsa-summary" id="fsaSummary"></div>' +
            '</div>';
        const input = this.el.querySelector('#fsaMonthly');
        if (input) {
            const rerender = () => {
                if (this._isIndexNow()) { this._applyIndexState(); return; }
                let val = parseFloat(input.value);
                if (!Number.isFinite(val)) val = 0;
                val = Math.max(0, Math.min(100, val));
                input.value = val;
                this._lastInput = input.value;
                this.monthly = val * 1e6;
                if (this._data) this._render();
            };
            input.addEventListener('input', rerender);
            input.addEventListener('change', rerender);
        }
        this._els = {
            chart: this.el.querySelector('#fsaChart'),
            summary: this.el.querySelector('#fsaSummary'),
            input: this.el.querySelector('#fsaMonthly'),
        };
        this._applyIndexState();
        return this;
    }

    setSymbol(symbol) {
        const sym = String(symbol || '').trim().toUpperCase();
        if (sym && sym === this.symbol && this._data) return;
        this.symbol = sym;
        this._applyIndexState();
        this._data = null;
        if (this._els && this._els.chart) {
            this._els.chart.innerHTML = '<div class="fss-muted">Đang cập nhật…</div>';
        }
        if (this._els && this._els.summary) this._els.summary.innerHTML = '';
        if (!sym) return;
        this._load();
    }

    // Chỉ số / mã không phải cổ phiếu → Góp mỗi tháng = 0, chặn nhập.
    _isIndexNow() {
        try { return !!this.isIndex(); } catch (e) { return isIndexSymbol(this.symbol); }
    }

    _applyIndexState() {
        if (!this._els || !this._els.input) return;
        const idx = this._isIndexNow();
        if (idx) {
            this._els.input.disabled = true;
            if (this._lastInput == null) this._lastInput = this._els.input.value;
            this._els.input.value = '0';
            this.monthly = 0;
            if (this._data) this._render();
        } else {
            this._els.input.disabled = false;
            if (this._lastInput != null) {
                this._els.input.value = this._lastInput;
                const val = Math.max(0, Math.min(100, parseFloat(this._lastInput) || 0));
                this.monthly = val * 1e6;
                if (this._data) this._render();
            }
        }
    }

    refreshIndexState() { this._applyIndexState(); }

    async _load() {
        if (this._loading || this._destroyed) return;
        const sym = this.symbol;
        this._loading = true;
        try {
            const [history, dividends] = await Promise.all([
                fetch('/api/stocks/' + encodeURIComponent(sym) + '/history?period=5y', { cache: 'no-store' }).then((r) => r.json()),
                fetch('/api/stocks/' + encodeURIComponent(sym) + '/dividends', { cache: 'no-store' }).then((r) => r.json()),
            ]);
            if (this._destroyed || this.symbol !== sym) return;
            this._data = {
                history: Array.isArray(history) ? history : [],
                dividends: Array.isArray(dividends) ? dividends : [],
            };
        } catch (e) {
            if (this._destroyed) return;
            this._data = { history: [], dividends: [] };
        }
        this._loading = false;
        this._render();
    }

    _render() {
        if (!this._els || this._destroyed) return;
        const { history, dividends } = this._data;
        // Dùng CHUNG thuật toán với block ở trang chi tiết cổ phiếu (1 nguồn duy nhất).
        if (!ACC) {
            this._els.chart.innerHTML = '<div class="fss-muted">Không nạp được công cụ tính tích sản</div>';
            this._els.summary.innerHTML = '';
            return;
        }
        const months = ACC.buildMonthlyPrices(history);
        const series = ACC.computeSeries(months, dividends, this.monthly);
        this._renderChart(months, series);
        this._renderSummary(series);
    }

    _renderChart(months, series) {
        const el = this._els.chart;
        if (!el) return;
        const n = series.capital.length;
        if (n < 2) {
            el.innerHTML = '<div class="fss-muted">Không có đủ dữ liệu để vẽ biểu đồ</div>';
            return;
        }
        const W = 360, H = 110;
        const padL = 50, padR = 6, padT = 6, padB = 16;
        const plotW = W - padL - padR;
        const plotH = H - padT - padB;
        const allVals = [...series.capital, ...series.bank, ...series.stock].filter((v) => Number.isFinite(v) && v > 0);
        let maxVal = allVals.length ? Math.max(...allVals) : this.monthly * n;
        if (!(maxVal > 0)) maxVal = this.monthly * n;

        const ticks = niceTicks(maxVal, 4);
        const lastTick = ticks[ticks.length - 1];
        const niceMax = (lastTick > 0) ? lastTick : 1;
        const xAt = (i) => padL + (n === 1 ? 0 : (i / (n - 1)) * plotW);
        const yAt = (v) => padT + plotH - (v / niceMax) * plotH;

        // Nhãn trục thời gian: đầu, 1/3, 2/3, cuối (định dạng MM/YY).
        const xIdx = [0, Math.round((n - 1) / 3), Math.round(((n - 1) * 2) / 3), n - 1]
            .filter((v, i, a) => a.indexOf(v) === i);
        const xLabel = (i) => {
            const m = months[i];
            if (!m) return '';
            return String(m.month).padStart(2, '0') + '/' + String(m.year).slice(2);
        };

        let svg = '<svg class="fsa-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Biểu đồ tích sản 5 năm">';
        // Đường lưới ngang theo mức tiền
        for (const t of ticks) {
            const y = yAt(t);
            svg += '<line class="fsa-grid" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"></line>';
        }
        for (const line of LINES) {
            const data = series[line.key];
            const pts = data.map((v, i) => xAt(i).toFixed(1) + ',' + yAt(v).toFixed(1)).join(' ');
            svg += '<polyline fill="none" stroke="' + line.color + '" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" points="' + pts + '"></polyline>';
        }
        svg += '</svg>';

        // Trục tiền (trái) + trục thời gian (dưới) — HTML overlay để không bị méo khi SVG giãn.
        let yHtml = '';
        for (const t of ticks) {
            yHtml += '<span class="fsa-ytick" style="top:' + ((yAt(t) / H) * 100).toFixed(2) + '%">' + fmtAxisMoney(t) + '</span>';
        }
        let xHtml = '';
        for (const i of xIdx) {
            xHtml += '<span class="fsa-xtick" style="left:' + ((xAt(i) / W) * 100).toFixed(2) + '%">' + xLabel(i) + '</span>';
        }

        el.innerHTML =
            '<div class="fsa-plot">' +
            svg +
            '<div class="fsa-yaxis">' + yHtml + '</div>' +
            '<div class="fsa-xaxis">' + xHtml + '</div>' +
            '</div>';
    }

    _renderSummary(series) {
        const el = this._els.summary;
        if (!el) return;
        const n = series.capital.length;
        if (n === 0) { el.innerHTML = '<div class="fss-muted">--</div>'; return; }
        const cap = series.capital[n - 1];
        const bank = series.bank[n - 1];
        const stock = series.stock[n - 1];
        const fmt = (v) => (v / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1 }) + ' tr';
        const diff = (v) => {
            const d = v - cap;
            const pct = cap > 0 ? (d / cap) * 100 : 0;
            const pos = d >= 0;
            return '<span class="fsa-diff ' + (pos ? 'pos' : 'neg') + '">' + (pos ? '▲' : '▼') +
                ' ' + Math.abs(d / 1e6).toFixed(1) + ' tr (' + (pos ? '+' : '') + pct.toFixed(1) + '%)</span>';
        };
        const row = (color, label, val, extra) =>
            '<div class="fsa-row">' +
            '<div class="fsa-row-top"><span class="fsa-label"><i class="fsa-dot" style="background:' + color + '"></i>' + label + '</span></div>' +
            '<div class="fsa-row-bottom"><span class="fsa-val">' + val + '</span>' + (extra || '') + '</div>' +
            '</div>';
        el.innerHTML =
            row('#94a3b8', 'Vốn góp', fmt(cap)) +
            row('#f59e0b', 'Gửi NH 7%', fmt(bank), diff(bank)) +
            row('#008080', 'Cổ phiếu + cổ tức', fmt(stock), diff(stock));
    }

    destroy() {
        this._destroyed = true;
    }
}
