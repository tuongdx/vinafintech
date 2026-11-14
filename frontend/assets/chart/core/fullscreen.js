/**
 * FullscreenManager — toàn màn hình biểu đồ (PHASE 22).
 *
 * Dùng Fullscreen API trên document của iframe (cần iframe cha có
 * allow="fullscreen"). Khi vào/ra toàn màn hình → bật/tắt class
 * `chart-fullscreen` trên body để CSS StockDetail dựng chart-block
 * phủ kín màn hình (position:fixed; inset:0).
 *
 * Khi không hỗ trợ (documentElement.requestFullscreen thiếu) → toggle no-op.
 */
export class FullscreenManager {
    /**
     * @param {object} deps
     *   - onChange (active) => void  (cập nhật icon nút)
     */
    constructor(deps) {
        this.onChange = deps.onChange || null;
        this._onFsChange = () => this._handleChange();
        this._cleanup = [];
        this.active = false;
    }

    create() {
        document.addEventListener('fullscreenchange', this._onFsChange);
        this._cleanup.push(() => document.removeEventListener('fullscreenchange', this._onFsChange));
        // Nếu đang fullscreen sẵn khi khởi tạo (hiếm) → đồng bộ class ngay.
        this._handleChange();
        return this;
    }

    isSupported() {
        return !!(document.documentElement &&
            typeof document.documentElement.requestFullscreen === 'function');
    }

    isActive() {
        return !!(document.fullscreenElement || document.webkitFullscreenElement);
    }

    // Được kích hoạt khi PARENT fullscreen iframe này HOẶC khung chứa iframe này (#frameHost —
    // shell dùng khung cố định để bấm 1 lần là vào đúng biểu đồ, xem index.html `openChartFullscreen`).
    isParentDriven() {
        if (!(window.parent && window.parent !== window && window.parent.document)) return false;
        let pe = null;
        try {
            const pd = window.parent.document;
            pe = pd.fullscreenElement || pd.webkitFullscreenElement;
        } catch (e) { return false; }
        if (!pe) return false;
        if (pe === window.frameElement) return true;
        try { return !!(pe.contains && window.frameElement && pe.contains(window.frameElement)); } catch (e) { return false; }
    }

    isFullscreen() {
        return this.isActive() || this.isParentDriven();
    }

    // Đồng bộ lại trạng thái + icon khi fullscreen do PARENT bật/thoát (document của iframe này
    // KHÔNG nhận được sự kiện fullscreenchange trong trường hợp đó).
    refresh() {
        this._handleChange();
        return this;
    }

    toggle() {
        if (this.isParentDriven()) {
            // Đang ở chế độ parent fullscreen (iframe này hoặc khung chứa nó) → thoát đúng như
            // nhấn Esc / nút X của trình duyệt: nhờ shell thoát (shell là bên đã bật fullscreen),
            // đồng thời gọi trực tiếp (cùng origin) để chắc chắn chạy cả khi message bị chặn.
            try { window.parent.postMessage({ type: 'vinafintech-exit-fullscreen' }, '*'); } catch (e) { /* ignore */ }
            try {
                const pd = window.parent.document;
                const exit = pd.exitFullscreen || pd.webkitExitFullscreen;
                if (exit) exit.call(pd);
            } catch (e) { /* ignore */ }
            return;
        }
        if (!this.isSupported()) return;
        if (this.isActive()) {
            const exit = document.exitFullscreen || document.webkitExitFullscreen;
            if (exit) { try { exit.call(document); } catch (e) {} }
        } else {
            const req = document.documentElement.requestFullscreen ||
                document.documentElement.webkitRequestFullscreen;
            if (req) {
                try {
                    const p = req.call(document.documentElement);
                    if (p && typeof p.catch === 'function') p.catch(() => { /* ignore */ });
                } catch (e) { /* ignore */ }
            }
        }
    }

    _handleChange() {
        this.active = this.isActive();
        // Parent đang fullscreen iframe này → luôn giữ class chart-fullscreen
        // (nếu không FullscreenManager sẽ gỡ class khi document iframe không tự fullscreen).
        const on = this.isParentDriven() || this.active;
        try { document.body.classList.toggle('chart-fullscreen', on); } catch (e) { /* ignore */ }
        if (this.onChange) { try { this.onChange(on); } catch (e) { /* ignore */ } }
    }

    destroy() {
        for (const fn of this._cleanup) { try { fn(); } catch (e) { /* ignore */ } }
        this._cleanup = [];
        try { document.body.classList.remove('chart-fullscreen'); } catch (e) { /* ignore */ }
    }
}
