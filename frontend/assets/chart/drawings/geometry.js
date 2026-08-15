/**
 * Geometry — toán hình học cho drawing engine (line, channel, fibonacci, measure).
 * Tất cả tính bằng pixel (đã map từ time/price) — KHÔNG lưu bằng pixel.
 */

/** Khoảng cách từ điểm (px,py) tới đoạn thẳng (x1,y1)-(x2,y2). */
export function pointSegDist(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.hypot(px - x1, py - y1);
    let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
    const cx = x1 + t * dx;
    const cy = y1 + t * dy;
    return Math.hypot(px - cx, py - cy);
}

/** y trên đường thẳng A-B tại x (nội/ngoại suy). */
export function lineYAt(Ax, Ay, Bx, By, x) {
    if (Bx === Ax) return Ay;
    return Ay + ((x - Ax) / (Bx - Ax)) * (By - Ay);
}

/** x trên đường thẳng A-B tại y. */
export function lineXAt(Ax, Ay, Bx, By, y) {
    if (By === Ay) return Ax;
    return Ax + ((y - Ay) / (By - Ay)) * (Bx - Ax);
}

/** Mức Fibonacci Retracement giữa 2 giá (start/end). */
export function fibonacciRetracementLevels(startPrice, endPrice) {
    const levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618];
    const high = Math.max(startPrice, endPrice);
    const low = Math.min(startPrice, endPrice);
    const diff = high - low;
    return levels.map((l) => ({ level: l, price: high - diff * l }));
}

/** Mức Fibonacci Extension từ swing A->B, chiếu từ C. */
export function fibonacciExtensionLevels(a, b, c) {
    const levels = [1, 1.272, 1.618, 2, 2.618];
    const dist = b - a;
    return levels.map((l) => ({ level: l, price: c + dist * l }));
}

/** Đo lường giữa 2 điểm: chênh lệch, %, số nến, thời gian. */
export function measureBetween(A, B, candles) {
    const diff = B.price - A.price;
    const pct = A.price ? (diff / A.price) * 100 : 0;
    const durationSec = Math.abs(B.time - A.time);
    const days = durationSec / 86400;
    let candleCount = 0;
    const t0 = Math.min(A.time, B.time);
    const t1 = Math.max(A.time, B.time);
    for (const c of candles) {
        if (c.time >= t0 && c.time <= t1) candleCount++;
    }
    return { diff, pct, durationSec, days, candleCount };
}
