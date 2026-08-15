/**
 * Indicator calculations — toán học chỉ báo kỹ thuật (thuần, không phụ thuộc chart).
 * Input: candles [{ time, open, high, low, close, volume }] (sort theo time).
 * Output: mảng { time, value } (đã bỏ phần warmup) — Lightweight Charts line
 * series không yêu cầu liền mạch, chỉ vẽ điểm có giá trị.
 */

/** SMA (Simple Moving Average) của close. */
export function ma(candles, period) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
        sum += candles[i].close;
        if (i >= period) sum -= candles[i - period].close;
        if (i >= period - 1) out.push({ time: candles[i].time, value: sum / period });
    }
    return out;
}

/** EMA (Exponential Moving Average) của close — seed = SMA(period). */
export function ema(candles, period) {
    const out = [];
    if (candles.length < period) return out;
    const k = 2 / (period + 1);
    let sum = 0;
    for (let i = 0; i < period; i++) sum += candles[i].close;
    let prev = sum / period;
    out.push({ time: candles[period - 1].time, value: prev });
    for (let i = period; i < candles.length; i++) {
        prev = (candles[i].close - prev) * k + prev;
        out.push({ time: candles[i].time, value: prev });
    }
    return out;
}

/** RSI (Relative Strength Index) — Wilder smoothing. */
export function rsi(candles, period) {
    const out = [];
    if (candles.length <= period) return out;
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
        const d = candles[i].close - candles[i - 1].close;
        if (d >= 0) gain += d; else loss -= d;
    }
    let avgGain = gain / period;
    let avgLoss = loss / period;
    const rsv = (ag, al) => (al === 0 ? 100 : 100 - 100 / (1 + ag / al));
    out.push({ time: candles[period].time, value: rsv(avgGain, avgLoss) });
    for (let i = period + 1; i < candles.length; i++) {
        const d = candles[i].close - candles[i - 1].close;
        const g = d > 0 ? d : 0;
        const l = d < 0 ? -d : 0;
        avgGain = (avgGain * (period - 1) + g) / period;
        avgLoss = (avgLoss * (period - 1) + l) / period;
        out.push({ time: candles[i].time, value: rsv(avgGain, avgLoss) });
    }
    return out;
}

/** EMA trên mảng giá trị { time, value } — dùng cho signal line MACD. */
function emaOfValues(items, period) {
    const out = [];
    if (items.length < period) return out;
    const k = 2 / (period + 1);
    let sum = 0;
    for (let i = 0; i < period; i++) sum += items[i].value;
    let prev = sum / period;
    out.push({ time: items[period - 1].time, value: prev });
    for (let i = period; i < items.length; i++) {
        prev = (items[i].value - prev) * k + prev;
        out.push({ time: items[i].time, value: prev });
    }
    return out;
}

/**
 * MACD (12,26,9).
 * Trả về: { macd, signal, hist } — hist có màu (>=0 xanh, <0 đỏ).
 */
export function macd(candles, fast, slow, signal) {
    const emaFast = ema(candles, fast);
    const emaSlow = ema(candles, slow);
    const slowByTime = new Map(emaSlow.map((e) => [e.time, e.value]));
    const macdLine = emaFast
        .filter((e) => slowByTime.has(e.time))
        .map((e) => ({ time: e.time, value: e.value - slowByTime.get(e.time) }));
    const sig = emaOfValues(macdLine, signal);
    const sigByTime = new Map(sig.map((s) => [s.time, s.value]));
    const hist = macdLine
        .map((m) => {
            const sv = sigByTime.get(m.time);
            if (sv == null) return null;
            const v = m.value - sv;
            return { time: m.time, value: v, color: v >= 0 ? 'rgba(38,166,154,0.9)' : 'rgba(239,83,80,0.9)' };
        })
        .filter(Boolean);
    return { macd: macdLine, signal: sig, hist };
}

/** ATR (Average True Range) — Wilder smoothing. */
export function atr(candles, period) {
    const out = [];
    if (candles.length <= period) return out;
    const trs = [];
    for (let i = 1; i < candles.length; i++) {
        const h = candles[i].high, l = candles[i].low, pc = candles[i - 1].close;
        trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
    }
    let sum = 0;
    for (let i = 0; i < period; i++) sum += trs[i];
    let prev = sum / period;
    out.push({ time: candles[period].time, value: prev });
    for (let i = period; i < trs.length; i++) {
        prev = (prev * (period - 1) + trs[i]) / period;
        out.push({ time: candles[i + 1].time, value: prev });
    }
    return out;
}

