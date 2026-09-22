/* TuniX — Lãi ngân hàng (trang lai-ngan-hang.html)
   Giai đoạn 1: đọc dữ liệu tổng hợp tham khảo từ API /api/bank-rates/* (không realtime).
   Nếu Giai đoạn 2 thay nguồn → backend vẫn trả đúng contract này, UI không đổi.
*/
(function () {
    'use strict';

    var API = '/api/bank-rates';
    var CH_LABEL = { online: 'Gửi Online', counter: 'Gửi tại quầy' };

    var state = {
        meta: null,
        channel: 'online',
        termSort: 12,
        mxSort: 'bank',
        search: '',
        rows: [] // mọi dòng lãi suất của channel hiện tại
    };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function fmtMoney(n) {
        n = Number(n);
        if (!isFinite(n)) return '—';
        return Math.round(n).toLocaleString('vi-VN') + ' đ';
    }
    function fmtRate(r) {
        r = Number(r);
        if (!isFinite(r)) return '—';
        return r.toFixed(2).replace('.', ',') + '%/năm';
    }
    function fmtRateShort(r) {
        r = Number(r);
        if (!isFinite(r)) return '—';
        return r.toFixed(2).replace('.', ',') + '%';
    }
    function parseMoney(str) {
        var digits = String(str == null ? '' : str).replace(/\D/g, '');
        if (!digits) return NaN;
        return parseInt(digits, 10);
    }
    function termLabel(t) { return t + ' tháng'; }

    // ---- Logo ngân hàng (VietQR — CDN công khai, logo chính thức) ----
    // key = bank_code TuniX → value = mã logo trên cdn.vietqr.io/img/{code}.png
    var BANK_LOGO = {
        'VCB': 'VCB', 'BID': 'BIDV', 'CTG': 'ICB', 'AGB': 'VBA', 'TCB': 'TCB', 'MBB': 'MB', 'ACB': 'ACB',
        'VPB': 'VPB', 'SHB': 'SHB', 'HDB': 'HDB', 'SGB': 'SGICB', 'PGB': 'PGB', 'LPB': 'LPB', 'OCB': 'OCB',
        'BVB': 'BVB', 'CIM': 'CIMB', 'MSB': 'MSB', 'VIB': 'VIB', 'NCB': 'NCB', 'NAB': 'NAB', 'ABB': 'ABB',
        'VBB': 'VIETBANK', 'STB': 'STB', 'SHR': 'SHBVN', 'WRB': 'WVN', 'BAV': 'BVB', 'BAB': 'BAB', 'UOB': 'UOB',
        'TPB': 'TPB', 'KLB': 'KLB', 'VAB': 'VAB', 'PVC': 'PVCB', 'GPB': 'GPB', 'EIB': 'EIB', 'SSB': 'SEAB',
        'STC': 'SCVN', 'SCB': 'SCB', 'HSB': 'HSBC'
    };
    var BANK_LOGO_CDN = 'https://cdn.vietqr.io/img/';
    function bankLogoHtml(code, size) {
        var vq = BANK_LOGO[String(code == null ? '' : code).toUpperCase()];
        if (!vq) return '';
        return '<img class="lnh-bank-logo' + (size === 'sm' ? ' sm' : '') + '" loading="lazy" alt="" src="' + BANK_LOGO_CDN + vq + '.png" onerror="this.style.display=\'none\'">';
    }

    function api(path, params) {
        var url = API + path;
        if (params) {
            var qs = Object.keys(params).filter(function (k) { return params[k] !== '' && params[k] != null; })
                .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
                .join('&');
            if (qs) url += '?' + qs;
        }
        return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    // ============ Meta + fill selects ============
    function fillMeta(m) {
        state.meta = m;
        var upd = m.updated_at ? new Date(m.updated_at) : null;
        $('metaUpdated').textContent = upd && !isNaN(upd)
            ? upd.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
                + (m.source_url ? ' · ' + upd.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' }) : '')
            : '—';

        var terms = m.terms || [];
        var banks = m.banks || [];
        var channels = m.channels || ['online', 'counter'];

        // ngân hàng
        var selBank = $('lnhBank');
        selBank.innerHTML = '';
        banks.forEach(function (b) {
            var o = document.createElement('option');
            o.value = b.code;
            o.textContent = b.name + ' (' + b.code + ')';
            selBank.appendChild(o);
        });

        // kỳ hạn (nhiều select)
        ['lnhTerm', 'lnhCmpTerm', 'lnhTermSort'].forEach(function (id) {
            var sel = $(id);
            sel.innerHTML = '';
            terms.forEach(function (t) {
                var o = document.createElement('option');
                o.value = t;
                o.textContent = termLabel(t);
                sel.appendChild(o);
            });
            if (sel.options.length) sel.value = String(12); // mặc định 12 tháng nếu có
        });

        // hình thức gửi
        var selCh = $('lnhChannel');
        selCh.innerHTML = '';
        channels.forEach(function (c) {
            var o = document.createElement('option');
            o.value = c;
            o.textContent = CH_LABEL[c] || c;
            selCh.appendChild(o);
        });
        if (selCh.options.length) selCh.value = 'online';

        // ẩn chip không có channel
        Array.prototype.forEach.call(document.querySelectorAll('#lnhChannelChips .lnh-chip'), function (btn) {
            btn.style.display = (channels.indexOf(btn.getAttribute('data-ch')) >= 0) ? '' : 'none';
        });
    }

    // ============ Matrix ============
    function setChannelChips(ch) {
        state.channel = ch;
        Array.prototype.forEach.call(document.querySelectorAll('#lnhChannelChips .lnh-chip'), function (b) {
            b.classList.toggle('on', b.getAttribute('data-ch') === ch);
        });
        var sel = $('lnhChannel');
        for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === ch) { sel.value = ch; break; }
        }
        $('lnhCmpChannelLabel').textContent = CH_LABEL[ch] || ch;
    }

    function loadMatrix(ch) {
        $('lnhMxBody').innerHTML = '<tr><td colspan="20" class="lnh-empty">Đang tải dữ liệu…</td></tr>';
        api('/rates', { channel: ch, sort: 'bank' }).then(function (rows) {
            state.rows = rows || [];
            renderMatrix();
            notifyParentHeight();
        }).catch(function () {
            $('lnhMxBody').innerHTML = '<tr><td colspan="20" class="lnh-empty">Không tải được dữ liệu. Vui lòng thử lại sau.</td></tr>';
        });
    }

    function orderedBanks() {
        var meta = state.meta || { terms: [], banks: [] };
        var terms = meta.terms || [];
        var rows = state.rows || [];
        var map = {}; // bank_code -> { name, byTerm:{term:rate}, code }
        rows.forEach(function (r) {
            if (!map[r.bank_code]) map[r.bank_code] = { code: r.bank_code, name: r.bank_name, byTerm: {} };
            map[r.bank_code].byTerm[r.term] = r.interest_rate;
        });
        var list = Object.keys(map).map(function (k) { return map[k]; });
        var q = (state.search || '').toLowerCase();
        if (q) {
            list = list.filter(function (b) {
                return (b.code + ' ' + b.name).toLowerCase().indexOf(q) >= 0;
            });
        }
        var term = state.termSort;
        if (state.mxSort === 'rate') {
            list.sort(function (a, b) {
                var ra = a.byTerm[term];
                var rb = b.byTerm[term];
                if (ra == null && rb == null) return a.name.localeCompare(b.name, 'vi');
                if (ra == null) return 1;
                if (rb == null) return -1;
                if (rb !== ra) return rb - ra;
                return a.name.localeCompare(b.name, 'vi');
            });
        } else {
            list.sort(function (a, b) { return a.name.localeCompare(b.name, 'vi'); });
        }
        return list;
    }

    function renderMatrix() {
        var meta = state.meta || { terms: [] };
        var terms = meta.terms || [];
        var head = $('lnhMxHead');
        var body = $('lnhMxBody');

        var thead = document.createElement('tr');
        var th0 = document.createElement('th');
        th0.className = 'sticky';
        th0.textContent = 'Ngân hàng';
        thead.appendChild(th0);
        terms.forEach(function (t) {
            var th = document.createElement('th');
            th.textContent = termLabel(t);
            th.dataset.term = t;
            if (t === state.termSort) th.classList.add('col-on');
            thead.appendChild(th);
        });
        head.innerHTML = '';
        head.appendChild(thead);

        var banks = orderedBanks();
        if (!banks.length) {
            body.innerHTML = '<tr><td colspan="20" class="lnh-empty">Không có ngân hàng nào khớp.</td></tr>';
            return;
        }

        // tính mức cao nhất ở cột termSort
        var bestRate = null;
        banks.forEach(function (b) {
            var r = b.byTerm[state.termSort];
            if (r != null && (bestRate == null || r > bestRate)) bestRate = r;
        });

        var frag = document.createDocumentFragment();
        banks.forEach(function (b) {
            var tr = document.createElement('tr');
            var td0 = document.createElement('td');
            td0.className = 'sticky';
            td0.innerHTML = '<span class="lnh-bcell">' + bankLogoHtml(b.code) + '<span class="lnh-bname">' + esc(b.name) + '</span></span>';
            tr.appendChild(td0);
            terms.forEach(function (t) {
                var td = document.createElement('td');
                var r = b.byTerm[t];
                if (r == null) {
                    td.className = 'na';
                    td.textContent = '—';
                } else {
                    td.className = 'rate';
                    td.textContent = fmtRateShort(r);
                    td.dataset.bank = b.code;
                    td.dataset.term = t;
                    td.title = b.name + ' · ' + termLabel(t) + ' · ' + fmtRate(r);
                    if (t === state.termSort) td.classList.add('col-on');
                    if (r === bestRate && t === state.termSort) td.classList.add('best');
                }
                tr.appendChild(td);
            });
            frag.appendChild(tr);
        });
        body.innerHTML = '';
        body.appendChild(frag);
    }

    function syncTermSort(t) {
        state.termSort = Number(t);
        var sel = $('lnhTermSort');
        for (var i = 0; i < sel.options.length; i++) {
            if (Number(sel.options[i].value) === state.termSort) { sel.value = String(state.termSort); break; }
        }
        renderMatrix();
        notifyParentHeight();
    }

    // ============ Tính lãi ============
    function showCalcError(msg) {
        $('lnhCalcResult').innerHTML = '<div class="lnh-msg">' + esc(msg) + '</div>';
        notifyParentHeight();
    }

    function runEstimate() {
        var amount = parseMoney($('lnhAmount').value);
        if (!(amount > 0)) { showCalcError('Vui lòng nhập số tiền gửi hợp lệ (lớn hơn 0).'); return; }
        var bank = $('lnhBank').value;
        var term = Number($('lnhTerm').value);
        var channel = $('lnhChannel').value;
        var host = $('lnhCalcResult');
        host.innerHTML = '<div class="lnh-msg info">Đang tính…</div>';
        api('/estimate', { amount: amount, bank: bank, term: term, channel: channel }).then(function (r) {
            if (!r.found) {
                host.innerHTML = '<div class="lnh-msg">' + esc(r.error || 'Chưa có lãi suất cho lựa chọn này.') + '</div>';
                notifyParentHeight();
                return;
            }
            var rate = fmtRate(r.interest_rate);
            var html = '';
            html += '<div class="lnh-rate-line"><i class="fas fa-percent"></i> Lãi suất áp dụng: <span class="rr">' + esc(rate) + '</span>';
            html += '<span class="lnh-rate-bank">' + bankLogoHtml(r.bank_code, 'sm') + '<span>' + esc(r.bank_name) + ' (' + esc(r.bank_code) + ') · ' + esc(r.channel_label) + ' · ' + termLabel(r.term) + '</span></span></div>';
            if (r.min_amount) html += '<div class="lnh-hint">Số tiền tối thiểu: ' + fmtMoney(r.min_amount) + '</div>';
            if (r.conditions) html += '<div class="lnh-hint">' + esc(r.conditions) + '</div>';
            html += '<div class="lnh-kpis">';
            html += '<div class="lnh-kpi"><div class="lk-label">Số tiền gửi</div><div class="lk-val">' + fmtMoney(r.amount) + '</div></div>';
            html += '<div class="lnh-kpi"><div class="lk-label">Lãi suất</div><div class="lk-val">' + esc(rate) + '</div></div>';
            html += '<div class="lnh-kpi"><div class="lk-label">Tiền lãi dự kiến</div><div class="lk-val">' + fmtMoney(r.expected_interest) + '</div></div>';
            html += '<div class="lnh-kpi hl"><div class="lk-label">Tổng nhận khi đáo hạn</div><div class="lk-val">' + fmtMoney(r.total_receive) + '</div></div>';
            html += '</div>';
            host.innerHTML = html;
            notifyParentHeight();
        }).catch(function () {
            showCalcError('Không kết nối được máy chủ. Vui lòng thử lại sau.');
        });
    }

    // ============ So sánh ============
    function runCompare() {
        var amount = parseMoney($('lnhCmpAmount').value);
        if (!(amount > 0)) {
            $('lnhCompareResult').innerHTML = '<div class="lnh-msg">Vui lòng nhập số tiền gửi hợp lệ (lớn hơn 0).</div>';
            notifyParentHeight();
            return;
        }
        var term = Number($('lnhCmpTerm').value);
        var channel = $('lnhChannel').value;
        var host = $('lnhCompareResult');
        host.innerHTML = '<div class="lnh-msg info">Đang so sánh…</div>';
        api('/compare', { amount: amount, term: term, channel: channel }).then(function (r) {
            var items = (r.items || []).slice();
            if (!items.length) {
                host.innerHTML = '<div class="lnh-msg">Chưa có dữ liệu so sánh cho lựa chọn này.</div>';
                notifyParentHeight();
                return;
            }
            var sort = $('lnhCmpSort').value;
            if (sort === 'rate_desc') items.sort(function (a, b) { return b.interest_rate - a.interest_rate || a.bank_name.localeCompare(b.bank_name, 'vi'); });
            else if (sort === 'rate_asc') items.sort(function (a, b) { return a.interest_rate - b.interest_rate || a.bank_name.localeCompare(b.bank_name, 'vi'); });
            else items.sort(function (a, b) { return a.bank_name.localeCompare(b.bank_name, 'vi'); });

            var bestRate = items[0] ? items[0].interest_rate : null;
            var html = '<div class="lnh-tblwrap" style="margin-top:12px;"><table class="lnh-table lnh-compare-table" style="min-width:0;width:100%;">';
            html += '<thead><tr><th style="text-align:left">Ngân hàng</th><th>Hình thức</th><th>Lãi suất</th><th class="num">Tiền lãi dự kiến</th><th class="num">Tổng nhận khi đáo hạn</th></tr></thead><tbody>';
            items.forEach(function (it, idx) {
                var best = it.interest_rate === bestRate;
                html += '<tr' + (best ? ' class="lnh-best-row"' : '') + '>';
                html += '<td style="text-align:left"><span class="lnh-bcell">' + bankLogoHtml(it.bank_code) + '<span class="lnh-bname">' + esc(it.bank_name) + '</span></span>' + (best ? '<span class="lnh-badge">Lãi cao nhất</span>' : '') + '</td>';
                html += '<td>' + esc(it.channel_label) + '</td>';
                html += '<td><b>' + fmtRateShort(it.interest_rate) + '</b></td>';
                html += '<td class="num">' + fmtMoney(it.expected_interest) + '</td>';
                html += '<td class="num cmp-total">' + fmtMoney(it.total_receive) + '</td>';
                html += '</tr>';
            });
            html += '</tbody></table></div>';
            html += '<div class="lnh-hint" style="margin-top:8px;">Gửi <b>' + fmtMoney(r.amount) + '</b> kỳ hạn <b>' + termLabel(r.term) + '</b>, ' + esc(CH_LABEL[channel] || channel) + '. Lãi ước tính theo lãi đơn trả lãi cuối kỳ.</div>';
            host.innerHTML = html;
            notifyParentHeight();
        }).catch(function () {
            host.innerHTML = '<div class="lnh-msg">Không kết nối được máy chủ. Vui lòng thử lại sau.</div>';
            notifyParentHeight();
        });
    }

    function fillExampleCalc() {
        $('lnhAmount').value = '500.000.000';
        var bank = $('lnhBank');
        for (var i = 0; i < bank.options.length; i++) {
            if (bank.options[i].value === 'VCB') { bank.value = 'VCB'; break; }
        }
        setChannelChips('online');
        $('lnhTerm').value = '12';
    }

    function fillExampleCompare() {
        $('lnhCmpAmount').value = '500.000.000';
        $('lnhCmpTerm').value = '12';
        $('lnhCmpSort').value = 'rate_desc';
    }

    // ============ Bind ============
    function bind() {
        $('lnhCalcBtn').addEventListener('click', runEstimate);
        $('lnhExBtn').addEventListener('click', function () { fillExampleCalc(); runEstimate(); });
        $('lnhCompareBtn').addEventListener('click', runCompare);
        $('lnhCmpExBtn').addEventListener('click', function () { fillExampleCompare(); runCompare(); });

        // nhấn Enter trong ô tiền
        ['lnhAmount', 'lnhCmpAmount'].forEach(function (id) {
            $(id).addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
            $(id).addEventListener('blur', function () {
                var n = parseMoney($(id).value);
                $(id).value = isFinite(n) && n > 0 ? Math.round(n).toLocaleString('vi-VN') : '';
            });
        });

        // channel: chips
        Array.prototype.forEach.call(document.querySelectorAll('#lnhChannelChips .lnh-chip'), function (b) {
            b.addEventListener('click', function () {
                setChannelChips(b.getAttribute('data-ch'));
                loadMatrix(state.channel);
            });
        });
        // channel: select
        $('lnhChannel').addEventListener('change', function () {
            setChannelChips($('lnhChannel').value);
            loadMatrix(state.channel);
        });

        // tìm kiếm
        $('lnhSearch').addEventListener('input', function () {
            state.search = $('lnhSearch').value;
            renderMatrix();
        });
        // kỳ hạn sắp xếp / highlight
        $('lnhTermSort').addEventListener('change', function () { syncTermSort($('lnhTermSort').value); });
        $('lnhMxSort').addEventListener('change', function () { state.mxSort = $('lnhMxSort').value; renderMatrix(); });

        // head th click -> chọn kỳ hạn
        $('lnhMxHead').addEventListener('click', function (e) {
            var th = e.target.closest('th[data-term]');
            if (th) syncTermSort(th.dataset.term);
        });
        // click ô lãi suất -> áp kỳ hạn vào công cụ tính
        $('lnhMxBody').addEventListener('click', function (e) {
            var td = e.target.closest('td.rate[data-term]');
            if (!td) return;
            var term = Number(td.dataset.term);
            $('lnhTerm').value = String(term);
            syncTermSort(term);
            $('lnhCalcResult').innerHTML = '<div class="lnh-msg info">Đã chọn kỳ hạn <b>' + termLabel(term) + '</b> cho công cụ tính. Nhấn <b>Tính lãi</b> để xem kết quả.</div>';
            notifyParentHeight();
        });
    }

    // ===== Tin tức ngân hàng (load 18 tin — hiển thị 6 tin/trang, mũi tên đổi trang) =====
    var NEWS_PER_PAGE = 6;
    var newsItems = [];
    var newsPage = 0;
    function newsPageCount() { return Math.max(1, Math.ceil(newsItems.length / NEWS_PER_PAGE)); }
    function fmtNewsDate(iso) {
        if (!iso) return '';
        var d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        var p = function (x) { return (x < 10 ? '0' : '') + x; };
        return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear() + ' · ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }
    function openNewsArticle(id, slug) {
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'tunix-open-tintuc', id: Number(id), slug: slug || '' }, '*');
            return;
        }
        window.location.href = slug ? '/' + slug + '-' + id + '.html' : '/tintuc?id=' + encodeURIComponent(id);
    }
    function newsItemHtml(n) {
        var thumb = n.image_url
            ? '<img src="' + esc(n.image_url) + '" alt="" loading="lazy">'
            : '<i class="fas fa-newspaper"></i>';
        return '<div class="lnh-nitem" data-id="' + esc(n.id) + '" data-slug="' + esc(n.slug || '') + '">' +
            '<div class="lnh-nthumb">' + thumb + '</div>' +
            '<div class="lnh-nbody">' +
                '<div class="lnh-ntitle">' + esc(n.title) + '</div>' +
                '<div class="lnh-ndate"><i class="far fa-clock"></i> ' + esc(fmtNewsDate(n.published_at || n.created_at)) + '</div>' +
            '</div></div>';
    }
    function updateNewsNav() {
        var prev = $('lnhNewsPrev'), next = $('lnhNewsNext');
        if (!prev || !next) return;
        var single = newsItems.length <= NEWS_PER_PAGE;
        prev.disabled = single || newsPage <= 0;
        next.disabled = single || newsPage >= newsPageCount() - 1;
    }
    function renderNewsPage() {
        var el = $('lnhNews');
        if (!el) return;
        var start = newsPage * NEWS_PER_PAGE;
        el.innerHTML = newsItems.slice(start, start + NEWS_PER_PAGE).map(newsItemHtml).join('');
        el.querySelectorAll('.lnh-nitem').forEach(function (it) {
            it.addEventListener('click', function () {
                openNewsArticle(it.getAttribute('data-id'), it.getAttribute('data-slug'));
            });
        });
        updateNewsNav();
        notifyParentHeight();
    }
    function newsGo(delta) {
        var n = newsPage + delta;
        if (n < 0 || n > newsPageCount() - 1) return;
        newsPage = n;
        renderNewsPage();
    }
    function loadNews() {
        var el = $('lnhNews');
        if (!el) return;
        var prev = $('lnhNewsPrev'), next = $('lnhNewsNext');
        if (prev && !prev.dataset.bound) { prev.dataset.bound = '1'; prev.addEventListener('click', function () { newsGo(-1); }); }
        if (next && !next.dataset.bound) { next.dataset.bound = '1'; next.addEventListener('click', function () { newsGo(1); }); }
        fetch('/api/news?category=ngan-hang&limit=18', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (list) {
                list = Array.isArray(list) ? list : [];
                newsItems = list;
                newsPage = 0;
                if (!list.length) {
                    el.innerHTML = '<div class="lnh-news-empty">Chưa có tin tức ngân hàng.</div>';
                    updateNewsNav();
                    notifyParentHeight();
                    return;
                }
                renderNewsPage();
            })
            .catch(function () {
                newsItems = [];
                el.innerHTML = '<div class="lnh-news-empty">Không tải được tin tức ngân hàng.</div>';
                updateNewsNav();
                notifyParentHeight();
            });
    }

    // ===== Embed / chiều cao / theme (giống các trang TuniX khác) =====
    if (window.self !== window.top) document.body.classList.add('embedded');
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
    function notifyParentHeight() {
        try {
            var el = document.querySelector('.lnh-page') || document.body;
            var height = Math.ceil(el.getBoundingClientRect().height);
            if (!height || height <= 0) return;
            if (Math.abs(height - (window.__lastSentH || 0)) < 2) return;
            window.__lastSentH = height;
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'tunix-embed-height', height: height }, '*');
            }
        } catch (e) {}
    }
    window.addEventListener('message', function (event) {
        var data = event.data;
        if (!data || data.type !== 'tunix-theme') return;
        var mode = data.mode === 'dark' ? 'dark' : 'light';
        localStorage.setItem('theme', mode);
        applyEmbedTheme(mode);
        setTimeout(notifyParentHeight, 50);
    });

    // ===== Khởi động =====
    function init() {
        bind();
        api('/meta').then(function (m) {
            fillMeta(m);
            // mặc định ví dụ
            $('lnhAmount').value = '500.000.000';
            $('lnhCmpAmount').value = '500.000.000';
            setChannelChips('online');
            fillExampleCalc();
            fillExampleCompare();
            syncTermSort(12);
            loadMatrix('online');
            loadNews();
        }).catch(function () {
            $('metaUpdated').textContent = '—';
            $('lnhMxBody').innerHTML = '<tr><td colspan="20" class="lnh-empty">Không tải được dữ liệu. Vui lòng thử lại sau.</td></tr>';
            notifyParentHeight();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.addEventListener('load', function () {
        applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
        setTimeout(notifyParentHeight, 60);
        setTimeout(notifyParentHeight, 400);
    });
    window.addEventListener('resize', notifyParentHeight);
    if (window.ResizeObserver) {
        var obs = new ResizeObserver(notifyParentHeight);
        obs.observe(document.querySelector('.lnh-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
    notifyParentHeight();
})();
