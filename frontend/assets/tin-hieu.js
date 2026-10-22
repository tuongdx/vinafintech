/* TuniX — trang "Tín hiệu" (/tin-hieu)
 *
 * Dành cho tài khoản ĐÃ ĐĂNG NHẬP và ĐƯỢC QUẢN TRỊ CẤP QUYỀN (users.signal_access)
 * hoặc tài khoản quản trị (super-admin/admin).
 *
 * BẢO MẬT: quyền được server kiểm tra (`require_signal_access`) — phần kiểm tra ở đây chỉ
 * để hiển thị màn hình phù hợp, KHÔNG phải lớp bảo vệ duy nhất.
 */
(function () {
    'use strict';

    var API = '/api/signals';
    var PAGE_SIZE = 30;
    var offset = 0;
    var total = 0;
    // Ngưỡng thanh khoản server đang áp cho TRANG NÀY (chỉ hiển thị mã có GTGD TB20 ≥ ngưỡng).
    var liqFilter = null;

    // ---------------- Sắp xếp (nút sort ở đầu mỗi cột) ----------------
    // `sortKey` = khoá cột gửi lên server (`symbol|strategy|signal|score|confidence|entry|`
    // `stop_loss|trailing_stop|state|created|updated`). Riêng "price" là giá REALTIME lấy theo
    // từng trang nên KHÔNG có ở server — client tự sắp các dòng đang hiển thị.
    var sortKey = 'confidence';
    var sortDir = 'desc';
    var DEFAULT_SORT_DIR = {
        confidence: 'desc', score: 'desc', updated: 'desc', created: 'desc', signal: 'desc',
        entry: 'desc', stop_loss: 'desc', trailing_stop: 'desc', price: 'desc',
        symbol: 'asc', strategy: 'asc', state: 'asc'
    };

    function sortIcon(dir) {
        return dir === 'asc' ? 'fa-sort-up' : (dir === 'desc' ? 'fa-sort-down' : 'fa-sort');
    }

    function updateSortIndicators() {
        var btns = document.querySelectorAll('#sigPanelRec .sig-table thead .sig-sort');
        for (var i = 0; i < btns.length; i++) {
            var key = btns[i].getAttribute('data-sort') || '';
            var active = key === sortKey;
            var icon = btns[i].querySelector('i');
            if (icon) icon.className = 'fas ' + (active ? sortIcon(sortDir) : 'fa-sort');
            btns[i].className = 'sig-sort' + (active ? ' on' : '');
            btns[i].setAttribute('aria-sort', active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none');
            var label = btns[i].textContent.replace(/\s+/g, ' ').trim();
            if (!btns[i].getAttribute('data-title')) {
                btns[i].setAttribute('data-title', btns[i].getAttribute('title') || label);
            }
            btns[i].title = btns[i].getAttribute('data-title') +
                ' — bấm để sắp xếp' + (active ? (sortDir === 'asc' ? ' (đang tăng dần, bấm để giảm dần)' : ' (đang giảm dần, bấm để tăng dần)') : '');
        }
    }

    function priceValueOf(tr) {
        var cell = tr.querySelector('td.sig-price');
        var raw = cell ? cell.getAttribute('data-price') : '';
        var v = Number(raw);
        return (raw !== '' && isFinite(v) && v > 0) ? v : null;
    }

    // Cột giá hiện tại: dữ liệu realtime chỉ có cho các dòng đang hiển thị ⇒ sắp trong trang.
    function sortRowsByPrice() {
        var body = $('sigTableBody');
        if (!body || sortKey !== 'price') return;
        var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
        if (rows.length < 2) return;
        var dir = sortDir === 'asc' ? 1 : -1;
        rows.sort(function (a, b) {
            var va = priceValueOf(a), vb = priceValueOf(b);
            if (va === null && vb === null) return 0;
            if (va === null) return 1;   // chưa có giá → xuống cuối
            if (vb === null) return -1;
            return (va - vb) * dir;
        });
        rows.forEach(function (tr) { body.appendChild(tr); });
        notifyParentHeight();
    }

    function bindSortHeaders() {
        // Chỉ bảng ở TAB KHUYẾN NGHỊ có nút sắp xếp (2 tab vị thế không cần).
        var head = document.querySelector('#sigPanelRec .sig-table thead');
        if (!head || head.getAttribute('data-sort-bound')) return;
        head.setAttribute('data-sort-bound', '1');
        head.addEventListener('click', function (e) {
            var btn = e.target && e.target.closest ? e.target.closest('.sig-sort') : null;
            if (!btn) return;
            var key = btn.getAttribute('data-sort') || 'confidence';
            if (key === sortKey) {
                sortDir = sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                sortKey = key;
                sortDir = btn.getAttribute('data-dir') || DEFAULT_SORT_DIR[key] || 'desc';
            }
            updateSortIndicators();
            if (sortKey === 'price') { sortRowsByPrice(); return; }  // không cần gọi lại server
            offset = 0;
            sigLoad(0);
        });
        updateSortIndicators();
    }

    var STATE_LABEL = {
        NO_SETUP: 'Chưa có setup', WATCH: 'Đang theo dõi', SETUP_FORMING: 'Đang hình thành',
        CONFIRMED: 'Đã xác nhận', ACTIVE: 'Đang hiệu lực', WEAKENING: 'Suy yếu',
        EXIT: 'Đã thoát', INVALIDATED: 'Vô hiệu'
    };
    var STATE_BADGE = {
        NO_SETUP: 'draft', WATCH: 'draft', SETUP_FORMING: 'pending', CONFIRMED: 'published',
        ACTIVE: 'active', WEAKENING: 'processing', EXIT: 'rejected', INVALIDATED: 'inactive'
    };
    var STRENGTH_LABEL = { VERY_STRONG: 'Rất mạnh', STRONG: 'Mạnh', MODERATE: 'Trung bình', WEAK: 'Yếu' };
    var STRATEGY_LABEL = { UPTREND: 'Uptrend', BOTTOM_FISHING: 'Bottom Fishing', SWING: 'Swing' };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function num(v, dec) {
        if (v === null || v === undefined || v === '') return '—';
        var n = Number(v);
        if (!isFinite(n)) return '—';
        return n.toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: dec === undefined ? 1 : dec });
    }
    function vnd(v) {
        if (v === null || v === undefined || v === '') return '—';
        var n = Number(v);
        return isFinite(n) ? Math.round(n).toLocaleString('vi-VN') : '—';
    }
    // Mốc thời gian: API trả ISO (mốc tín hiệu/vị thế là UTC, mốc lịch quét là +07:00).
    // Chuỗi KHÔNG có múi giờ ⇒ hiểu là UTC (nếu để trình duyệt tự đoán, nó coi là giờ địa
    // phương ⇒ lệch 7 giờ). Hiển thị theo GIỜ VN cho mọi người, giống các trang khác của TuniX.
    function dt(iso) {
        if (!iso) return '—';
        var text = String(iso);
        if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) text += 'Z';
        var d = new Date(text);
        if (isNaN(d.getTime())) return '—';
        var vn = new Date(d.getTime() + 7 * 3600 * 1000);   // VN = UTC+7
        function p(v) { return (v < 10 ? '0' : '') + v; }
        return p(vn.getUTCHours()) + ':' + p(vn.getUTCMinutes()) + ':' + p(vn.getUTCSeconds()) +
            ' ' + p(vn.getUTCDate()) + '/' + p(vn.getUTCMonth() + 1) + '/' + vn.getUTCFullYear();
    }
    function signalHtml(signal) {
        if (signal === 'BUY') return '<span class="sig-signal buy">BUY</span>';
        if (signal === 'SELL') return '<span class="sig-signal sell">SELL</span>';
        return '<span style="color:#8a99b1;">—</span>';
    }

    // ---------------- Giá hiện tại (realtime) ----------------
    // Bảng có thể tới 30 dòng/lần ⇒ KHÔNG gọi `/api/stocks/{mã}/quote` từng mã mà gọi 1 request
    // `/api/signals/quotes?symbols=…` (server gom lô + cache 15s). Lỗi mạng/nguồn giá KHÔNG làm hỏng bảng.
    var liveQuotes = {};

    function quoteCellHtml(q) {
        if (!q || !q.price) return '<span style="color:#8a99b1;">—</span>';
        var pct = q.percent_change;
        var cls = pct > 0 ? 'up' : (pct < 0 ? 'down' : 'flat');
        var sign = pct > 0 ? '+' : '';
        var sub = (pct === null || pct === undefined)
            ? '' : '<small class="pc ' + cls + '">' + sign + num(pct, 2) + '%</small>';
        return '<b>' + vnd(q.price) + '</b>' + sub;
    }

    function fillPriceCells() {
        // Ô giá realtime có ở CẢ 3 tab (Khuyến nghị + Đang mở) ⇒ quét toàn trang.
        var cells = document.querySelectorAll('td.sig-price');
        var changed = false;
        for (var i = 0; i < cells.length; i++) {
            var sym = (cells[i].getAttribute('data-sym') || '').toUpperCase();
            var q = liveQuotes[sym];
            if (!q || !q.price) continue;
            var html = quoteCellHtml(q);
            if (cells[i].getAttribute('data-px') === html) continue;
            cells[i].setAttribute('data-px', html);
            cells[i].setAttribute('data-price', q.price);
            cells[i].innerHTML = html;
            cells[i].title = 'Giá hiện tại' + (q.as_of ? ' · ' + dt(q.as_of) : '') +
                (q.percent_change == null ? '' : ' (' + (q.percent_change > 0 ? '+' : '') + num(q.percent_change, 2) + '%)');
            changed = true;
        }
        if (changed) {
            refreshPositionCells();   // lãi/lỗ tạm tính của vị thế đang mở bám theo giá mới
            notifyParentHeight();
        }
    }

    function loadLivePrices() {
        var cells = document.querySelectorAll('td.sig-price');
        if (!cells.length) return Promise.resolve();
        var seen = {}, syms = [];
        for (var i = 0; i < cells.length; i++) {
            var s = (cells[i].getAttribute('data-sym') || '').toUpperCase();
            if (s && !seen[s]) { seen[s] = true; syms.push(s); }
        }
        if (!syms.length) return Promise.resolve();
        // Dùng fetch RIÊNG (không qua `api()`) để lỗi quyền/phiên của luồng giá KHÔNG kích hoạt
        // màn hình "bị khóa" — giá hiện tại chỉ là thông tin phụ, lỗi thì để "—".
        return fetch(API + '/quotes?symbols=' + encodeURIComponent(syms.join(',')), {
            headers: { 'Authorization': 'Bearer ' + token() },
            cache: 'no-store'
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
            var items = (d && d.items) || {};
            Object.keys(items).forEach(function (k) { liveQuotes[k.toUpperCase()] = items[k]; });
            fillPriceCells();
        }).catch(function () { /* nguồn giá lỗi → giữ "—", không phá bảng */ });
    }

    // ---------------- Xác thực ----------------
    function token() { return localStorage.getItem('tunix_access_token') || ''; }

    function refreshSession() {
        if (window.parent && window.parent !== window && typeof window.parent.refreshAccessToken === 'function') {
            try { return Promise.resolve(window.parent.refreshAccessToken()); } catch (e) { /* fallback bên dưới */ }
        }
        var refresh = localStorage.getItem('tunix_refresh_token');
        if (!refresh) return Promise.resolve(false);
        return fetch('/api/auth/refresh-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refresh })
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
            if (!d || !d.access_token) return false;
            localStorage.setItem('tunix_access_token', d.access_token);
            if (d.refresh_token) localStorage.setItem('tunix_refresh_token', d.refresh_token);
            if (d.user) localStorage.setItem('tunix_user', JSON.stringify(d.user));
            return true;
        }).catch(function () { return false; });
    }

    function api(path, options, retried) {
        var opts = options || {};
        var headers = opts.headers || {};
        headers['Authorization'] = 'Bearer ' + token();
        if (opts.body) headers['Content-Type'] = 'application/json';
        return fetch(path, {
            method: opts.method || 'GET',
            headers: headers,
            body: opts.body || undefined,
            cache: 'no-store'
        }).then(function (res) {
            if (res.status === 401 && !retried) {
                return refreshSession().then(function (ok) {
                    if (!ok) { showLocked('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'); throw new Error('Phiên đăng nhập đã hết hạn.'); }
                    return api(path, options, true);
                });
            }
            if (res.status === 403) {
                showLocked('Tài khoản của bạn chưa được cấp quyền dùng tính năng Tín hiệu.');
                throw new Error('Tài khoản chưa được cấp quyền dùng tính năng Tín hiệu.');
            }
            if (!res.ok) {
                return res.json().catch(function () { return {}; }).then(function (d) {
                    throw new Error(d.detail || ('Yêu cầu thất bại (' + res.status + ')'));
                });
            }
            return res.json();
        });
    }

    function showLocked(message) {
        var locked = $('thLocked');
        var main = $('thMain');
        if (main) main.style.display = 'none';
        if (locked) {
            locked.style.display = '';
            var p = locked.querySelector('p');
            if (p && message) p.innerHTML = esc(message) + '<br>Vui lòng liên hệ ban quản trị TuniX nếu bạn muốn sử dụng tính năng này.';
        }
        notifyParentHeight();
    }

    // ---------------- Tổng quan ----------------
    function stat(label, value, sub) {
        return '<div class="sig-stat"><div class="lbl">' + esc(label) + '</div><div class="val">' +
            (value === null || value === undefined ? '—' : esc(String(value))) + '</div>' +
            (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') + '</div>';
    }

    function loadOverview() {
        return api(API + '/overview').then(function (d) {
            var pos = d.positions || {};
            var exp = d.expiry || {};
            if (exp.max_holding_sessions) {
                var exEl = $('thExpiry');
                if (exEl) exEl.textContent = exp.max_holding_sessions;
            }
            liqFilter = d.liquidity_filter || null;
            renderLiquidityPill();
            renderScanSchedule(d.scan_schedule || null);
            // Khối Tổng quan CHỈ còn 3 chỉ số về VỊ THẾ (theo yêu cầu):
            // tỷ lệ thắng · lãi/lỗ trung bình · số vị thế. Số liệu lấy từ vị thế người dùng
            // tự mở (nút ON/OFF); tỷ lệ thắng & lãi/lỗ chỉ tính trên vị thế ĐÃ ĐÓNG (đã chốt).
            var open = pos.open === undefined ? null : pos.open;
            var closed = pos.closed === undefined ? null : pos.closed;
            var totalPos = (open === null || closed === null) ? null : (open + closed);
            $('sigOverview').innerHTML = [
                stat('Tỷ lệ thắng', pos.win_rate === null || pos.win_rate === undefined ? '—' : pos.win_rate + '%',
                    closed ? closed + ' vị thế đã đóng' : 'chưa có vị thế đóng'),
                stat('Lãi/lỗ trung bình',
                    (pos.average_gain_pct == null ? '—' : pos.average_gain_pct + '%') + ' / ' +
                    (pos.average_loss_pct == null ? '—' : pos.average_loss_pct + '%'),
                    'lãi TB / lỗ TB (mỗi vị thế đã đóng)'),
                stat('Số vị thế', totalPos === null ? '—' : totalPos,
                    (open === null ? '—' : open) + ' đang mở · ' + (closed === null ? '—' : closed) + ' đã đóng')
            ].join('');
            // "Quét gần nhất": ưu tiên giờ của lần QUÉT TỰ ĐỘNG gần nhất, sau đó tới thời điểm
            // dữ liệu tín hiệu được ghi/cập nhật gần nhất trong DB (server trả ở `last_scan`).
            var sched = d.scan_schedule || {};
            setUpdated(sched.last_run_at || d.last_scan || null);
            notifyParentHeight();
        }).catch(function (err) {
            if (String(err.message || '').indexOf('quyền') === -1) {
                $('sigOverview').innerHTML = stat('Lỗi', '—', err.message || 'Không tải được tổng quan');
            }
        });
    }

    // Pill "Tự động quét": cho biết bảng được hệ thống cập nhật theo lịch (không cần mở lại trang).
    function renderScanSchedule(sched) {
        var el = $('thSchedule');
        if (!el) return;
        if (!sched || !sched.enabled || !(sched.times || []).length) {
            el.style.display = 'none';
            return;
        }
        var times = sched.times.join(' · ');
        el.style.display = '';
        el.innerHTML = '<i class="fas fa-rotate"></i> Tự động quét: <b>' + esc(times) + '</b> (giờ VN)' +
            (sched.worker_alive === false ? ' <b style="color:#d29922;">(chưa bật)</b>' : '');
        el.title = 'Hệ thống tự quét toàn thị trường (mã đủ điều kiện) vào ' + times +
            ' giờ VN' + (sched.weekdays_only ? ' các ngày Thứ 2 – Thứ 6' : ' hằng ngày') + '.' +
            (sched.next_run_at ? '\nLần quét tới: ' + dt(sched.next_run_at) : '') +
            (sched.last_run_at ? '\nLần quét gần nhất: ' + dt(sched.last_run_at) : '');
    }

    // Pill mô tả BỘ LỌC THANH KHOẢN đang áp (server trả trong /overview) — ngưỡng đổi được
    // qua config engine nên KHÔNG hard-code trong HTML.
    function renderLiquidityPill() {
        var el = $('thLiquidity');
        if (!el) return;
        var liq = liqFilter;
        if (!liq || !liq.enabled) { el.style.display = 'none'; return; }
        var sessions = liq.sessions || 20;
        var minTy = liq.min_avg_20d_ty;
        el.style.display = '';
        el.innerHTML = '<i class="fas fa-filter"></i> Thanh khoản: TB' + num(sessions, 0) +
            ' ≥ <b>' + num(minTy, 0) + ' tỷ</b>/phiên' +
            (liq.symbols ? ' · ' + num(liq.symbols, 0) + ' mã đạt chuẩn' : '');
        el.title = 'Danh sách chỉ gồm cổ phiếu có giá trị giao dịch trung bình ' + sessions +
            ' phiên gần nhất từ ' + minTy + ' tỷ đồng/phiên trở lên.';
    }

    function setUpdated(iso, state) {
        var el = $('thUpdated');
        if (el) el.textContent = iso ? dt(iso) : '—';
        var st = $('thDataState');
        if (st) st.textContent = state || (iso ? 'đã có dữ liệu quét' : 'chưa quét trong phiên này');
    }

    function setDataState(text) {
        var st = $('thDataState');
        if (st) st.textContent = text || '—';
    }

    // Thông báo khi bảng TRỐNG.
    function emptyRowHint() {
        var hint = 'Hiện chưa có cổ phiếu nào có tín hiệu MUA đủ điều kiện.';
        if (liqFilter && liqFilter.enabled) {
            hint += ' Danh sách chỉ xét cổ phiếu có thanh khoản TB' +
                num(liqFilter.sessions || 20, 0) + ' ≥ ' + num(liqFilter.min_avg_20d_ty, 0) + ' tỷ/phiên.';
        }
        return hint + ' Hệ thống tự phân tích lại khi bạn mở trang.';
    }

    // ---------------- Danh sách ----------------
    // Trang Tín hiệu LUÔN chỉ hiển thị tín hiệu MUA đã đủ điều kiện xác nhận.
    function filterQuery(withPaging) {
        var params = new URLSearchParams();
        params.set('signal', 'BUY');
        params.set('state', 'CONFIRMED,ACTIVE');
        // Cột "Giá hiện tại" không có ở server (giá realtime) → giữ sắp xếp mặc định của server.
        if (sortKey && sortKey !== 'price') { params.set('sort', sortKey); params.set('direction', sortDir); }
        if (withPaging) { params.set('limit', PAGE_SIZE); params.set('offset', offset); }
        return params;
    }

    // ---------------- VỊ THẾ (nút ON/OFF) ----------------
    var positionsBySym = {};   // {MÃ: vị thế mới nhất (ưu tiên vị thế ĐANG MỞ)}
    var positionsItems = [];   // TOÀN BỘ vị thế (đang mở + đã đóng) — nguồn dữ liệu cho tab Đang mở/Đã đóng
    var posSummary = null;     // thống kê vị thế server trả về (open/closed/win_rate/lãi-lỗ TB…)
    var signalBySym = {};      // {MÃ: {id, strategy}} của trang tín hiệu đang hiển thị (để ghi vào vị thế khi bật ON)
    var posBusy = {};
    var activeTab = 'rec';     // 'rec' (khuyến nghị) | 'open' (đang mở) | 'closed' (đã đóng)

    function posOf(sym) { return positionsBySym[String(sym || '').toUpperCase()] || null; }

    function openPositions() {
        return positionsItems.filter(function (p) { return p.status === 'OPEN'; });
    }

    function closedPositions() {
        return positionsItems.filter(function (p) { return p.status === 'CLOSED'; });
    }

    function indexPositions(items, summary) {
        positionsItems = items || [];
        if (summary) posSummary = summary;
        positionsBySym = {};
        positionsItems.forEach(function (p) {
            var sym = String(p.symbol || '').toUpperCase();
            var cur = positionsBySym[sym];
            if (!cur || (cur.status !== 'OPEN' && p.status === 'OPEN')) positionsBySym[sym] = p;
        });
        updateTabCounts();
    }

    // Số lượng hiển thị trên nhãn từng tab (đang mở = vị thế ON chưa chốt; đã đóng = đã tắt OFF).
    function updateTabCounts() {
        var open = (posSummary && posSummary.open != null) ? posSummary.open : openPositions().length;
        var closed = (posSummary && posSummary.closed != null) ? posSummary.closed : closedPositions().length;
        var e = $('sigCntOpen');
        if (e) e.textContent = num(open, 0);
        e = $('sigCntClosed');
        if (e) e.textContent = num(closed, 0);
    }

    function loadPositions() {
        // fetch riêng (không qua api()) để lỗi phiên ở luồng vị thế không đá người dùng ra màn hình khóa.
        return fetch(API + '/positions', { headers: { 'Authorization': 'Bearer ' + token() }, cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) indexPositions(d.items, d.summary); return d; })
            .catch(function () { return null; });
    }

    function posEntryHtml(pos) {
        if (!pos || pos.status !== 'OPEN' || !pos.entry_price) return '<span class="muted-dash">—</span>';
        return '<b>' + vnd(pos.entry_price) + '</b>';
    }

    // Ô lãi/lỗ (%) dùng chung cho cả 3 tab (xanh = lãi, đỏ = lỗ, xám = không đổi giá).
    function pctHtml(pct) {
        if (pct === null || pct === undefined || pct === '' || !isFinite(Number(pct))) {
            return '<span class="muted-dash">—</span>';
        }
        var v = Number(pct);
        var cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
        return '<span class="pos-pnl ' + cls + '">' + (v > 0 ? '+' : '') + num(v, 2) + '%</span>';
    }

    // Lãi/lỗ TẠM TÍNH của vị thế ĐANG MỞ: ưu tiên giá realtime vừa nạp để bám sát giá hiện tại.
    function posPnlHtml(pos, quote) {
        if (!pos || pos.status !== 'OPEN') return '<span class="muted-dash">—</span>';
        var pct = null;
        if (quote && quote.price && pos.entry_price) pct = (quote.price / pos.entry_price - 1) * 100;
        if (pct === null) pct = pos.return_pct;
        return pctHtml(pct);
    }

    function toggleHtml(sym, pos) {
        var on = !!(pos && pos.status === 'OPEN');
        return '<button type="button" class="sig-switch' + (on ? ' on' : '') + '" role="switch"' +
            ' aria-checked="' + on + '" data-sym="' + esc(sym) + '"' +            ' onclick="sigTogglePosition(\'' + esc(sym) + '\', event)"' +            ' title="' + (on ? 'Đang mở vị thế — tắt để CHỐT lãi/lỗ' : 'Bật để mở vị thế tại giá hiện tại') + '">' +
            '<span class="knob"></span></button>';
    }

    // ============ 3 TAB: Khuyến nghị · Đang mở · Đã đóng ============
    // Tab Khuyến nghị = danh sách tín hiệu MUA (bảng sắp xếp được, có phân trang).
    // Tab Đang mở / Đã đóng = VỊ THẾ của chính tài khoản (nguồn: /api/signals/positions).
    function showPanel(id, visible) {
        var el = $(id);
        if (el) el.style.display = visible ? '' : 'none';
    }

    function setTab(name) {
        activeTab = (name === 'open' || name === 'closed') ? name : 'rec';
        var btns = document.querySelectorAll('.sig-tab');
        for (var i = 0; i < btns.length; i++) {
            var on = btns[i].getAttribute('data-tab') === activeTab;
            btns[i].classList.toggle('on', on);
            btns[i].setAttribute('aria-selected', on ? 'true' : 'false');
        }
        showPanel('sigPanelRec', activeTab === 'rec');
        showPanel('sigPanelOpen', activeTab === 'open');
        showPanel('sigPanelClosed', activeTab === 'closed');
        renderActiveTab();
        notifyParentHeight();
    }

    // Vẽ lại bảng của tab đang mở theo dữ liệu vị thế vừa nạp (positionsItems).
    function renderActiveTab() {
        if (activeTab === 'open') { renderOpenTab(); loadLivePrices(); }
        else if (activeTab === 'closed') renderClosedTab();
    }

    function renderOpenTab() {
        var body = $('sigOpenBody');
        if (!body) return;
        var rows = openPositions();
        body.innerHTML = rows.length ? rows.map(openRowHtml).join('')
            : '<tr><td colspan="7" class="th-empty">Chưa có vị thế nào đang mở. Bật <b>ON</b> ở tab ' +
              '<b>Khuyến nghị</b> để mở vị thế tại giá hiện tại.</td></tr>';
        updateTabCounts();
    }

    function openRowHtml(p) {
        var sym = String(p.symbol || '').toUpperCase();
        var price = p.current_price ? '<b>' + vnd(p.current_price) + '</b>' : '<span class="muted-dash">—</span>';
        return '<tr>' +
            '<td><b>' + esc(sym) + '</b><span data-div="' + esc(sym) + '"></span></td>' +
            '<td>' + esc(STRATEGY_LABEL[p.strategy] || p.strategy || '—') + '</td>' +
            '<td><b>' + vnd(p.entry_price) + '</b></td>' +
            '<td class="sig-price" data-sym="' + esc(sym) + '" data-price="' + (p.current_price || '') + '">' + price + '</td>' +
            '<td class="sig-pos-pnl" data-sym="' + esc(sym) + '">' + posPnlHtml(p, liveQuotes[sym]) + '</td>' +
            '<td style="font-size:11.5px;">' + dt(p.entry_at) + '</td>' +
            '<td class="sig-pos-cell" data-sym="' + esc(sym) + '">' + toggleHtml(sym, p) + '</td>' +
            '</tr>';
    }

    // Dòng tổng kết: số vị thế ĐÃ ĐÓNG + tỷ lệ thắng + lãi/lỗ TB + tổng lãi/lỗ (nguồn: summary của server).
    function closedSummaryHtml() {
        var s = posSummary || {};
        var closed = s.closed != null ? s.closed : closedPositions().length;
        var totalCls = s.total_return_pct > 0 ? 'up' : (s.total_return_pct < 0 ? 'down' : 'flat');
        return '<i class="fas fa-flag-checkered" style="color:#008080;"></i> ' +
            '<b>' + num(closed, 0) + '</b> vị thế đã đóng' +
            '<span>Tỷ lệ thắng:</span> <b>' + (s.win_rate == null ? '—' : num(s.win_rate, 2) + '%') + '</b>' +
            '<span>Lãi TB:</span> <b class="pos-pnl up">' +
                (s.average_gain_pct == null ? '—' : '+' + num(s.average_gain_pct, 2) + '%') + '</b>' +
            '<span>Lỗ TB:</span> <b class="pos-pnl down">' +
                (s.average_loss_pct == null ? '—' : num(s.average_loss_pct, 2) + '%') + '</b>' +
            '<span>Tổng lãi/lỗ:</span> <b class="pos-pnl ' + totalCls + '">' +
                (s.total_return_pct == null ? '—' : (s.total_return_pct > 0 ? '+' : '') + num(s.total_return_pct, 2) + '%') + '</b>';
    }

    function renderClosedTab() {
        var body = $('sigClosedBody');
        if (!body) return;
        var rows = closedPositions();
        var sum = $('sigClosedSum');
        if (sum) sum.innerHTML = closedSummaryHtml();
        body.innerHTML = rows.length ? rows.map(closedRowHtml).join('')
            : '<tr><td colspan="8" class="th-empty">Chưa có vị thế nào đã đóng. Tắt <b>OFF</b> ở tab ' +
              '<b>Đang mở</b> để chốt lãi/lỗ — kết quả (và tỷ lệ thắng) sẽ hiện ở đây.</td></tr>';
        updateTabCounts();
    }

    function closedRowHtml(p) {
        var sym = String(p.symbol || '').toUpperCase();
        var res = String(p.result || '').toUpperCase();
        var label = (res === 'WIN' || res === 'LOSS' || res === 'FLAT') ? res : (res || '—');
        var cls = res === 'WIN' ? 'active' : (res === 'LOSS' ? 'rejected' : 'draft');
        return '<tr>' +
            '<td><b>' + esc(sym) + '</b><span data-div="' + esc(sym) + '"></span></td>' +
            '<td>' + esc(STRATEGY_LABEL[p.strategy] || p.strategy || '—') + '</td>' +
            '<td><b>' + vnd(p.entry_price) + '</b></td>' +
            '<td><b>' + vnd(p.exit_price) + '</b></td>' +
            '<td>' + pctHtml(p.return_pct) + '</td>' +
            '<td><span class="sig-badge ' + cls + '">' + esc(label) + '</span></td>' +
            '<td style="font-size:11.5px;">' + dt(p.entry_at) + '</td>' +
            '<td style="font-size:11.5px;">' + dt(p.exit_at) + '</td>' +
            '</tr>';
    }

    function refreshPositionCells() {
        var cells = document.querySelectorAll('td.sig-pos-entry');
        for (var i = 0; i < cells.length; i++) {
            var sym = (cells[i].getAttribute('data-sym') || '').toUpperCase();
            cells[i].innerHTML = posEntryHtml(posOf(sym));
        }
        cells = document.querySelectorAll('td.sig-pos-pnl');
        for (i = 0; i < cells.length; i++) {
            sym = (cells[i].getAttribute('data-sym') || '').toUpperCase();
            cells[i].innerHTML = posPnlHtml(posOf(sym), liveQuotes[sym]);
        }
        cells = document.querySelectorAll('td.sig-pos-cell');
        for (i = 0; i < cells.length; i++) {
            sym = (cells[i].getAttribute('data-sym') || '').toUpperCase();
            cells[i].innerHTML = toggleHtml(sym, posOf(sym));
        }
        notifyParentHeight();
    }

    function toastPos(message, isError) {
        var el = $('sigToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'sigToast';
            el.className = 'sig-toast';
            document.body.appendChild(el);
        }
        el.className = 'sig-toast show' + (isError ? ' err' : '');
        el.textContent = message || '';
        clearTimeout(window.__sigToastTimer);
        window.__sigToastTimer = setTimeout(function () { el.className = 'sig-toast'; }, 3200);
    }

    function setToggleBusy(sym, busy) {
        // Nút ON/OFF xuất hiện ở cả tab Khuyến nghị và tab Đang mở ⇒ cập nhật mọi nút của mã đó.
        var btns = document.querySelectorAll('.sig-switch[data-sym="' + sym + '"]');
        for (var i = 0; i < btns.length; i++) {
            btns[i].disabled = !!busy;
            btns[i].classList.toggle('busy', !!busy);
        }
    }

    // Bật vị thế = mở tại giá hiện tại; tắt vị thế = chốt lãi/lỗ (ghi vào tỷ lệ thắng).
    function sigTogglePosition(sym, ev) {
        if (ev) { ev.stopPropagation(); ev.preventDefault(); }
        sym = String(sym || '').toUpperCase();
        if (!sym || posBusy[sym]) return;
        var pos = posOf(sym);
        var isOpen = !!(pos && pos.status === 'OPEN');
        if (isOpen && !window.confirm('Chốt vị thế ' + sym + ' tại giá hiện tại?\nKết quả sẽ được ghi vào tỷ lệ thắng / lãi-lỗ.')) {
            return;
        }
        posBusy[sym] = true;
        setToggleBusy(sym, true);
        var sig = signalBySym[sym] || {};
        var req = isOpen
            ? api(API + '/positions/' + pos.id + '/close', { method: 'POST', body: JSON.stringify({}) })
            : api(API + '/positions', { method: 'POST', body: JSON.stringify({
                  symbol: sym,
                  strategy: sig.strategy || (pos || {}).strategy || '',
                  signal_id: sig.id || null
              }) });
        req.then(function (d) {
            toastPos((d && d.message) || (isOpen ? ('Đã đóng vị thế ' + sym) : ('Đã mở vị thế ' + sym)));
            return loadPositions();
        }).then(function () {
            // Mở vị thế ⇒ chuyển sang tab "Đang mở"; chốt vị thế ⇒ chuyển sang tab "Đã đóng"
            // (đồng thời cập nhật số vị thế đã đóng / lãi-lỗ / tỷ lệ thắng ở khối Tổng quan).
            setTab(isOpen ? 'closed' : 'open');
            refreshPositionCells();
            return loadOverview();   // vị thế đổi ⇒ tỷ lệ thắng / lãi-lỗ đổi
        }).catch(function (err) {
            toastPos((err && err.message) || 'Không cập nhật được vị thế', true);
        }).then(function () {
            posBusy[sym] = false;
            setToggleBusy(sym, false);
        });
    }

    function rowHtml(r) {
        var sym = String(r.symbol || '').toUpperCase();
        var pos = posOf(sym);
        return '<tr onclick="sigOpenDetail(' + r.id + ')" title="Xem chi tiết &amp; lý do hình thành tín hiệu">' +
            '<td><b>' + esc(r.symbol) + '</b><span data-div="' + esc(r.symbol) + '"></span>' +
                '<div><span class="sig-badge st-mini ' + (STATE_BADGE[r.state] || 'draft') + '">' + esc(STATE_LABEL[r.state] || r.state || '—') + '</span></div></td>' +
            '<td>' + esc(STRATEGY_LABEL[r.strategy] || r.strategy || '—') + '</td>' +
            '<td>' + num(r.score, 1) + '</td>' +
            '<td>' + num(r.confidence, 1) + '</td>' +
            '<td class="sig-price" data-sym="' + esc(r.symbol) + '">' + quoteCellHtml(liveQuotes[sym]) + '</td>' +
            '<td class="sig-pos-entry" data-sym="' + esc(r.symbol) + '">' + posEntryHtml(pos) + '</td>' +
            '<td class="sig-pos-pnl" data-sym="' + esc(r.symbol) + '">' + posPnlHtml(pos, liveQuotes[sym]) + '</td>' +
            '<td>' + vnd(r.entry) + '</td>' +
            '<td>' + vnd(r.stop_loss) + '</td>' +
            '<td>' + vnd(r.trailing_stop) + '</td>' +
            '<td class="sig-pos-cell" data-sym="' + esc(r.symbol) + '">' + toggleHtml(sym, pos) + '</td>' +
            '<td style="font-size:11.5px;">' + dt(r.created_at) + '</td>' +
            '</tr>';
    }

    function sigLoad(newOffset) {
        if (newOffset !== undefined && newOffset !== null) offset = Math.max(0, newOffset);
        var body = $('sigTableBody');
        if (!body) return;
        body.innerHTML = '<tr><td colspan="12" class="th-empty">Đang tải…</td></tr>';
        return api(API + '?' + filterQuery(true).toString()).then(function (d) {
            total = d.total || 0;
            var cntRec = $('sigCntRec');
            if (cntRec) cntRec.textContent = num(total, 0);
            var items = d.items || [];
            if (items.length) emptyReloads = 0;
            // Nhớ chiến thuật + signal_id của từng mã đang hiển thị ⇒ khi bật ON ghi được vào vị thế.
            signalBySym = {};
            items.forEach(function (r) {
                var s = String(r.symbol || '').toUpperCase();
                if (s) signalBySym[s] = { id: r.id, strategy: r.strategy };
            });
            body.innerHTML = items.length ? items.map(rowHtml).join('')
                : '<tr><td colspan="12" class="th-empty">' + emptyRowHint() + '</td></tr>';
            // Giá hiện tại: nạp 1 request cho cả trang; nếu đang sắp theo giá thì sắp lại sau khi có giá.
            loadLivePrices().then(sortRowsByPrice);
            var pager = $('sigPager');
            if (pager) {
                var from = total ? offset + 1 : 0;
                var to = Math.min(offset + PAGE_SIZE, total);
                pager.innerHTML = 'Hiển thị ' + from + '–' + to + ' / ' + total + ' tín hiệu ' +
                    '<button type="button" class="th-btn ghost" style="padding:5px 12px;" onclick="sigLoad(' + Math.max(0, offset - PAGE_SIZE) + ')"' + (offset <= 0 ? ' disabled' : '') + '>← Trước</button>' +
                    '<button type="button" class="th-btn ghost" style="padding:5px 12px;" onclick="sigLoad(' + (offset + PAGE_SIZE) + ')"' + (offset + PAGE_SIZE >= total ? ' disabled' : '') + '>Sau →</button>';
            }
            notifyParentHeight();
        }).catch(function (err) {
            body.innerHTML = '<tr><td colspan="12" class="th-empty" style="color:#d0314e;">' + esc(err.message || 'Không tải được danh sách') + '</td></tr>';
        });
    }

    function sigReload() {
        setUpdated(null);
        // Nạp VỊ THẾ trước để bảng vẽ đúng trạng thái nút ON/OFF ngay từ đầu.
        return loadPositions().then(function () {
            setTab(activeTab);   // vẽ lại bảng của tab đang mở (Đang mở / Đã đóng) theo dữ liệu vị thế mới
            return Promise.all([loadOverview(), sigLoad(0)]);
        });
    }

    // ---------------- Cập nhật TÍN HIỆU T+ ----------------
    // Tín hiệu của TuniX là tín hiệu T+ (ngắn hạn): quá hạn nắm giữ thì KHÔNG còn hiệu lực.
    // Mỗi lần mở trang, server sẽ (1) đóng tín hiệu đã quá hạn và (2) phân tích lại các mã còn
    // tín hiệu đang mở bằng nến mới nhất. Chạy NỀN: danh sách hiện trước, cập nhật xong mới vẽ lại.
    var refreshing = false;
    // Số lần tự tải lại danh sách khi bảng TRỐNG (server có thể đang quét bù ở nền sau khi
    // instance vừa "thức dậy" — xem `signal_engine/scheduler.py`).
    var emptyReloads = 0;
    var EMPTY_RELOAD_MAX = 5;

    function sigAutoRefresh(manual) {
        if (refreshing) return Promise.resolve();
        refreshing = true;
        setDataState('đang cập nhật tín hiệu theo phiên mới nhất…');
        return api(API + '/refresh', { method: 'POST', body: JSON.stringify({}) }).then(function (d) {
            var expired = (d && d.expired) || 0;
            var refreshed = (d && d.refreshed) || 0;
            var pending = (d && d.pending) || 0;
            if (d && d.max_holding_sessions) {
                var exEl = $('thExpiry');
                if (exEl) exEl.textContent = d.max_holding_sessions;
            }
            var notes = [];
            if (expired) notes.push(expired + ' tín hiệu hết hạn T+ đã đóng');
            if (refreshed) notes.push(refreshed + ' mã phân tích lại');
            if (pending) notes.push(pending + ' mã còn lại sẽ cập nhật ở lần mở sau');
            setUpdated((d && d.generated_at) || null,
                notes.length ? ('đã cập nhật: ' + notes.join(' · ')) : 'tín hiệu đang còn hiệu lực');
            if (expired || refreshed) return Promise.all([loadOverview(), sigLoad(0)]);
        }).catch(function (err) {
            setDataState(manual ? ('không cập nhật được: ' + (err.message || 'lỗi không xác định'))
                                : 'vẫn dùng dữ liệu lần quét trước');
        }).then(function () {
            refreshing = false;
        });
    }

    function sigBoot() {
        // Hiện dữ liệu hiện có NGAY, sau đó cập nhật lại tín hiệu T+ ở luồng nền (không chặn UI).
        return sigReload().then(function () { return sigAutoRefresh(false); });
    }

    // ---------------- Chi tiết ----------------
    function condRow(c) {
        var mark = c.state === 'PASS' ? '<span class="ok">✓</span>'
            : c.state === 'FAIL' ? '<span class="no">✗</span>' : '<span class="na">—</span>';
        var value = c.value === null || c.value === undefined ? '—' : JSON.stringify(c.value);
        return '<div class="sig-cond">' + mark + '<div><b>' + esc(c.label || c.key) + '</b> · giá trị: ' +
            esc(String(value)) + ' · ngưỡng: ' + esc(String(c.threshold == null ? '—' : c.threshold)) +
            (c.required ? ' · <span style="color:#008080;font-weight:600;">bắt buộc</span>' : '') +
            (c.state === 'UNAVAILABLE' && c.reason ? ' <span class="sig-note">(' + esc(c.reason) + ')</span>' : '') +
            '</div></div>';
    }

    function sigOpenDetail(id) {
        var modal = $('sigModal');
        var body = $('sigModalBody');
        var meta = $('sigModalMeta');
        if (!modal || !body) return;
        body.innerHTML = '<div class="sig-note">Đang tải chi tiết…</div>';
        meta.innerHTML = '';
        modal.classList.add('show');
        api(API + '/' + id).then(function (d) {
            $('sigModalTitle').textContent = d.symbol + ' · ' + (STRATEGY_LABEL[d.strategy] || d.strategy) + ' · ' + (d.signal || 'không tín hiệu');
            meta.innerHTML = [
                '<span class="sig-badge">Tín hiệu: ' + esc(d.signal || '—') + '</span>',
                '<span class="sig-badge">Trạng thái: ' + esc(STATE_LABEL[d.state] || d.state || '—') + '</span>',
                '<span class="sig-badge">Score: ' + num(d.score, 1) + '</span>',
                '<span class="sig-badge">Độ tin cậy: ' + num(d.confidence, 1) + '</span>',
                '<span class="sig-badge">Setup: ' + esc(STRENGTH_LABEL[d.setup_strength] || d.setup_strength || '—') + '</span>',
                '<span class="sig-badge">Rủi ro: ' + esc(d.risk_level || '—') + '</span>',
                '<span class="sig-badge">Regime: ' + esc(d.market_regime || '—') + '</span>',
                '<span class="sig-badge">Xác nhận: ' + (d.confirmations || 0) + '/' + (d.total_conditions || 0) + '</span>'
            ].join(' ');

            var levels = (d.audit && d.audit.decision && d.audit.decision.levels) || {};
            var ai = d.ai || {};
            var models = ai.models || [];
            var aiHtml = models.length ? models.map(function (m) {
                var ok = !!m.trained;
                return '<div class="sig-cond"><span class="' + (ok ? 'ok' : 'na') + '">' + (ok ? '✓' : '—') + '</span>' +
                    '<div><b>' + esc(m.model) + '</b> · ' + esc(m.status) + ' · hướng: ' +
                    esc(String(m.direction || (ok ? '—' : 'chưa có prediction'))) + '</div></div>';
            }).join('') : '<div class="sig-cond"><span class="na">—</span><div>Chưa có model AI nào được train.</div></div>';

            var conditions = d.conditions || [];
            var passed = conditions.filter(function (c) { return c.state === 'PASS'; });
            var failed = conditions.filter(function (c) { return c.state === 'FAIL'; });
            var unavailable = conditions.filter(function (c) { return c.state === 'UNAVAILABLE'; });
            var reasons = d.reasons || [];
            var history = d.history || [];

            body.innerHTML =
                '<div class="sig-block"><h4>Mức giao dịch tham chiếu</h4>' +
                    '<div class="sig-cond"><span class="ok">•</span><div><b>Entry:</b> ' + vnd(d.entry) +
                    ' · <b>Stop Loss:</b> ' + vnd(d.stop_loss) + ' · <b>Take Profit:</b> ' + vnd(d.take_profit) +
                    ' · <b>Trailing Stop:</b> ' + vnd(d.trailing_stop) + '</div></div>' +
                    '<div class="sig-cond"><span class="na">i</span><div>R:R = ' + esc(String(levels.rr_ratio == null ? '—' : levels.rr_ratio)) +
                    ' · rủi ro/lệnh = ' + esc(String(levels.risk_pct_from_entry == null ? '—' : levels.risk_pct_from_entry)) + '% giá' +
                    ' · ATR = ' + esc(String(levels.atr == null ? '—' : levels.atr)) + '</div></div>' +
                    '<div class="sig-note">Mức giá tính theo bội số ATR tại thời điểm tín hiệu (cấu hình engine) — chỉ để tham chiếu.</div>' +
                '</div>' +
                '<div class="sig-block"><h4>AI (' + esc(ai.status || '—') + ')</h4>' + aiHtml +
                    (ai.note ? '<div class="sig-note">' + esc(ai.note) + '</div>' : '') + '</div>' +
                '<div class="sig-block"><h4>Điều kiện — ' + passed.length + '/' + conditions.length + ' đạt</h4>' +
                    (conditions.length ? conditions.map(condRow).join('') : '<div class="sig-note">Chưa có điều kiện nào.</div>') +
                    (failed.length ? '<div class="sig-note">Chưa đạt: ' + failed.map(function (c) { return esc(c.label); }).join('; ') + '</div>' : '') +
                    (unavailable.length ? '<div class="sig-note">' + unavailable.length + ' điều kiện chưa đánh giá được (thiếu dữ liệu/model).</div>' : '') +
                '</div>' +
                '<div class="sig-block"><h4>Vì sao có tín hiệu này?</h4>' +
                    (reasons.length ? '<ul class="sig-why">' + reasons.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>'
                                    : '<div class="sig-note">Chưa có lý do nào (tín hiệu chưa hình thành).</div>') +
                '</div>' +
                '<div class="sig-block"><h4>Lịch sử trạng thái</h4>' +
                    (history.length ? '<div class="sig-tablewrap"><table class="sig-table"><thead><tr><th>Thời gian</th><th>Từ</th><th>Đến</th><th>Tín hiệu</th><th>Lý do</th><th>Giá</th><th>Kết quả</th></tr></thead><tbody>' +
                        history.map(function (h) {
                            return '<tr><td style="font-size:11.5px;">' + dt(h.created_at) + '</td><td>' + esc(h.from_state || '—') +
                                '</td><td>' + esc(h.to_state || '—') + '</td><td>' + signalHtml(h.signal) +
                                '</td><td style="font-size:11.5px;">' + esc(h.reason || '—') + '</td><td>' + vnd(h.price) +
                                '</td><td>' + esc(h.result || (h.return_pct == null ? '—' : h.return_pct + '%')) + '</td></tr>';
                        }).join('') + '</tbody></table></div>'
                        : '<div class="sig-note">Chưa có bản ghi lịch sử.</div>') +
                '</div>' +
                '<div class="sig-note">Snapshot lưu lúc: ' + esc(d.as_of || '—') + ' · engine ' + esc(d.config_version || '') + '</div>';
            notifyParentHeight();
        }).catch(function (err) {
            body.innerHTML = '<div class="sig-note" style="color:#d0314e;">' + esc(err.message || 'Không tải được chi tiết') + '</div>';
        });
    }

    function sigCloseDetail() {
        var modal = $('sigModal');
        if (modal) modal.classList.remove('show');
    }

    // ---------------- Khởi động ----------------
    function init() {
        var modal = $('sigModal');
        if (modal) {
            modal.addEventListener('click', function (e) { if (e.target === modal) sigCloseDetail(); });
        }
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape') sigCloseDetail(); });
        bindSortHeaders();
        // Giá hiện tại tự làm mới mỗi phút (chỉ khi tab đang hiển thị & bảng có dữ liệu).
        setInterval(function () {
            if (document.hidden) return;
            if (!document.querySelector('td.sig-price')) {
                // Bảng TRỐNG ⇒ tải lại danh sách vài lần (dữ liệu có thể đang được quét bù ở nền).
                if (activeTab === 'rec' && emptyReloads < EMPTY_RELOAD_MAX && token()) {
                    emptyReloads += 1;
                    sigLoad(0);
                }
                return;
            }
            loadLivePrices().then(sortRowsByPrice);
        }, 60000);

        if (!token()) {
            showLocked('Bạn cần đăng nhập để dùng tính năng Tín hiệu.');
            return;
        }
        // Kiểm tra quyền (server vẫn là lớp bảo vệ chính) rồi mới tải dữ liệu.
        fetch('/api/auth/me', { headers: { 'Authorization': 'Bearer ' + token() }, cache: 'no-store' })
            .then(function (r) {
                if (r.status === 401) return refreshSession().then(function (ok) { return ok ? null : 'expired'; });
                return r.ok ? r.json() : null;
            })
            .then(function (u) {
                if (u === 'expired') { showLocked('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'); return; }
                if (u) localStorage.setItem('tunix_user', JSON.stringify(u));
                var allowed = u && (u.signal_access === true || u.role === 'super-admin' || u.role === 'admin');
                if (!allowed) {
                    showLocked('Tài khoản của bạn chưa được cấp quyền dùng tính năng Tín hiệu.');
                    return;
                }
                sigBoot();
            })
            .catch(function () {
                // Không gọi được /me (mạng) → vẫn thử tải danh sách; server sẽ trả 401/403 nếu không đủ quyền.
                sigReload();
            });
    }

    // ===== Embed / chiều cao / theme (giống các trang TuniX khác) =====
    if (window.self !== window.top) document.body.classList.add('embedded');
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
    function notifyParentHeight() {
        try {
            var el = document.querySelector('.th-page') || document.body;
            var height = Math.ceil(el.getBoundingClientRect().height);
            if (!height || height <= 0) return;
            if (Math.abs(height - (window.__lastSentH || 0)) < 2) return;
            window.__lastSentH = height;
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'tunix-embed-height', height: height }, '*');
            }
        } catch (e) { /* ignore */ }
    }
    window.addEventListener('message', function (event) {
        var data = event.data;
        if (!data || data.type !== 'tunix-theme') return;
        var mode = data.mode === 'dark' ? 'dark' : 'light';
        localStorage.setItem('theme', mode);
        applyEmbedTheme(mode);
        setTimeout(notifyParentHeight, 50);
    });
    window.addEventListener('resize', notifyParentHeight);
    if (window.ResizeObserver) {
        var obs = new ResizeObserver(notifyParentHeight);
        obs.observe(document.querySelector('.th-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.addEventListener('load', function () {
        setTimeout(notifyParentHeight, 60);
        setTimeout(notifyParentHeight, 400);
    });

    // Hàm dùng trong HTML (inline onclick)
    window.sigLoad = sigLoad;
    window.sigReload = sigReload;
    window.sigAutoRefresh = sigAutoRefresh;
    window.sigOpenDetail = sigOpenDetail;
    window.sigCloseDetail = sigCloseDetail;
    window.sigTogglePosition = sigTogglePosition;
    window.sigSetTab = setTab;
})();
