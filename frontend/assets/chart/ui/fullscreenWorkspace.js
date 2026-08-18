/**
 * FullscreenWorkspace — mở rộng CHART FULLSCREEN thành workspace phân tích hoàn chỉnh (2026-08-18).
 *
 *  RIGHT SIDEBAR  : thông tin cổ phiếu + Tổng quan + Chỉ báo (đồng bộ 2 chiều với chart)
 *                   + Hỗ trợ / Kháng cự (đồng bộ với AnalysisEngine của chart).
 *  BOTTOM DASHBOARD: Market Breadth + Tin tức nổi bật + TuniX AI Phân tích.
 *
 * KHÔNG tạo chart / indicator / analysis mới — tái sử dụng engine hiện tại
 * (IndicatorEngine, AnalysisEngine, IndicatorPanel) và API TuniX hiện có.
 * KHÔNG hard-code dữ liệu: trường nào chưa có API → hiển thị "--".
 *
 * Chỉ hiển thị khi body có class `chart-fullscreen` (CSS ẩn trong normal mode),
 * tự phát hiện qua MutationObserver nên hoạt động với mọi đường vào fullscreen
 * (nút toàn màn hình, ?fs=1, parent-driven).
 */
import { detectSR } from '../analysis/analysis.js';
import { computeConfluence } from '../analysis/confluence.js';
import { INDICATOR_DEFS } from '../indicators/indicatorConfig.js';
import { AccumulationPanel, isIndexSymbol } from './accumulationPanel.js';

const LAYOUT_KEY = 'tunix.fsLayout.v1';

// Nhãn ngắn cho từng chỉ báo (sidebar) — giữ gọn, chuyên nghiệp.
const SHORT_LABELS = {
    MA: 'MA', EMA: 'EMA', BOLL: 'BB', RSI: 'RSI', MACD: 'MACD', ATR: 'ATR',
    STOCH: 'Stoch', ADX: 'ADX', CCI: 'CCI', OBV: 'OBV', ICHIMOKU: 'Ichimoku',
};

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtNum(n, dec = 0) {
    const v = Number(n);
    if (!Number.isFinite(v) || v === 0) return '--';
    return v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtSigned(n, dec = 2) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '--';
    const abs = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    if (v > 0) return '+' + abs;
    if (v < 0) return '-' + abs;
    return abs;
}

function fmtBig(n) {
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return '--';
    if (v >= 1e12) return (v / 1e12).toLocaleString('en-US', { maximumFractionDigits: 1 }) + ' nghìn tỷ';
    if (v >= 1e9) return (v / 1e9).toLocaleString('en-US', { maximumFractionDigits: 1 }) + ' tỷ';
    if (v >= 1e6) return (v / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1 }) + ' triệu';
    return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function fmtInd(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '--';
    if (Math.abs(n) >= 1e9) return (n / 1e9).toLocaleString('en-US', { maximumFractionDigits: 2 }) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toLocaleString('en-US', { maximumFractionDigits: 2 }) + 'M';
    return n.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function timeAgo(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return '';
    const diff = Date.now() - t;
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'vừa xong';
    if (m < 60) return m + ' phút trước';
    const h = Math.floor(m / 60);
    if (h < 24) return h + ' giờ trước';
    const d = Math.floor(h / 24);
    if (d < 30) return d + ' ngày trước';
    return new Date(t).toLocaleDateString('vi-VN');
}

// Cơ bản / Định giá / Rủi ro (dữ liệu screener).
const RISK_MAP = {
    low: { label: 'Thấp', cls: 'fs-risk-low' },
    medium: { label: 'Trung bình', cls: 'fs-risk-med' },
    high: { label: 'Cao', cls: 'fs-risk-high' },
    extreme: { label: 'Cực cao', cls: 'fs-risk-ext' },
};
function riskInfo(level) {
    const l = String(level || '').trim().toLowerCase();
    return RISK_MAP[l] || { label: '--', cls: '' };
}
function fmtPoint(v, max) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '--';
    const txt = (max === 20) ? n.toFixed(0) : n.toFixed(1).replace(/\.0$/, '');
    return txt + '/' + max;
}
function pointCls(v, max) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    const r = n / (max || 5);
    if (r >= 0.7) return 'fs-pt-high';
    if (r >= 0.4) return 'fs-pt-mid';
    return 'fs-pt-low';
}
function basicInfo(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return { label: '--', cls: '' };
    if (n >= 14) return { label: 'Rất tốt', cls: 'fs-basic-rat-tot' };
    if (n >= 10) return { label: 'Tốt', cls: 'fs-basic-tot' };
    if (n >= 5) return { label: 'Trung bình', cls: 'fs-basic-tb' };
    return { label: 'Chưa đạt', cls: 'fs-basic-chua-dat' };
}

function isFullscreen() {
    return !!(document.body && document.body.classList.contains('chart-fullscreen'));
}

export class FullscreenWorkspace {
    /**
     * @param {object} deps
     *   - sidebarEl, bottomEl        : container DOM
     *   - getSymbol, getBars, getTimeframe
     *   - getEnabledIndicators, indicatorValuesAtTime
     *   - indicatorPanel, indicatorEngine, analysisEngine
     *   - onOpenAi(symbol)           : mở TuniX AI
     *   - theme
     */
    constructor(deps) {
        this.sidebarEl = deps.sidebarEl;
        this.bottomEl = deps.bottomEl;
        this.getSymbol = deps.getSymbol || (() => '');
        this.getBars = deps.getBars || (() => []);
        this.getTimeframe = deps.getTimeframe || (() => '1D');
        this.getEnabledIndicators = deps.getEnabledIndicators || (() => []);
        this.indicatorValuesAtTime = deps.indicatorValuesAtTime || (() => []);
        this.indicatorPanel = deps.indicatorPanel || null;
        this.indicatorEngine = deps.indicatorEngine || null;
        this.analysisEngine = deps.analysisEngine || null;
        this.onOpenAi = deps.onOpenAi || null;
        this.onSelectSymbol = deps.onSelectSymbol || null;
        this.theme = deps.theme === 'dark' ? 'dark' : 'light';

        this.symbol = '';
        this.mode = 'detail'; // 'detail' | 'list'
        this.listTab = 'vn30'; // 'vn30' | 'hnx30' | 'hose' | 'hnx' | 'upcom' | <sector-slug>
        this.stockLists = null;
        this._stockListLoading = false;
        this._searchTerm = '';
        this._candles = [];
        this._quote = null;
        this._quoteLoading = false;
        this._news = [];
        this._newsLoading = false;
        this._fund = null;
        this._ai = null;
        this._aiLoading = false;
        this._sr = { resistances: [], supports: [], strongZone: null, strongStrength: null };
        this.accumPanel = null;
        this._els = {};
        this._srCheckbox = null;
        this._mo = null;
        this._destroyed = false;

        const saved = this._readLayout();
        this.sidebarOpen = saved ? saved.rightSidebarOpen !== false : true;
        this.bottomOpen = saved ? saved.bottomDashboardOpen !== false : true;
    }

