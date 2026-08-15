/**
 * MarketBreadth (PHASE 5) — panel "Độ rộng thị trường".
 * Lấy dữ liệu các chỉ số chính (vnindex/vn30/hnx/upcom) từ backend
 * (/api/stocks/index-detail), tính:
 *   - % chỉ số đóng trên MA20 (breadth) → gauge.
 *   - Từng chỉ số: giá, % thay đổi, trạng thái >/< MA20.
 * Lazy-load lần đầu mở; đóng/mở qua nút "Thị trường" ở thanh phân tích.
 */
export class MarketBreadth {
    /**
     * @param {object} deps
     *   - apiBase: 'string' mặc định '/api'
     *   - indices: [{key, label}]
     */
    constructor(deps = {}) {
        this.apiBase = deps.apiBase || '/api';
        this.indices = deps.indices || [
            { key: 'vnindex', label: 'VN-Index' },
            { key: 'vn30', label: 'VN30' },
            { key: 'hnx', label: 'HNX' },
            { key: 'upcom', label: 'UPCOM' },
        ];
        this.el = null;
        this.loaded = false;
        this.loading = false;
        this.visible = false;
    }

    create() {
        const el = document.createElement('div');
        el.className = 'market-breadth';
        el.style.display = 'none';
        el.innerHTML =
            '<div class="mb-head">' +
            '<i class="fa-solid fa-chart-pie"></i> Độ rộng thị trường' +
            '<span class="mb-gauge">--</span>' +
            '<button type="button" class="mb-close" title="Đóng"><i class="fa-solid fa-xmark"></i></button>' +
            '</div>' +
            '<div class="mb-body"><div class="mb-loading">Đang tải dữ liệu...</div></div>';
        el.querySelector('.mb-close').addEventListener('click', () => this.hide());
        this.el = el;
        return el;
    }

    async show() {
        this.visible = true;
        if (this.el) this.el.style.display = 'block';
        if (!this.loaded && !this.loading) {
            this.loading = true;
            try { await this.load(); } catch (e) { /* handled trong load */ } finally { this.loading = false; }
        }
    }

    hide() {
        this.visible = false;
        if (this.el) this.el.style.display = 'none';
    }

    toggle() { this.visible ? this.hide() : this.show(); }

    async load() {
        const body = this.el.querySelector('.mb-body');
        try {
            const results = await Promise.all(this.indices.map(async (idx) => {
                try {
                    const res = await fetch(this.apiBase + '/stocks/index-detail?key=' + encodeURIComponent(idx.key) + '&period=1y');
                    if (!res.ok) return { ...idx, data: null, error: true };
                    const data = await res.json();
                    return { ...idx, data, error: false };
                } catch (e) {
                    return { ...idx, data: null, error: true };
                }
            }));
            this._render(results);
            this.loaded = true;
        } catch (e) {
            if (body) body.innerHTML = '<div class="mb-error">Không tải được dữ liệu thị trường.</div>';
        }
    }

    _render(results) {
        const body = this.el.querySelector('.mb-body');
        const rows = [];
        let above = 0, valid = 0;
        for (const r of results) {
            const d = r.data;
            const closes = d && Array.isArray(d.history)
                ? d.history.map((p) => Number(p && p.close)).filter(Number.isFinite)
                : [];
            let above20 = null;
            if (closes.length >= 20) {
                const avg = closes.slice(-20).reduce((s, c) => s + c, 0) / 20;
                above20 = closes[closes.length - 1] > avg;
            }
            if (above20 !== null) { valid++; if (above20) above++; }
            const pct = d && d.percent_change != null ? Number(d.percent_change) : null;
            rows.push({
                label: r.label,
                value: d ? d.value : null,
                pct,
                above20,
                error: !!r.error,
            });
        }
        const breadth = valid ? Math.round((above / valid) * 100) : null;
        const gauge = this.el.querySelector('.mb-gauge');
        if (gauge) {
            gauge.textContent = breadth != null ? breadth + '%' : '--';
            gauge.classList.remove('up', 'down', 'flat');
            if (breadth != null) gauge.classList.add(breadth >= 60 ? 'up' : breadth <= 40 ? 'down' : 'flat');
        }
        const fmt = (v) => (v != null ? Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }) : '--');
        body.innerHTML = '<div class="mb-rows">' + rows.map((r) => {
            const chg = r.pct != null ? (r.pct >= 0 ? '+' : '') + r.pct.toFixed(2) + '%' : '';
            const chgCls = r.pct == null ? '' : r.pct >= 0 ? 'up' : 'down';
            const ma = r.above20 === null ? '<span class="mb-ma">--</span>'
                : '<span class="mb-ma ' + (r.above20 ? 'up' : 'down') + '">' + (r.above20 ? '&gt;MA20' : '&lt;MA20') + '</span>';
            const dotCls = r.above20 === null ? '' : r.above20 ? 'up' : 'down';
            return '<div class="mb-row">' +
                '<span class="mb-dot ' + dotCls + '"></span>' +
                '<span class="mb-name">' + r.label + '</span>' +
                '<span class="mb-val">' + fmt(r.value) + '</span>' +
                '<span class="mb-chg ' + chgCls + '">' + chg + '</span>' +
                ma +
                '</div>';
        }).join('') + '</div>';
    }

    destroy() { if (this.el) this.el.remove(); }
}
