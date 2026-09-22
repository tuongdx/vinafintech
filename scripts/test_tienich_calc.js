/**
 * Test cho module tính toán "Tiện ích" (frontend/assets/tienich-calc.js).
 * Chạy:  node scripts/test_tienich_calc.js
 */
'use strict';
const assert = require('assert');
const path = require('path');
const Calc = require(path.join(__dirname, '..', 'frontend', 'assets', 'tienich-calc.js'));

function near(actual, expected, tol, label) {
    assert.ok(
        Math.abs(actual - expected) <= (tol == null ? 0.01 : tol),
        label + ': expected ~' + expected + ' got ' + actual
    );
}

let passed = 0;
function ok(cond, label) {
    assert.ok(cond, label);
    passed++;
}

// ---------- Tool 1: Giá điều chỉnh ----------
// 1a) Chỉ cổ tức tiền mặt
{
    const r = Calc.calcAdjusted(30000, [{ type: 'cash', cash: 1000 }]);
    ok(!r.errors, 'cash: no errors');
    near(r.finalPrice, 29000, 0.01, 'cash final');
    near(r.steps[0].after, 29000, 0.01, 'cash step after');
}

// 1b) Chỉ cổ phiếu thưởng 100% → giá chia đôi
{
    const r = Calc.calcAdjusted(100000, [{ type: 'bonus', rate: 100 }]);
    near(r.finalPrice, 50000, 0.01, 'bonus 100% halves price');
}
// 1c) Cổ tức bằng cổ phiếu 25%
{
    const r = Calc.calcAdjusted(50000, [{ type: 'stock', rate: 25 }]);
    near(r.finalPrice, 40000, 0.01, 'stock div 25% -> /1.25');
}
// 1d) Quyền mua 50% giá 10000 trên giá 20000
{
    const r = Calc.calcAdjusted(20000, [{ type: 'rights', rate: 50, issuePrice: 10000 }]);
    near(r.finalPrice, (20000 + 0.5 * 10000) / 1.5, 0.01, 'rights formula');
}
// 1e) Chuỗi nhiều sự kiện tuần tự (đúng ví dụ người dùng): 30.000 − 1.000 → −20% cổ tức cp → quyền mua 20% @15.000
{
    const r = Calc.calcAdjusted(30000, [
        { type: 'cash', cash: 1000 },
        { type: 'stock', rate: 20 },
        { type: 'rights', rate: 20, issuePrice: 15000 }
    ]);
    ok(!r.errors, 'chain no errors');
    const afterCash = 30000 - 1000;            // 29000
    const afterStock = afterCash / 1.2;         // 24166.666...
    const finalExpected = (afterStock + 0.2 * 15000) / 1.2;
    near(r.steps[0].after, afterCash, 0.01, 'chain step1 cash');
    near(r.steps[1].after, afterStock, 0.01, 'chain step2 stock');
    near(r.steps[2].after, finalExpected, 0.01, 'chain step3 rights');
    near(r.finalPrice, finalExpected, 0.01, 'chain final');
}
// 1f) Không cộng gộp tỷ lệ: 2 sự kiện bonus 100% + bonus 100% phải cho P0/4 chứ KHÔNG phải P0/(1+2)
{
    const r = Calc.calcAdjusted(100000, [
        { type: 'bonus', rate: 100 },
        { type: 'bonus', rate: 100 }
    ]);
    near(r.finalPrice, 100000 / 4, 0.01, 'two 100% bonuses = /4 (stepwise, not added)');
}

// 1g) Số lượng cổ phiếu: 1.000 cp → cổ tức cp 20% + bonus 50% + rights 20%@15000 + cash 1000
{
    const r = Calc.calcAdjusted(30000, [
        { type: 'cash', cash: 1000 },
        { type: 'stock', rate: 20 },
        { type: 'bonus', rate: 50 },
        { type: 'rights', rate: 20, issuePrice: 15000 }
    ], 1000);
    ok(r.hasQty, 'qty present');
    ok(r.startQty === 1000, 'start qty');
    const q1 = 1000;                                   // cash: ko đổi
    const q2 = q1 + Math.floor(q1 * 0.2);              // = 1200
    const q3 = q2 + Math.floor(q2 * 0.5);              // = 1200 + 600 = 1800
    const q4 = q3 + Math.floor(q3 * 0.2);              // = 1800 + 360 = 2160
    ok(r.finalQty === q4, 'qty final expected ' + q4 + ' got ' + r.finalQty);
    near(r.totalCashDividend, 1000 * 1000, 0.01, 'total cash dividend (1k cp x 1000)');
    near(r.totalRightsPay, 360 * 15000, 0.01, 'total rights pay');
    near(r.totalSharesReceived, q4 - 1000, 0.01, 'shares received total');
}