    // ---------------- lifecycle ----------------
    create() {
        this._buildSidebar();
        this._buildBottom();
        if (this.bottomEl) {
            const accumEl = this.bottomEl.querySelector('.fsb-panel-accum .fsb-panel-body');
            if (accumEl) {
                this.accumPanel = new AccumulationPanel({
                    el: accumEl,
                    getSymbol: this.getSymbol,
                    isIndex: () => this._isIndexSymbol(),
                });
                this.accumPanel.create();
            }
        }
        this._applyLayout();
        this.setMode(this.mode);
        // Tự theo dõi trạng thái fullscreen (mọi đường vào đều bật class chart-fullscreen trên body).
        if (typeof MutationObserver !== 'undefined' && document.body) {
            this._mo = new MutationObserver(() => this._onFullscreenChanged());
            this._mo.observe(document.body, { attributes: true, attributeFilter: ['class'] });
        }
        this._onFullscreenChanged();
        return this;
    }

    destroy() {
        this._destroyed = true;
        if (this._mo) { try { this._mo.disconnect(); } catch (e) { /* ignore */ } this._mo = null; }
        if (this.accumPanel) { try { this.accumPanel.destroy(); } catch (e) { /* ignore */ } this.accumPanel = null; }
    }

    setTheme(mode) {
        this.theme = mode === 'dark' ? 'dark' : 'light';
    }

    // ---------------- data hooks (gọi từ controller) ----------------
    onSymbolChange(symbol) {
        this.symbol = String(symbol || '').trim().toUpperCase();
        this._quote = null; this._news = [];
        this._ai = null; this._aiLoading = false;
        this._fund = null;
        if (this.sidebarEl) {
            if (this._els.sym) this._els.sym.textContent = this.symbol || '--';
            if (this._els.name) this._els.name.textContent = '--';
            if (this._els.price) this._els.price.textContent = '--';
            if (this._els.chg) this._els.chg.textContent = '--';
        }
        this._renderAll();
        this._loadQuote();
        this._loadNews();
        this._loadFundamentals();
        this._maybeLoadAI();
        // Giả định tích sản: theo mã hiện tại.
        if (this.accumPanel) this.accumPanel.setSymbol(this.symbol);
        if (this._els.fsaSym) this._els.fsaSym.textContent = this.symbol || '';
    }

    onDataChange(candles) {
        this._candles = Array.isArray(candles) ? candles : [];
        this.refreshSR();
        this.refreshIndicators();
        this._renderAll();
    }

    refreshIndicators() { this._renderIndicators(); }

    refreshSR() {
        const bars = (this.getBars && this.getBars().length) ? this.getBars() : this._candles;
        this._sr = this._computeSR(bars);
        this._renderSR();
    }

    syncSrCheckbox() {
        if (this._srCheckbox && this.analysisEngine) {
            this._srCheckbox.checked = !!this.analysisEngine.isEnabled('sr');
        }
    }

    // ---------------- collapse / expand ----------------
    setSidebarOpen(open) {
        this.sidebarOpen = !!open;
        if (this.sidebarEl) this.sidebarEl.classList.toggle('collapsed', !open);
        const b = this.sidebarEl && this.sidebarEl.querySelector('.fss-collapse');
        if (b) b.innerHTML = open ? '◀' : '▶';
        this._saveLayout();
    }

    setBottomOpen(open) {
        this.bottomOpen = !!open;
        if (this.bottomEl) this.bottomEl.classList.toggle('collapsed', !open);
        const b = this.bottomEl && this.bottomEl.querySelector('.fsb-collapse');
        if (b) b.innerHTML = open ? '▼' : '▲';
        this._saveLayout();
    }

