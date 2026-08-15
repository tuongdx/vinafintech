/**
 * SettingsPanel (PHASE 23) — nút "Cài đặt" + popover bật/tắt các tuỳ chọn hiển thị.
 * Các mục: Lưới · Nam châm · Chú thích · Đồng bộ crosshair · Khối lượng · Thanh công cụ vẽ.
 * Giao tiếp qua onChange(key, on); trạng thái do controller quản lý (persist).
 */
function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export const SETTING_ITEMS = [
    { key: 'grid', label: 'Lưới', desc: 'Đường lưới ngang/dọc' },
    { key: 'magnet', label: 'Nam châm', desc: 'Crosshair bám vào giá' },
    { key: 'legend', label: 'Chú thích', desc: 'OHLC + Volume trên chart' },
    { key: 'sync', label: 'Đồng bộ crosshair', desc: 'Đường ngang ở các sub-pane' },
    { key: 'volume', label: 'Khối lượng', desc: 'Histogram volume' },
    { key: 'toolbar', label: 'Thanh công cụ vẽ', desc: 'Bút vẽ / thước đo' },
];

export class SettingsPanel {
    /**
     * @param {object} deps
     *   - onChange (key, on) => void
     *   - state: { grid, magnet, legend, sync, volume, toolbar }
     */
    constructor(deps) {
        this.onChange = deps.onChange || null;
        this.state = Object.assign({}, deps.state || {});
        this.wrap = null;
        this.btn = null;
        this.pop = null;
        this.rows = {};
        this._outside = null;
    }

    create() {
        const wrap = document.createElement('div');
        wrap.className = 'chart-set-wrap';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'an-btn set-btn';
        btn.innerHTML = '<i class="fa-solid fa-gear"></i>';
        btn.addEventListener('click', (e) => { e.stopPropagation(); this.toggle(); });

        const pop = document.createElement('div');
        pop.className = 'set-pop';
        pop.style.display = 'none';

        const title = document.createElement('div');
        title.className = 'set-title';
        title.textContent = 'Cài đặt biểu đồ';
        pop.appendChild(title);

        for (const item of SETTING_ITEMS) {
            const row = document.createElement('label');
            row.className = 'set-row';
            const on = !!this.state[item.key];

            const info = document.createElement('span');
            info.className = 'set-info';
            info.innerHTML = '<span class="set-name">' + esc(item.label) + '</span>' +
                (item.desc ? '<span class="set-desc">' + esc(item.desc) + '</span>' : '');

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'set-cb';
            cb.checked = on;
            cb.addEventListener('change', () => {
                this.state[item.key] = cb.checked;
                if (this.onChange) this.onChange(item.key, cb.checked);
            });

            row.appendChild(info);
            row.appendChild(cb);
            pop.appendChild(row);
            this.rows[item.key] = cb;
        }

        wrap.appendChild(btn);
        wrap.appendChild(pop);
        this.wrap = wrap;
        this.btn = btn;
        this.pop = pop;

        // Đóng khi click ra ngoài
        this._outside = (e) => { if (this.wrap && !this.wrap.contains(e.target)) this.close(); };
        document.addEventListener('click', this._outside);
        return wrap;
    }

    /** Cập nhật trạng thái từ ngoài (restore) rồi đồng bộ checkbox. */
    setState(state) {
        this.state = Object.assign({}, state || {});
        for (const key of Object.keys(this.rows)) {
            this.rows[key].checked = !!this.state[key];
        }
    }

    toggle() { this.pop.style.display === 'none' ? this.open() : this.close(); }
    open() { if (this.pop) this.pop.style.display = 'block'; }
    close() { if (this.pop) this.pop.style.display = 'none'; }

    destroy() {
        if (this._outside) { document.removeEventListener('click', this._outside); this._outside = null; }
        if (this.wrap) { try { this.wrap.remove(); } catch (e) { /* ignore */ } this.wrap = null; }
        this.pop = null;
        this.btn = null;
        this.rows = {};
    }
}
