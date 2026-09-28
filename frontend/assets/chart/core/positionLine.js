/**
 * PositionLine — "VẠCH VỊ THẾ" trên biểu đồ.
 *
 * Nếu người dùng đang đăng nhập và CÓ vị thế ở mã đang xem (danh mục đầu tư), module vẽ:
 *   1. Một đường giá vốn (avg_price ĐÃ điều chỉnh cổ tức/chia tách — cùng số liệu với trang
 *      Quản lý tài sản) bằng price line NATIVE của Lightweight Charts ⇒ tự hiển thị ở cả
 *      chế độ thường, chế độ toàn màn hình (Fullscreen API) và khi parent fullscreen iframe.
 *      Màu: xanh khi giá hiện tại ≥ giá vốn, đỏ khi đang dưới giá vốn.
 *   2. Badge thông tin vị thế (số lượng · giá vốn · % lãi/lỗ theo giá đóng cửa nến cuối).
 *
 * Không đăng nhập / không có vị thế ⇒ không vẽ gì (không hiện vạch, không hiện badge).
 * Dữ liệu lấy từ GET /api/portfolio/position/{symbol} và cache trong RAM 60s.
 */
const TTL_MS = 60000;
const TOKEN_KEY = 'tunix_access_token';

export class PositionLine {
    /**
     * @param {object} deps
     *   - chartEngine: có getCandleSeries()/getBars()
     *   - badgeEl: phần tử HTML hiển thị thông tin vị thế (có thể null)
     *   - getSymbol: () => mã đang xem
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.badgeEl = deps.badgeEl || null;
        this.getSymbol = typeof deps.getSymbol === 'function' ? deps.getSymbol : () => '';
        this.line = null;        // price line native
        this.lineSeries = null;  // series mà `line` đang gắn vào (phát hiện series bị tạo lại)
        this.data = null;        // dữ liệu vị thế (đã chuẩn hoá) hoặc null
        this.symbol = '';        // mã của `data`
        this.fetchedAt = 0;
        this.seq = 0;            // chống race khi đổi mã liên tục
    }

    static token() {
        try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
    }

    // ---------- Nạp dữ liệu vị thế (cache 60s theo mã) ----------
    async ensure(symbol) {
        const sym = String(symbol || this.getSymbol() || '').trim().toUpperCase();
        if (!sym) return;
        if (this.symbol === sym && (Date.now() - this.fetchedAt) < TTL_MS) {
            this.render();
            return;
        }
        const token = PositionLine.token();
        if (!token) {
            // Chưa đăng nhập → không có vị thế nào để vẽ.
            this.symbol = sym;
            this.fetchedAt = Date.now();
            this.data = null;
            this.clear();
            return;
        }
        const seq = ++this.seq;
        let payload = null;
        try {
            const res = await fetch('/api/portfolio/position/' + encodeURIComponent(sym), {
                headers: { Authorization: 'Bearer ' + token },
                cache: 'no-store',
            });
            if (res.status === 401) {   // phiên hết hạn → coi như không có vị thế
                if (seq === this.seq) { this.symbol = sym; this.fetchedAt = Date.now(); this.data = null; this.clear(); }
                return;
            }
            payload = res.ok ? await res.json() : null;
        } catch (e) {
            payload = null;
        }
        if (seq !== this.seq) return;   // đã đổi mã trong lúc chờ
        this.symbol = sym;
        this.fetchedAt = Date.now();
        this.data = (payload && payload.has_position) ? payload : null;
        if (this.data) this.render(); else this.clear();
    }

    // Bỏ cache của mã hiện tại rồi nạp lại (dùng khi vị thế vừa được sửa ở nơi khác).
    async reload(symbol) {
        this.fetchedAt = 0;
        await this.ensure(symbol || this.getSymbol());
    }

    _bars() {
        return (this.chartEngine && this.chartEngine.getBars) ? this.chartEngine.getBars() : [];
    }

    _lastClose() {
        const bars = this._bars();
        if (!bars || !bars.length) return 0;
        return Number(bars[bars.length - 1].close) || 0;
    }

    _fmt(n, d) {
        const v = Number(n) || 0;
        return v.toLocaleString('vi-VN', { maximumFractionDigits: d == null ? 0 : d });
    }

    // ---------- Vẽ / cập nhật đường giá vốn + badge ----------
    render() {
        const series = this.chartEngine ? this.chartEngine.getCandleSeries() : null;
        const d = this.data;
        const avg = d ? (Number(d.avg_price) || 0) : 0;
        if (!series || !d || !(avg > 0)) {
            this.removeLine();
            this.hideBadge();
            return;
        }
        const close = this._lastClose();
        const up = !close || close >= avg;      // chưa có giá → mặc định xanh (trung tính hơn đỏ)
        const color = up ? '#26a69a' : '#ef5350';

        // Series bị tạo lại (đổi theme / khởi tạo lại chart) → line cũ không còn thuộc series này.
        if (this.line && this.lineSeries !== series) this.removeLine();

        if (!this.line) {
            try {
                this.line = series.createPriceLine({
                    price: avg,
                    color: color,
                    lineWidth: 1,               // mảnh — không lấn át nến/đường giá mới nhất
                    lineStyle: 3,               // NÉT ĐỨT DÀI (LargeDashed) — vẫn là nét đứt nhưng phân biệt
                                                // được với đường "giá mới nhất" (nét đứt ngắn, style 2)
                    axisLabelVisible: true,
                    title: 'Vị thế',
                });
                this.lineSeries = series;
            } catch (e) {
                this.line = null;
                this.lineSeries = null;
            }
        } else {
            try { this.line.applyOptions({ price: avg, color: color, axisLabelVisible: true, title: 'Vị thế' }); } catch (e) { /* ignore */ }
        }
        this.renderBadge(avg, close, color);
    }

    renderBadge(avg, close, color) {
        if (!this.badgeEl) return;
        const d = this.data || {};
        const qty = Number(d.quantity) || 0;
        const pct = close > 0 && avg > 0 ? ((close - avg) / avg * 100) : null;
        const sign = pct == null ? '' : (pct >= 0 ? '+' : '');
        const pctTxt = pct == null ? '--' : sign + pct.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + '%';
        const pendingShares = Number(d.pending_shares) || 0;
        const pendingCash = Number(d.pending_cash) || 0;
        let pending = '';
        if (pendingShares > 0 || pendingCash > 0) {
            const bits = [];
            if (pendingShares > 0) bits.push('+' + this._fmt(pendingShares) + ' cp');
            if (pendingCash > 0) bits.push('+' + this._fmt(pendingCash) + ' đ');
            pending = '<span class="cpb-pend" title="Cổ tức đã chốt quyền, chưa tới ngày chia">'
                + '<i class="fa-solid fa-clock"></i> Chờ nhận ' + bits.join(' · ') + '</span>';
        }
        const title = 'Vị thế của bạn ở ' + (d.symbol || this.symbol) + ': ' + this._fmt(qty) + ' cp'
            + ' · giá vốn ' + this._fmt(avg) + ' đ'
            + (close > 0 ? ' · giá hiện tại ' + this._fmt(close) + ' đ (' + pctTxt + ')' : '')
            + (pendingShares > 0 || pendingCash > 0 ? ' · cổ tức chờ nhận (chưa vào số lượng)' : '');
        this.badgeEl.innerHTML =
            '<span class="cpb-dot" style="background:' + color + '"></span>'
            + '<span class="cpb-label">Vị thế</span>'
            + '<span class="cpb-qty">' + this._fmt(qty) + ' cp</span>'
            + '<span class="cpb-sep">·</span>'
            + '<span class="cpb-avg">Giá vốn <b>' + this._fmt(avg) + '</b></span>'
            + '<span class="cpb-sep">·</span>'
            + '<span class="cpb-pct" style="color:' + color + '">' + pctTxt + '</span>'
            + pending;
        this.badgeEl.title = title;
        this.badgeEl.style.display = 'inline-flex';
    }

    hideBadge() {
        if (this.badgeEl) {
            this.badgeEl.style.display = 'none';
            this.badgeEl.innerHTML = '';
            this.badgeEl.removeAttribute('title');
        }
    }

    removeLine() {
        const series = this.chartEngine ? this.chartEngine.getCandleSeries() : null;
        const target = this.lineSeries || series;
        if (target && this.line) {
            try { target.removePriceLine(this.line); } catch (e) { /* ignore */ }
        }
        this.line = null;
        this.lineSeries = null;
    }

    // Không có vị thế → xoá sạch (vạch + badge).
    clear() {
        this.removeLine();
        this.hideBadge();
    }

    destroy() {
        this.seq++;
        this.data = null;
        this.clear();
    }
}