    // ---------------- build DOM ----------------
    _buildSidebar() {
        if (!this.sidebarEl) return;
        this.sidebarEl.innerHTML =
            '<div class="fss-head">' +
            '  <div class="fss-modes" role="tablist" aria-label="Chế độ sidebar">' +
            '    <button type="button" class="fss-mode-btn active" data-mode="detail" title="Chi tiết cổ phiếu"><i class="fa-solid fa-chart-simple"></i><span class="fss-mode-label">Chi tiết CP</span></button>' +
            '    <button type="button" class="fss-mode-btn" data-mode="list" title="Danh sách cổ phiếu"><i class="fa-solid fa-list-ul"></i><span class="fss-mode-label">Danh sách CP</span></button>' +
            '  </div>' +
            '  <button type="button" class="fss-collapse" title="Thu gọn / mở sidebar">◀</button>' +
            '</div>' +
            '<div class="fss-scroll">' +
            '  <div class="fss-detail">' +
            '    <div class="fss-stock">' +
            '      <div class="fss-stock-top"><span class="fss-stock-sym">--</span><span class="fss-stock-tf">--</span></div>' +
            '      <div class="fss-stock-name">--</div>' +
            '      <div class="fss-stock-price">--</div>' +
            '      <div class="fss-stock-chg">--</div>' +
            '    </div>' +
            '    <div class="fss-section">' +
            '      <div class="fss-sec-title">Tổng quan</div>' +
            '      <div class="fss-ovw" id="fsOverview"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '    </div>' +
            '    <div class="fss-section">' +
            '      <div class="fss-sec-title">Cơ bản</div>' +
            '      <div class="fss-ovw" id="fsFundamentals"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '    </div>' +
            '    <div class="fss-section">' +
            '      <div class="fss-sec-title">Định giá</div>' +
            '      <div class="fss-ovw" id="fsValuation"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '    </div>' +
            '    <div class="fss-section">' +
            '      <div class="fss-sec-title">Rủi ro</div>' +
            '      <div class="fss-ovw" id="fsRisk"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '    </div>' +
            '    <div class="fss-section">' +
            '      <div class="fss-sec-title fss-sec-title-row"><span>Chỉ báo</span>' +
            '        <button type="button" class="fss-add-btn" id="fsAddInd" title="Mở bảng chọn chỉ báo">+ Thêm chỉ báo</button></div>' +
            '      <div class="fss-ind" id="fsIndicators"><div class="fss-muted">Chưa có chỉ báo nào</div></div>' +
            '    </div>' +
            '    <div class="fss-section">' +
            '      <div class="fss-sec-title fss-sec-title-row"><span>Hỗ trợ / Kháng cự</span>' +
            '        <label class="fss-sr-toggle" title="Bật/tắt S/R trên chart"><input type="checkbox" id="fsSrToggle"> Hiển thị</label></div>' +
            '      <div class="fss-sr" id="fsSr"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '    </div>' +
            '  </div>' +
            '  <div class="fss-list">' +
            '    <div class="fss-list-nav">' +
            '      <button type="button" class="fss-nav-btn" id="fsTabPrev" title="Tab trước"><i class="fa-solid fa-chevron-left"></i></button>' +
            '      <div class="fss-list-tabs" id="fsListTabs" role="tablist" aria-label="Nhóm cổ phiếu"></div>' +
            '      <button type="button" class="fss-nav-btn" id="fsTabNext" title="Tab sau"><i class="fa-solid fa-chevron-right"></i></button>' +
            '    </div>' +
            '    <div class="fss-list-search"><i class="fa-solid fa-magnifying-glass"></i>' +
            '      <input type="text" id="fsStockSearch" placeholder="Tìm mã / tên cổ phiếu..." autocomplete="off" spellcheck="false" />' +
            '    </div>' +
            '    <div class="fss-list-hint" id="fsListHint"></div>' +
            '    <div class="fss-list-stocks" id="fsStockList"><div class="fss-muted">Đang tải…</div></div>' +
            '  </div>' +
            '</div>';

        this._srCheckbox = this.sidebarEl.querySelector('#fsSrToggle');
        if (this._srCheckbox) {
            this._srCheckbox.addEventListener('change', () => {
                if (this.analysisEngine) this.analysisEngine.toggle('sr');
                this.syncSrCheckbox();
            });
        }
        const addBtn = this.sidebarEl.querySelector('#fsAddInd');
        if (addBtn) addBtn.addEventListener('click', () => { if (this.indicatorPanel) this.indicatorPanel.toggle(); });

        const collapse = this.sidebarEl.querySelector('.fss-collapse');
        if (collapse) collapse.addEventListener('click', () => this.setSidebarOpen(!this.sidebarOpen));

        // Nút chuyển chế độ: Chi tiết ↔ Danh sách cổ phiếu.
        this._els.modeBtns = this.sidebarEl.querySelectorAll('.fss-mode-btn');
        Array.from(this._els.modeBtns).forEach((btn) => {
            btn.addEventListener('click', () => this.setMode(btn.dataset.mode === 'list' ? 'list' : 'detail'));
        });

        // Ô tìm kiếm trong chế độ danh sách.
        this._els.search = this.sidebarEl.querySelector('#fsStockSearch');
        if (this._els.search) {
            this._els.search.addEventListener('input', () => {
                this._searchTerm = this._els.search.value;
                this._renderStockList();
            });
        }
        this._els.list = this.sidebarEl.querySelector('#fsStockList');
        this._els.listHint = this.sidebarEl.querySelector('#fsListHint');
        // Nút chuyển tab trước/sau (máy tính — cuộn ngang khó thấy trên desktop).
        this._els.tabPrev = this.sidebarEl.querySelector('#fsTabPrev');
        this._els.tabNext = this.sidebarEl.querySelector('#fsTabNext');
        if (this._els.tabPrev) this._els.tabPrev.addEventListener('click', () => this._goTab(-1));
        if (this._els.tabNext) this._els.tabNext.addEventListener('click', () => this._goTab(1));

        this._els.sym = this.sidebarEl.querySelector('.fss-stock-sym');
        this._els.tf = this.sidebarEl.querySelector('.fss-stock-tf');
        this._els.name = this.sidebarEl.querySelector('.fss-stock-name');
        this._els.price = this.sidebarEl.querySelector('.fss-stock-price');
        this._els.chg = this.sidebarEl.querySelector('.fss-stock-chg');
        this._els.ovw = this.sidebarEl.querySelector('#fsOverview');
        this._els.ind = this.sidebarEl.querySelector('#fsIndicators');
        this._els.sr = this.sidebarEl.querySelector('#fsSr');
        this._els.fund = this.sidebarEl.querySelector('#fsFundamentals');
        this._els.valu = this.sidebarEl.querySelector('#fsValuation');
        this._els.risk = this.sidebarEl.querySelector('#fsRisk');
    }

    // ---------------- mode: detail / list ----------------
    setMode(mode) {
        this.mode = mode === 'list' ? 'list' : 'detail';
        if (this.sidebarEl) this.sidebarEl.classList.toggle('mode-list', this.mode === 'list');
        if (this._els.modeBtns) {
            Array.from(this._els.modeBtns).forEach((btn) => {
                btn.classList.toggle('active', btn.dataset.mode === this.mode);
            });
        }
        if (this.mode === 'list') {
            this._loadStockLists();
            this._renderListTabs();
            this._renderStockList();
        } else {
            this._renderAll();
        }
    }

    async _loadStockLists() {
        if (this.stockLists || this._stockListLoading) return;
        this._stockListLoading = true;
        this._renderStockList();
        try {
            const data = await this._fetchJSON('/stocks/stock-lists');
            this.stockLists = (data && typeof data === 'object') ? data : null;
        } catch (e) {
            this.stockLists = null;
        }
        this._stockListLoading = false;
        this._renderListTabs();
        this._renderStockList();
    }

