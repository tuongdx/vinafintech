/* TuniX — Tiện ích → "Chia đều vốn đầu tư": logic giao diện.
 *
 * Người dùng CHỈ nhập 2 thứ: tổng vốn + danh sách mã cổ phiếu.
 * Giá hiện tại lấy từ API CÓ SẴN của TuniX: GET /api/stocks/{mã}/quote
 * (cùng nguồn dữ liệu với trang chi tiết cổ phiếu) — KHÔNG tạo API giá riêng.
 *
 * Toàn bộ phép tính nằm ở module thuần frontend/assets/tienich-calc.js
 * (allocateEqualCapital / equalCapital / sharesByLot / purchaseValue / remainingCash).
 */
(function () {
    'use strict';
    const Calc = window.TuniXCalc;
    if (!Calc || window.__tiAllocLoaded) return;
    if (!document.getElementById('panelAlloc')) return; // không ở trang Tiện ích → thoát
    window.__tiAllocLoaded = true;

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    /** "40.000.000 đ" */
    function money(v) { return Calc.fmtVND(v, 0); }
    /** giờ máy người dùng: "22:15:32 16/09/2026" */
    function fmtNow(d) {
        return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds()) +
            ' ' + pad2(d.getDate()) + '/' + pad2(d.getMonth() + 1) + '/' + d.getFullYear();
    }
    /** ISO UTC (as_of của API) → giờ VN (UTC+7): "16/09/2026 15:00" */
    function fmtVnFromIso(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        const t = new Date(d.getTime() + 7 * 3600 * 1000);
        return pad2(t.getUTCDate()) + '/' + pad2(t.getUTCMonth() + 1) + '/' + t.getUTCFullYear() +
            ' ' + pad2(t.getUTCHours()) + ':' + pad2(t.getUTCMinutes());
    }

    const state = { symbols: [], busy: false };
    const totalEl = $('alTotal');
    const symInput = $('alSymbolInput');
    const chipsEl = $('alChips');
    const resEl = $('alResult');
    const errEl = $('alErrors');
    const calcBtn = $('alCalc');

    // ---------- Thông báo lỗi / trạng thái ----------
    function showErrors(list) {
        if (!errEl) return;
        const arr = (list || []).filter(Boolean);
        if (!arr.length) { errEl.innerHTML = ''; errEl.style.display = 'none'; return; }
        errEl.innerHTML = '<ul style="margin:0;padding:0;">' + arr.map(function (m) {
            return '<li>' + esc(m) + '</li>';
        }).join('') + '</ul>';
        errEl.style.display = 'block';
    }
    function clearErrors() { showErrors([]); }

    function setBusy(on) {
        state.busy = !!on;
        if (calcBtn) {
            calcBtn.disabled = state.busy;
            calcBtn.innerHTML = state.busy
                ? '<i class="fas fa-spinner fa-spin"></i> Đang lấy giá…'
                : '<i class="fas fa-calculator"></i> Tính toán';
        }
        if (state.busy) clearErrors();
    }

    // ---------- Tổng vốn: tự format kiểu Việt Nam (200.000.000) ----------
    function readTotal() {
        const raw = String(totalEl.value || '').replace(/[^\d]/g, '');
        return raw === '' ? NaN : Number(raw);
    }
    function formatTotal() {
        const raw = String(totalEl.value || '').replace(/[^\d]/g, '');
        totalEl.value = raw ? Number(raw).toLocaleString('vi-VN') : '';
    }
    totalEl.addEventListener('input', function () {
        formatTotal();
        try { totalEl.setSelectionRange(totalEl.value.length, totalEl.value.length); } catch (e) { }
        clearErrors();
    });
    totalEl.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); calculate(); }
    });

    // ---------- Danh sách mã (chip) ----------
    function setSymHint(extra) {
        const el = $('alSymHint');
        if (!el) return;
        el.innerHTML = 'Nhập mã rồi bấm <b>+ Thêm mã</b> (hoặc Enter). Có thể dán nhiều mã: <i>HPG, ACB, CTG…</i>' +
            (extra ? ' <span style="color:#008080;font-weight:700">' + esc(extra) + '</span>' : '');
    }
    function renderChips() {
        if (!chipsEl) return;
        chipsEl.innerHTML = state.symbols.map(function (s) {
            return '<span class="al-chip"><b>' + esc(s) + '</b>' +
                '<button type="button" class="al-chip-x" data-sym="' + esc(s) + '" aria-label="Xóa ' + esc(s) + '" title="Xóa ' + esc(s) + '">&times;</button></span>';
        }).join('');
        const cnt = $('alCount');
        if (cnt) cnt.textContent = state.symbols.length
            ? 'Đã chọn ' + state.symbols.length + ' mã'
            : '';
    }
    /** Thêm mã từ ô nhập: viết HOA, bỏ trùng, bỏ ký tự lạ. */
    function addFromInput() {
        const raw = String(symInput.value || '');
        const list = Calc.normalizeSymbols(raw);
        if (!list.length) { symInput.value = ''; return; }
        let dup = 0;
        list.forEach(function (s) {
            if (state.symbols.indexOf(s) === -1) state.symbols.push(s); else dup++;
        });
        symInput.value = '';
        renderChips();
        clearErrors();
        setSymHint(dup ? 'Đã bỏ qua ' + dup + ' mã trùng.' : '');
        symInput.focus();
    }
    function removeSymbol(sym) {
        state.symbols = state.symbols.filter(function (s) { return s !== sym; });
        renderChips();
        clearErrors();
        setSymHint('');
    }
    $('alAdd').addEventListener('click', addFromInput);
    symInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ',' || e.key === ';' || e.key === ' ' || e.key === 'Tab') {
            if (!String(symInput.value || '').trim()) return;
            e.preventDefault();
            addFromInput();
        }
    });
    symInput.addEventListener('blur', function () {
        if (String(symInput.value || '').trim()) addFromInput();
    });
    chipsEl.addEventListener('click', function (e) {
        const btn = e.target.closest('.al-chip-x');
        if (!btn) return;
        removeSymbol(btn.getAttribute('data-sym'));
    });

    // ---------- Lấy giá hiện tại (dùng API giá có sẵn của TuniX) ----------
    function fetchQuote(sym) {
        return fetch('/api/stocks/' + encodeURIComponent(sym) + '/quote', { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)); })
            .then(function (d) { return { symbol: sym, ok: true, data: d }; })
            .catch(function () { return { symbol: sym, ok: false, data: null }; });
    }

    // ---------- Tính toán + hiển thị ----------
    async function calculate() {
        if (state.busy) return;

        // 1) Kiểm tra tổng vốn
        const total = readTotal();
        if (!String(totalEl.value || '').trim() || !isFinite(total)) {
            return showErrors(['Vui lòng nhập tổng số tiền đầu tư.']);
        }
        if (!(total > 0)) return showErrors(['Số tiền đầu tư phải lớn hơn 0.']);
        // 2) Kiểm tra danh sách mã
        if (!state.symbols.length) {
            return showErrors(['Vui lòng nhập ít nhất một mã cổ phiếu.']);
        }

        setBusy(true);
        try {
            // 3) + 4) Lấy giá hiện tại của từng mã (song song) và kiểm tra mã hợp lệ
            const quotes = await Promise.all(state.symbols.map(fetchQuote));
            const items = [];
            const errs = [];
            let maxAsOf = '';
            quotes.forEach(function (q) {
                if (!q.ok) {
                    errs.push('Không thể lấy dữ liệu giá của mã ' + q.symbol + '. Vui lòng thử lại.');
                    return;
                }
                const d = q.data || {};
                if (d.valid === false) {
                    errs.push('Không tìm thấy mã cổ phiếu ' + q.symbol + '.');
                    return;
                }
                // Chỉ số (VNINDEX, VN30…) không phải cổ phiếu mua được → báo như mã không tồn tại.
                if (String(d.exchange || '').toUpperCase() === 'INDEX') {
                    errs.push('Không tìm thấy mã cổ phiếu ' + q.symbol + '.');
                    return;
                }
                const price = Number(d.price || 0);
                if (!(price > 0)) {
                    errs.push('Không thể lấy dữ liệu giá của mã ' + q.symbol + '. Vui lòng thử lại.');
                    return;
                }
                if (d.as_of && String(d.as_of) > maxAsOf) maxAsOf = String(d.as_of);
                items.push({ symbol: q.symbol, price: price, name: d.name || '' });
            });

            // 5) → 9) Đếm mã, chia đều vốn, tính số lượng theo lô, giá trị mua, tiền dư
            const res = Calc.allocateEqualCapital(total, items, Calc.ALLOC_LOT);
            if (res.errors && res.errors.length) return showErrors(res.errors);

            renderResult(res, errs, { fetchedAt: new Date(), asOf: maxAsOf });
        } catch (e) {
            showErrors(['Không thể tính toán do lỗi kết nối dữ liệu. Vui lòng thử lại.']);
            if (resEl) resEl.innerHTML = '';
        } finally {
            setBusy(false);
        }
    }

    function renderResult(res, skipped, meta) {
        const s = res.summary;
        const miss = skipped || [];
        if (!res.rows.length) {
            if (resEl) resEl.innerHTML = '';
            showErrors(miss.length ? miss : ['Không có mã nào lấy được giá. Vui lòng thử lại.']);
            return;
        }
        showErrors(miss);

        const rowsHtml = res.rows.map(function (r) {
            const qtyNote = r.qty === 0
                ? '<div class="al-sub">Chưa đủ 1 lô ' + s.lot + ' cổ phiếu</div>'
                : '';
            return '<tr>' +
                '<td><span class="al-sym">' + esc(r.symbol) + '</span>' +
                    (r.name ? '<div class="al-sub">' + esc(r.name) + '</div>' : '') + '</td>' +
                '<td class="num">' + money(r.price) + '</td>' +
                '<td class="num">' + money(r.capital) + '</td>' +
                '<td class="num"><b>' + r.qty.toLocaleString('vi-VN') + '</b>' + qtyNote + '</td>' +
                '<td class="num">' + money(r.value) + '</td>' +
                '<td class="num al-remain">' + money(r.remain) + '</td>' +
                '</tr>';
        }).join('');

        const asOfTxt = fmtVnFromIso(meta.asOf);
        const html =
            '<div class="ti-card">' +
                '<h3><i class="fas fa-balance-scale"></i> Kết quả chia vốn</h3>' +
                '<div class="al-meta">' +
                    '<span><i class="fas fa-clock"></i> Dữ liệu giá cập nhật lúc: <b>' + esc(fmtNow(meta.fetchedAt)) + '</b></span>' +
                    (asOfTxt ? '<span class="al-dot">•</span><span>Mốc giá thị trường: <b>' + esc(asOfTxt) + '</b></span>' : '') +
                    '<button type="button" class="ti-btn ghost al-refresh" id="alRefresh"><i class="fas fa-sync-alt"></i> Cập nhật giá</button>' +
                '</div>' +
                (miss.length
                    ? '<div class="ti-hint" style="margin-bottom:10px;">Đã bỏ qua ' + miss.length + ' mã không lấy được giá — số mã dùng để chia vốn là <b>' + s.count + '</b>.</div>'
                    : '') +
                '<div class="al-tblwrap"><table class="al-tbl">' +
                    '<thead><tr>' +
                        '<th>Mã</th>' +
                        '<th class="num">Giá hiện tại</th>' +
                        '<th class="num">Vốn phân bổ</th>' +
                        '<th class="num">Số lượng mua</th>' +
                        '<th class="num">Giá trị mua</th>' +
                        '<th class="num">Tiền dư</th>' +
                    '</tr></thead><tbody>' + rowsHtml + '</tbody>' +
                '</table></div>' +
                '<div class="ti-kpis">' +
                    '<div class="kpi"><div class="k-label">Tổng vốn</div><div class="k-val">' + money(s.total) + '</div></div>' +
                    '<div class="kpi"><div class="k-label">Số mã</div><div class="k-val">' + s.count + '</div></div>' +
                    '<div class="kpi"><div class="k-label">Vốn phân bổ / mã</div><div class="k-val">' + money(s.capital) + '</div></div>' +
                    '<div class="kpi hl"><div class="k-label">Tổng giá trị mua</div><div class="k-val">' + money(s.buyValue) + '</div></div>' +
                    '<div class="kpi hl"><div class="k-label">Tổng tiền dư</div><div class="k-val">' + money(s.remain) + '</div></div>' +
                '</div>' +
                '<div class="ti-hint">Lô giao dịch: <b>' + s.lot + '</b> cổ phiếu · Tổng giá trị mua + Tổng tiền dư = Tổng vốn <b>(' + money(s.total) + ')</b>.</div>' +
            '</div>';

        if (resEl) resEl.innerHTML = html;
        const refreshBtn = $('alRefresh');
        if (refreshBtn) refreshBtn.addEventListener('click', function () { calculate(); });
        notifyParentHeight();
    }

    // Bấm "Tính toán"
    calcBtn.addEventListener('click', function () { calculate(); });

    // Cho phép trang khác (nếu cần) gọi lại tính toán, và báo chiều cao cho shell khi mở panel.
    window.TuniXAlloc = {
        recalc: function () { calculate(); },
        setSymbols: function (list) {
            state.symbols = Calc.normalizeSymbols(list);
            renderChips();
        }
    };

    setSymHint('');
    renderChips();

    function notifyParentHeight() {
        try {
            const el = document.querySelector('.ti-page') || document.body;
            const height = Math.ceil(el.getBoundingClientRect().height);
            if (!height || height <= 0) return;
            if (Math.abs(height - (window.__lastSentH || 0)) < 2) return;
            window.__lastSentH = height;
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'tunix-embed-height', height: height }, '*');
            }
        } catch (e) { }
    }
})();
