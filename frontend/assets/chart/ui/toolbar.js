/**
 * Toolbar — thanh công cụ drawing bên trái chart.
 * Buttons: Crosshair, Trendline, Horizontal, Vertical, Channel, Fib, Text, Marker, Measure,
 * Magnet, Lock, Hide, Delete, Undo, Redo.
 */
import { TOOL_DEFS, TOOL_TYPES } from '../drawings/shapes.js';

export class ChartToolbar {
    /**
     * @param {object} deps
     *   - engine   (DrawingEngine)
     *   - manager  (DrawingManager)
     */
    constructor(deps) {
        this.engine = deps.engine;
        this.manager = deps.manager;
        this.el = null;
        this.buttons = {};
    }

    create() {
        const el = document.createElement('div');
        el.className = 'chart-toolbar';

        // --- crosshair (chế độ chuột thường: chọn/pan/zoom) ---
        el.appendChild(this._toolBtn('crosshair'));
        el.appendChild(this._divider());

        // --- tools (tạo drawing) ---
        for (const t of TOOL_TYPES) {
            el.appendChild(this._toolBtn(t));
        }
        el.appendChild(this._divider());

        // --- actions ---
        el.appendChild(this._actionBtn('magnet', 'fa-magnet', 'Từ tính (snap vào OHLC)', () => {
            this.engine.setMagnet(!this.engine.magnet);
            this._toggleActive('magnet', this.engine.magnet);
        }));
        el.appendChild(this._actionBtn('lock', 'fa-lock', 'Khóa / mở khóa (chống kéo)', () => this.engine.lockSelected()));
        el.appendChild(this._actionBtn('hide', 'fa-eye-slash', 'Ẩn drawing đang chọn', () => this.engine.hideSelected()));
        el.appendChild(this._actionBtn('delete', 'fa-trash', 'Xóa drawing đang chọn (Delete)', () => this.engine.deleteSelected()));
        el.appendChild(this._divider());
        el.appendChild(this._actionBtn('undo', 'fa-undo', 'Hoàn tác (Ctrl+Z)', () => this.engine.undo()));
        el.appendChild(this._actionBtn('redo', 'fa-redo', 'Làm lại (Ctrl+Y)', () => this.engine.redo()));

        this.el = el;
        this._refresh();
        return el;
    }

    _toolBtn(type) {
        const def = TOOL_DEFS[type];
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ct-btn ct-tool' + (type === 'trendline' ? ' ct-trendline' : '');
        btn.title = def.label + (def.showPoints === false ? '' : (def.minPoints ? ' (' + def.minPoints + ' điểm)' : ''));
        btn.dataset.tool = type;
        if (def.icon === 'text') {
            btn.innerHTML = '<span class="ct-fib-text">' + def.text + '</span>';
        } else {
            btn.innerHTML = '<i class="fa-solid ' + def.icon + '"' + (def.rotate ? ' style="transform:rotate(' + def.rotate + 'deg)"' : '') + '></i>';
        }
        btn.addEventListener('click', () => {
            if (type === 'trendline') {
                // click lại để mở menu chọn loại? — giữ đơn giản: chọn trendline
            }
            this.engine.setTool(type);
            this._refresh();
        });
        this.buttons[type] = btn;
        return btn;
    }

    _actionBtn(key, icon, title, fn) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ct-btn ct-action';
        btn.title = title;
        btn.dataset.key = key;
        btn.innerHTML = '<i class="fa-solid ' + icon + '"></i>';
        btn.addEventListener('click', fn);
        this.buttons[key] = btn;
        return btn;
    }

    _divider() {
        const d = document.createElement('div');
        d.className = 'ct-divider';
        return d;
    }

    _toggleActive(key, on) {
        const b = this.buttons[key];
        if (b) b.classList.toggle('ct-active', on);
    }

    _refresh() {
        const tool = this.engine.getTool();
        for (const t of ['crosshair', ...TOOL_TYPES]) {
            this._toggleActive(t, tool === t);
        }
    }

    destroy() { if (this.el) this.el.remove(); }
}