    // Chọn tab danh sách: vn30 | hnx30 | hose | hnx | upcom | <sector-slug>
    setListTab(tab) {
        const order = this._tabOrder();
        this.listTab = order.indexOf(tab) !== -1 ? tab : 'vn30';
        this._searchTerm = '';
        if (this._els.search) this._els.search.value = '';
        this._renderListTabs();
        this._scrollActiveTabIntoView();
        this._renderStockList();
    }

    // Thứ tự tab: 5 nhóm cố định + các ngành (economicSector) hiển thị thẳng làm tab.
    _tabOrder() {
        const base = ['vn30', 'hnx30', 'hose', 'hnx', 'upcom'];
        const sectors = (this.stockLists && Array.isArray(this.stockLists.sectors))
            ? this.stockLists.sectors.map((s) => s.slug) : [];
        return base.concat(sectors);
    }

    _tabLabel(id) {
        const labels = { vn30: 'VN30', hnx30: 'HNX30', hose: 'HOSE', hnx: 'HNX', upcom: 'UPCOM' };
        if (labels[id]) return labels[id];
        const sec = ((this.stockLists && this.stockLists.sectors) || []).find((s) => s.slug === id);
        return sec ? sec.name : String(id || '').toUpperCase();
    }

    // Dựng dãy tab (dynamic) — 5 nhóm + mỗi ngành một tab.
    _renderListTabs() {
        const container = this.sidebarEl ? this.sidebarEl.querySelector('.fss-list-tabs') : null;
        if (!container) return;
        container.innerHTML = this._tabOrder().map((id) =>
            '<button type="button" class="fss-tab' + (this.listTab === id ? ' active' : '') + '" data-tab="' + esc(id) + '">' +
            esc(this._tabLabel(id)) + '</button>'
        ).join('');
        Array.from(container.querySelectorAll('.fss-tab')).forEach((btn) => {
            btn.addEventListener('click', () => this.setListTab(btn.dataset.tab));
        });
    }

    // Cuộn dãy tab ngang để tab đang chọn luôn hiển thị (khi next/prev qua ◀ ▶).
    _scrollActiveTabIntoView() {
        const container = this.sidebarEl ? this.sidebarEl.querySelector('.fss-list-tabs') : null;
        if (!container) return;
        const active = Array.from(container.querySelectorAll('.fss-tab')).find((b) => b.classList.contains('active'));
        if (!active) return;
        const cr = container.getBoundingClientRect();
        const ar = active.getBoundingClientRect();
        const sl = container.scrollLeft;
        const cw = container.clientWidth;
        // Vị trí tab trong toạ độ nội dung cuộn (offsetLeft có thể sai vì offsetParent khác container).
        const relLeft = ar.left - cr.left + sl;
        const relRight = relLeft + ar.width;
        try {
            if (relRight > sl + cw - 4) {
                container.scrollTo({ left: relRight - cw + 4, behavior: 'smooth' });
            } else if (relLeft < sl + 4) {
                container.scrollTo({ left: Math.max(0, relLeft - 4), behavior: 'smooth' });
            }
        } catch (e) { /* ignore */ }
    }

    // Chuyển sang tab kế trước (-1) / kế sau (+1), vòng tròn khi hết.
    _goTab(dir) {
        const order = this._tabOrder();
        const idx = order.indexOf(this.listTab);
        const next = order[(idx + (dir || 1) + order.length) % order.length];
        this.setListTab(next);
    }

    // Trả về danh sách cổ phiếu (đã sắp theo mã) cho tab đang chọn.
    _currentList() {
        const sl = this.stockLists;
        const stocks = (sl && Array.isArray(sl.stocks)) ? sl.stocks : [];
        if (!stocks.length) return [];
        switch (this.listTab) {
            case 'vn30': {
                const set = new Set(sl.vn30 || []);
                return stocks.filter((s) => set.has(s.symbol));
            }
            case 'hnx30': {
                const set = new Set(sl.hnx30 || []);
                return stocks.filter((s) => set.has(s.symbol));
            }
            case 'hose':
                return stocks.filter((s) => s.exchange === 'HOSE');
            case 'hnx':
                return stocks.filter((s) => s.exchange === 'HNX');
            case 'upcom':
                return stocks.filter((s) => s.exchange === 'UPCOM');
            default:
                return stocks.filter((s) => s.sector === this.listTab);
        }
    }

    _stripDiacritics(s) {
        return String(s || '')
            .replace(/đ/g, 'd').replace(/Đ/g, 'D')
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLowerCase();
    }

    _renderStockList() {
        if (!this._els.list) return;
        const term = this._stripDiacritics((this._searchTerm || '').trim());
        let rows = this._currentList();
        if (term) {
            rows = rows.filter((r) => {
                const hay = this._stripDiacritics((r.symbol || '') + ' ' + (r.name || ''));
                return hay.indexOf(term) !== -1;
            });
        }

        const hintMap = {
            vn30: 'Danh sách cổ phiếu VN30 — nhấn để mở',
            hnx30: 'Top 30 vốn hóa HNX — nhấn để mở',
            hose: 'Cổ phiếu sàn HOSE — nhấn để mở',
            hnx: 'Cổ phiếu sàn HNX — nhấn để mở',
            upcom: 'Cổ phiếu sàn UPCOM — nhấn để mở',
        };
        const hint = hintMap[this.listTab] || ('Cổ phiếu ngành ' + this._tabLabel(this.listTab) + ' — nhấn để mở');
        if (this._els.listHint) this._els.listHint.textContent = hint;

        let html = '';
        if (!this.stockLists && this._stockListLoading) {
            html = '<div class="fss-muted">Đang tải…</div>';
        } else if (!rows.length) {
            if (term) {
                const openTerm = (this._searchTerm || '').trim().toUpperCase();
                html = '<button type="button" class="fsl-open" data-symbol="' + esc(openTerm) + '">' +
                    '<i class="fa-solid fa-arrow-up-right-from-square"></i> Mở cổ phiếu ' + esc(openTerm) +
                    ' (không có trong danh sách này)</button>' +
                    '<div class="fss-muted">Không tìm thấy trong danh sách. Dùng ô tìm kiếm ở header để tìm mã khác.</div>';
            } else {
                html = '<div class="fss-muted">Không có dữ liệu cổ phiếu.</div>';
            }
        } else {
            html = rows.map((r) => {
                const pct = Number(r.percent_change);
                const cls = pct > 0 ? 'up' : (pct < 0 ? 'down' : '');
                const pctText = (pct > 0 ? '+' : '') + (Number.isFinite(pct) ? pct.toFixed(2) + '%' : '--');
                return '<button type="button" class="fsl-row" data-symbol="' + esc(r.symbol) + '" title="Mở ' + esc(r.symbol) + '">' +
                    '<span class="fsl-main"><span class="fsl-symbol">' + esc(r.symbol) + '</span>' +
                    '<span class="fsl-name">' + esc(r.name || '') + '</span></span>' +
                    '<span class="fsl-right"><span class="fsl-price">' + fmtNum(r.price, 0) + '</span>' +
                    '<span class="fsl-pct ' + cls + '">' + pctText + '</span></span>' +
                    '</button>';
            }).join('');
        }
        this._els.list.innerHTML = html;
        Array.from(this._els.list.querySelectorAll('[data-symbol]')).forEach((el) => {
            el.addEventListener('click', () => this._pickStock(el.dataset.symbol));
        });
    }

