/**
 * AnalysisPanel (PHASE 12) — dropdown "Phân tích" thay cho 4 nút chip cũ.
 * Các mục: S/R · Volume Profile · Hội tụ (engine.toggle) + Thị trường (onAction).
 * API giữ nguyên: setActive(key, on), refresh(), onAction(key) — controller không đổi.
 */
export class AnalysisPanel {
    /**
     * @param {object} deps
     *   - engine   (AnalysisEngine)
     *   - onAction (key) => void   cho nút không thuộc overlay (vd 'mb')
     */
    constructor(deps) {
        this.engine = deps.engine;
        this.onAction = deps.onAction || null;
        this.overlayKeys = ['sr', 'vp', 'cf'];
        this.rows = [
            { key: 'sr', label: 'S/R', desc: 'Kháng cự / Hỗ trợ' },
            { key: 'vp', label: 'Volume Profile', desc: 'Khối lượng theo giá' },
            { key: 'cf', label: 'Hội tụ', desc: 'Vùng hội tụ đa yếu tố' },
            { key: 'mb', label: 'Thị trường', desc: 'Độ rộng thị trường' },
        ];
        this.wrap = null;
        this.btn = null;
        this.panel = null;
        this.checkboxes = {};
        this._outside = null;
    }

    create() {
        const wrap = document.createElement('div');
        wrap.className = 'an-drop';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ind-btn an-btn';
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span class="btn-label">Phân tích</span>';
        btn.addEventListener('click', (e) => { e.stopPropagation(); this.toggle(); });

        const panel = document.createElement('div');
        panel.className = 'indicator-panel an-panel';
        panel.style.display = 'none';
        for (const r of this.rows) this._addRow(panel, r);

        wrap.appendChild(btn);
        wrap.appendChild(panel);

        this.wrap = wrap;
        this.btn = btn;
        this.panel = panel;
        this._outside = (e) => { if (this.wrap && !this.wrap.contains(e.target)) this.close(); };
        document.addEventListener('pointerdown', this._outside);
        this.refresh();
        return wrap;
    }

    _addRow(panel, r) {
        const row = document.createElement('label');
        row.className = 'ind-row';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'ind-cb';
        const name = document.createElement('span');
        name.className = 'ind-name';
        name.textContent = r.label;
        row.appendChild(cb);
        row.appendChild(name);
        if (r.desc) {
            const d = document.createElement('span');
            d.className = 'ind-desc';
            d.textContent = r.desc;
            row.appendChild(d);
        }
        cb.addEventListener('change', () => {
            if (this.overlayKeys.indexOf(r.key) !== -1) {
                cb.checked = this.engine.toggle(r.key);
            } else if (this.onAction) {
                this.onAction(r.key);
            }
        });
        this.checkboxes[r.key] = cb;
        panel.appendChild(row);
    }

    setActive(key, on) {
        if (this.checkboxes[key]) this.checkboxes[key].checked = !!on;
    }

    refresh() {
        for (const k of this.overlayKeys) {
            if (this.checkboxes[k]) this.checkboxes[k].checked = this.engine.isEnabled(k);
        }
    }

    toggle() { this.panel.style.display === 'none' ? this.open() : this.close(); }
    open() {
        this.refresh();
        this.panel.style.display = 'block';
    }
    close() { if (this.panel) this.panel.style.display = 'none'; }

    destroy() {
        if (this._outside) document.removeEventListener('pointerdown', this._outside);
        if (this.wrap) this.wrap.remove();
    }
}
