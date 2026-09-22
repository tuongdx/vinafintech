/* TuniX — Tiện ích: logic giao diện (phụ thuộc tiênich-calc.js). */
(function () {
    const Calc = window.TuniXCalc;
    if (!Calc) { return; }
    if (window.__tiLoaded) return;
    window.__tiLoaded = true;

    function $(id) { return document.getElementById(id); }
    function fmtVND(v) { return Calc.fmtVND(v); }
    function readMoney(input) {
        let s = String(input.value || '').replace(/\s/g, '');
        s = s.replace(/,/g, '').replace(/\./g, '');
        const n = parseInt(s, 10);
        return isFinite(n) ? n : NaN;
    }
    function readDec(input) {
        const s = String(input.value || '').replace(/\s/g, '').replace(',', '.');
        const n = parseFloat(s);
        return isFinite(n) ? n : NaN;
    }
    function fmtInput(input, dec) {
        const n = dec ? readDec(input) : readMoney(input);
        if (isFinite(n)) {
            input.value = n.toLocaleString('vi-VN', { maximumFractionDigits: dec ? 4 : 0 });
        } else if (String(input.value).trim() !== '') {
            input.value = '';
        }
    }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ---------- Bộ chọn công cụ ----------
    const TOOL_META = {
        adj: {
            title: '📈 Giá điều chỉnh cổ phiếu', icon: 'fa-chart-line',
            head: 'Giá điều chỉnh cổ phiếu',
            sub: 'Tính giá cổ phiếu sau các sự kiện thay đổi vốn (cổ tức tiền mặt, cổ tức cổ phiếu, thưởng, phát hành quyền mua).'
        },
        dep: {
            title: '🏦 Lợi nhuận tiền gửi', icon: 'fa-piggy-bank',
            head: 'Lợi nhuận tiền gửi',
            sub: 'Tính tiền lãi ngân hàng theo lãi suất, kỳ hạn, hình thức trả lãi (lãi đơn / lãi kép).'
        },
        ex: {
            title: '💱 Tỷ giá ngoại tệ', icon: 'fa-coins',
            head: 'Tỷ giá ngoại tệ',
            sub: 'Xem tỷ giá và hoán đổi giữa VND với các ngoại tệ phổ biến (USD, JPY, KRW, CNY, SGD, EUR, GBP, TWD, AUD).'
        },
        alloc: {
            title: '⚖️ Chia đều vốn đầu tư', icon: 'fa-balance-scale',
            head: 'Chia đều vốn đầu tư',
            sub: 'Nhập tổng tiền và các mã muốn mua — TuniX tự lấy giá hiện tại, tự chia đều vốn và tính số lượng mua theo lô 100.'
        }
    };
    function showTool(tool) {
        // Panel của từng công cụ. Thiếu panel = HTML phiên bản CŨ bị cache ở trình duyệt
        // (xem ghi chú: công cụ mới phải được bump ?v= khi sửa tienich.html/tienich.js).
        const panels = { adj: $('panelAdj'), dep: $('panelDep'), ex: $('panelEx'), alloc: $('panelAlloc') };
        let key = TOOL_META[tool] ? tool : 'adj';
        // PHÒNG HỜ: công cụ được yêu cầu nhưng PANEL không tồn tại trong HTML hiện có
        // → mở công cụ đầu tiên CÓ panel, để vùng công cụ KHÔNG bị trống (trước đây trang
        // chỉ còn đúng dòng ghi chú cuối trang ⇒ người dùng tưởng tính năng bị ẩn theo tài khoản).
        if (!panels[key]) {
            const first = Object.keys(panels).filter(function (k) { return !!panels[k]; })[0];
            if (first) key = first;
        }
        Object.keys(panels).forEach(function (k) {
            if (panels[k]) panels[k].style.display = k === key ? 'block' : 'none';
        });
        const meta = TOOL_META[key] || TOOL_META.adj;
        // Toolbar title: chỉ giữ emoji + tên công cụ (bỏ icon FA trước title vì trùng/không cần).
        $('toolTitle').innerHTML = '<span>' + esc(meta.title) + '</span>';
        // Khối xanh đầu trang đổi text theo công cụ (cùng vị trí/màu, chỉ khác tiêu đề).
        const hh = document.querySelector('.ti-head h1');
        if (hh) hh.textContent = meta.head || 'Tiện ích';
        const hs = document.querySelector('.ti-head .ti-sub');
        if (hs) hs.textContent = meta.sub || '';
        document.querySelectorAll('.ti-chip').forEach(function (c) {
            c.classList.toggle('on', c.getAttribute('data-tool') === key);
        });
        setTimeout(notifyParentHeight, 60);
    }
    document.querySelectorAll('.ti-chip').forEach(function (el) {
        el.addEventListener('click', function () {
            const t = el.getAttribute('data-tool');
            showTool(t);
            // Báo shell đổi công cụ → cập nhật link URL đẹp /tien-ich/{slug} (không reload iframe).
            if (window.parent && window.parent !== window) {
                try { window.parent.postMessage({ type: 'tunix-ti-nav', tool: t }, '*'); } catch (e) {}
            }
        });
    });

    // ================= Tool 1: Giá điều chỉnh =================
    const EV_TYPES = ['cash', 'stock', 'bonus', 'rights'];
    function evTypeLabel(t) { return Calc.EVENT_LABELS[t] || t; }

    function evFieldsHtml(t) {
        let h = '';
        if (t === 'cash') {
            h += '<div class="ti-field"><label>Số tiền cổ tức <span class="opt">(đ/cổ phiếu)</span></label>' +
                '<div class="ti-input-wrap"><input type="text" class="ev-cash" inputmode="numeric" placeholder="VD: 1000" /><span class="pre">đ/cp</span></div></div>';
        } else if (t === 'rights') {
            h += '<div class="ti-field"><label>Tỷ lệ quyền mua <span class="opt">(%)</span></label>' +
                '<div class="ti-input-wrap"><input type="text" class="ev-rate" inputmode="decimal" placeholder="VD: 20" /><span class="pre">%</span></div></div>' +
                '<div class="ti-field"><label>Giá phát hành <span class="opt">(đ/cổ phiếu)</span></label>' +
                '<div class="ti-input-wrap"><input type="text" class="ev-ip" inputmode="numeric" placeholder="VD: 15000" /><span class="pre">đ</span></div></div>';
        } else {
            const nm = t === 'stock' ? 'cổ tức bằng cổ phiếu' : 'cổ phiếu thưởng';
            h += '<div class="ti-field"><label>Tỷ lệ ' + nm + ' <span class="opt">(%)</span></label>' +
                '<div class="ti-input-wrap"><input type="text" class="ev-rate" inputmode="decimal" placeholder="VD: 20" /><span class="pre">%</span></div></div>';
        }
        return h;
    }
    function evSummary(block) {
        const t = block.querySelector('.ev-type').value;
        const parts = [];
        if (t === 'cash') { const v = readMoney(block.querySelector('.ev-cash')); if (isFinite(v)) parts.push(fmtVND(v) + '/cổ phiếu'); }
        else if (t === 'rights') {
            const r = readDec(block.querySelector('.ev-rate'));
            const ip = readMoney(block.querySelector('.ev-ip'));
            if (isFinite(r)) parts.push(Calc.round(r, 4) + '%');
            if (isFinite(ip)) parts.push('giá phát hành ' + fmtVND(ip));
        } else if (t === 'stock' || t === 'bonus') { const r = readDec(block.querySelector('.ev-rate')); if (isFinite(r)) parts.push(Calc.round(r, 4) + '%'); }
        return parts.join(' — ');
    }
    function renumberEvents() {
        document.querySelectorAll('#evList .ev').forEach(function (b, i) {
            b.querySelector('.ev-idx').textContent = (i + 1);
            const s = evSummary(b);
            b.querySelector('.ev-summary').textContent = s ? ('Đang nhập: ' + s) : '';
        });
    }
    function addEvent(type) {
        const wrap = $('evList');
        const block = document.createElement('div');
        block.className = 'ev';
        block.innerHTML =
            '<div class="ev-head">' +
                '<div class="ev-name">Sự kiện <span class="ev-idx"></span></div>' +
                '<button type="button" class="ev-rm" title="Xóa sự kiện"><i class="fas fa-trash"></i></button>' +
            '</div>' +
            '<div class="ti-input-wrap" style="margin-bottom:10px;"><select class="ev-type">' +
                EV_TYPES.map(function (t) { return '<option value="' + t + '">' + esc(evTypeLabel(t)) + '</option>'; }).join('') +
            '</select></div>' +
            '<div class="ev-fields"></div>' +
            '<div class="ev-summary"></div>';
        block.querySelector('.ev-type').value = type || EV_TYPES[0];
        block.querySelector('.ev-fields').innerHTML = evFieldsHtml(block.querySelector('.ev-type').value);
        block.querySelector('.ev-type').addEventListener('change', function () {
            block.querySelector('.ev-fields').innerHTML = evFieldsHtml(this.value);
            renumberEvents();
        });
        block.querySelectorAll('.ev-fields input').forEach(function (inp) {
            inp.addEventListener('blur', function () { fmtInput(inp, inp.classList.contains('ev-rate')); renumberEvents(); });
        });
        block.querySelector('.ev-rm').addEventListener('click', function () {
            block.remove();
            if (!wrap.children.length) addEvent('cash');
            renumberEvents();
            setTimeout(notifyParentHeight, 40);
        });
        wrap.appendChild(block);
        renumberEvents();
        setTimeout(notifyParentHeight, 40);
    }
    $('addEv').addEventListener('click', function () { addEvent(); });
    $('resetAdj').addEventListener('click', function () {
        $('adjPrice').value = ''; $('adjQty').value = ''; $('adjResult').innerHTML = '';
        $('evList').innerHTML = ''; addEvent('cash');
        setTimeout(notifyParentHeight, 40);
    });

    function renderAdjResult(r) {
        const el = $('adjResult');
        if (!r || r.errors) {
            const errs = (r && r.errors) || ['Không tính được.'];
            el.innerHTML = '<div class="err-list"><ul>' + errs.map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul></div>';
            notifyParentHeight();
            return;
        }
        let html = '<div class="ti-result">';
        html += '<div class="ti-card"><h3><i class="fas fa-arrow-down"></i> Quá trình điều chỉnh</h3><div class="adj-chain">';
        html += '<div class="adj-step"><div class="as-name">Giá trước điều chỉnh</div><div class="as-val">' + fmtVND(r.priceBefore, 0) + ' <b>đ/cổ phiếu</b></div></div>';
        r.steps.forEach(function (s) {
            html += '<div class="adj-arrow"><i class="fas fa-arrow-down"></i></div>';
            html += '<div class="adj-step">' +
                '<div class="as-name"><span class="n">Sự kiện ' + s.index + '</span> — ' + esc(s.label) + '</div>' +
                (s.detail ? '<div class="as-detail">' + esc(s.detail) + '</div>' : '') +
                (s.formula ? '<div class="as-math">Công thức: ' + esc(s.formula) + '</div>' : '') +
                '<div class="as-val">' + fmtVND(s.before, 0) + ' → <b>' + fmtVND(s.after, 0) + ' đ/cổ phiếu</b></div>' +
                (s.sharesReceived != null ? '<div class="as-detail">Nhận thêm ' + s.sharesReceived + ' cổ phiếu</div>' : '') +
                (s.cashReceived != null ? '<div class="as-detail">Nhận cổ tức tiền mặt: ' + fmtVND(s.cashReceived) + '</div>' : '') +
                (s.rightsPaid != null ? '<div class="as-detail">Tiền mua quyền: ' + fmtVND(s.rightsPaid) + '</div>' : '') +
                '</div>';
        });
        html += '<div class="adj-arrow"><i class="fas fa-arrow-down"></i></div>';
        html += '<div class="adj-final"><div class="af-label">Giá điều chỉnh cuối cùng</div>' +
                '<div class="af-price">' + fmtVND(r.finalPrice, 0) + '<span>/cổ phiếu</span></div></div>';
        html += '</div></div>';

        if (r.hasQty) {
            html += '<div class="ti-card"><h3><i class="fas fa-hashtag"></i> Diễn biến số cổ phiếu</h3><div class="ti-kpis">' +
                '<div class="kpi"><div class="k-label">Số cổ phiếu ban đầu</div><div class="k-val">' + r.startQty + '</div></div>' +
                '<div class="kpi"><div class="k-label">Tổng cổ phiếu nhận thêm</div><div class="k-val">' + r.totalSharesReceived + '</div></div>' +
                '<div class="kpi hl"><div class="k-label">Tổng cổ phiếu sau sự kiện</div><div class="k-val">' + r.finalQty + '</div></div>' +
                (r.totalCashDividend > 0 ? '<div class="kpi"><div class="k-label">Cổ tức tiền mặt nhận</div><div class="k-val">' + fmtVND(r.totalCashDividend) + '</div></div>' : '') +
                (r.totalRightsPay > 0 ? '<div class="kpi"><div class="k-label">Tiền mua quyền (nộp thêm)</div><div class="k-val">' + fmtVND(r.totalRightsPay) + '</div></div>' : '') +
                '</div>' +
                '<div class="ti-kpis">' +
                '<div class="kpi"><div class="k-label">Giá trị danh nghĩa trước</div><div class="k-val">' + fmtVND(r.nominalBefore) + '</div></div>' +
                '<div class="kpi"><div class="k-label">Giá trị danh nghĩa sau</div><div class="k-val">' + fmtVND(r.nominalAfter) + '</div></div>' +
                '</div></div>';
        }
        html += '<div class="ti-note"><i class="fas fa-circle-info"></i> Giá điều chỉnh là mức giá <b>lý thuyết</b> được tính dựa trên các sự kiện doanh nghiệp. Giá giao dịch thực tế có thể khác do cung cầu thị trường. Không sử dụng kết quả này để đưa ra khuyến nghị mua, bán hoặc nắm giữ cổ phiếu.</div>';
        html += '</div>';
        el.innerHTML = html;
        notifyParentHeight();
    }
    $('calcAdj').addEventListener('click', function () {
        const price = readMoney($('adjPrice'));
        const qtyRaw = $('adjQty').value.trim();
        const events = [];
        document.querySelectorAll('#evList .ev').forEach(function (b) {
            const t = b.querySelector('.ev-type').value;
            const e = { type: t };
            if (t === 'cash') { e.cash = readMoney(b.querySelector('.ev-cash')); }
            else if (t === 'rights') { e.rate = readDec(b.querySelector('.ev-rate')); e.issuePrice = readMoney(b.querySelector('.ev-ip')); }
            else { e.rate = readDec(b.querySelector('.ev-rate')); }
            events.push(e);
        });
        const r = Calc.calcAdjusted(price, events, qtyRaw === '' ? undefined : parseInt(String(qtyRaw).replace(/\D/g, ''), 10));
        renderAdjResult(r);
    });

    // ================= Tool 2: Lợi nhuận tiền gửi =================
    function updateDepHint() {
        const mode = $('depMode').value;
        const hints = {
            end: 'Lãi được trả một lần khi đáo hạn (lãi đơn).',
            month: 'Lãi trả hàng tháng; gốc giữ nguyên đến khi đáo hạn.',
            quarter: 'Lãi trả mỗi quý; gốc giữ nguyên đến khi đáo hạn.',
            compound: 'Lãi được nhập vào gốc hàng tháng (lãi kép).'
        };
        $('depHint').textContent = hints[mode] || '';
    }
    $('depMode').addEventListener('change', updateDepHint);
    $('depRecurring').addEventListener('change', function () {
        $('depExtraRow').style.display = this.checked ? 'grid' : 'none';
        setTimeout(notifyParentHeight, 40);
    });

    function drawChart(points) {
        const host = $('depChart');
        if (!host || !points || points.length < 2) return;
        const W = 640, H = 210, PL = 14, PR = 10, PT = 12, PB = 26;
        let min = points[0].value, max = points[0].value;
        points.forEach(function (p) { if (p.value < min) min = p.value; if (p.value > max) max = p.value; });
        if (max === min) max = min * 1.1 + 1;
        const pad = (max - min) * 0.08; min -= pad; max += pad;
        function X(i) { return PL + (i / (points.length - 1)) * (W - PL - PR); }
        function Y(v) { return H - PB - ((v - min) / (max - min)) * (H - PT - PB); }
        let d = '';
        points.forEach(function (p, i) { d += (i ? ' L' : 'M') + X(i).toFixed(1) + ' ' + Y(p.value).toFixed(1); });
        const area = d + ' L' + X(points.length - 1).toFixed(1) + ' ' + (H - PB) + ' L' + X(0).toFixed(1) + ' ' + (H - PB) + ' Z';
        const n = points.length;
        const ticks = Math.min(6, n);
        const idxs = [];
        for (let i = 0; i < ticks; i++) {
            idxs.push(Math.round(i * (n - 1) / (ticks - 1)));
        }
        host.innerHTML =
            '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" role="img">' +
            '<defs><linearGradient id="tig" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0" stop-color="#008080" stop-opacity="0.22"/><stop offset="1" stop-color="#008080" stop-opacity="0.02"/></linearGradient></defs>' +
            '<path d="' + area + '" fill="url(#tig)"></path>' +
            '<path d="' + d + '" fill="none" stroke="#008080" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"></path>' +
            idxs.map(function (idx) {
                return '<text x="' + X(idx).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="9.5" fill="#8a99b1">' + points[idx].m + ' th</text>';
            }).join('') +
            '<text x="' + (W - PR) + '" y="' + (PT + 8) + '" text-anchor="end" font-size="10" fill="#5a6b85">' + fmtVND(points[n - 1].value) + '</text>' +
            '</svg>';
    }

    function renderDepResult(r) {
        const el = $('depResult');
        if (!r || r.errors) {
            const errs = (r && r.errors) || ['Không tính được.'];
            el.innerHTML = '<div class="err-list"><ul>' + errs.map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul></div>';
            notifyParentHeight();
            return;
        }
        let html = '<div class="ti-result"><div class="ti-card">';
        html += '<h3><i class="fas fa-piggy-bank"></i> Kết quả tiền gửi</h3>';
        html += '<div class="ti-kpis">' +
            '<div class="kpi"><div class="k-label">Tiền gốc</div><div class="k-val">' + fmtVND(r.principal) + '</div></div>' +
            (r.totalExtra > 0 ? '<div class="kpi"><div class="k-label">Tổng tiền gửi thêm</div><div class="k-val">' + fmtVND(r.totalExtra) + '</div></div>' : '') +
            '<div class="kpi"><div class="k-label">Tổng tiền đã gửi</div><div class="k-val">' + fmtVND(r.totalDeposited) + '</div></div>' +
            '<div class="kpi hl"><div class="k-label">Tiền lãi</div><div class="k-val">' + fmtVND(r.totalInterest) + '</div></div>' +
            '<div class="kpi"><div class="k-label">Lãi suất</div><div class="k-val">' + Calc.round(r.rateAnnualPct, 2) + '%/năm</div></div>' +
            '<div class="kpi"><div class="k-label">Thời gian gửi</div><div class="k-val">' + r.months + ' tháng</div></div>' +
            '</div>';
        html += '<div class="adj-final" style="margin-top:12px;"><div class="af-label">Tổng tiền nhận được</div>' +
                '<div class="af-price">' + fmtVND(r.totalReceive) + '</div></div>';
        html += '<div class="ti-hint" style="margin-top:8px;">Hình thức: <b>' + esc(r.modeLabel) + '</b>. ' + esc(r.scheduleNote) + '</div>';
        html += '</div>';
        if (r.points && r.points.length >= 2) {
            html += '<div class="ti-card ti-chart"><h3><i class="fas fa-chart-area"></i> Tăng trưởng giá trị theo thời gian</h3><div id="depChart"></div>' +
                    '<div class="ti-hint">' + (r.mode === 'compound' ? 'Giá trị bao gồm lãi nhập gốc (lãi kép).' : 'Giá trị tích lũy gốc + lãi (lãi đơn, giả định giữ đến đáo hạn).') + '</div></div>';
        }
        html += '<div class="ti-note"><i class="fas fa-circle-info"></i> Kết quả là ước tính lý thuyết theo lãi suất danh nghĩa, chưa gồm phí/thuế và có thể khác ngân hàng thực tế (cách quy tròn, ngày tính lãi). Không phải khuyến nghị đầu tư.</div>';
        html += '</div>';
        el.innerHTML = html;
        if (r.points && r.points.length >= 2) drawChart(r.points);
        notifyParentHeight();
    }
    $('calcDep').addEventListener('click', function () {
        const principal = readMoney($('depPrincipal'));
        const rate = readDec($('depRate'));
        const months = readMoney($('depMonths'));
        const mode = $('depMode').value;
        const recurring = $('depRecurring').checked;
        const extra = recurring ? readMoney($('depExtra')) : 0;
        const r = Calc.calcDeposit({ principal: principal, rateAnnualPct: rate, months: months, mode: mode, monthlyExtra: extra });
        renderDepResult(r);
    });
    $('exampleDep').addEventListener('click', function () {
        $('depPrincipal').value = '100,000,000';
        $('depRate').value = '5';
        $('depMonths').value = '12';
        $('depMode').value = 'compound';
        $('depRecurring').checked = false;
        $('depExtraRow').style.display = 'none';
        updateDepHint();
        $('calcDep').click();
    });
    $('resetDep').addEventListener('click', function () {
        ['depPrincipal', 'depRate', 'depMonths', 'depExtra'].forEach(function (id) { $(id).value = ''; });
        $('depResult').innerHTML = '';
        $('depRecurring').checked = false;
        $('depExtraRow').style.display = 'none';
        updateDepHint();
        setTimeout(notifyParentHeight, 40);
    });
    ['adjPrice', 'adjQty', 'depPrincipal', 'depMonths', 'depExtra'].forEach(function (id) {
        $(id).addEventListener('blur', function () { fmtInput($(id), false); });
    });
    $('depRate').addEventListener('blur', function () { fmtInput($('depRate'), true); });
    ['adjPrice', 'adjQty', 'depPrincipal', 'depRate', 'depMonths', 'depExtra'].forEach(function (id) {
        $(id).addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
    });

    updateDepHint();
    addEvent('cash');

    // Hiển thị thẳng một công cụ; menu con truyền ?tool=adj|dep|ex|alloc để mở đúng màn hình.
    // Dùng TOOL_META làm nguồn duy nhất ⇒ công cụ mới thêm vào TOOL_META là deep-link được ngay
    // (trước đây là chuỗi if/else nên công cụ mới dễ bị rơi vào nhánh mặc định).
    (function () {
        try {
            const t = new URLSearchParams(location.search).get('tool');
            showTool(TOOL_META[t] ? t : 'adj');
        } catch (e) {
            showTool('adj');
        }
    })();

    // ===== Embed / chiều cao / theme =====
    if (window.self !== window.top) document.body.classList.add('embedded');
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
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
        } catch (e) {}
    }
    window.addEventListener('message', function (event) {
        const data = event.data;
        if (!data) return;
        // Shell báo đổi công cụ (đã mở sẵn trang Tiện ích) → hiển thị đúng màn hình.
        if (data.type === 'tunix-ti-tool' && (data.tool === 'adj' || data.tool === 'dep' || data.tool === 'ex' || data.tool === 'alloc')) {
            showTool(data.tool);
            return;
        }
        if (data.type !== 'tunix-theme') return;
        const mode = data.mode === 'dark' ? 'dark' : 'light';
        localStorage.setItem('theme', mode);
        applyEmbedTheme(mode);
        setTimeout(notifyParentHeight, 50);
    });
    window.addEventListener('load', function () {
        applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
        setTimeout(notifyParentHeight, 60);
        setTimeout(notifyParentHeight, 400);
    });
    window.addEventListener('resize', notifyParentHeight);
    if (window.ResizeObserver) {
        const obs = new ResizeObserver(notifyParentHeight);
        obs.observe(document.querySelector('.ti-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
    notifyParentHeight();
})();