    _pickStock(symbol) {
        const sym = String(symbol || '').trim().toUpperCase();
        if (!sym) return;
        // Về chế độ chi tiết trước, rồi đổi mã chart (controller xử lý, giữ fullscreen).
        this.setMode('detail');
        if (this.onSelectSymbol) this.onSelectSymbol(sym);
    }

    _buildBottom() {
        if (!this.bottomEl) return;
        this.bottomEl.innerHTML =
            '<div class="fsb-head">' +
            '  <span class="fsb-head-label"><i class="fa-solid fa-gauge-high"></i> Dashboard</span>' +
            '  <button type="button" class="fsb-collapse" title="Thu gọn / mở dashboard">▼</button>' +
            '</div>' +
            '<div class="fsb-body">' +
            '  <div class="fsb-panel fsb-panel-accum">' +
            '    <div class="fsb-panel-title"><i class="fa-solid fa-piggy-bank"></i> Giả định tích sản <span class="fsa-sym" id="fsaSym"></span></div>' +
            '    <div class="fsb-panel-body" id="fsAccum"></div>' +
            '  </div>' +
            '  <div class="fsb-panel fsb-news">' +
            '    <div class="fsb-panel-title">Tin tức nổi bật</div>' +
            '    <div class="fsb-panel-body" id="fsNews"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '  </div>' +
            '  <div class="fsb-panel fsb-ai">' +
            '    <div class="fsb-panel-title"><i class="fa-solid fa-robot"></i> TuniAI phân tích</div>' +
            '    <div class="fsb-panel-body" id="fsAi"><div class="fss-muted">Đang cập nhật…</div></div>' +
            '  </div>' +
            '</div>';

        const collapse = this.bottomEl.querySelector('.fsb-collapse');
        if (collapse) collapse.addEventListener('click', () => this.setBottomOpen(!this.bottomOpen));

        this._els.news = this.bottomEl.querySelector('#fsNews');
        this._els.ai = this.bottomEl.querySelector('#fsAi');
        this._els.fsaSym = this.bottomEl.querySelector('#fsaSym');
        this._els.accum = this.bottomEl.querySelector('#fsAccum');
    }

    // ---------------- fullscreen observer ----------------
    _onFullscreenChanged() {
        if (this._destroyed) return;
        const on = isFullscreen();
        try { this._renderAll(); } catch (e) { /* không để lỗi render phá vỡ chart */ }
        if (on) {
            this._maybeLoadAI();
        }
    }

