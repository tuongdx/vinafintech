/**
 * Confluence (PHASE 5) — tìm vùng hội tụ của nhiều yếu tố kỹ thuật.
 *
 * Gom các mức giá đến từ nhiều nguồn khác nhau thành "vùng hội tụ":
 *   - S/R tự động (detectSR)
 *   - MA20 / MA50
 *   - Bollinger Bands (upper / mid / lower)
 *   - Volume Profile (POC / VAH / VAL)
 *   - Fibonacci retracement của swing gần nhất (0.236 / 0.382 / 0.5 / 0.618)
 *
 * Vùng có từ `minFactors` yếu tố khác loại trùng nhau → đáng chú ý (confluence).
 * Trả về: [{ price, low, high, factors[], count, kind }] (support/resistance).
 */
import { detectSR, computeVolumeProfile } from './analysis.js';
import { ma, bollinger } from '../indicators/calculations.js';

export function computeConfluence(candles, opts = {}) {
    const { minFactors = 2, maxZones = 6, tolPct = 0.01, swingBars = 90 } = opts;
    if (!Array.isArray(candles) || !candles.length) return [];
    const current = candles[candles.length - 1].close;

    const levels = [];

    // S/R
    for (const lv of detectSR(candles)) levels.push({ price: lv.price, name: 'S/R' });

    // MA20 / MA50
    const m20 = ma(candles, 20);
    if (m20.length) levels.push({ price: m20[m20.length - 1].value, name: 'MA20' });
    const m50 = ma(candles, 50);
    if (m50.length) levels.push({ price: m50[m50.length - 1].value, name: 'MA50' });

    // Bollinger
    const b = bollinger(candles, 20, 2);
    if (b.upper.length) levels.push({ price: b.upper[b.upper.length - 1].value, name: 'BOLL' });
    if (b.mid.length) levels.push({ price: b.mid[b.mid.length - 1].value, name: 'BOLL' });
    if (b.lower.length) levels.push({ price: b.lower[b.lower.length - 1].value, name: 'BOLL' });

    // Volume Profile
    const vp = computeVolumeProfile(candles);
    if (vp.poc) levels.push({ price: vp.poc, name: 'POC' });
    if (vp.vah) levels.push({ price: vp.vah, name: 'VAH' });
    if (vp.val) levels.push({ price: vp.val, name: 'VAL' });

    // Fibonacci retracement của swing gần nhất
    const seg = candles.slice(-swingBars);
    let hi = -Infinity, lo = Infinity;
    for (const c of seg) { if (c.high > hi) hi = c.high; if (c.low < lo) lo = c.low; }
    if (hi > lo) {
        const diff = hi - lo;
        for (const r of [0.236, 0.382, 0.5, 0.618]) {
            levels.push({ price: hi - diff * r, name: 'Fib' });
        }
    }

    if (!levels.length) return [];

    // Gom cụm theo khoảng giá (tol = tolPct của giá hiện tại)
    levels.sort((a, b) => a.price - b.price);
    const tol = current * tolPct;
    const clusters = [];
    let cur = { prices: [levels[0].price], names: new Set([levels[0].name]), min: levels[0].price, max: levels[0].price };
    for (let i = 1; i < levels.length; i++) {
        const lv = levels[i];
        if (lv.price - cur.max <= tol) {
            cur.prices.push(lv.price);
            cur.names.add(lv.name);
            cur.max = Math.max(cur.max, lv.price);
            cur.min = Math.min(cur.min, lv.price);
        } else {
            clusters.push(cur);
            cur = { prices: [lv.price], names: new Set([lv.name]), min: lv.price, max: lv.price };
        }
    }
    clusters.push(cur);

    return clusters
        .map((z) => {
            const price = z.prices.reduce((s, p) => s + p, 0) / z.prices.length;
            return {
                price,
                low: z.min,
                high: z.max,
                factors: [...z.names],
                count: z.names.size,
                kind: price <= current ? 'support' : 'resistance',
            };
        })
        .filter((z) => z.count >= minFactors)
        .sort((a, b) => b.count - a.count)
        .slice(0, maxZones)
        .sort((a, b) => a.price - b.price);
}