/**
 * Bollinger Bands (period, mult).
 * Trả về: { upper, mid, lower } — mid = SMA(period).
 */
export function bollinger(candles, period, mult) {
    const mid = ma(candles, period);
    const upper = [];
    const lower = [];
    for (let i = period - 1; i < candles.length; i++) {
        const m = mid[i - (period - 1)].value;
        let sq = 0;
        for (let j = i - period + 1; j <= i; j++) {
            const d = candles[j].close - m;
            sq += d * d;
        }
        const sd = Math.sqrt(sq / period);
        const t = candles[i].time;
        upper.push({ time: t, value: m + mult * sd });
        lower.push({ time: t, value: m - mult * sd });
    }
    return { upper, mid, lower };
}

/** SMA trên mảng { time, value } — dùng cho smoothing Stochastic. */
function smaOf(items, period) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < items.length; i++) {
        sum += items[i].value;
        if (i >= period) sum -= items[i - period].value;
        if (i >= period - 1) out.push({ time: items[i].time, value: sum / period });
    }
    return out;
}

/**
 * Stochastic Oscillator (kPeriod, dPeriod, smooth).
 * %K = (C - LL)/(HH - LL)*100; smooth bằng SMA(smooth); %D = SMA(dPeriod) của %K.
 * Trả về: { k, d }.
 */
export function stochastic(candles, kPeriod, dPeriod, smooth) {
    const raw = [];
    for (let i = kPeriod - 1; i < candles.length; i++) {
        let hh = -Infinity, ll = Infinity;
        for (let j = i - kPeriod + 1; j <= i; j++) {
            if (candles[j].high > hh) hh = candles[j].high;
            if (candles[j].low < ll) ll = candles[j].low;
        }
        const rng = hh - ll;
        raw.push({ time: candles[i].time, value: rng === 0 ? 50 : ((candles[i].close - ll) / rng) * 100 });
    }
    const k = smaOf(raw, smooth);
    const d = smaOf(k, dPeriod);
    return { k, d };
}

/**
 * ADX (Average Directional Index) — Wilder. period.
 * Trả về: { adx, plus (+DI), minus (-DI) }.
 */
export function adx(candles, period) {
    const empty = { adx: [], plus: [], minus: [] };
    if (candles.length <= period + 1) return empty;
    const trs = [], pdm = [], ndm = [];
    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], p = candles[i - 1];
        trs.push(Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close)));
        const up = c.high - p.high, dn = p.low - c.low;
        pdm.push(up > dn && up > 0 ? up : 0);
        ndm.push(dn > up && dn > 0 ? dn : 0);
    }
    let trSum = 0, pdmSum = 0, ndmSum = 0;
    for (let i = 0; i < period; i++) { trSum += trs[i]; pdmSum += pdm[i]; ndmSum += ndm[i]; }
    let atr = trSum / period, apdm = pdmSum / period, andm = ndmSum / period;
    const plus = [], minus = [], dxs = [];
    for (let i = period; i < trs.length; i++) {
        atr = (atr * (period - 1) + trs[i]) / period;
        apdm = (apdm * (period - 1) + pdm[i]) / period;
        andm = (andm * (period - 1) + ndm[i]) / period;
        const pDI = atr ? (100 * apdm) / atr : 0;
        const nDI = atr ? (100 * andm) / atr : 0;
        const dx = pDI + nDI === 0 ? 0 : (100 * Math.abs(pDI - nDI)) / (pDI + nDI);
        dxs.push(dx);
        plus.push({ time: candles[i + 1].time, value: pDI });
        minus.push({ time: candles[i + 1].time, value: nDI });
    }
    const adxVals = [];
    if (dxs.length >= period) {
        let s = 0;
        for (let k = 0; k < period; k++) s += dxs[k];
        let prev = s / period;
        adxVals.push({ time: candles[2 * period].time, value: prev });
        for (let k = period; k < dxs.length; k++) {
            prev = (prev * (period - 1) + dxs[k]) / period;
            adxVals.push({ time: candles[2 * period + (k - period + 1)].time, value: prev });
        }
    }
    return { adx: adxVals, plus, minus };
}

