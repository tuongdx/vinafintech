/**
 * LastPriceLine — đường giá mới nhất (PHASE 25).
 *
 * Dùng price line native của Lightweight Charts (createPriceLine) trên series
 * nến: đường nét đứt tại giá đóng cửa nến cuối + nhãn giá trên trục phải.
 * Màu: xanh (tăng) / đỏ (giảm) theo so sánh close cuối vs nến trước.
 * Cập nhật mỗi khi có dữ liệu mới (setData / đổi kỳ hạn).
 */
export class LastPriceLine {
    /**
     * @param {object} deps
     *   - chartEngine (có getCandleSeries/getBars)
     */
    constructor(deps) {
        this.chartEngine = deps.chartEngine;
        this.line = null;
    }

    create() {
        const series = this.chartEngine.getCandleSeries();
        if (!series || this.line) return;
        try {
            this.line = series.createPriceLine({
                price: 0,
                color: '#26a69a',
                lineWidth: 1,
                lineStyle: 2, // dashed
                axisLabelVisible: true,
                title: '',
            });
        } catch (e) {
            this.line = null;
        }
        this.update();
        return this;
    }

    update() {
        const series = this.chartEngine.getCandleSeries();
        const bars = this.chartEngine.getBars ? this.chartEngine.getBars() : [];
        if (!series || !this.line || !bars.length) return;
        const last = bars[bars.length - 1];
        const prev = bars[bars.length - 2];
        const up = prev ? Number(last.close) >= Number(prev.close) : true;
        const color = up ? '#26a69a' : '#ef5350';
        try { this.line.applyOptions({ price: Number(last.close), color }); } catch (e) { /* ignore */ }
    }

    destroy() {
        const series = this.chartEngine.getCandleSeries();
        if (series && this.line) {
            try { series.removePriceLine(this.line); } catch (e) { /* ignore */ }
        }
        this.line = null;
    }
}
