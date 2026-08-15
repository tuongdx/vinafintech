/**
 * ChartConfig — cấu hình & hằng số chung cho chart (không phụ thuộc UI).
 *
 * TIMEFRAME = đơn vị của một cây nến (1D/1W/1M) — ĐỘC LẬP với zoom.
 * ZOOM = số lượng nến đang hiển thị (visible range).
 * DATASET = toàn bộ lịch sử có sẵn của mã (từ biên dữ liệu → hiện tại), do
 * HistoryLoader tải dần theo (symbol, timeframe) và cache riêng.
 *
 * Lưu ý: 1H (Giờ) KHÔNG có trong danh sách — nguồn dữ liệu TuniX/Simplize chưa
 * cung cấp OHLCV intraday, nên không hiển thị lựa chọn 1 giờ (tránh chọn rồi báo lỗi).
 */

export const DEFAULT_TIMEFRAME = '1D';

// Thứ tự hiển thị trong dropdown (code). UI hiển thị tiếng Việt (TIMEFRAME_LABEL).
// KBS (backend) cung cấp đủ dữ liệu cho 1H (intraday) + 1D ~10 năm + 1W/1M.
export const TIMEFRAMES = ['1H', '1D', '1W', '1M'];

export const TIMEFRAME_LABEL = {
    '1D': 'Ngày',
    '1W': 'Tuần',
    '1M': 'Tháng',
    '1H': 'Giờ',
};

// Visible range mặc định (số ngày calendar) khi mở chart — vùng gần hiện tại.
export const TIMEFRAME_DAYS = {
    '1D': 180,
    '1W': 540,
    '1M': 730,
    '1H': 30,
};

// Số nến tải ban đầu theo timeframe. Backend KBS trả `countback` nến gần nhất
// (1D ~500 daily ≈ 2 năm, 1H ~500 nến giờ ≈ 1 tháng), loader tự tải thêm về quá khứ.
export const TIMEFRAME_COUNTBACK = {
    '1D': 500,
    '1W': 500,
    '1M': 500,
    '1H': 500,
};

/** Chuyển timeframe code → nhãn tiếng Việt. */
export function timeframeLabel(tf) {
    return TIMEFRAME_LABEL[String(tf || '').toUpperCase()] || 'Ngày';
}

/** Màu sắc theo theme (dark/light) — dùng chung cho engine + legend. */
export function themeColors(mode) {
    const dark = mode === 'dark';
    return {
        mode: dark ? 'dark' : 'light',
        background: dark ? '#0d1117' : '#ffffff',
        text: dark ? '#8b949e' : '#6b7a8f',
        grid: dark ? 'rgba(255,255,255,0.06)' : 'rgba(46,46,46,0.05)',
        border: dark ? '#30363d' : '#e2e8f0',
        up: '#26a69a',
        down: '#ef5350',
        volumeUp: 'rgba(38,166,154,0.55)',
        volumeDown: 'rgba(239,83,80,0.55)',
    };
}
