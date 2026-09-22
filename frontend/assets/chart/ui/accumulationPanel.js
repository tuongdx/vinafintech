/**
 * AccumulationPanel — "Giả định tích sản" cho Bottom Dashboard fullscreen (2026-08-18).
 *
 * Tính toán GIỐNG feature "Giả định tích sản 5 năm" của StockDetail (cùng API + thuật toán):
 *   - GET /api/stocks/{symbol}/history?period=5y  → giá đóng cửa cuối mỗi tháng
 *   - GET /api/stocks/{symbol}/dividends          → cổ tức (tiền mặt / cổ phiếu thưởng)
 * 3 đường: Vốn góp (không lãi) / Gửi ngân hàng 7%/năm / Đầu tư cổ phiếu + cổ tức.
 */
const ACCUM_MONTHS = 60;      // 5 năm = 60 tháng
const ACCUM_BANK_RATE = 0.07; // Lãi suất ngân hàng giả định 7%/năm

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

function accumMonthKey(date) {
    if (date == null) return '';
    let d;
    if (typeof date === 'string' && /^\d{2}\/\d{2}\/\d{4}$/.test(date.trim())) {
        const parts = date.trim().split('/').map(Number);
        d = new Date(parts[2], parts[1] - 1, parts[0]);
    } else {
        d = new Date(date);
    }
    if (Number.isNaN(d.getTime())) return '';
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}

function accumMonthKeyFrom(y, m0) {
    return y + '-' + String(m0 + 1).padStart(2, '0');
}

// Chuỗi số từ nguồn dữ liệu: dấu PHẨY phân cách nghìn + dấu CHẤM là phần thập phân
// ("5,035.18 đồng/CP" ⇒ 5035.18, KHÔNG phải 503518). Dữ liệu kiểu Việt Nam
// ("5.035,18") cũng phải đúng ⇒ quy tắc: dấu xuất hiện SAU CÙNG là dấu thập phân.
// Chỉ có 1 loại dấu mà khối cuối đúng 3 chữ số ⇒ phân cách nghìn ("1,000" ⇒ 1000);
// còn lại là thập phân ("10.5" ⇒ 10.5).
function parseVnNumber(s) {
    if (s == null) return NaN;
    let t = String(s).trim().replace(/\s/g, '');
    if (!t) return NaN;
    const iComma = t.lastIndexOf(',');
    const iDot = t.lastIndexOf('.');
    let dec = '';
    if (iComma >= 0 && iDot >= 0) {
        dec = iComma > iDot ? ',' : '.';
    } else {
        const sep = iComma >= 0 ? ',' : (iDot >= 0 ? '.' : '');
        if (sep && t.length - t.lastIndexOf(sep) - 1 !== 3) dec = sep;
    }
    if (dec) {
        const other = dec === ',' ? '.' : ',';
        const i = t.lastIndexOf(dec);
        t = t.slice(0, i).split(other).join('').split(dec).join('') + '.' + t.slice(i + 1);
    } else {
        t = t.split(',').join('').split('.').join('');
    }
    const n = Number(t);
    return Number.isFinite(n) ? n : NaN;
}

function parseDividend(content) {
    const text = String(content || '').toLowerCase().replace(/\s+/g, ' ');
    const out = { cash: 0, stockPct: 0 };
    const isStock = /(cổ phiếu|co phieu|thưởng phiếu|thuong phieu|thưởng cp|thuong cp|cp thưởng)/.test(text);
    const isCash = /(tiền mặt|tien mat|bằng tiền|bang tien)/.test(text) || /[đd]\/\s*(cp|cổ phiếu|co phieu)/.test(text) || /đồng\/(cp|cổ phiếu|co phieu)/.test(text);

    let m = text.match(/(\d[\d.,]*)\s*(?:đ|vnd|vnđ|đồng|dong)\s*\/\s*(?:cp|cổ phiếu|co phieu)/);
    if (m) out.cash = parseVnNumber(m[1]);

    if (!out.cash && isCash) {
        m = text.match(/(\d+(?:[.,]\d+)?)\s*%/);
        if (m) out.cash = (parseFloat(m[1].replace(',', '.')) / 100) * 10000;
    }

    if (isStock) {
        m = text.match(/(\d+(?:[.,]\d+)?)\s*%\s*(?:bằng cổ phiếu|cổ phiếu thưởng|cổ tức bằng cổ phiếu|thưởng phiếu)/) ||
            text.match(/(?:bằng cổ phiếu|cổ phiếu thưởng|cổ tức bằng cổ phiếu|thưởng phiếu)[^%]*?(\d+(?:[.,]\d+)?)\s*%/);
        if (m) out.stockPct = parseFloat(m[m.length - 1].replace(',', '.'));
    }

    if (isStock || /tỷ lệ\s*\d+\s*:\s*\d+/.test(text)) {
        m = text.match(/tỷ lệ\s*(\d+)\s*[:/]\s*(\d+)/) || text.match(/(\d+)\s*cp\s*[:/]\s*(\d+)\s*cp/);
        if (m) {
            const a = parseInt(m[1], 10);
            const b = parseInt(m[2], 10);
            if (a > 0) out.stockPct = (b / a) * 100;
        }
    }

    if (!Number.isFinite(out.cash) || out.cash < 0) out.cash = 0;
    if (!Number.isFinite(out.stockPct) || out.stockPct < 0 || out.stockPct > 1000) out.stockPct = 0;
    return out;
}