// Validation
{
    const r = Calc.calcAdjusted(0, [{ type: 'cash', cash: 100 }]);
    ok(Array.isArray(r.errors) && r.errors.length > 0, 'invalid price -> errors');
    const r2 = Calc.calcAdjusted(10000, [{ type: 'rights', rate: 0, issuePrice: 5000 }]);
    ok(Array.isArray(r2.errors) && r2.errors.length > 0, 'rights rate 0 -> errors');
}

// ---------- Tool 2: Tiền gửi ----------
// 2a) Lãi đơn cuối kỳ 12 tháng 5%/năm
{
    const r = Calc.calcDeposit({ principal: 100000000, rateAnnualPct: 5, months: 12, mode: 'end' });
    near(r.totalInterest, 5000000, 0.01, 'simple 12m 5% interest');
    near(r.totalReceive, 105000000, 0.01, 'simple 12m 5% receive');
}
// 2b) Lãi kép 100tr 5%/năm gửi 12 tháng (nhập gốc hàng tháng)
{
    const r = Calc.calcDeposit({ principal: 100000000, rateAnnualPct: 5, months: 12, mode: 'compound' });
    const expected = 100000000 * Math.pow(1 + 0.05 / 12, 12);
    near(r.totalReceive, expected, 1, 'compound FV ~105.116.190');
    ok(r.totalInterest > 5000000, 'compound interest > simple interest');
}
// 2c) Gửi thêm định kỳ + lãi kép (tổng tiền đã gửi = gốc + thêm×tháng)
{
    const r = Calc.calcDeposit({ principal: 100000000, rateAnnualPct: 5, months: 12, mode: 'compound', monthlyExtra: 1000000 });
    ok(r.totalDeposited === 112000000, 'compound+extra deposited total');
    ok(r.points.length === 13, 'points = months + 1 (kèm điểm 0)');
    ok(r.points[r.points.length - 1].value === r.totalReceive, 'last point = totalReceive');
}
// 2d) Lãi đơn + gửi thêm: lãi thêm = extra × r% × n(n+1)/2 / 12
{
    const r = Calc.calcDeposit({ principal: 100000000, rateAnnualPct: 5, months: 12, mode: 'end', monthlyExtra: 1000000 });
    const expected = 5000000 + 1000000 * 0.05 * (12 * 13 / 2) / 12; // 5.325.000
    near(r.totalInterest, expected, 0.01, 'simple + extra interest');
    near(r.totalReceive, 112000000 + expected, 0.01, 'simple + extra receive');
}

// Validation
{
    const r = Calc.calcDeposit({ principal: 0, rateAnnualPct: 5, months: 6, mode: 'end' });
    ok(Array.isArray(r.errors) && r.errors.length > 0, 'deposit principal 0 -> errors');
}

