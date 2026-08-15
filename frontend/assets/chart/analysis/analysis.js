/**
 * S/R + Volume Profile — thuật toán (thuần, không phụ thuộc chart).
 * Input: candles [{ time, open, high, low, close, volume }].
 */

/**
 * Phát hiện kháng cự / hỗ trợ tự động (PHASE 4).
 * - Tìm pivot high/low (fractal, window = pivot).
 * - Gom cụm theo khoảng giá (clusterPct của giá trung bình).
 * - Mức = tâm cụm, strength = số pivot chạm; phân loại support/resistance
 *   theo vị trí so với giá hiện tại (dưới = support, trên = resistance).
 */
export function detectSR(candles, opts = {}) {
    const { pivot = 2, maxLevels = 8, clusterPct = 0.004 } = opts;
    if (!Array.isArray(candles) || candles.length < pivot * 2 + 1) return [];

    const pivots = [];
    for (let i = pivot; i < candles.length - pivot; i++) {
        const h = candles[i].high;
        const l = candles[i].low;
        let ph = true, pl = true;
        for (let j = i - pivot; j <= i + pivot; j++) {
            if (j === i) continue;
            if (candles[j].high >= h) ph = false;
            if (candles[j].low <= l) pl = false;
        }
        if (ph) pivots.push({ price: h, dir: 1 });
        if (pl) pivots.push({ price: l, dir: -1 });
    }
    if (!pivots.length) return [];

    const sorted = [...pivots].sort((a, b) => a.price - b.price);
    const avg = sorted.reduce((s, p) => s + p.price, 0) / sorted.length;
    const tol = avg * clusterPct;

    const clusters = [];
    let cur = { min: sorted[0].price, max: sorted[0].price, count: 1, dirSum: sorted[0].dir };
    for (let i = 1; i < sorted.length; i++) {
        const p = sorted[i];
        if (p.price - cur.max <= tol) {
            cur.max = Math.max(cur.max, p.price);
            cur.min = Math.min(cur.min, p.price);
            cur.count++;
            cur.dirSum += p.dir;
        } else {
            clusters.push(cur);
            cur = { min: p.price, max: p.price, count: 1, dirSum: p.dir };
        }
    }
    clusters.push(cur);

    const currentPrice = opts.currentPrice != null
        ? opts.currentPrice
        : (candles.length ? candles[candles.length - 1].close : 0);

    return clusters
        .map((c) => ({
            price: (c.min + c.max) / 2,
            touches: c.count,
            kind: (c.min + c.max) / 2 <= currentPrice ? 'support' : 'resistance',
        }))
        .sort((a, b) => b.touches - a.touches)
        .slice(0, maxLevels)
        .sort((a, b) => a.price - b.price);
}

/**
 * Volume Profile (PHASE 4).
 * Chia biên độ giá thành `buckets` khoang, cộng khối lượng mỗi nến phân bổ
 * đều theo số khoang nến đi qua. Trả về:
 *   { buckets: [{ priceLow, priceHigh, volume, isPoc, inVA }], poc, vah, val, total, maxVol }
 * Value Area = tập các khoang có volume cao nhất chiếm 70% tổng (mở rộng từ POC).
 */
export function computeVolumeProfile(candles, opts = {}) {
    const { buckets = 26 } = opts;
    const empty = { buckets: [], poc: null, vah: null, val: null, total: 0, maxVol: 0 };
    if (!Array.isArray(candles) || !candles.length) return empty;

    let min = Infinity, max = -Infinity;
    for (const c of candles) {
        if (c.low < min) min = c.low;
        if (c.high > max) max = c.high;
    }
    if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return empty;

    const step = (max - min) / buckets;
    const vols = new Array(buckets).fill(0);
    for (const c of candles) {
        let hi = Math.floor((c.high - min) / step);
        let lo = Math.floor((c.low - min) / step);
        hi = Math.min(buckets - 1, Math.max(0, hi));
        lo = Math.min(buckets - 1, Math.max(0, lo));
        if (lo > hi) { const t = hi; hi = lo; lo = t; }
        const per = c.volume / (hi - lo + 1);
        for (let b = lo; b <= hi; b++) vols[b] += per;
    }

    let total = 0;
    for (const v of vols) total += v;
    if (total <= 0) return empty;

    let pocIdx = 0;
    for (let i = 0; i < buckets; i++) if (vols[i] > vols[pocIdx]) pocIdx = i;

    // Value Area: thêm dần khoang có volume lớn nhất đến khi đủ 70%
    const order = vols.map((_, i) => i).sort((a, b) => vols[b] - vols[a]);
    const inVA = new Set([pocIdx]);
    let acc = vols[pocIdx];
    for (const i of order) {
        if (inVA.has(i)) continue;
        if (acc / total >= 0.7) break;
        inVA.add(i);
        acc += vols[i];
    }
    let vahIdx = pocIdx, valIdx = pocIdx;
    for (const i of inVA) {
        if (i > vahIdx) vahIdx = i;
        if (i < valIdx) valIdx = i;
    }

    return {
        buckets: vols.map((v, i) => ({
            index: i,
            priceLow: min + i * step,
            priceHigh: min + (i + 1) * step,
            volume: v,
            isPoc: i === pocIdx,
            inVA: inVA.has(i),
        })),
        poc: min + (pocIdx + 0.5) * step,
        vah: min + (vahIdx + 1) * step,
        val: min + valIdx * step,
        total,
        maxVol: vols[pocIdx],
    };
}
