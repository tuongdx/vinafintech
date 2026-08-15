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

    // Được kích hoạt khi PARENT fullscreen chính iframe này (icon nến ở header) —
    // parent có user activation nên 1 click là vào fullscreen trên MỌI trình duyệt.
    isParentDriven() {
        return !!(window.parent && window.parent !== window &&
            window.parent.document &&
            ((window.parent.document.fullscreenElement || window.parent.document.webkitFullscreenElement) === window.frameElement));
    }

    isFullscreen() {
        return this.isActive() || this.isParentDriven();
    }

    toggle() {
        if (this.isParentDriven()) {
            // Đang ở chế độ parent fullscreen iframe này → thoát fullscreen parent (như Esc).
            const exit = window.parent.document.exitFullscreen || window.parent.document.webkitExitFullscreen;
            if (exit) { try { exit.call(window.parent.document); } catch (e) {} }
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
