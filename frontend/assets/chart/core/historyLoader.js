/**
 * HistoryLoader — đảm bảo DATASET (toàn bộ lịch sử có sẵn của mã) được tải đủ,
 * theo (symbol, timeframe). Zoom/pan chỉ đổi VISIBLE RANGE; khi người dùng kéo
 * về quá khứ hoặc zoom out chạm mép trái dữ liệu đã tải, loader tự request thêm.
 *
 * Flow:
 *   Zoom/Pan
 *     → subscribeVisibleRangeChange (debounce)
 *     → lấy visible TIME range [from, to] (getVisibleRange)
 *     → so với cache (symbol, interval): earliest / earliestBound / complete
 *     → nếu mép trái visible chạm/gần đầu dữ liệu đã tải → request vùng cũ hơn
 *       [targetFrom, cache.earliest) từ backend
 *     → merge vào cache (dedup/sort/prepend)
 *     → onDataUpdate(candles) → chart cập nhật + bảo toàn view (giữ TIME range)
 *
 * LƯU Ý LWC v5.2.1: KHÔNG dùng getVisibleLogicalRange để quyết định tải. Tọa độ
 * logical của LWC là TỶ LỆ THỜI GIAN (span khuếch đại theo gap giữa nến) chứ
 * không phải index bar — với data 1H (gap đêm/cuối tuần lớn) logical range lệch
 * rất xa số nến thực. Dùng TIME range (getVisibleRange) — bị clamp theo biên
 * data nhưng ĐÚNG ý nghĩa: vr.from ≈ cache.earliest khi chạm mép trái → tải.
 *
 * Chống lặp (#4): mỗi (symbol, interval) chỉ 1 request đang bay (busy); sau khi
 * xong tự check lại. Preload (#5): tải sớm khi mép trái còn cách đầu dữ liệu
 * < ~30% visible span. Dừng (#3): cache.earliest <= earliestBound, HOẶC request
 * không thêm nến cũ nào (EMPTY-STOP) → đánh dấu complete, tránh lặp trống vô hạn.
 *
 * Log debug: bật bằng window.__vinafintechChartDebug.debug = true (development).
 */
export class HistoryLoader {
    /**
     * @param {object} deps
     *   - provider       : DataProvider (fetchRange / getCached)
     *   - getChart       : () => LWC chart
     *   - getSymbol      : () => symbol
     *   - getInterval    : () => timeframe ('1D'|'1W'|'1M'|'1H')
     *   - onDataUpdate   : (candles) => void   (merge xong, chart đã có dữ liệu mới)
     *   - onLoading      : (loading) => void   (badge trạng thái, không khoá UI)
     *   - onError        : (msg) => void       (hiển thị lỗi + retry)
     *   - debounceMs     : số ms trễ sau mỗi lần đổi visible range (mặc định 200)
     */
    constructor(deps) {
        this.provider = deps.provider;
        this.getChart = deps.getChart;
        this.getSymbol = deps.getSymbol;
        this.getInterval = deps.getInterval;
        this.onDataUpdate = deps.onDataUpdate || null;
        this.onLoading = deps.onLoading || null;
        this.onError = deps.onError || null;
        this.debounceMs = deps.debounceMs || 200;
        this._handler = null;
        this._timer = null;
        this._busy = new Set(); // "SYMBOL:INTERVAL" đang có request
        this._destroyed = false;
    }

    start() {
        const chart = this.getChart();
        if (!chart || this._handler) return this;
        this._handler = () => this.schedule();
        // Subscribe cả TIME lẫn LOGICAL range change: một số thao tác (fitContent,
        // setVisibleRange) chỉ kích hoạt một trong hai sự kiện → subscribe cả hai
        // để chắc chắn loader luôn check sau mọi thay đổi visible range.
        try { chart.timeScale().subscribeVisibleRangeChange(this._handler); } catch (e) { /* ignore */ }
        try { chart.timeScale().subscribeVisibleLogicalRangeChange(this._handler); } catch (e) { /* ignore */ }
        return this;
    }

    schedule() {
        if (this._destroyed) return;
        if (this._timer) clearTimeout(this._timer);
        this._timer = setTimeout(() => {
            this._timer = null;
            this.check().catch(() => { /* không để lỗi làm hỏng loop */ });
        }, this.debounceMs);
    }

