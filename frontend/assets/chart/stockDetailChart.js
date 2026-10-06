/**
 * stockDetailChart — Controller (entry) cho khối chart của trang chi tiết cổ phiếu.
 * PHASE 1 — Chart Core · PHASE 2 — Drawing Engine · PHASE 3 — Indicator Engine
 * PHASE 4 — S/R + Volume Profile · PHASE 5 — Confluence + Market Breadth.
 *
 * Gắn kết: DATA (DataProvider) → STATE (ChartState) → RENDERING (ChartEngine)
 *          → INTERACTION (Legend / Drawing / Indicator).
 *
 * API trả về: { load(symbol?), setTimeframe(tf), setTheme(mode), destroy() }
 */
import { ChartEngine } from './core/chartEngine.js';
import { ChartState } from './core/chartState.js';
import { DataProvider } from './data/dataProvider.js';
import { Legend } from './ui/legend.js';
import { ChartToolbar } from './ui/toolbar.js';
import { IndicatorEngine } from './indicators/indicatorEngine.js';
import { IndicatorPanel } from './ui/indicatorPanel.js';
import { AnalysisEngine } from './analysis/analysisEngine.js';
import { AnalysisPanel } from './ui/analysisPanel.js';
import { MarketBreadth } from './ui/marketBreadth.js';
import { DrawingManager } from './drawings/drawingManager.js';
import { DrawingEngine } from './drawings/drawingEngine.js';
import { TIMEFRAME_DAYS, TIMEFRAME_COUNTBACK } from './core/chartConfig.js';
import { INDICATOR_DEFS } from './indicators/indicatorConfig.js';
import { STORAGE_KEYS, storageGet, storageSet } from './core/persistence.js';
import { exportChartPNG } from './core/chartExport.js';
import { ComparisonEngine } from './compare/comparisonEngine.js';
import { ComparisonPanel } from './ui/comparisonPanel.js';
import { PaneResizer } from './core/paneResizer.js';
import { CrosshairSync } from './core/crosshairSync.js';
import { HotkeyManager } from './core/hotkeys.js';
import { FullscreenManager } from './core/fullscreen.js';
import { SettingsPanel } from './ui/settingsPanel.js';
import { LastPriceLine } from './core/lastPriceLine.js';
// `?v=` BẮT BUỘC: /static/* không version bị HTTP cache 1 giờ. Bump khi sửa positionLine.js.
import { PositionLine } from './core/positionLine.js?v=20260927d';
import { HistoryLoader } from './core/historyLoader.js';
import { FullscreenWorkspace } from './ui/fullscreenWorkspace.js?v=20261006a';