// ---------- Tool 3: Chia đều vốn đầu tư ----------
// 3a) Chuẩn hoá mã: viết HOA + bỏ trùng (đúng ví dụ "hpg, acb, ctg, HPG")
{
    const list = Calc.normalizeSymbols('hpg, acb, ctg, HPG');
    ok(list.length === 3 && list[0] === 'HPG' && list[1] === 'ACB' && list[2] === 'CTG',
        'normalizeSymbols: HOA + bỏ trùng, giữ thứ tự');
    ok(Calc.normalizeSymbols(['  vnm ', 'VNM', 'fpt']).join(',') === 'VNM,FPT', 'normalizeSymbols: mảng + trim');
}
// 3b) Vốn phân bổ mỗi mã = tổng vốn / số mã
{
    near(Calc.equalCapital(200000000, 5), 40000000, 0.01, 'equalCapital 200tr/5');
    ok(isNaN(Calc.equalCapital(0, 5)), 'equalCapital: tổng vốn 0 -> NaN');
    ok(isNaN(Calc.equalCapital(1000000, 0)), 'equalCapital: 0 mã -> NaN');
}
// 3c) Số lượng mua theo lô 100 — làm tròn XUỐNG (không được vượt vốn phân bổ)
{
    ok(Calc.sharesByLot(40000000, 25000) === 1600, 'sharesByLot: 40tr/25.000 = 1.600');
    ok(Calc.sharesByLot(40000000, 26000) === 1500, 'sharesByLot: 1.538 -> 1.500 (floor lô 100)');
    ok(Calc.sharesByLot(39000000, 26000) === 1500, 'sharesByLot: không làm tròn lên');
    ok(Calc.sharesByLot(90000, 25000) === 0, 'sharesByLot: chưa đủ 1 lô -> 0');
    ok(Calc.sharesByLot(40000000, 0) === 0, 'sharesByLot: giá 0 -> 0');
}
// 3d) Giá trị mua + tiền dư
{
    ok(Calc.purchaseValue(1500, 26000) === 39000000, 'purchaseValue 1.500 × 26.000');
    ok(Calc.remainingCash(40000000, 39000000) === 1000000, 'remainingCash 40tr − 39tr = 1tr');
}
// 3e) Chia đều 5 mã (đúng ví dụ trong đề bài) — giá là ví dụ, không hard-code trong code
{
    const items = [
        { symbol: 'HPG', price: 25000 },
        { symbol: 'ACB', price: 27000 },
        { symbol: 'CTG', price: 45000 },
        { symbol: 'APH', price: 10000 },
        { symbol: 'AAS', price: 8000 }
    ];
    const r = Calc.allocateEqualCapital(200000000, items);
    ok(!r.errors.length, 'alloc: không có lỗi');
    ok(r.summary.capital === 40000000, 'alloc: vốn phân bổ/mã = 40.000.000');
    ok(r.summary.count === 5, 'alloc: đếm đúng 5 mã');
    ok(r.summary.lot === 100, 'alloc: lô mặc định = 100');
    ok(r.rows[0].qty === 1600 && r.rows[0].value === 40000000 && r.rows[0].remain === 0,
        'alloc: HPG 1.600 × 25.000 = 40.000.000, dư 0');
    ok(r.rows[3].qty === 4000 && r.rows[3].value === 40000000, 'alloc: APH 4.000 × 10.000');
    ok(r.rows[1].qty === 1400 && r.rows[1].remain === 2200000, 'alloc: ACB 1.400 × 27.000, dư 2.200.000');
    ok(r.summary.buyValue + r.summary.remain === r.summary.total,
        'alloc: Tổng giá trị mua + Tổng tiền dư = Tổng vốn');
}
// 3f) Tổng vốn KHÔNG chia hết cho số mã → bất biến vẫn phải đúng
{
    const r = Calc.allocateEqualCapital(100000000, [{ symbol: 'A', price: 12000 }, { symbol: 'B', price: 23000 }, { symbol: 'C', price: 9000 }]);
    ok(r.summary.buyValue + r.summary.remain === r.summary.total, 'alloc: bất biến với vốn lẻ (100tr/3)');
}
// 3g) Mã không lấy được giá (price = 0) → không mua, dư toàn bộ phần vốn phân bổ
{
    const r = Calc.allocateEqualCapital(20000000, [{ symbol: 'XXX', price: 0 }]);
    ok(r.rows[0].qty === 0 && r.rows[0].value === 0 && r.rows[0].remain === 20000000, 'alloc: giá 0 -> dư toàn bộ');
}
// 3h) Lỗi nhập liệu (thông điệp đúng như yêu cầu)
{
    ok(Calc.allocateEqualCapital('', [{ symbol: 'HPG', price: 25000 }]).errors[0] === 'Vui lòng nhập tổng số tiền đầu tư.',
        'alloc: chưa nhập tiền');
    ok(Calc.allocateEqualCapital(0, [{ symbol: 'HPG', price: 25000 }]).errors[0] === 'Số tiền đầu tư phải lớn hơn 0.',
        'alloc: tiền = 0');
    ok(Calc.allocateEqualCapital(-5000000, [{ symbol: 'HPG', price: 25000 }]).errors[0] === 'Số tiền đầu tư phải lớn hơn 0.',
        'alloc: tiền âm');
    ok(Calc.allocateEqualCapital(20000000, []).errors[0] === 'Vui lòng nhập ít nhất một mã cổ phiếu.',
        'alloc: chưa nhập mã');
}

console.log('✅ Tất cả ' + passed + ' test đã PASS');