    /** Kiểm tra visible LOGICAL range so với cache; nếu thiếu lịch sử → request. */
    async check() {
        if (this._destroyed) return;
        const chart = this.getChart();
        if (!chart) return;
        const symbol = this.getSymbol();
        const interval = this.getInterval();
        if (!symbol || !interval) return;

        const cache = this.provider.getCached(symbol, interval);
        if (!cache || !cache.candles.length) return;
        // Đã tải tới biên lịch sử → thôi, không request nữa (chống lặp).
        if (cache.complete) return;

        const ts = chart.timeScale();
        // Dùng TIME range (getVisibleRange) — ổn định cho mọi timeframe. KHÔNG dùng
        // getVisibleLogicalRange: với LWC v5.2.1 tọa độ logical là TỶ LỆ THỜI GIAN
        // (khuếch đại theo gap nến), không phải index bar → không so được với
        // số nến thực. getVisibleRange bị clamp theo biên dữ liệu: khi người dùng
        // nhìn tới (hoặc zoom ra ngoài) mép trái dữ liệu đã tải thì vr.from ≈
        // cache.earliest → đúng điều kiện để tải thêm lịch sử cũ hơn.
        let vr = null;
        try { vr = ts.getVisibleRange(); } catch (e) { return; }
        if (!vr || vr.to <= vr.from) return;

        const n = cache.candles.length;
        const t0 = cache.candles[0].time;
        const t1 = cache.candles[n - 1].time;
        const avgSpan = n > 1 ? (t1 - t0) / (n - 1) : 86400;
        const span = vr.to - vr.from;
        // Người dùng đang xem (gần như) TOÀN BỘ lịch sử đã tải (zoom-out hết cỡ)?
        const loadedSpan = cache.latest - cache.earliest;
        const wasZoomedOut = loadedSpan > 0 && span >= loadedSpan * 0.9;
        // Preload buffer: ~30% chiều rộng visible (hoặc tối thiểu 30 ngày).
        const bufferTime = Math.max(30 * 86400, Math.floor(span * 0.3));
        // Mép trái visible còn cách đầu dữ liệu > buffer → chưa cần tải.
        if (vr.from > cache.earliest + bufferTime) return;

        // Tải thêm một khối TRƯỚC mép trái visible (có đệm 1.5x để kéo đủ).
        const targetFrom = Math.floor(vr.from - bufferTime * 1.5);
        const requestTo = cache.earliest;
        if (targetFrom >= requestTo) return;

        const busyKey = symbol + ':' + interval;
        if (this._busy.has(busyKey)) return; // đang có request cho (symbol, interval)

        this._log('Need historical data:', this._fmt(targetFrom), '→', this._fmt(requestTo));
        this._busy.add(busyKey);
        if (this.onLoading) this.onLoading(true);
        const prevEarliest = cache.earliest;
        try {
            const norm = await this.provider.fetchRange(symbol, interval, targetFrom, requestTo);
            this._log('Received:', norm.candles.length, 'candles · Cache updated');
            const updated = this.provider.getCached(symbol, interval);
            // EMPTY-STOP: nếu backend không trả thêm bar CŨ HƠN nào (earliest không
            // dịch trái) nghĩa là đã hết lịch sử cho khoảng này (VD: KBS hết data
            // 1H ở mốc cũ hơn) → đánh dấu biên để DỪNG, tránh lặp request trống vô hạn.
            if (updated && updated.earliest === prevEarliest) {
                updated.earliestBound = updated.earliest;
                updated.complete = true;
                this._log('No older data → stop loading at', this._fmt(updated.earliest));
            }
            if (this.onDataUpdate && updated) this.onDataUpdate(updated.candles);
            // Cascade toàn bộ: nếu người dùng đang zoom-out hết cỡ (nhìn toàn bộ lịch sử
            // đã tải) và vừa tải thêm dữ liệu CŨ HƠN → mở rộng khung nhìn sang biên mới
            // để hiện phần vừa tải, tự tải tiếp cho tới khi hết biên lịch sử — không cần
            // bấm zoom-out lặp lại nhiều lần để xem hết dữ liệu 1 cổ phiếu.
            if (updated && wasZoomedOut && !updated.complete &&
                updated.earliest && updated.earliest < prevEarliest) {
                try { ts.setVisibleRange({ from: updated.earliest, to: updated.latest }); }
                catch (e) { /* ignore: LWC không đặt được → dừng cascade tự nhiên */ }
            }
            // Có thể vẫn chưa đủ (vừa tải chạm biên) → kiểm tra tiếp.
            setTimeout(() => this.check().catch(() => {}), 0);
        } catch (err) {
            this._log('Error:', err && err.message ? err.message : err);
            if (this.onError) this.onError((err && err.message) || 'Không tải thêm được dữ liệu lịch sử');
        } finally {
            this._busy.delete(busyKey);
            if (this.onLoading) this.onLoading(false);
        }
    }

    _fmt(ts) {
        const d = new Date(Number(ts) * 1000);
        return isNaN(d.getTime()) ? String(ts) : d.toISOString().slice(0, 10);
    }

    _log(...args) {
        try {
            if (window && window.__vinafintechChartDebug && window.__vinafintechChartDebug.debug) {
                console.log.apply(console, ['[Chart]'].concat(args));
            }
        } catch (e) { /* ignore */ }
    }

    destroy() {
        this._destroyed = true;
        if (this._timer) { clearTimeout(this._timer); this._timer = null; }
        const chart = this.getChart();
        if (chart && this._handler) {
            try { chart.timeScale().unsubscribeVisibleRangeChange(this._handler); } catch (e) { /* ignore */ }
            try { chart.timeScale().unsubscribeVisibleLogicalRangeChange(this._handler); } catch (e) { /* ignore */ }
        }
        this._handler = null;
        this._busy.clear();
    }
}
