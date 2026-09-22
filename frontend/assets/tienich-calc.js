/**
 * TuniX — "Tiện ích": các hàm tính toán thuần (không phụ thuộc DOM) để dễ kiểm thử.
 * Dùng chung cho trình duyệt (window.TuniXCalc) và Node (module.exports).
 *
 * Tool 1 — Giá điều chỉnh cổ phiếu (corporate actions)
 * Tool 2 — Lợi nhuận tiền gửi (deposit)
 */
(function (root) {
    'use strict';

    // ---------- helpers ----------
    function num(v) {
        if (v === null || v === undefined || v === '') return NaN;
        const n = Number(String(v).replace(/\s/g, ''));
        return isFinite(n) ? n : NaN;
    }
    function round(v, d) {
        const m = Math.pow(10, d == null ? 2 : d);
        return Math.round((v + 1e-9) * m) / m;
    }
    // Số cổ phiếu nhận thêm: làm tròn xuống (bỏ phần lẻ như thực tế các đợt phát hành).
    function floorShare(v) {
        if (!isFinite(v) || v < 0) return 0;
        return Math.floor(v + 1e-9);
    }
    function fmtVND(v, dec) {
        if (v === null || v === undefined || !isFinite(v)) return '—';
        const neg = v < 0 ? '-' : '';
        const abs = Math.abs(v);
        const r = round(abs, dec == null ? 0 : dec);
        const s = r.toLocaleString('vi-VN', { maximumFractionDigits: dec == null ? 0 : dec });
        return neg + s + ' đ';
    }

    // ---------- Tool 1: Giá điều chỉnh cổ phiếu ----------
    // Mỗi sự kiện áp TUẦN TỰ lên giá hiện tại (không cộng gộp tỷ lệ) theo thứ tự người dùng thêm.
    //   cash  : P' = P - cổ tức/cp
    //   stock : P' = P / (1 + tỷ lệ%)        (cổ tức bằng cổ phiếu — phát hành cổ phiếu miễn phí)
    //   bonus : P' = P / (1 + tỷ lệ%)        (cổ phiếu thưởng — phát hành miễn phí)
    //   rights: P' = (P + tỷ lệ% × giá phát hành) / (1 + tỷ lệ%)   (phát hành quyền mua)
    const EVENT_LABELS = {
        cash: 'Cổ tức tiền mặt',
        stock: 'Cổ tức bằng cổ phiếu',
        bonus: 'Cổ phiếu thưởng',
        rights: 'Phát hành quyền mua'
    };

    function validateEvents(events) {
        const errs = [];
        if (!Array.isArray(events) || !events.length) {
            errs.push('Cần ít nhất 1 sự kiện điều chỉnh.');
            return errs;
        }
        events.forEach(function (e, i) {
            const t = e && e.type;
            const n = i + 1;
            if (t === 'cash') {
                const c = num(e.cash);
                if (!(c >= 0)) errs.push('Sự kiện ' + n + ' (cổ tức tiền mặt): nhập số tiền cổ tức/cổ phiếu ≥ 0.');
            } else if (t === 'stock' || t === 'bonus') {
                const r = num(e.rate);
                if (!(r >= 0)) errs.push('Sự kiện ' + n + ': nhập tỷ lệ % hợp lệ (≥ 0).');
            } else if (t === 'rights') {
                const r = num(e.rate);
                const ip = num(e.issuePrice);
                if (!(r > 0)) errs.push('Sự kiện ' + n + ' (quyền mua): tỷ lệ quyền mua phải > 0.');
                if (!(ip >= 0)) errs.push('Sự kiện ' + n + ' (quyền mua): nhập giá phát hành hợp lệ (≥ 0).');
            } else {
                errs.push('Sự kiện ' + n + ': loại sự kiện không hợp lệ.');
            }
        });
        return errs;
    }

    /**
     * Tính giá điều chỉnh + diễn biến số lượng cổ phiếu (nếu có) qua từng sự kiện.
     * @param {number} price  giá trước điều chỉnh (đ/cp)
     * @param {Array}  events mảng {type, cash?, rate?, issuePrice?} theo thứ tự áp dụng
     * @param {number} [qty]  số cổ phiếu đang nắm giữ (tùy chọn)
     */
    function calcAdjusted(price, events, qty) {
        const errs = [];
        if (!(num(price) > 0)) errs.push('Nhập giá trước điều chỉnh > 0.');
        const hasQty = qty !== undefined && qty !== null && qty !== '';
        if (hasQty && !(num(qty) >= 0)) errs.push('Số cổ phiếu nắm giữ phải là số ≥ 0.');
        errs.push.apply(errs, validateEvents(events));
        if (errs.length) return { errors: errs };

        let p = num(price);
        let q = hasQty ? Math.floor(num(qty) + 1e-9) : null;
        const startQty = q;
        const steps = [];
        let totalCashDiv = 0;
        let totalRightsPay = 0;

        events.forEach(function (e, i) {
            const type = e.type;
            const before = round(p, 2);
            const step = {
                index: i + 1,
                type: type,
                label: EVENT_LABELS[type] || type,
                detail: '',
                before: before,
                after: null,
                sharesReceived: null,
                cashReceived: null,
                rightsPaid: null,
                qtyBefore: q,
                qtyAfter: null,
                formula: ''
            };
            if (type === 'cash') {
                const amt = num(e.cash);
                p = p - amt;
                step.formula = 'P' + (i ? '' : '₀') + ' − ' + round(amt, 0) + ' đ (cổ tức/cp)';
                step.detail = 'Trừ ' + fmtVND(amt, 0) + '/cổ phiếu';
                if (q !== null) {
                    const cashNow = round(amt * q, 0);
                    totalCashDiv += cashNow;
                    step.cashReceived = round(cashNow, 0);
                }
            } else if (type === 'stock' || type === 'bonus') {
                const rate = num(e.rate) / 100;
                p = p / (1 + rate);
                step.formula = 'P ÷ (1 + ' + round(rate * 100, 4) + '%)';
                step.detail = 'Tỷ lệ ' + round(rate * 100, 4) + '%';
                if (q !== null) {
                    const add = floorShare(q * rate);
                    q += add;
                    step.sharesReceived = add;
                }
            } else if (type === 'rights') {
                const rate = num(e.rate) / 100;
                const ip = num(e.issuePrice);
                p = (p + rate * ip) / (1 + rate);
                step.formula = '(P + ' + round(rate * 100, 4) + '% × ' + round(ip, 0) + ') ÷ (1 + ' + round(rate * 100, 4) + '%)';
                step.detail = 'Tỷ lệ ' + round(rate * 100, 4) + '% — giá phát hành ' + fmtVND(ip, 0);
                if (q !== null) {
                    const add = floorShare(q * rate);
                    const pay = round(add * ip, 0);
                    q += add;
                    totalRightsPay += pay;
                    step.sharesReceived = add;
                    step.rightsPaid = round(pay, 0);
                }
            }
            step.after = round(p, 2);
            step.qtyAfter = q;
            steps.push(step);
        });

        const result = {
            priceBefore: round(num(price), 2),
            finalPrice: round(p, 2),
            steps: steps
        };
        if (hasQty && q !== null) {
            result.hasQty = true;
            result.startQty = startQty;
            result.finalQty = q;
            result.totalSharesReceived = q - startQty;
            result.totalCashDividend = round(totalCashDiv, 0);
            result.totalRightsPay = round(totalRightsPay, 0);
            result.nominalBefore = round(startQty * num(price), 0);
            result.nominalAfter = round(q * p, 0);
        } else {
            result.hasQty = false;
        }
        return result;
    }

    // ---------- Tool 2: Lợi nhuận tiền gửi ----------
    const MODE_LABELS = {
        end: 'Cuối kỳ (lãi đơn)',
        month: 'Hàng tháng',
        quarter: 'Hàng quý',
        compound: 'Lãi nhập gốc (theo tháng)'
    };

    /**
     * Tính tiền lãi tiền gửi.
     * @param {object} o {principal, rateAnnualPct, months, mode, monthlyExtra}
     *   mode: end | month | quarter | compound
     *   monthlyExtra: tiền gửi thêm ĐẦU mỗi tháng (nếu > 0)
     */
    function calcDeposit(o) {
        const principal = num(o.principal);
        const rate = num(o.rateAnnualPct);
        const months = Math.max(1, Math.floor(num(o.months) || 1));
        const mode = o.mode || 'end';
        const extra = num(o.monthlyExtra) > 0 ? num(o.monthlyExtra) : 0;

        const errs = [];
        if (!(principal > 0)) errs.push('Nhập số tiền gửi > 0.');
        if (!(rate >= 0)) errs.push('Nhập lãi suất %/năm hợp lệ (≥ 0).');
        if (!(months >= 1)) errs.push('Thời gian gửi phải ≥ 1 tháng.');
        if (extra < 0 || (num(o.monthlyExtra) > 0 && !isFinite(extra))) errs.push('Số tiền gửi thêm hàng tháng không hợp lệ.');
        if (!MODE_LABELS[mode]) errs.push('Hình thức tính lãi không hợp lệ.');
        if (errs.length) return { errors: errs };

        const rDec = rate / 100;         // lãi suất năm (dạng thập phân)
        const im = rDec / 12;            // lãi suất tháng

        let totalDeposited = principal;
        let totalInterest = 0;
        let final = 0;
        const points = [{ m: 0, value: round(principal, 0) }];

        if (mode === 'compound') {
            // Lãi nhập gốc theo tháng; gửi thêm đầu tháng nếu có.
            let bal = principal;
            for (let m = 1; m <= months; m++) {
                if (extra > 0) { bal += extra; totalDeposited += extra; }
                bal += bal * im;
                points.push({ m: m, value: round(bal, 0) });
            }
            final = bal;
            totalInterest = round(final - totalDeposited, 0);
        } else {
            // Các hình thức lãi đơn: lãi = gốc × %năm × (số tháng/12).
            // Tiền gửi thêm ở đầu tháng k được hưởng lãi cho (n - k + 1) tháng còn lại.
            const baseInt = principal * rDec * (months / 12);
            let extraInt = 0;
            if (extra > 0) {
                // Σ (n-k+1), k=1..n = n(n+1)/2  → /12 năm
                extraInt = extra * rDec * ((months * (months + 1)) / 2) / 12;
            }
            totalInterest = round(baseInt + extraInt, 0);
            final = principal + extra * months + totalInterest;
            totalDeposited = principal + extra * months;

            // Đồ thị giá trị tích lũy (lãi dồn theo tháng nếu giữ đến đáo hạn).
            let cumExtra = 0;
            for (let m = 1; m <= months; m++) {
                if (extra > 0) cumExtra += extra;
                const intP = principal * rDec * (m / 12);
                let intE = 0;
                if (extra > 0) intE = extra * rDec * ((m * (m + 1)) / 2) / 12;
                points.push({ m: m, value: round(principal + cumExtra + intP + intE, 0) });
            }
        }

        // Số lần chi trả lãi theo hình thức.
        let payoutTimes = 1;
        let scheduleNote = 'Nhận toàn bộ gốc + lãi một lần khi đáo hạn.';
        if (mode === 'month') {
            payoutTimes = months;
            scheduleNote = 'Nhận lãi hàng tháng; gốc + lãi tháng cuối khi đáo hạn.';
        } else if (mode === 'quarter') {
            payoutTimes = Math.floor(months / 3);
            scheduleNote = 'Nhận lãi mỗi quý (3 tháng); gốc + lãi quý cuối khi đáo hạn.';
        } else if (mode === 'compound') {
            scheduleNote = 'Lãi được nhập vào gốc hàng tháng (lãi kép).';
        }

        return {
            principal: round(principal, 0),
            monthlyExtra: extra ? round(extra, 0) : 0,
            months: months,
            rateAnnualPct: rate,
            mode: mode,
            modeLabel: MODE_LABELS[mode],
            totalDeposited: round(totalDeposited, 0),
            totalExtra: extra ? round(extra * months, 0) : 0,
            totalInterest: totalInterest,
            totalReceive: round(final, 0),
            payoutTimes: payoutTimes,
            scheduleNote: scheduleNote,
            points: points
        };
    }

    // ---------- Tool 3: Chia đều vốn đầu tư ----------
    // Người dùng CHỈ nhập: (1) tổng vốn, (2) danh sách mã. Giá hiện tại do API thị trường
    // của TuniX cung cấp (không hard-code giá, không bắt người dùng nhập giá).
    //   Vốn phân bổ mỗi mã = Tổng vốn / Số mã
    //   Số lượng lý thuyết = Vốn phân bổ / Giá hiện tại
    //   Số lượng mua      = floor(Số lượng lý thuyết / Lô) × Lô   (KHÔNG làm tròn lên để không vượt vốn)
    //   Giá trị mua       = Số lượng mua × Giá hiện tại
    //   Tiền dư của mã    = Vốn phân bổ − Giá trị mua
    const ALLOC_LOT = 100; // lô giao dịch mặc định: 100 cổ phiếu

    /**
     * Chuẩn hoá danh sách mã: cắt khoảng trắng, VIẾT HOA, bỏ ký tự lạ, bỏ mã trùng (giữ thứ tự xuất hiện).
     * @param {string|string[]} input chuỗi người dùng gõ/dán ("hpg, acb ctg HPG") hoặc mảng mã
     * @returns {string[]} ví dụ: ['HPG','ACB','CTG']
     */
    function normalizeSymbols(input) {
        const raw = Array.isArray(input) ? input : String(input == null ? '' : input).split(/[\s,;|/\\\t]+/);
        const out = [];
        const seen = {};
        raw.forEach(function (s) {
            const t = String(s == null ? '' : s).trim().toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 10);
            if (!t || seen[t]) return;
            seen[t] = 1;
            out.push(t);
        });
        return out;
    }

    /** Vốn phân bổ cho mỗi mã = Tổng vốn / Số mã. */
    function equalCapital(total, count) {
        const t = num(total), n = num(count);
        if (!(t > 0) || !(n > 0)) return NaN;
        return t / n;
    }

    /** Số lượng mua theo lô = floor(vốn phân bổ / giá / lô) × lô (làm tròn XUỐNG). */
    function sharesByLot(capital, price, lot) {
        const c = num(capital), p = num(price);
        const L = num(lot) > 0 ? num(lot) : ALLOC_LOT;
        if (!(c > 0) || !(p > 0)) return 0;
        return Math.floor(c / p / L + 1e-9) * L;
    }

    /** Giá trị mua thực tế = số lượng mua × giá hiện tại. */
    function purchaseValue(qty, price) {
        const q = num(qty), p = num(price);
        if (!(q > 0) || !(p > 0)) return 0;
        return q * p;
    }

    /** Tiền dư của mã = vốn phân bổ − giá trị mua thực tế. */
    function remainingCash(capital, value) {
        const c = num(capital);
        if (!isFinite(c)) return 0;
        const v = num(value);
        return c - (isFinite(v) ? v : 0);
    }

    /**
     * Chia đều tổng vốn cho danh sách mã ĐÃ CÓ GIÁ hiện tại.
     * @param {number} total tổng vốn đầu tư (đồng)
     * @param {Array<{symbol:string, price:number, name?:string}>} items mã + giá hiện tại
     * @param {number} [lot=100] lô giao dịch
     * @returns {{errors:string[], rows:Array, summary:Object}} errors rỗng nghĩa là tính thành công
     */
    function allocateEqualCapital(total, items, lot) {
        const errs = [];
        const hasTotal = !(total === null || total === undefined || String(total).trim() === '');
        const t = num(total);
        if (!hasTotal || !isFinite(t)) {
            errs.push('Vui lòng nhập tổng số tiền đầu tư.');
        } else if (!(t > 0)) {
            errs.push('Số tiền đầu tư phải lớn hơn 0.');
        }
        const list = (Array.isArray(items) ? items : []).filter(function (i) {
            return i && i.symbol;
        });
        if (!list.length) errs.push('Vui lòng nhập ít nhất một mã cổ phiếu.');
        if (errs.length) return { errors: errs, rows: [], summary: null };

        const L = num(lot) > 0 ? num(lot) : ALLOC_LOT;
        const count = list.length;
        const capital = equalCapital(t, count); // có thể lẻ đồng nếu tổng vốn không chia hết
        const rows = list.map(function (it) {
            const price = num(it.price);
            const qty = sharesByLot(capital, price, L);
            const value = purchaseValue(qty, price);
            return {
                symbol: String(it.symbol).toUpperCase(),
                name: it.name ? String(it.name) : '',
                price: isFinite(price) ? price : 0,
                capital: capital,
                qty: qty,
                value: round(value, 0),
                remain: round(remainingCash(capital, value), 0)
            };
        });
        const buyValue = round(rows.reduce(function (a, r) { return a + r.value; }, 0), 0);
        return {
            errors: [],
            rows: rows,
            summary: {
                total: round(t, 0),
                count: count,
                lot: L,
                capital: capital,
                buyValue: buyValue,
                // Tính từ tổng vốn ⇒ luôn đảm bảo: Tổng giá trị mua + Tổng tiền dư = Tổng vốn.
                remain: round(t - buyValue, 0)
            }
        };
    }

    // ---------- Public API ----------
    const api = {
        num: num,
        round: round,
        floorShare: floorShare,
        fmtVND: fmtVND,
        EVENT_LABELS: EVENT_LABELS,
        MODE_LABELS: MODE_LABELS,
        ALLOC_LOT: ALLOC_LOT,
        validateEvents: validateEvents,
        calcAdjusted: calcAdjusted,
        calcDeposit: calcDeposit,
        normalizeSymbols: normalizeSymbols,
        equalCapital: equalCapital,
        sharesByLot: sharesByLot,
        purchaseValue: purchaseValue,
        remainingCash: remainingCash,
        allocateEqualCapital: allocateEqualCapital
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.TuniXCalc = api;
    }
})(typeof self !== 'undefined' ? self : this);