function buildAccumMonthlyPrices(history) {
    const byMonth = {};
    (Array.isArray(history) ? history : []).forEach((bar) => {
        const k = accumMonthKey(bar.time);
        const c = Number(bar.close);
        if (!k || !Number.isFinite(c) || c <= 0) return;
        byMonth[k] = c;
    });
    const keys = Object.keys(byMonth).sort();
    if (!keys.length) return [];

    const now = new Date();
    const fullStart = new Date(now.getFullYear(), now.getMonth() - (ACCUM_MONTHS - 1), 1);
    const fullStartKey = accumMonthKey(fullStart);
    const earliest = keys[0];
    const startKey = earliest <= fullStartKey ? fullStartKey : earliest;

    const [sy, sm] = startKey.split('-').map(Number);
    const months = [];
    const curY = now.getFullYear();
    const curM = now.getMonth() + 1;
    let y = sy;
    let m = sm;
    while (y < curY || (y === curY && m <= curM)) {
        months.push({ year: y, month: m, price: null });
        m += 1;
        if (m > 12) { m = 1; y += 1; }
    }
    const firstPrice = byMonth[earliest];
    let lastPrice = null;
    for (let idx = 0; idx < months.length; idx++) {
        const k = accumMonthKeyFrom(months[idx].year, months[idx].month - 1);
        const price = byMonth[k];
        if (price !== undefined && price !== null) {
            months[idx].price = price;
            lastPrice = price;
        } else {
            months[idx].price = lastPrice !== null ? lastPrice : (firstPrice !== undefined ? firstPrice : null);
        }
    }
    return months;
}

function computeAccumSeries(months, dividends, monthly) {
    const capital = [];
    const bank = [];
    const stock = [];

    const monthlyRate = ACCUM_BANK_RATE / 12;
    let bankBalance = 0;
    for (let i = 0; i < months.length; i++) {
        capital.push(monthly * (i + 1));
        bankBalance = (bankBalance + monthly) * (1 + monthlyRate);
        bank.push(bankBalance);
    }

    const divByMonth = {};
    (Array.isArray(dividends) ? dividends : []).forEach((d) => {
        const k = accumMonthKey(d.date);
        if (!k) return;
        const parsed = parseDividend(d.content);
        if (parsed.cash > 0 || parsed.stockPct > 0) {
            if (!divByMonth[k]) divByMonth[k] = [];
            divByMonth[k].push(parsed);
        }
    });

    const hasPrice = months.some((m) => m.price !== null && m.price > 0);
    let shares = 0;
    let cash = 0;
    for (let i = 0; i < months.length; i++) {
        const m = months[i];
        const price = m.price;
        if (price !== null && price > 0) shares += monthly / price;
        const k = accumMonthKeyFrom(m.year, m.month - 1);
        const divs = divByMonth[k] || [];
        divs.forEach((div) => {
            if (div.cash > 0) cash += div.cash * shares;
            if (div.stockPct > 0) shares *= (1 + div.stockPct / 100);
        });
        stock.push(hasPrice && price !== null && price > 0 ? shares * price + cash : capital[i]);
    }
    return { capital, bank, stock, months };
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
        const months = buildAccumMonthlyPrices(history);
        const series = computeAccumSeries(months, dividends, this.monthly);
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
