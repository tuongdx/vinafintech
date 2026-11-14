/**
 * Nhãn "Sắp chia cổ tức" — DÙNG CHUNG cho mọi bảng trong toàn site.
 *
 * Cách dùng (2 bước):
 *   1. Thêm script:  <script src="/static/dividend-badge.js?v=20260926c"></script>
 *   2. Trong template mỗi dòng/bảng, đặt chỗ trống cạnh mã:
 *          <span data-div="FPT"></span>
 *      Script tự quét (kể cả khi bảng render lại sau này nhờ MutationObserver),
 *      gọi 1 request gộp cho tất cả mã chưa biết rồi điền nhãn + tooltip.
 *
 * Nguồn: GET /api/stocks/upcoming-dividends?symbols=A,B,C (public, server trả nhanh
 * từ cache — xem app/services/dividend_adjust.upcoming_for).
 * Nhãn CHỈ hiện trong cửa sổ: 5 NGÀY TRƯỚC ngày GDKHQ → HẾT ngày GDKHQ (server lọc,
 * xem _VD_LEAD_DAYS/_in_upcoming_window) ⇒ hết ngày GDKHQ là tự mất.
 * Rê chuột → hiện ngày GDKHQ + ngày trả + tỷ lệ (tiền và/hoặc cổ phiếu).
 */
(function () {
    'use strict';

    const API = '/api/stocks/upcoming-dividends';
    const STYLE_ID = 'vinafintechDivBadgeCss';
    const MAX_PER_BATCH = 150;
    const MAX_BATCHES = 4;
    const FAIL_RETRY_MS = 60000;

    const data = {};      // MÃ -> {date, cash, stock_pct, label} (chỉ mã có đợt sắp tới)
    const known = {};     // MÃ -> true (đã hỏi server, kể cả trường hợp không có đợt)
    let inflight = null;
    let failedAt = 0;
    let batches = 0;
    let sweeping = false;

    function ensureCss() {
        if (document.getElementById(STYLE_ID)) return;
        const st = document.createElement('style');
        st.id = STYLE_ID;
        st.textContent =
            '.tx-div{display:inline-flex;align-items:center;gap:4px;margin-left:6px;white-space:nowrap;' +
            'font-size:10px;font-weight:700;letter-spacing:.2px;vertical-align:middle;cursor:help;' +
            'color:#b07800;background:rgba(245,166,35,.12);border:1px solid rgba(245,166,35,.35);' +
            'border-radius:20px;padding:2px 7px;}' +
            'body.dark-mode .tx-div{color:#e6c36a;background:rgba(230,195,106,.12);border-color:rgba(230,195,106,.35);}';
        (document.head || document.documentElement).appendChild(st);
    }

    function fmtDate(iso) {
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
        return m ? (m[3] + '/' + m[2] + '/' + m[1]) : String(iso || '');
    }

    function fmtMoney(v) {
        const n = Number(v) || 0;
        return n.toLocaleString('vi-VN', { maximumFractionDigits: 0 });
    }

    // Tỷ lệ của 1 đợt: tiền (đ/cp + % mệnh giá 10.000đ) và/hoặc cổ phiếu (%)
    function rateText(e) {
        const bits = [];
        const cash = Number(e && e.cash) || 0;
        const pct = Number(e && e.stock_pct) || 0;
        if (cash > 0) {
            bits.push(fmtMoney(cash) + ' đ/cp (' + (cash / 100).toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + '% mệnh giá)');
        }
        if (pct > 0) bits.push(pct.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + '% cổ phiếu');
        return bits.join(' + ') || '—';
    }

    function badgeHtml(sym) {
        const e = data[String(sym || '').toUpperCase()];
        if (!e) return '';
        const chips = ['Sắp chia cổ tức'];
        const exTxt = fmtDate(e.ex_date || e.date);
        if (exTxt) chips.push('Ngày GDKHQ: ' + exTxt);
        const payTxt = e.pay_date ? fmtDate(e.pay_date) : '';
        if (payTxt && payTxt !== exTxt) chips.push('Ngày trả: ' + payTxt);
        chips.push('Tỷ lệ: ' + rateText(e));
        if (e.label) chips.push(e.label);
        const esc = String(chips.join(' • ')).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        return '<span class="tx-div" title="' + esc + '">Sắp chia cổ tức</span>';
    }

    function paint(root) {
        const scope = root || document;
        const nodes = scope.querySelectorAll ? scope.querySelectorAll('[data-div]') : [];
        Array.prototype.forEach.call(nodes, function (el) {
            const sym = String(el.getAttribute('data-div') || '').trim().toUpperCase();
            const html = badgeHtml(sym);
            if (el.__txDivSym === sym && el.__txDivHtml === html) return; // không đổi → khỏi ghi DOM
            el.__txDivSym = sym;
            el.__txDivHtml = html;
            el.innerHTML = html;
        });
    }

    function load(symbols) {
        const unknown = [];
        const seen = {};
        (symbols || []).forEach(function (s) {
            const sym = String(s || '').trim().toUpperCase();
            if (!sym || seen[sym] || known[sym]) return;
            seen[sym] = true;
            unknown.push(sym);
        });
        if (!unknown.length) return Promise.resolve();
        if (inflight) return inflight;
        if (failedAt && Date.now() - failedAt < FAIL_RETRY_MS) return Promise.resolve();
        if (batches >= MAX_BATCHES) return Promise.resolve();

        const batch = unknown.slice(0, MAX_PER_BATCH);
        batches += 1;
        inflight = fetch(API + '?symbols=' + encodeURIComponent(batch.join(',')), { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (payload) {
                batch.forEach(function (s) { known[s] = true; });
                Object.keys(payload || {}).forEach(function (s) {
                    data[String(s).toUpperCase()] = payload[s];
                });
                failedAt = 0;
            })
            .catch(function () { failedAt = Date.now(); })
            .then(function () {
                inflight = null;
                paint();
                // Còn mã chưa biết (bảng dài) → chạy tiếp batch sau.
                if (document.querySelector('[data-div]')) setTimeout(sweep, 0);
            });
        return inflight;
    }

    function sweep() {
        if (sweeping) return;
        const nodes = document.querySelectorAll('[data-div]');
        if (!nodes.length) return;
        const syms = [];
        Array.prototype.forEach.call(nodes, function (el) {
            const sym = String(el.getAttribute('data-div') || '').trim().toUpperCase();
            if (sym) syms.push(sym);
        });
        sweeping = true;
        Promise.resolve(load(syms)).then(function () {
            sweeping = false;
            paint();
        }, function () { sweeping = false; });
    }

    function init() {
        ensureCss();
        sweep();
        try {
            const obs = new MutationObserver(function () {
                clearTimeout(obs.__t);
                obs.__t = setTimeout(sweep, 200);
            });
            obs.observe(document.body || document.documentElement, { childList: true, subtree: true });
        } catch (e) { /* trình duyệt cũ: vẫn chạy sweep lúc tải trang */ }
    }

    window.VinaFintechDiv = { paint: paint, sweep: sweep, load: load, badgeHtml: badgeHtml, data: data, rateText: rateText };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
