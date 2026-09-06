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

console.log('✅ Tất cả ' + passed + ' test đã PASS');