/** CCI (Commodity Channel Index) — period. */
export function cci(candles, period) {
    const out = [];
    for (let i = period - 1; i < candles.length; i++) {
        const tps = [];
        let tpSum = 0;
        for (let j = i - period + 1; j <= i; j++) {
            const tp = (candles[j].high + candles[j].low + candles[j].close) / 3;
            tps.push(tp);
            tpSum += tp;
        }
        const mean = tpSum / period;
        let md = 0;
        for (const tp of tps) md += Math.abs(tp - mean);
        md /= period;
        const cur = tps[tps.length - 1];
        out.push({ time: candles[i].time, value: md === 0 ? 0 : (cur - mean) / (0.015 * md) });
    }
    return out;
}

/** OBV (On-Balance Volume) — cộng/trừ volume theo chiều đóng cửa. */
export function obv(candles) {
    const out = [];
    let vol = 0;
    for (let i = 0; i < candles.length; i++) {
        if (i > 0) {
            const c = candles[i].close, p = candles[i - 1].close;
            if (c > p) vol += candles[i].volume;
            else if (c < p) vol -= candles[i].volume;
        }
        out.push({ time: candles[i].time, value: vol });
    }
    return out;
}

/**
 * Ichimoku Kinko Hyo (tenkan=9, kijun=26, senkou=52, displacement=kijun).
 * Trả về { tenkan, kijun, spanA, spanB, chikou }.
 *   - Tenkan/Kijun/SpanA/SpanB/Chikou đều là [{time, value}].
 *   - Span A/B được DỜI LÊN `displacement` nến (leading) — dùng timestamp nến
 *     thật phía trước (không cộng ngày giả). Chikou = close dời về sau 26 nến.
 */
export function ichimoku(candles, tenkan = 9, kijun = 26, senkou = 52, displacement) {
    const disp = displacement || kijun;
    const n = candles.length;
    const empty = { tenkan: [], kijun: [], spanA: [], spanB: [], chikou: [] };
    if (!n) return empty;

    const tenkanArr = new Array(n).fill(null);
    const kijunArr = new Array(n).fill(null);
    const senkouArr = new Array(n).fill(null);
    for (let i = 0; i < n; i++) {
        const t0 = Math.max(0, i - tenkan + 1);
        const k0 = Math.max(0, i - kijun + 1);
        const s0 = Math.max(0, i - senkou + 1);
        let hhT = -Infinity, llT = Infinity, hhK = -Infinity, llK = Infinity, hhS = -Infinity, llS = Infinity;
        for (let j = t0; j <= i; j++) { if (candles[j].high > hhT) hhT = candles[j].high; if (candles[j].low < llT) llT = candles[j].low; }
        for (let j = k0; j <= i; j++) { if (candles[j].high > hhK) hhK = candles[j].high; if (candles[j].low < llK) llK = candles[j].low; }
        for (let j = s0; j <= i; j++) { if (candles[j].high > hhS) hhS = candles[j].high; if (candles[j].low < llS) llS = candles[j].low; }
        if (i >= tenkan - 1) tenkanArr[i] = (hhT + llT) / 2;
        if (i >= kijun - 1) kijunArr[i] = (hhK + llK) / 2;
        if (i >= senkou - 1) senkouArr[i] = (hhS + llS) / 2;
    }

    const tenkanLine = [], kijunLine = [], spanA = [], spanB = [], chikou = [];
    for (let i = 0; i < n; i++) {
        if (tenkanArr[i] != null) tenkanLine.push({ time: candles[i].time, value: tenkanArr[i] });
        if (kijunArr[i] != null) kijunLine.push({ time: candles[i].time, value: kijunArr[i] });
        if (i - disp >= 0) chikou.push({ time: candles[i].time, value: candles[i - disp].close });
        const src = i - disp;
        if (src >= 0 && tenkanArr[src] != null && kijunArr[src] != null) {
            spanA.push({ time: candles[i].time, value: (tenkanArr[src] + kijunArr[src]) / 2 });
        }
        if (src >= 0 && senkouArr[src] != null) {
            spanB.push({ time: candles[i].time, value: senkouArr[src] });
        }
    }
    return { tenkan: tenkanLine, kijun: kijunLine, spanA, spanB, chikou };
}
