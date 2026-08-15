/**
 * IndicatorPanel — nút "Chỉ báo" + dropdown để bật/tắt & chỉnh thông số.
 * Toggle checkbox → engine.add/remove; đổi số → engine.update.
 */
import { INDICATOR_DEFS, INDICATOR_KEYS } from '../indicators/indicatorConfig.js';

export class IndicatorPanel {
    /**
     * @param {object} deps
     *   - engine (IndicatorEngine)
     */
    constructor(deps) {
        this.engine = deps.engine;
        this.wrap = null;
        this.btn = null;
        this.panel = null;
        this.checkboxes = {};
        this.inputs = {};
        this._outside = null;
    }

    create() {
        const wrap = document.createElement('div');
        wrap.className = 'chart-ind-wrap';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ind-btn';
        btn.innerHTML = '<i class="fa-solid fa-chart-line"></i> <span class="btn-label">Chỉ báo</span>';
        btn.addEventListener('click', (e) => { e.stopPropagation(); this.toggle(); });

        const panel = document.createElement('div');
        panel.className = 'indicator-panel';
        panel.style.display = 'none';

        for (const key of INDICATOR_KEYS) this._addRow(panel, key);

        wrap.appendChild(btn);
        wrap.appendChild(panel);

        this._outside = (e) => {
            if (this.wrap && !this.wrap.contains(e.target)) this.close();
        };
        document.addEventListener('pointerdown', this._outside);

        this.wrap = wrap;
        this.btn = btn;
        this.panel = panel;
        return wrap;
    }

    _addRow(panel, key) {
        const def = INDICATOR_DEFS[key];
        const row = document.createElement('label');
        row.className = 'ind-row';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'ind-cb';

        const dot = document.createElement('i');
        dot.className = 'ind-dot';
        dot.style.background = def.color;

        const name = document.createElement('span');
        name.className = 'ind-name';
        name.textContent = def.label;

        const fields = document.createElement('span');
        fields.className = 'ind-fields';
        const inputs = [];
        for (const f of def.fields) {
            const inp = document.createElement('input');
            inp.type = 'number';
            inp.className = 'ind-num';
            inp.min = f.min;
            inp.max = f.max;
            inp.step = f.step || 1;
            inp.value = f.def;
            inp.dataset.key = key;
            inp.dataset.field = f.key;
            inp.title = f.label;
            inputs.push(inp);
            fields.appendChild(inp);
        }

        row.appendChild(cb);
        row.appendChild(dot);
        row.appendChild(name);
        row.appendChild(fields);
        panel.appendChild(row);

        this.checkboxes[key] = cb;
        this.inputs[key] = inputs;

        cb.addEventListener('change', () => {
            if (cb.checked) {
                const s = {};
                for (const inp of inputs) s[inp.dataset.field] = parseFloat(inp.value) || 0;
                this.engine.add(key, s);
            } else {
                this.engine.remove(key);
            }
        });
        for (const inp of inputs) {
            inp.addEventListener('change', () => {
                if (this.engine.isEnabled(key)) {
                    this.engine.update(key, { [inp.dataset.field]: parseFloat(inp.value) || 0 });
                }
            });
        }
    }

    refresh() {
        for (const key of INDICATOR_KEYS) {
            this.checkboxes[key].checked = this.engine.isEnabled(key);
        }
    }

    toggle() { this.panel.style.display === 'none' ? this.open() : this.close(); }

    open() {
        this.refresh();
        this.panel.style.display = 'block';
    }
    close() {
        this.panel.style.display = 'none';
    }

    destroy() {
        if (this._outside) document.removeEventListener('pointerdown', this._outside);
        if (this.wrap) this.wrap.remove();
    }
}