    // ---------------- storage ----------------
    _readLayout() {
        try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || 'null'); } catch (e) { return null; }
    }
    _saveLayout() {
        try {
            localStorage.setItem(LAYOUT_KEY, JSON.stringify({
                rightSidebarOpen: this.sidebarOpen,
                bottomDashboardOpen: this.bottomOpen,
            }));
        } catch (e) { /* ignore */ }
    }
    _applyLayout() {
        if (this.sidebarEl) this.sidebarEl.classList.toggle('collapsed', !this.sidebarOpen);
        if (this.bottomEl) this.bottomEl.classList.toggle('collapsed', !this.bottomOpen);
    }

    // ---------------- fetch helpers ----------------
    async _fetchJSON(path) {
        const res = await fetch('/api' + path, { cache: 'no-store' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
    }

    // ---------------- data loading ----------------
    async _loadQuote() {
        if (!this.symbol) return;
        this._quoteLoading = true;
        try {
            const q = await this._fetchJSON('/stocks/' + encodeURIComponent(this.symbol) + '/quote');
            this._quote = (q && q.valid !== false) ? q : null;
        } catch (e) { this._quote = null; }
        this._quoteLoading = false;
        this._renderStock();
        this._renderOverview();
        if (this.accumPanel) this.accumPanel.refreshIndexState();
    }

    // Mã không phải cổ phiếu (chỉ số VNINDEX/VN30/HNX/UPCOM…, crypto, vàng…) → không tích sản.
    _isIndexSymbol() {
        if (this._quote && this._quote.exchange) {
            const ex = String(this._quote.exchange).toUpperCase();
            if (ex === 'HOSE' || ex === 'HNX' || ex === 'UPCOM') return false;
            return true;
        }
        return isIndexSymbol(this.symbol);
    }

    async _loadNews() {
        if (!this.symbol) return;
        this._newsLoading = true;
        try {
            let list = [];
            try {
                const rel = await this._fetchJSON('/news/related?symbol=' + encodeURIComponent(this.symbol) + '&limit=5');
                if (Array.isArray(rel) && rel.length) list = rel;
            } catch (e) { /* bỏ qua, fallback tin mới nhất */ }
            if (!list.length) {
                const latest = await this._fetchJSON('/news?limit=5');
                if (Array.isArray(latest)) list = latest;
            }
            this._news = list.slice(0, 5);
        } catch (e) { this._news = []; }
        this._newsLoading = false;
        this._renderNews();
    }

    _maybeLoadAI() {
        if (!isFullscreen()) return; // chỉ gọi AI khi đang fullscreen workspace
        if (this._ai || this._aiLoading) return;
        this._loadAI();
    }

    async _loadAI() {
        const sym = this.symbol;
        const token = localStorage.getItem('tunix_access_token');
        if (!token) {
            this._ai = { status: 'login' };
            this._renderAI();
            return;
        }
        this._aiLoading = true;
        this._ai = { status: 'loading' };
        this._renderAI();
        try {
            const res = await fetch('/api/ai/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (token || ''),
                },
                body: JSON.stringify({
                    stream: false,
                    messages: [{
                        role: 'user',
                        content: 'Hãy phân tích kỹ thuật cổ phiếu ' + sym +
                            ' dựa trên dữ liệu hiện có: xu hướng ngắn hạn, các vùng hỗ trợ / kháng cự, ' +
                            'và trạng thái các chỉ báo (RSI, MACD, MA). Trả lời ngắn gọn 5-7 dòng, tiếng Việt. ' +
                            'KHÔNG đưa khuyến nghị mua/bán cụ thể.',
                    }],
                }),
            });
            if (!res.ok) {
                let detail = 'Lỗi khi gọi TuniAI. Vui lòng thử lại.';
                try { const d = await res.json(); if (d && d.detail) detail = d.detail; } catch (e) { /* ignore */ }
                if (res.status === 401) detail = 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.';
                this._ai = { status: 'error', detail };
                this._renderAI();
                return;
            }
            const data = await res.json();
            this._ai = { status: 'ok', reply: (data && data.reply) || '' };
        } catch (e) {
            this._ai = { status: 'error', detail: 'Lỗi kết nối. Vui lòng thử lại.' };
        }
        this._aiLoading = false;
        this._renderAI();
    }

    _openAi() {
        if (this.onOpenAi) this.onOpenAi(this.symbol || this.getSymbol());
    }

    // Nút "Đăng nhập" khi chưa đăng nhập → điều hướng đến form đăng nhập.
    _openLogin() {
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'tunix-go-login' }, '*');
        } else {
            window.location.href = '/Login';
        }
    }

    // ---------------- computations ----------------
    _computeSR(bars) {
        if (!Array.isArray(bars) || bars.length < 6) return { resistances: [], supports: [], strongZone: null };
        const levels = detectSR(bars, {});
        const zones = computeConfluence(bars);
        let strongZone = null;
        for (const z of zones) {
            if (z.kind === 'support' && (!strongZone || z.count > strongZone.count)) strongZone = z;
        }
        const resistances = levels.filter((l) => l.kind === 'resistance').sort((a, b) => b.price - a.price).slice(0, 3);
        const supports = levels.filter((l) => l.kind === 'support').sort((a, b) => a.price - b.price).slice(0, 3);
        const maxT = Math.max(1, ...levels.map((l) => l.touches));
        const strength = (l) => Math.max(40, Math.min(95, Math.round((l.touches / maxT) * 100)));
        return {
            resistances: resistances.map((l) => ({ ...l, strength: strength(l) })),
            supports: supports.map((l) => ({ ...l, strength: strength(l) })),
            strongZone,
            strongStrength: strongZone ? Math.max(60, Math.min(95, 70 + strongZone.count * 8)) : null,
        };
    }

    // ---------------- render ----------------
    _renderAll() {
        try { this._renderStock(); } catch (e) { /* ignore */ }
        try { this._renderOverview(); } catch (e) { /* ignore */ }
        try { this._renderFundamentals(); } catch (e) { /* ignore */ }
        try { this._renderIndicators(); } catch (e) { /* ignore */ }
        try { this._renderSR(); } catch (e) { /* ignore */ }
        try { this._renderNews(); } catch (e) { /* ignore */ }
        try { this._renderAI(); } catch (e) { /* ignore */ }
        try { this.syncSrCheckbox(); } catch (e) { /* ignore */ }
    }

    _renderStock() {
        if (!this._els.sym) return;
        const q = this._quote;
        if (this._els.sym) this._els.sym.textContent = q && q.symbol ? q.symbol : (this.symbol || '--');
        if (this._els.tf) this._els.tf.textContent = this.getTimeframe ? this.getTimeframe() : '1D';
        if (this._els.name) this._els.name.textContent = q && q.name ? q.name : (q && q.exchange ? q.exchange : '--');
        const price = q ? Number(q.price) : NaN;
        if (this._els.price) {
            this._els.price.textContent = Number.isFinite(price) ? fmtNum(price, price < 1000 ? 1 : 0) : '--';
            this._els.price.className = 'fss-stock-price';
            if (Number.isFinite(price)) {
                const pos = Number(q.change) >= 0;
                this._els.price.classList.add(pos ? 'up' : 'down');
            }
        }
        if (this._els.chg) {
            const chg = q ? Number(q.change) : NaN;
            const pct = q ? Number(q.percent_change) : NaN;
            if (Number.isFinite(chg) && Number.isFinite(pct)) {
                const pos = chg >= 0;
                this._els.chg.textContent = fmtSigned(chg, 0) + ' / ' + fmtSigned(pct, 2) + '%';
                this._els.chg.className = 'fss-stock-chg ' + (pos ? 'up' : 'down');
            } else {
                this._els.chg.textContent = '--';
                this._els.chg.className = 'fss-stock-chg';
            }
        }
    }

    _renderOverview() {
        if (!this._els.ovw) return;
        const q = this._quote;
        const v = (x) => { const n = Number(x); return Number.isFinite(n) && n !== 0 ? n : null; };
        const price = v(q && q.price);
        const change = v(q && q.change);
        const reference = v(q && q.reference_price);
        const open = v(q && q.open);
        const high = v(q && q.high);
        const low = v(q && q.low);
        const volume = v(q && q.volume);
        const value = v(q && q.value);
        const marketCap = v(q && q.market_cap);
        const pe = v(q && q.pe);
        const pb = v(q && q.pb);

        const row = (label, valHtml, cls) =>
            '<div class="fss-row"><span class="fss-row-label">' + label + '</span>' +
            '<span class="fss-row-val' + (cls ? ' ' + cls : '') + '">' + valHtml + '</span></div>';

        const rows = [
            row('Tham chiếu', reference != null ? fmtNum(reference, reference < 1000 ? 1 : 0) : '--'),
            row('Mở cửa', open != null ? fmtNum(open, open < 1000 ? 1 : 0) : '--'),
            row('Thấp - Cao',
                (low != null && high != null)
                    ? fmtNum(low, low < 1000 ? 1 : 0) + ' - ' + fmtNum(high, high < 1000 ? 1 : 0)
                    : '--'),
            row('Khối lượng', volume != null ? fmtNum(volume) : '--'),
            row('Giá trị', value != null ? fmtBig(value) : '--'),
            row('Vốn hóa', marketCap != null ? fmtBig(marketCap) : '--'),
            row('P/E', pe != null ? fmtNum(pe, 2) : '--'),
            row('P/B', pb != null ? fmtNum(pb, 2) : '--'),
        ];
        this._els.ovw.innerHTML = rows.join('');
    }

    async _loadFundamentals() {
        if (!this.symbol) return;
        this._fund = null;
        this._renderFundamentals();
        try {
            const d = await this._fetchJSON('/stocks/' + encodeURIComponent(this.symbol) + '/fundamentals');
            this._fund = (d && d.valid !== false) ? d : null;
        } catch (e) { this._fund = null; }
        this._renderFundamentals();
    }

    // Cơ bản / Định giá / Rủi ro (từ /stocks/{symbol}/fundamentals — screener).
    _renderFundamentals() {
        if (!this._els.fund) return;
        const f = this._fund;
        const row = (label, valHtml) =>
            '<div class="fss-row"><span class="fss-row-label">' + label + '</span>' +
            '<span class="fss-row-val">' + valHtml + '</span></div>';
        if (!f || !f.valid) {
            const ph = '<div class="fss-muted">--</div>';
            this._els.fund.innerHTML = ph;
            this._els.valu.innerHTML = ph;
            this._els.risk.innerHTML = ph;
            return;
        }
        const pt = (v, max) => '<span class="' + pointCls(v, max) + '">' + fmtPoint(v, max) + '</span>';
        const basic = basicInfo(f.qualityScore);

        this._els.fund.innerHTML = [
            row('Xếp loại', '<span class="fs-risk-badge ' + basic.cls + '">' + basic.label + '</span>'),
            row('Ngành', esc(f.sectorName || '--')),
            row('Ngành con', esc(f.industryName || '--')),
            row('Tăng trưởng', pt(f.growthPoint, 5)),
            row('Kết quả KD', pt(f.performancePoint, 5)),
            row('Sức khỏe TC', pt(f.financialHealthPoint, 5)),
            row('Cổ tức', pt(f.dividendPoint, 5)),
            row('Chất lượng', pt(f.qualityScore, 20)),
        ].join('');

        const vp = Number(f.valuationPoint);
        const note = Number.isFinite(vp)
            ? (vp >= 3
                ? '<span class="fs-val-note ok">Có biên an toàn</span>'
                : '<span class="fs-val-note">Chưa có biên an toàn</span>')
            : '';
        this._els.valu.innerHTML = [
            row('Điểm định giá', pt(f.valuationPoint, 5)),
            row('P/E', Number.isFinite(Number(f.peRatio)) ? fmtNum(Number(f.peRatio), 2) : '--'),
            row('P/B', Number.isFinite(Number(f.pbRatio)) ? fmtNum(Number(f.pbRatio), 2) : '--'),
            (note ? row('Nhận định', note) : ''),
        ].join('');

        const risk = riskInfo(f.overallRiskLevel);
        this._els.risk.innerHTML =
            '<div class="fss-row"><span class="fss-row-label">Mức rủi ro</span>' +
            '<span class="fs-risk-badge ' + risk.cls + '">' + risk.label + '</span></div>';
    }

    _buildIndLabel(key, settings) {
        const def = INDICATOR_DEFS[key];
        if (!def) return key;
        const label = SHORT_LABELS[key] || def.label;
        const parts = [];
        for (const f of def.fields) {
            const val = settings && settings[f.key] != null ? settings[f.key] : f.def;
            parts.push(val);
        }
        return label + (parts.length ? ' ' + parts.join(' ') : '');
    }

    _renderIndicators() {
        if (!this._els.ind) return;
        const list = this.getEnabledIndicators();
        if (!list.length) {
            this._els.ind.innerHTML = '<div class="fss-muted">Chưa có chỉ báo nào</div>';
            return;
        }
        const bars = this.getBars();
        const last = bars && bars.length ? bars[bars.length - 1] : null;
        let values = [];
        if (last && this.indicatorValuesAtTime) {
            values = this.indicatorValuesAtTime(last.time);
        }
        const valMap = {};
        for (const v of values) valMap[v.key] = v.value;
        this._els.ind.innerHTML = list.map((item) => {
            const def = INDICATOR_DEFS[item.key];
            const color = def ? def.color : '#2962ff';
            const label = this._buildIndLabel(item.key, item.settings);
            const hasVal = item.key in valMap;
            return '<div class="fss-ind-row">' +
                '<span class="fss-ind-eye" title="Đang bật">👁</span>' +
                '<span class="fss-ind-dot" style="background:' + color + '"></span>' +
                '<span class="fss-ind-name" title="' + esc(label) + '">' + esc(label) + '</span>' +
                '<span class="fss-ind-val">' + (hasVal ? fmtInd(valMap[item.key]) : '--') + '</span>' +
                '<button type="button" class="fss-ind-btn" data-act="settings" data-key="' + esc(item.key) + '" title="Cài đặt">⚙</button>' +
                '<button type="button" class="fss-ind-btn fss-ind-x" data-act="remove" data-key="' + esc(item.key) + '" title="Xóa chỉ báo">×</button>' +
                '</div>';
        }).join('');
        // bind actions
        Array.from(this._els.ind.querySelectorAll('.fss-ind-btn')).forEach((btn) => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.key;
                if (btn.dataset.act === 'remove') {
                    if (this.indicatorEngine && this.indicatorEngine.isEnabled(key)) this.indicatorEngine.remove(key);
                } else if (btn.dataset.act === 'settings') {
                    if (this.indicatorPanel) this.indicatorPanel.toggle();
                }
            });
        });
    }

    _renderSR() {
        if (!this._els.sr) return;
        const sr = this._sr;
        const fmt = (p) => fmtNum(p, p < 1000 ? 1 : 0);
        const bar = (strength) =>
            '<span class="fs-sr-bar"><i style="width:' + strength + '%"></i></span>' +
            '<span class="fs-sr-str-num">' + strength + '/100</span>';
        let html = '';

        if (sr.resistances.length) {
            html += '<div class="fs-sr-h res">Kháng cự</div>';
            sr.resistances.forEach((l, i) => {
                const tag = ['R3', 'R2', 'R1'][i] || ('R' + (i + 1));
                html += '<div class="fs-sr-row">' +
                    '<span class="fs-sr-price res">' + fmt(l.price) + '</span>' +
                    '<span class="fs-sr-type res">' + tag + '</span>' +
                    '<span class="fs-sr-str">' + bar(l.strength) + '</span></div>';
            });
        }
        if (sr.supports.length) {
            html += '<div class="fs-sr-h sup">Hỗ trợ</div>';
            sr.supports.forEach((l, i) => {
                const tag = ['S3', 'S2', 'S1'][i] || ('S' + (i + 1));
                html += '<div class="fs-sr-row">' +
                    '<span class="fs-sr-price sup">' + fmt(l.price) + '</span>' +
                    '<span class="fs-sr-type sup">' + tag + '</span>' +
                    '<span class="fs-sr-str">' + bar(l.strength) + '</span></div>';
            });
        }
        if (sr.strongZone && sr.strongStrength != null) {
            html += '<div class="fs-sr-zone">' +
                '<span class="fs-sr-zone-price strong">' + fmt(sr.strongZone.low) + ' – ' + fmt(sr.strongZone.high) + '</span>' +
                '<span class="fs-sr-zone-label strong">Strong Support</span>' +
                '<span class="fs-sr-str">' + bar(sr.strongStrength) + '</span></div>';
        }
        if (!html) html = '<div class="fss-muted">--</div>';
        this._els.sr.innerHTML = html;
    }

    _renderNews() {
        if (!this._els.news) return;
        if (!this._news.length) {
            this._els.news.innerHTML = '<div class="fss-muted">Không có tin mới</div>';
            return;
        }
        this._els.news.innerHTML = this._news.map((n) => {
            const title = n.title || '--';
            const time = timeAgo(n.created_at);
            const tag = (n.tags || '').split(',').map((s) => s.trim()).filter(Boolean);
            const symTag = tag.length ? tag[0] : '';
            return '<a href="#" class="fsb-news-item" data-id="' + Number(n.id) + '" data-slug="' + esc(n.slug || '') + '" title="' + esc(title) + '">' +
                '<span class="fsb-news-title">' + esc(title) + '</span>' +
                '<span class="fsb-news-meta">' +
                (symTag ? '<span class="fsb-news-tag">' + esc(symTag) + '</span>' : '') +
                '<span class="fsb-news-time">' + esc(time) + '</span>' +
                '</span></a>';
        }).join('') +
            '<a href="#" class="fsb-news-more" id="fsNewsMore">Xem thêm →</a>';

        Array.from(this._els.news.querySelectorAll('.fsb-news-item')).forEach((a) => {
            a.addEventListener('click', (e) => {
                e.preventDefault();
                const id = a.dataset.id;
                if (window.parent && window.parent !== window) {
                    window.parent.postMessage({ type: 'tunix-open-tintuc', id: Number(id), slug: a.dataset.slug }, '*');
                }
            });
        });
        const more = this._els.news.querySelector('#fsNewsMore');
        if (more) more.addEventListener('click', (e) => {
            e.preventDefault();
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'tunix-open-url', url: '/tin-tuc' }, '*');
            } else {
                window.location.href = '/tin-tuc';
            }
        });
    }

    _setAiHtml(html) {
        if (!this._els.ai) return;
        this._els.ai.innerHTML = html;
        const btn = this._els.ai.querySelector('#fsAiOpen');
        if (btn) btn.addEventListener('click', () => this._openAi());
        const loginBtn = this._els.ai.querySelector('#fsAiLogin');
        if (loginBtn) loginBtn.addEventListener('click', () => this._openLogin());
    }

    _renderAI() {
        if (!this._els.ai) return;
        const ai = this._ai;
        if (!ai) {
            this._setAiHtml(
                '<div class="fss-muted">Đang cập nhật…</div>' + this._aiFooter()
            );
            return;
        }
        if (ai.status === 'loading') {
            this._setAiHtml('<div class="fsb-ai-loading"><i class="fa-solid fa-spinner fa-spin"></i> Đang cập nhật…</div>');
            return;
        }
        if (ai.status === 'login') {
            this._setAiHtml(
                '<div class="fss-muted">Đăng nhập để dùng TuniAI phân tích.</div>' +
                '<button type="button" class="fsb-ai-btn" id="fsAiLogin">Đăng nhập</button>'
            );
            return;
        }
        if (ai.status === 'error') {
            this._setAiHtml(
                '<div class="fsb-ai-error">' + esc(ai.detail || 'Không tải được phân tích.') + '</div>' + this._aiFooter()
            );
            return;
        }
        if (ai.status === 'ok') {
            const body = ai.reply
                ? ai.reply.split('\n').map((l) => '<div class="fsb-ai-line">' + esc(l) + '</div>').join('')
                : '<div class="fss-muted">--</div>';
            this._setAiHtml('<div class="fsb-ai-body">' + body + '</div>' + this._aiFooter());
            return;
        }
        this._setAiHtml('<div class="fss-muted">--</div>' + this._aiFooter());
    }

    _aiFooter() {
        return '<button type="button" class="fsb-ai-btn" id="fsAiOpen">Phân tích chi tiết →</button>';
    }
}
