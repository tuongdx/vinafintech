/**
 * Legend — hiển thị OHLC + Volume + giá trị chỉ báo (tooltip) trên chart.
 * Dùng CSS class có sẵn: .cl-symbol / .cl-up / .cl-down / .cl-vol / .cl-ind.
 */
export class Legend {
    constructor(rootEl) {
        this.root = rootEl;
    }

    update(symbol, bar, volume, inds) {
        if (!this.root) return;
        if (!bar) {
            this.root.innerHTML = '';
            return;
        }
        const fmt = (n) => Number(n).toLocaleString('en-US');
        const fmtInd = (v) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
        const up = Number(bar.close) >= Number(bar.open);
        let html =
            '<span class="cl-symbol">' + escapeHtml(symbol) + '</span>' +
            '<span>O <b>' + fmt(bar.open) + '</b></span>' +
            '<span>H <b>' + fmt(bar.high) + '</b></span>' +
            '<span>L <b>' + fmt(bar.low) + '</b></span>' +
            '<span>C <b class="' + (up ? 'cl-up' : 'cl-down') + '">' + fmt(bar.close) + '</b></span>' +
            '<span class="cl-vol">Vol <b>' + fmt(volume || 0) + '</b></span>';
        if (Array.isArray(inds) && inds.length) {
            html += inds.map((i) => {
                const c = i.color || '#888';
                return '<span class="cl-ind"><i class="cl-dot" style="background:' + c + '"></i>' +
                    escapeHtml(i.label) + ' <b>' + fmtInd(i.value) + '</b></span>';
            }).join('');
        }
        this.root.innerHTML = html;
    }

    clear() {
        if (this.root) this.root.innerHTML = '';
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
