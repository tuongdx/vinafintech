/**
 * HotkeyManager — quản lý phím tắt nâng cao cho chart (PHASE 21).
 *
 * Tránh xung đột với DrawingEngine (PHASE 14): công cụ vẽ dùng 1-9, 0, m,
 * Escape, Delete/Backspace, Ctrl+Z/Y. Bộ phím này dùng các phím KHÁC:
 *   - Điều hướng : ← / → (cuộn), + / = (zoom in), - / _ (zoom out), F (khớp dữ liệu)
 *   - Kỳ hạn     : [ / ] (trước / sau)
 *   - Phân tích  : S (S/R), V (Volume Profile), C (Hội tụ), B (Độ rộng thị trường)
 *   - Hành động  : E (xuất PNG), R (đặt lại bố cục), ? (bảng phím tắt), Esc (đóng popup)
 *
 * Bỏ qua khi đang gõ trong input/textarea/contenteditable (VD ô tìm kiếm so sánh,
 * ô nhập text vẽ) và khi có Ctrl/Meta/Alt (để tránh đè tổ hợp trình duyệt/hệ thống).
 */
export class HotkeyManager {
    /**
     * @param {object} deps
     *   - chartEngine (có getChart → timeScale)
     *   - callbacks { onTimeframe(dir), onToggle(key), onExport(), onReset(), onHelp(), onClosePopovers() }
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.cbs = deps.callbacks || {};
        this._onKey = (e) => this._handle(e);
        this._cleanup = [];
    }

    create() {
        window.addEventListener('keydown', this._onKey);
        this._cleanup.push(() => window.removeEventListener('keydown', this._onKey));
        return this;
    }

    destroy() {
        for (const fn of this._cleanup) { try { fn(); } catch (e) { /* ignore */ } }
        this._cleanup = [];
    }

    _isTyping(e) {
        const t = e.target;
        return !!(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable));
    }

    _handle(e) {
        if (this._isTyping(e)) return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const k = e.key;
        const lk = k.toLowerCase();

        // ---- Điều hướng ----
        // Shift+←/→: di chuyển crosshair từng nến (PHASE 24)
        if (e.shiftKey && (k === 'ArrowLeft' || k === 'ArrowRight')) {
            e.preventDefault();
            if (this.cbs.onStep) this.cbs.onStep(k === 'ArrowLeft' ? -1 : 1);
            return;
        }
        if (k === 'ArrowLeft') { e.preventDefault(); this._pan(-1); return; }
        if (k === 'ArrowRight') { e.preventDefault(); this._pan(1); return; }
        if (k === '+' || k === '=') { e.preventDefault(); this._zoom(-1); return; }
        if (k === '-' || k === '_') { e.preventDefault(); this._zoom(1); return; }
        if (lk === 'f') { e.preventDefault(); this._fit(); return; }

        // ---- Timeframe ----
        if (k === '[') { if (this.cbs.onTimeframe) { e.preventDefault(); this.cbs.onTimeframe(-1); } return; }
        if (k === ']') { if (this.cbs.onTimeframe) { e.preventDefault(); this.cbs.onTimeframe(1); } return; }

        // ---- Phân tích ----
        if (lk === 's') { if (this.cbs.onToggle) { e.preventDefault(); this.cbs.onToggle('sr'); } return; }
        if (lk === 'v') { if (this.cbs.onToggle) { e.preventDefault(); this.cbs.onToggle('vp'); } return; }
        if (lk === 'c') { if (this.cbs.onToggle) { e.preventDefault(); this.cbs.onToggle('cf'); } return; }
        if (lk === 'b') { if (this.cbs.onToggle) { e.preventDefault(); this.cbs.onToggle('mb'); } return; }

        // ---- Hành động ----
        if (lk === 'e') { if (this.cbs.onExport) { e.preventDefault(); this.cbs.onExport(); } return; }
        if (lk === 'r') { if (this.cbs.onReset) { e.preventDefault(); this.cbs.onReset(); } return; }

        // ---- Bảng phím tắt ----
        if (k === '?' || (k === '/' && e.shiftKey)) { e.preventDefault(); if (this.cbs.onHelp) this.cbs.onHelp(); return; }

        // ---- Esc: đóng popup / bảng phím tắt (vẽ thì DrawingEngine tự xử lý) ----
        if (k === 'Escape') { if (this.cbs.onClosePopovers) this.cbs.onClosePopovers(); }
    }

    _pan(dir) {
        const chart = this.chartEngine.getChart();
        if (!chart) return;
        try {
            const r = chart.timeScale().getVisibleLogicalRange();
            if (!r) return;
            const span = r.to - r.from;
            const shift = Math.max(1, Math.round(span * 0.6));
            // v5.2.1 không có shiftVisibleRange → dịch cửa sổ qua setVisibleLogicalRange
            // (timeScale tự clamp theo biên dữ liệu).
            chart.timeScale().setVisibleLogicalRange({
                from: r.from + dir * shift,
                to: r.to + dir * shift,
            });
        } catch (e) { /* ignore */ }
    }

    _zoom(dir) {
        const chart = this.chartEngine.getChart();
        if (!chart) return;
        try {
            const r = chart.timeScale().getVisibleLogicalRange();
            if (!r) return;
            const span = r.to - r.from;
            const factor = dir < 0 ? 0.75 : 1.3; // zoom in → thu hẹp span
            const newSpan = Math.max(1, span * factor);
            const center = (r.from + r.to) / 2;
            chart.timeScale().setVisibleLogicalRange({
                from: center - newSpan / 2,
                to: center + newSpan / 2,
            });
        } catch (e) { /* ignore */ }
    }

    _fit() {
        const chart = this.chartEngine.getChart();
        if (!chart) return;
        try { chart.timeScale().fitContent(); } catch (e) { /* ignore */ }
    }
}