export function createStockDetailChart(options) {
    const container = options.container;
    const placeholderEl = options.placeholderEl || null;
    const state = new ChartState({
        symbol: options.symbol || '',
        timeframe: options.timeframe || '1D',
        theme: options.theme === 'dark' ? 'dark' : 'light',
    });
    const legend = new Legend(options.legendEl || null);
    const provider = new DataProvider();
    const toolbarEl = options.toolbarEl || null;
    const indicatorEl = options.indicatorEl || null;
    const analysisEl = options.analysisEl || null;
    const breadthEl = options.breadthEl || null;
    const exportEl = options.exportEl || null;
    const compareEl = options.compareEl || null;
    const resetEl = options.resetEl || null;
    const helpEl = options.helpEl || null;
    const fsEl = options.fsEl || null;
    const settingsEl = options.settingsEl || null;
    const legendEl = options.legendEl || null;
    const sidebarEl = options.sidebarEl || null;
    const bottomEl = options.bottomEl || null;
    const posBadgeEl = options.posBadgeEl || null;
    let engine = null;
    let drawingManager = null;
    let drawingEngine = null;
    let toolbar = null;
    let indicatorEngine = null;
    let indicatorPanel = null;
    let analysisEngine = null;
    let analysisPanel = null;
    let marketBreadth = null;
    let exportBtn = null;
    let comparisonEngine = null;
    let comparisonPanel = null;
    let compareRestored = false;
    let baseChartHeight = 0;
    let lastPaneCount = 0;
    let paneResizer = null;
    let resetBtn = null;
    let crosshairSync = null;
    let hotkeys = null;
    let helpBtn = null;
    let helpOverlay = null;
    let fsManager = null;
    let fsBtn = null;
    let settingsPanel = null;
    let lastPriceLine = null;
    let positionLine = null;
    let historyLoader = null;
    let statusEl = null;
    let workspace = null;
    let drawingsRestored = false;
    let indicatorsRestored = false;
    let analysisRestored = false;

    const onHeightChange = typeof options.onHeightChange === 'function' ? options.onHeightChange : null;
    const onTimeframeChange = typeof options.onTimeframeChange === 'function' ? options.onTimeframeChange : null;
    const onSelectSymbol = typeof options.onSelectSymbol === 'function' ? options.onSelectSymbol : null;

    // PHASE 11 — tự tăng chiều cao chart theo số sub-pane (chỉ báo) để pane không bị ép quá thấp.
    function recalcChartHeight() {
        const chart = engine ? engine.getChart() : null;
        if (!chart || !placeholderEl) return;
        let panes = [];
        try { panes = chart.panes(); } catch (e) { /* ignore */ }
        const count = panes && panes.length ? panes.length : 1;
        if (!baseChartHeight) baseChartHeight = placeholderEl.offsetHeight || 480;
        // Chỉ đặt stretch mặc định khi SỐ PANE thay đổi (không đè lên kéo resize thủ công)
        if (panes && panes.length && count !== lastPaneCount) {
            lastPaneCount = count;
            try { panes[0].setStretchFactor(3); } catch (e) { /* ignore */ }
            for (let i = 1; i < panes.length; i++) { try { panes[i].setStretchFactor(1); } catch (e) { /* ignore */ } }
        }
        const minMain = 240;
        const perSub = 110;
        const target = Math.max(baseChartHeight, minMain + Math.max(0, count - 1) * perSub);
        if (Math.abs(placeholderEl.style.height ? parseFloat(placeholderEl.style.height) : 0 - target) > 2) {
            placeholderEl.style.height = target + 'px';
        }
        if (onHeightChange) onHeightChange();
    }

    function timeframe() {
        return state.getTimeframe();
    }

    // Cập nhật legend (OHLC + Vol + giá trị chỉ báo tại bar) — PHASE 15
    function updateLegend(bar, vol) {
        let inds = [];
        if (bar && indicatorEngine) inds = indicatorEngine.valuesAtTime(bar.time);
        legend.update(state.getSymbol(), bar, vol, inds);
    }

    function refreshLegend() {
        if (!engine) return;
        const bars = engine.getBars ? engine.getBars() : [];
        const last = bars[bars.length - 1] || null;
        updateLegend(last, last ? last.volume : 0);
    }

    // ---------- Dataset / lazy-load + loading state ----------
    // Áp dụng dữ liệu lên chart + cập nhật chỉ báo/phân tích/đường vẽ.
    // `preserve=true` dùng khi prepend thêm lịch sử → giữ nguyên visible range.
    function applyCandles(candles, opts) {
        if (!engine) return;
        const preserve = !!(opts && opts.preserve);
        if (preserve) engine.updateData(candles);
        else engine.setData(candles);
        state.set({ bars: candles, loading: false, error: null });
        if (indicatorEngine) indicatorEngine.setData(candles);
        if (analysisEngine) analysisEngine.setData(candles);
        if (lastPriceLine) lastPriceLine.update();
        if (positionLine) positionLine.render();   // giá vốn: cập nhật lại màu/% theo nến mới
        refreshLegend();
        if (drawingEngine) drawingEngine.render();
    }

    // Đặt visible range ban đầu (vùng gần hiện tại); clamp theo dữ liệu để
    // không có khoảng trống bên trái.
    function setInitialRange(days, candles) {
        if (!engine || !engine.getChart()) return;
        if (!days || days <= 0) { engine.setVisibleRange(0); return; }
        const now = Math.floor(Date.now() / 1000);
        let from = now - days * 86400;
        if (candles && candles.length && candles[0].time > from) from = candles[0].time - 1;
        try {
            engine.getChart().timeScale().setVisibleRange({ from, to: now });
        } catch (e) { /* ignore */ }
    }

    function showStatus(html, isError) {
        if (!statusEl) statusEl = document.getElementById('chartStatus');
        if (!statusEl) return;
        statusEl.innerHTML = html;
        statusEl.className = 'chart-status' + (isError ? ' cs-error' : '');
        statusEl.style.display = 'inline-flex';
    }

    function hideStatus() {
        if (!statusEl) statusEl = document.getElementById('chartStatus');
        if (statusEl) statusEl.style.display = 'none';
    }

    function ensureHistoryLoader() {
        if (!engine || !engine.getChart()) return;
        destroyHistoryLoader();
        historyLoader = new HistoryLoader({
            provider,
            getChart: () => (engine ? engine.getChart() : null),
            getSymbol: () => state.getSymbol(),
            getInterval: () => state.getTimeframe(),
            onDataUpdate: (candles) => applyCandles(candles, { preserve: true }),
            onLoading: (loading) => {
                if (loading) {
                    showStatus('<span class="cs-spin"><i class="fa-solid fa-circle-notch"></i></span> Đang tải lịch sử…');
                } else {
                    hideStatus();
                }
            },
            onError: () => {
                showStatus(
                    '<span><i class="fa-solid fa-triangle-exclamation"></i> Không tải thêm được lịch sử</span>' +
                    ' <button type="button" class="cs-retry">Thử lại</button>',
                    true
                );
                const b = statusEl && statusEl.querySelector('.cs-retry');
                if (b) b.addEventListener('click', () => { hideStatus(); if (historyLoader) historyLoader.schedule(); });
            },
        });
        historyLoader.start();
        historyLoader.schedule();
        if (window && window.__tunixChartDebug) {
            window.__tunixChartDebug.historyLoader = historyLoader;
            window.__tunixChartDebug.provider = provider;
            window.__tunixChartDebug.debug = false; // bật true để xem log lazy-load
        }
    }

    function destroyHistoryLoader() {
        if (historyLoader) { try { historyLoader.destroy(); } catch (e) { /* ignore */ } historyLoader = null; }
        hideStatus();
    }

    function destroyDrawings() {
        if (toolbar) { try { toolbar.destroy(); } catch (e) { /* ignore */ } toolbar = null; }
        if (drawingEngine) { try { drawingEngine.destroy(); } catch (e) { /* ignore */ } drawingEngine = null; }
        drawingManager = null;
    }

    function destroyIndicators() {
        if (indicatorPanel) { try { indicatorPanel.destroy(); } catch (e) { /* ignore */ } indicatorPanel = null; }
        if (indicatorEngine) { try { indicatorEngine.destroy(); } catch (e) { /* ignore */ } indicatorEngine = null; }
    }

    function destroyAnalysis() {
        if (marketBreadth) { try { marketBreadth.destroy(); } catch (e) { /* ignore */ } marketBreadth = null; }
        if (analysisPanel) { try { analysisPanel.destroy(); } catch (e) { /* ignore */ } analysisPanel = null; }
        if (analysisEngine) { try { analysisEngine.destroy(); } catch (e) { /* ignore */ } analysisEngine = null; }
    }

    function destroyExport() {
        if (exportBtn) { try { exportBtn.remove(); } catch (e) { /* ignore */ } exportBtn = null; }
    }

    function destroyComparison() {
        if (comparisonPanel) { try { comparisonPanel.destroy(); } catch (e) { /* ignore */ } comparisonPanel = null; }
        if (comparisonEngine) { try { comparisonEngine.destroy(); } catch (e) { /* ignore */ } comparisonEngine = null; }
    }

    // PHASE 17 — lưu/khôi phục bố cục pane (stretch factor) + nút đặt lại.
    function persistLayout() {
        const chart = engine ? engine.getChart() : null;
        if (!chart) return;
        try {
            const stretch = chart.panes().map((p) => p.getStretchFactor());
            storageSet(STORAGE_KEYS.layout, { stretch });
        } catch (e) { /* ignore */ }
    }

    function applyLayoutStretch() {
        const chart = engine ? engine.getChart() : null;
        if (!chart) return;
        const panes = chart.panes();
        if (!panes || !panes.length) return;
        lastPaneCount = panes.length; // đánh dấu layout đã thiết lập → recalcChartHeight không reset stretch
        const saved = storageGet(STORAGE_KEYS.layout, null);
        if (saved && Array.isArray(saved.stretch) && saved.stretch.length === panes.length) {
            try {
                panes.forEach((p, i) => { p.setStretchFactor(Math.max(0.1, Number(saved.stretch[i]))); });
            } catch (e) { /* ignore */ }
        }
    }

    function destroyReset() {
        if (resetBtn) { try { resetBtn.remove(); } catch (e) { /* ignore */ } resetBtn = null; }
    }

    // Đồng bộ crosshair giữa các sub-pane (PHASE 20).
    function ensureCrosshairSync() {
        if (!engine || !engine.getChart()) return;
        if (!crosshairSync) {
            crosshairSync = new CrosshairSync({ chartEngine: engine, indicatorEngine });
        }
        crosshairSync.create(); // idempotent
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.crosshairSync = crosshairSync;
    }

    function destroyCrosshairSync() {
        if (crosshairSync) { try { crosshairSync.destroy(); } catch (e) { /* ignore */ } crosshairSync = null; }
    }

    // ---------- Phím tắt nâng cao + Bảng phím tắt (PHASE 21) ----------
    // Timeframe cycle: Ngày → Tuần → Tháng → Giờ → Ngày...
    const TIMEFRAME_CYCLE = ['1D', '1W', '1M', '1H'];

    function cycleTimeframe(dir) {
        let idx = TIMEFRAME_CYCLE.indexOf(state.getTimeframe());
        if (idx === -1) idx = 0;
        idx = (idx + (dir || 1) + TIMEFRAME_CYCLE.length) % TIMEFRAME_CYCLE.length;
        const next = TIMEFRAME_CYCLE[idx];
        // Cập nhật dropdown (label + active) qua callback nếu có.
        if (onTimeframeChange) onTimeframeChange(next);
        else setTimeframe(next);
    }

    // Di chuyển crosshair từng nến (PHASE 24) — Shift+←/→.
    function stepBars(dir) {
        const eng = engine;
        if (!eng) return;
        const bars = eng.getBars ? eng.getBars() : [];
        if (!bars.length) return;
        let idx = bars.length - 1;
        const cur = eng.getCrosshairTime ? eng.getCrosshairTime() : null;
        if (cur != null) {
            let lo = 0, hi = bars.length - 1, found = -1;
            while (lo <= hi) {
                const mid = (lo + hi) >> 1;
                if (bars[mid].time === cur) { found = mid; break; }
                if (bars[mid].time < cur) lo = mid + 1; else hi = mid - 1;
            }
            if (found !== -1) idx = found;
        }
        idx = Math.max(0, Math.min(bars.length - 1, idx + dir));
        const b = bars[idx];
        const chart = eng.getChart();
        const series = eng.getCandleSeries();
        if (chart && series && b) {
            try { chart.setCrosshairPosition(b.close, b.time, series); } catch (e) { /* ignore */ }
        }
        eng.crosshairTime = b.time;
        updateLegend(b, b.volume);
        if (crosshairSync) crosshairSync.setProgrammaticTime(b.time);
    }

    function toggleHotkey(key) {
        if (key === 'mb') { toggleBreadth(); return; }
        if (!analysisEngine) return;
        analysisEngine.toggle(key);
        if (analysisPanel) analysisPanel.refresh();
        persistAnalysis();
        refreshLegend();
    }

    function showHelp() { if (helpOverlay) helpOverlay.style.display = 'flex'; }
    function hideHelp() { if (helpOverlay) helpOverlay.style.display = 'none'; }

    function toggleHelp() {
        if (!helpOverlay) return;
        // Kiểm tra theo computed style (ban đầu display:none nằm trong CSS, chưa có inline).
        let hidden = true;
        try { hidden = getComputedStyle(helpOverlay).display === 'none'; } catch (e) { /* ignore */ }
        if (hidden) showHelp(); else hideHelp();
    }

    function closePopovers() {
        if (indicatorPanel) { try { indicatorPanel.close(); } catch (e) { /* ignore */ } }
        if (analysisPanel) { try { analysisPanel.close(); } catch (e) { /* ignore */ } }
        if (comparisonPanel) { try { comparisonPanel.close(); } catch (e) { /* ignore */ } }
        if (settingsPanel) { try { settingsPanel.close(); } catch (e) { /* ignore */ } }
        hideHelp();
    }

    function ensureHotkeys() {
        if (!engine || !engine.getChart()) return;
        if (!hotkeys) {
            hotkeys = new HotkeyManager({
                chartEngine: engine,
                callbacks: {
                    onTimeframe: (dir) => cycleTimeframe(dir),
                    onStep: (dir) => stepBars(dir),
                    onToggle: (key) => toggleHotkey(key),
                    onExport: () => { if (exportBtn) { try { exportBtn.click(); } catch (e) { /* ignore */ } } },
                    onReset: () => { if (resetBtn) { try { resetBtn.click(); } catch (e) { /* ignore */ } } },
                    onHelp: () => toggleHelp(),
                    onClosePopovers: () => closePopovers(),
                },
            });
        }
        hotkeys.create(); // idempotent
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.hotkeys = hotkeys;
    }

    function destroyHotkeys() {
        if (hotkeys) { try { hotkeys.destroy(); } catch (e) { /* ignore */ } hotkeys = null; }
    }

    function ensureHelp() {
        if (helpEl && !helpBtn) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'an-btn hh-btn';
            btn.innerHTML = '<i class="fa-solid fa-keyboard"></i>';
            btn.title = 'Bảng phím tắt (?)';
            btn.addEventListener('click', (e) => { e.stopPropagation(); toggleHelp(); });
            helpEl.appendChild(btn);
            helpBtn = btn;
        }
        if (!helpOverlay) helpOverlay = document.getElementById('hotkeyHelp');
        if (helpOverlay && !helpOverlay.__bound) {
            helpOverlay.__bound = true;
            // Click nền → đóng
            helpOverlay.addEventListener('click', (e) => { if (e.target === helpOverlay) hideHelp(); });
            const closeBtn = document.getElementById('hotkeyHelpClose');
            if (closeBtn) closeBtn.addEventListener('click', (e) => { e.stopPropagation(); hideHelp(); });
        }
    }

    function destroyHelp() {
        if (helpBtn) { try { helpBtn.remove(); } catch (e) { /* ignore */ } helpBtn = null; }
        // Reset cờ để controller mới (sau destroy/recreate) re-bind handler đóng —
        // nếu không, click X / click nền gọi hideHelp của controller cũ (helpOverlay=null)
        // → KHÔNG đóng được bảng phím tắt (bug khi đổi mã trong fullscreen).
        if (helpOverlay) { try { helpOverlay.__bound = false; } catch (e) { /* ignore */ } }
        helpOverlay = null;
    }

    // ---------- Toàn màn hình biểu đồ (PHASE 22) ----------
    function refreshFsBtn(active) {
        if (!fsBtn) return;
        fsBtn.innerHTML = active
            ? '<i class="fa-solid fa-compress"></i>'
            : '<i class="fa-solid fa-expand"></i>';
        fsBtn.title = active ? 'Thoát toàn màn hình (Esc)' : 'Toàn màn hình biểu đồ';
        fsBtn.classList.toggle('fs-active', !!active);
    }

    function ensureFullscreen() {
        if (!fsEl) return;
        if (!fsBtn) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'an-btn fs-btn';
            btn.innerHTML = '<i class="fa-solid fa-expand"></i>';
            btn.title = 'Toàn màn hình biểu đồ';
            btn.addEventListener('click', (e) => { e.stopPropagation(); if (fsManager) fsManager.toggle(); });
            fsEl.appendChild(btn);
            fsBtn = btn;
        }
        if (!fsManager) {
            fsManager = new FullscreenManager({ onChange: (active) => {
                refreshFsBtn(active);
                // Thoát toàn màn hình (tự fullscreen trong iframe) → trả chart về chiều cao bình thường.
                if (!active) resetChartHeightAfterFullscreen();
            } });
            fsManager.create();
        }
        refreshFsBtn(fsManager.isFullscreen());
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.fullscreen = fsManager;
    }

    function destroyFullscreen() {
        if (fsManager) { try { fsManager.destroy(); } catch (e) { /* ignore */ } fsManager = null; }
        if (fsBtn) { try { fsBtn.remove(); } catch (e) { /* ignore */ } fsBtn = null; }
    }

    // Sau khi thoát toàn màn hình: trả chart về chiều cao mặc định (CSS .chart-placeholder ~480px).
    // Khi chart được khởi tạo trong lúc fullscreen (?fs=1), `baseChartHeight` ghi nhận chiều cao
    // fullscreen (placeholder height:auto !important) rồi giữ nguyên bằng inline style → sau khi
    // thoát fullscreen chart vẫn cao hơn bình thường. Hàm này reset để recalc lại từ chiều cao CSS.
    function resetChartHeightAfterFullscreen() {
        if (!placeholderEl) return;
        placeholderEl.style.height = '';
        baseChartHeight = 0;
        recalcChartHeight();
    }

    // ---------- Cài đặt biểu đồ (PHASE 23) ----------
    const SETTINGS_DEFAULTS = { grid: true, magnet: false, legend: true, sync: true, volume: true, toolbar: true };

    function applySetting(key, on) {
        const chart = engine ? engine.getChart() : null;
        switch (key) {
            case 'grid':
                if (chart) chart.applyOptions({ grid: { vertLines: { visible: !!on }, horzLines: { visible: !!on } } });
                break;
            case 'magnet':
                if (chart) chart.applyOptions({ crosshair: { mode: on ? 1 : 0 } });
                break;
            case 'legend':
                if (legendEl) legendEl.style.display = on ? '' : 'none';
                break;
            case 'sync':
                if (crosshairSync) crosshairSync.setEnabled(!!on);
                break;
            case 'volume': {
                const vs = engine ? engine.getVolumeSeries() : null;
                if (vs) vs.applyOptions({ visible: !!on });
                break;
            }
            case 'toolbar':
                if (toolbarEl) toolbarEl.style.display = on ? '' : 'none';
                break;
            default:
                break;
        }
    }

    function persistSettings() {
        storageSet(STORAGE_KEYS.settings, Object.assign({}, settingsPanel ? settingsPanel.state : SETTINGS_DEFAULTS));
    }

    function ensureSettings() {
        if (!settingsEl) return;
        if (!settingsPanel) {
            const saved = Object.assign({}, SETTINGS_DEFAULTS, storageGet(STORAGE_KEYS.settings, {}) || {});
            settingsPanel = new SettingsPanel({
                state: saved,
                onChange: (key, on) => { applySetting(key, on); persistSettings(); },
            });
            settingsEl.appendChild(settingsPanel.create());
            for (const key of Object.keys(saved)) applySetting(key, saved[key]);
        }
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.settingsPanel = settingsPanel;
    }

    function destroySettings() {
        if (settingsPanel) { try { settingsPanel.destroy(); } catch (e) { /* ignore */ } settingsPanel = null; }
    }

    // ---------- Đường giá mới nhất (PHASE 25) ----------
    function ensureLastPriceLine() {
        if (!engine || !engine.getChart()) return;
        if (!lastPriceLine) {
            lastPriceLine = new LastPriceLine({ chartEngine: engine });
        }
        lastPriceLine.create(); // idempotent
        lastPriceLine.update();
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.lastPriceLine = lastPriceLine;
    }

    function destroyLastPriceLine() {
        if (lastPriceLine) { try { lastPriceLine.destroy(); } catch (e) { /* ignore */ } lastPriceLine = null; }
    }

    // ---------- Vạch vị thế (đường giá vốn của mã đang xem) ----------
    // Chỉ vẽ khi người dùng đã đăng nhập và CÓ vị thế ở mã đó — số liệu lấy từ /api/portfolio/position/{symbol}
    // (đã điều chỉnh cổ tức). Dùng price line native nên tự có mặt ở chế độ toàn màn hình.
    function ensurePositionLine() {
        if (!engine || !engine.getChart()) return;
        if (!positionLine) {
            positionLine = new PositionLine({
                chartEngine: engine,
                badgeEl: posBadgeEl,
                getSymbol: () => state.getSymbol(),
            });
        }
        positionLine.ensure(state.getSymbol());
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.positionLine = positionLine;
    }

    function destroyPositionLine() {
        if (positionLine) { try { positionLine.destroy(); } catch (e) { /* ignore */ } positionLine = null; }
    }

    // Reset BIỂU ĐỒ về mặc định — chỉ reset chart (không reload cả trang, không hỏi xác nhận).
    function resetChart() {
        // 1) Xoá mọi thiết lập đã lưu (drawings/indicators/analysis/compare/layout/settings)
        try {
            for (const k of Object.keys(localStorage)) if (k.indexOf('tunix.') === 0) localStorage.removeItem(k);
        } catch (e) { /* ignore */ }

        // 2) Xoá drawings
        if (drawingManager) { try { drawingManager.clearAll(); } catch (e) { /* ignore */ } }

        // 3) Tắt hết chỉ báo (thu sub-pane về mặc định)
        if (indicatorEngine) {
            for (const e of indicatorEngine.getEnabled()) {
                try { indicatorEngine.remove(e.key); } catch (err) { /* ignore */ }
            }
        }
        if (indicatorPanel) { try { indicatorPanel.refresh(); } catch (e) { /* ignore */ } }

        // 4) Tắt phân tích (S/R, Volume Profile, Hội tụ)
        if (analysisEngine) {
            for (const k of ['sr', 'vp', 'cf']) {
                if (analysisEngine.isEnabled(k)) { try { analysisEngine.toggle(k); } catch (e) { /* ignore */ } }
            }
        }
        // 5) Ẩn độ rộng thị trường
        if (marketBreadth && marketBreadth.visible) { try { marketBreadth.hide(); } catch (e) { /* ignore */ } }
        if (analysisPanel) {
            try { analysisPanel.setActive('mb', false); } catch (e) { /* ignore */ }
            try { analysisPanel.refresh(); } catch (e) { /* ignore */ }
        }

        // 6) Xoá so sánh
        if (comparisonEngine) { try { comparisonEngine.clear(); } catch (e) { /* ignore */ } }
        refreshCompareChips();

        // 7) Cài đặt về mặc định (lưới/nam châm/chú thích/đồng bộ/khối lượng/thanh công cụ)
        if (settingsPanel) {
            const defaults = Object.assign({}, SETTINGS_DEFAULTS);
            try { settingsPanel.setState(defaults); } catch (e) { /* ignore */ }
            for (const key of Object.keys(defaults)) applySetting(key, defaults[key]);
            persistSettings();
        }

        // 8) Reset kích thước pane về mặc định (stretch 3:1:1...)
        if (engine && engine.getChart()) {
            try {
                const panes = engine.getChart().panes();
                panes.forEach((p, i) => p.setStretchFactor(i === 0 ? 3 : 1));
                persistLayout();
            } catch (e) { /* ignore */ }
        }

        // 9) Vẽ lại overlay + chỉ báo (giờ rỗng) + legend + chiều cao
        const bars = engine ? engine.getBars() : [];
        if (bars && bars.length) {
            try { if (indicatorEngine) indicatorEngine.setData(bars); } catch (e) { /* ignore */ }
            try { if (analysisEngine) analysisEngine.setData(bars); } catch (e) { /* ignore */ }
            try { if (lastPriceLine) lastPriceLine.update(); } catch (e) { /* ignore */ }
            refreshLegend();
            try { if (drawingEngine) drawingEngine.render(); } catch (e) { /* ignore */ }
            recalcChartHeight();
        }
        if (onHeightChange) onHeightChange();
    }

    function ensureReset() {
        if (!resetEl || resetBtn) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ind-btn reset-btn';
        btn.innerHTML = '<i class="fa-solid fa-rotate-left"></i>';
        btn.title = 'Đặt lại biểu đồ về mặc định';
        btn.addEventListener('click', () => { resetChart(); });
        resetEl.appendChild(btn);
        resetBtn = btn;
    }

    // Kéo resize pane thủ công (PHASE 16).
    function ensurePaneResizer() {
        if (!engine || !engine.getChart()) return;
        if (!paneResizer) {
            paneResizer = new PaneResizer({
                chartEngine: engine,
                onChange: () => { persistLayout(); if (onHeightChange) onHeightChange(); },
            });
        }
        paneResizer.create(); // idempotent
        paneResizer.place();
    }

    function destroyPaneResizer() {
        if (paneResizer) { try { paneResizer.destroy(); } catch (e) { /* ignore */ } paneResizer = null; }
    }

    // So sánh đa mã (PHASE 8).
    function refreshCompareChips() {
        if (!comparisonPanel || !comparisonEngine) return;
        const items = comparisonEngine.getSymbols().map((s) => {
            const d = comparisonEngine.getDisplay(s);
            return { symbol: s, color: comparisonEngine.getColor(s), text: d.text, cls: d.cls };
        });
        comparisonPanel.setResult(items);
        comparisonPanel.setModeActive(comparisonEngine.getMode());
    }

    function persistCompare() {
        storageSet(STORAGE_KEYS.compare, { mode: comparisonEngine.getMode(), symbols: comparisonEngine.getSymbols() });
    }

    function ensureComparison() {
        if (!engine || !engine.getChart()) return;
        if (!comparisonEngine) comparisonEngine = new ComparisonEngine({ chartEngine: engine });
        if (compareEl && !comparisonPanel) {
            comparisonPanel = new ComparisonPanel({
                onAdd: async (sym) => {
                    const ok = await comparisonEngine.addSymbol(sym, state.getTimeframe());
                    if (ok) { persistCompare(); refreshCompareChips(); }
                    return ok;
                },
                onRemove: (sym) => {
                    comparisonEngine.removeSymbol(sym);
                    persistCompare();
                    refreshCompareChips();
                },
                onClearAll: () => {
                    comparisonEngine.clear();
                    persistCompare();
                    refreshCompareChips();
                },
                onModeChange: async (mode) => {
                    await comparisonEngine.setMode(mode, state.getTimeframe());
                    persistCompare();
                    refreshCompareChips();
                },
            });
            compareEl.appendChild(comparisonPanel.create());
        }
        if (!compareRestored) {
            compareRestored = true;
            const saved = storageGet(STORAGE_KEYS.compare, null);
            // Migration: định dạng cũ là string/array; mới là { mode, symbols }
            let savedList = [], savedMode = 'pct';
            if (Array.isArray(saved)) savedList = saved;
            else if (typeof saved === 'string' && saved) savedList = [saved];
            else if (saved && Array.isArray(saved.symbols)) { savedList = saved.symbols; savedMode = saved.mode === 'abs' ? 'abs' : 'pct'; }
            (async () => {
                if (savedMode === 'abs') await comparisonEngine.setMode('abs', state.getTimeframe());
                for (const s of savedList) {
                    if (s) await comparisonEngine.addSymbol(s, state.getTimeframe());
                }
                refreshCompareChips();
            })();
        }
        if (window) window.__tunixChartDebug.comparisonEngine = comparisonEngine;
    }

    // Nút xuất ảnh PNG (PHASE 7).
    function ensureExport() {
        if (window && window.__tunixChartDebug) window.__tunixChartDebug.exportPNG = exportChartPNG;
        if (!exportEl || exportBtn) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'an-btn ex-btn';
        btn.innerHTML = '<i class="fa-solid fa-camera"></i>';
        btn.title = 'Xuất biểu đồ ra ảnh PNG (có cả drawing & phân tích)';
        btn.addEventListener('click', async () => {
            try {
                const overlays = [];
                if (drawingEngine && drawingEngine.svg) overlays.push(drawingEngine.svg);
                if (analysisEngine && analysisEngine.svg) overlays.push(analysisEngine.svg);
                const url = await exportChartPNG({
                    chartEngine: engine,
                    overlays,
                    title: state.getSymbol() + ' · ' + state.getTimeframe(),
                });
                if (url) {
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'TuniX-' + state.getSymbol() + '-' + state.getTimeframe() + '.png';
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                }
            } catch (e) { /* ignore */ }
        });
        exportEl.appendChild(btn);
        exportBtn = btn;
    }

    function toggleBreadth() {
        if (!marketBreadth) return;
        marketBreadth.toggle();
        if (analysisPanel) analysisPanel.setActive('mb', marketBreadth.visible);
        persistAnalysis();
    }

    function persistAnalysis() {
        storageSet(STORAGE_KEYS.analysis, Object.assign(
            {}, analysisEngine ? analysisEngine.enabled : {}, { mb: marketBreadth ? marketBreadth.visible : false }
        ));
    }

    // Tạo hệ thống chỉ báo (PHASE 3) — một lần, sau khi engine sẵn sàng.
    function ensureIndicators() {
        if (!engine || !engine.getChart()) return;
        if (!indicatorEngine) {
            indicatorEngine = new IndicatorEngine({
                chartEngine: engine,
                onChange: () => {
                    storageSet(STORAGE_KEYS.indicators, indicatorEngine.getEnabled());
                    recalcChartHeight();
                    refreshLegend();
                    if (paneResizer) paneResizer.place();
                    if (workspace) workspace.refreshIndicators();
                },
            });
        }
        if (indicatorEl && !indicatorPanel) {
            indicatorPanel = new IndicatorPanel({ engine: indicatorEngine });
            indicatorEl.appendChild(indicatorPanel.create());
        }
        // Khôi phục cài đặt chỉ báo (PHASE 6) — một lần.
        if (!indicatorsRestored) {
            indicatorsRestored = true;
            const saved = storageGet(STORAGE_KEYS.indicators, []);
            if (Array.isArray(saved)) {
                for (const item of saved) {
                    if (item && item.key && INDICATOR_DEFS[item.key]) {
                        indicatorEngine.add(item.key, item.settings || {});
                    }
                }
            }
            if (indicatorPanel) indicatorPanel.refresh();
            applyLayoutStretch();
        }
        if (window) window.__tunixChartDebug = { engine, indicatorEngine, drawingEngine, drawingManager, analysisEngine };
    }

    // Tạo hệ thống phân tích S/R + Volume Profile (PHASE 4).
    function ensureAnalysis() {
        if (!engine || !engine.getChart()) return;
        if (!analysisEngine) {
            analysisEngine = new AnalysisEngine({
                chartEngine: engine,
                onChange: () => { persistAnalysis(); if (workspace) workspace.refreshSR(); if (workspace) workspace.syncSrCheckbox(); },
            });
        }
        analysisEngine.create(); // idempotent
        if (analysisEl && !analysisPanel) {
            analysisPanel = new AnalysisPanel({
                engine: analysisEngine,
                onAction: (key) => { if (key === 'mb') toggleBreadth(); },
            });
            analysisEl.appendChild(analysisPanel.create());
        }
        if (breadthEl && !marketBreadth) {
            marketBreadth = new MarketBreadth({});
            breadthEl.appendChild(marketBreadth.create());
        }
        // Khôi phục bật/tắt phân tích (PHASE 6) — một lần.
        if (!analysisRestored) {
            analysisRestored = true;
            const saved = storageGet(STORAGE_KEYS.analysis, {}) || {};
            // Khôi phục MB trước (để persistAnalysis ghi đúng trạng thái mb).
            if (saved.mb && marketBreadth) {
                marketBreadth.show();
                if (analysisPanel) analysisPanel.setActive('mb', true);
            }
            if (saved.sr) analysisEngine.toggle('sr');
            if (saved.vp) analysisEngine.toggle('vp');
            if (saved.cf) analysisEngine.toggle('cf');
            if (analysisPanel) analysisPanel.refresh();
            persistAnalysis();
        }
        if (window) window.__tunixChartDebug.analysisEngine = analysisEngine;
        if (window) window.__tunixChartDebug.marketBreadth = marketBreadth;
    }

    // Tạo (hoặc gắn lại) hệ thống drawing sau khi engine render sẵn sàng.
    function ensureDrawings() {
        if (!engine || !engine.getChart()) return;
        if (!drawingManager) {
            drawingManager = new DrawingManager({
                symbol: state.getSymbol(),
                timeframe: timeframe(),
                onChange: () => storageSet(STORAGE_KEYS.drawings, drawingManager.getAll()),
            });
        }
        // Khôi phục drawings (PHASE 6) — một lần.
        if (!drawingsRestored) {
            drawingsRestored = true;
            const saved = storageGet(STORAGE_KEYS.drawings, []);
            if (Array.isArray(saved)) drawingManager.loadDrawings(saved);
        }
        if (!drawingEngine) {
            drawingEngine = new DrawingEngine({
                chartEngine: engine,
                manager: drawingManager,
                getCandles: () => (engine.getBars ? engine.getBars() : []),
            });
        }
        drawingEngine.create(); // idempotent: chỉ gắn lại svg nếu chưa có
        if (toolbarEl && !toolbar) {
            toolbar = new ChartToolbar({ engine: drawingEngine, manager: drawingManager });
            toolbarEl.appendChild(toolbar.create());
        }
    }

    function showMessage(html) {
        if (!container) return;
        const dark = state.getTheme() === 'dark';
        container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
            'padding:24px;text-align:center;font-size:13px;line-height:1.7;' +
            'color:' + (dark ? '#9fb0c7' : '#8a99b1') + ';">' + html + '</div>';
    }

    function ensureEngine() {
        if (engine) return engine;
        try {
            engine = new ChartEngine(container, {
                theme: state.getTheme(),
                onLegend: (bar, vol) => updateLegend(bar, vol),
            });
            engine.create();
            ensureDrawings();
            ensureIndicators();
            ensureCrosshairSync();
            ensureAnalysis();
            ensureExport();
            ensureComparison();
            ensurePaneResizer();
            ensureReset();
            ensureHotkeys();
            ensureHelp();
            ensureFullscreen();
            ensureSettings();
            ensureLastPriceLine();
            ensurePositionLine();
            ensureWorkspace();
            recalcChartHeight();
        } catch (e) {
            engine = null;
            showMessage('Không thể khởi tạo biểu đồ. Vui lòng tải lại trang.');
        }
        return engine;
    }

    // ---------- Fullscreen workspace (Right Sidebar + Bottom Dashboard) ----------
    // Chỉ mở rộng layout FULLSCREEN — normal mode giữ nguyên (CSS ẩn sidebar/dashboard).
    function ensureWorkspace() {
        if (!sidebarEl && !bottomEl) return;
        if (!workspace) {
            workspace = new FullscreenWorkspace({
                sidebarEl,
                bottomEl,
                getSymbol: () => state.getSymbol(),
                getBars: () => (engine && engine.getBars ? engine.getBars() : []),
                getTimeframe: () => state.getTimeframe(),
                getEnabledIndicators: () => (indicatorEngine ? indicatorEngine.getEnabled() : []),
                indicatorValuesAtTime: (t) => (indicatorEngine ? indicatorEngine.valuesAtTime(t) : []),
                indicatorPanel: indicatorPanel || null,
                indicatorEngine: indicatorEngine || null,
                analysisEngine: analysisEngine || null,
                theme: state.getTheme(),
                onSelectSymbol: (symbol) => {
                    // Đổi mã NGAY TRONG TRANG (giữ fullscreen): ưu tiên callback từ
                    // StockDetail (cập nhật header/quote/panels), fallback load() của chart.
                    if (onSelectSymbol) onSelectSymbol(symbol);
                    else load(symbol);
                },
                onOpenAi: (symbol) => {
                    // Thoát fullscreen để người dùng dùng được chat TuniAI (panel chat nằm ở parent,
                    // sẽ bị khuất sau iframe fullscreen). Exit fullscreen rồi mới mở chat.
                    if (fsManager && fsManager.isFullscreen()) {
                        try { fsManager.toggle(); } catch (e) { /* ignore */ }
                    }
                    if (window.parent && window.parent !== window) {
                        window.parent.postMessage({ type: 'tunix-ai-open', symbol: symbol || '' }, '*');
                    }
                },
            });
            workspace.create();
        }
        workspace.setTheme(state.getTheme());
    }

    function destroyWorkspace() {
        if (workspace) { try { workspace.destroy(); } catch (e) { /* ignore */ } workspace = null; }
    }

    async function load(symbolOverride, restoreRange) {
        if (symbolOverride) state.set({ symbol: String(symbolOverride).toUpperCase() });
        if (!container) return;
        const symbol = state.getSymbol();
        const timeframe = state.getTimeframe();

        const eng = ensureEngine();
        if (!eng) return;

        try {
            // Dataset theo (symbol, timeframe) — cache riêng từng mã/timeframe (yêu cầu #19).
            // Nếu đã có cache thì tái sử dụng, không request lại.
            let candles = null;
            const cached = provider.getCached(symbol, timeframe);
            if (cached && cached.candles && cached.candles.length) {
                candles = cached.candles;
            } else {
                const countback = TIMEFRAME_COUNTBACK[timeframe] || 500;
                const normalized = await provider.fetchInitial(symbol, timeframe, countback);
                candles = (normalized && normalized.candles) || [];
            }
            if (!candles.length) {
                showMessage('Không có dữ liệu biểu đồ cho mã này ở timeframe đã chọn.');
                return;
            }
            state.set({ bars: candles, loading: false, error: null });
            eng.setData(candles);
            // Khi đổi timeframe (restoreRange != null): LƯU Ý LWC v5.2.1
            // `setVisibleRange` KHÔNG mở rộng view (chỉ thu hẹp) và có thể COLLAPSE
            // range lớn thành vài nến cuối → không thể giữ đúng visible range cũ.
            // Giải pháp bền vững: fitContent hiển thị TOÀN BỘ dataset mới (hoạt động
            // đúng trên dữ liệu vừa setData) — view luôn hợp lý, không bị kẹt 1-2 nến.
            if (restoreRange) {
                try { eng.getChart().timeScale().fitContent(); }
                catch (e) { setInitialRange(TIMEFRAME_DAYS[timeframe] || 180, candles); }
            } else {
                setInitialRange(TIMEFRAME_DAYS[timeframe] || 180, candles);
            }
            ensureDrawings();
            ensureIndicators();
            ensureCrosshairSync();
            ensureAnalysis();
            ensureExport();
            ensureComparison();
            if (indicatorEngine) indicatorEngine.setData(candles);
            if (analysisEngine) analysisEngine.setData(candles);
            if (lastPriceLine) lastPriceLine.update();
            refreshLegend();
            ensurePaneResizer();
            ensureReset();
            ensureHotkeys();
            ensureHelp();
            ensureFullscreen();
            ensureSettings();
            ensureLastPriceLine();
            ensurePositionLine();
            ensureHistoryLoader();
            // So sánh: refetch theo timeframe mới để đồng bộ dữ liệu
            if (comparisonEngine && comparisonEngine.getSymbols().length) {
                comparisonEngine.refetch(timeframe).then(() => refreshCompareChips());
            }
            recalcChartHeight();
            if (drawingManager) drawingManager.setContext(symbol, timeframe);
            if (drawingEngine) drawingEngine.render();
            // Fullscreen workspace: đồng bộ theo mã + dữ liệu mới (sidebar/dashboard).
            ensureWorkspace();
            if (workspace) workspace.onDataChange(candles);
            if (workspace) workspace.onSymbolChange(symbol);
        } catch (e) {
            state.set({ loading: false, error: String(e && e.message ? e.message : '') });
            showMessage('Không tải được dữ liệu biểu đồ. Vui lòng thử lại.');
        }
        if (onHeightChange) onHeightChange();
    }

    function setTimeframe(tf) {
        tf = String(tf || '1D').toUpperCase();
        if (tf === state.getTimeframe()) return;
        // Giữ visible range (thời gian) hiện tại khi đổi timeframe — không reset view.
        let restoreRange = null;
        if (engine && engine.getChart()) {
            try { restoreRange = engine.getChart().timeScale().getVisibleRange(); } catch (e) { /* ignore */ }
        }
        state.set({ timeframe: tf });
        load(null, restoreRange);
    }

    function setTheme(mode) {
        state.set({ theme: mode === 'dark' ? 'dark' : 'light' });
        if (engine) engine.setTheme(state.getTheme());
        if (crosshairSync) crosshairSync.refresh();
        if (workspace) workspace.setTheme(state.getTheme());
    }

    function destroy() {
        destroyHistoryLoader();
        destroyCrosshairSync();
        destroyHotkeys();
        destroyHelp();
        destroyFullscreen();
        destroySettings();
        destroyLastPriceLine();
        destroyPositionLine();
        destroyReset();
        destroyPaneResizer();
        destroyComparison();
        destroyExport();
        destroyAnalysis();
        destroyIndicators();
        destroyDrawings();
        destroyWorkspace();
        if (engine) {
            engine.destroy();
            engine = null;
        }
        legend.clear();
    }

    return { load, setTimeframe, setTheme, destroy, resetChartHeight: resetChartHeightAfterFullscreen,
             // Đồng bộ nút toàn màn hình khi fullscreen do SHELL bật/thoát (parent-driven).
             refreshFullscreen: () => { if (fsManager) fsManager.refresh(); } };
}
