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
    var scanning = false;

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
    function dt(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        return isNaN(d.getTime()) ? '—' : d.toLocaleString('vi-VN', { hour12: false });
    }
    function signalHtml(signal) {
        if (signal === 'BUY') return '<span class="sig-signal buy">BUY</span>';
        if (signal === 'SELL') return '<span class="sig-signal sell">SELL</span>';
        return '<span style="color:#8a99b1;">—</span>';
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
            var perf = d.performance || {};
            var byStrategy = d.by_strategy || {};
            var byState = d.by_state || {};
            $('sigOverview').innerHTML = [
                stat('Tổng tín hiệu', d.total_signals, d.active_signals + ' đang mở'),
                stat('Uptrend', byStrategy.UPTREND || 0),
                stat('Bottom Fishing', byStrategy.BOTTOM_FISHING || 0),
                stat('Swing', byStrategy.SWING || 0),
                stat('Đã thoát', byState.EXIT || 0),
                stat('Tỷ lệ thắng', perf.win_rate === null || perf.win_rate === undefined ? '—' : perf.win_rate + '%',
                    perf.closed_with_result ? perf.closed_with_result + ' lệnh đã đóng' : 'chưa có lệnh đóng'),
                stat('Profit factor', perf.profit_factor === null || perf.profit_factor === undefined ? '—' : perf.profit_factor),
                stat('Lãi/lỗ TB', (perf.average_gain_pct == null ? '—' : perf.average_gain_pct + '%') + ' / ' +
                    (perf.average_loss_pct == null ? '—' : perf.average_loss_pct + '%')),
                stat('Drawdown tối đa', perf.max_drawdown_pct === null || perf.max_drawdown_pct === undefined ? '—' : perf.max_drawdown_pct + '%'),
                stat('Nắm giữ TB', perf.average_holding_days == null ? '—' : perf.average_holding_days + ' phiên'),
                stat('Model AI', d.model_status === 'MODEL_NOT_TRAINED' ? 'Chưa train' : (d.model_status || '—'), 'engine ' + (d.config_version || ''))
            ].join('');
            setUpdated(null);
            notifyParentHeight();
        }).catch(function (err) {
            if (String(err.message || '').indexOf('quyền') === -1) {
                $('sigOverview').innerHTML = stat('Lỗi', '—', err.message || 'Không tải được tổng quan');
            }
        });
    }

    function setUpdated(iso) {
        var el = $('thUpdated');
        if (el) el.textContent = iso ? dt(iso) : '—';
        var st = $('thDataState');
        if (st) st.textContent = iso ? 'đã có dữ liệu quét' : 'chưa quét trong phiên này';
    }

    // ---------------- Danh sách ----------------
    function filterQuery(withPaging) {
        var params = new URLSearchParams();
        var symbol = ($('sigSymbol') || {}).value || '';
        var strategy = ($('sigStrategy') || {}).value || '';
        var signal = ($('sigSignal') || {}).value || '';
        var state = ($('sigState') || {}).value || '';
        var minScore = ($('sigMinScore') || {}).value || '';
        var activeOnly = ($('sigActiveOnly') || {}).checked;
        if (symbol.trim()) params.set('symbol', symbol.trim().toUpperCase());
        if (strategy) params.set('strategy', strategy);
        if (signal) params.set('signal', signal);
        if (state) params.set('state', state);
        if (minScore !== '') params.set('min_score', minScore);
        if (activeOnly) params.set('active_only', 'true');
        if (withPaging) { params.set('limit', PAGE_SIZE); params.set('offset', offset); }
        return params;
    }

    function rowHtml(r) {
        return '<tr onclick="sigOpenDetail(' + r.id + ')" title="Xem chi tiết &amp; lý do hình thành tín hiệu">' +
            '<td><b>' + esc(r.symbol) + '</b></td>' +
            '<td>' + esc(STRATEGY_LABEL[r.strategy] || r.strategy || '—') + '</td>' +
            '<td>' + signalHtml(r.signal) + '</td>' +
            '<td>' + num(r.score, 1) + '</td>' +
            '<td>' + num(r.confidence, 1) + '</td>' +
            '<td>' + vnd(r.entry) + '</td>' +
            '<td>' + vnd(r.stop_loss) + '</td>' +
            '<td>' + vnd(r.trailing_stop) + '</td>' +
            '<td><span class="sig-badge ' + (STATE_BADGE[r.state] || 'draft') + '">' + esc(STATE_LABEL[r.state] || r.state || '—') + '</span></td>' +
            '<td style="font-size:11.5px;">' + dt(r.created_at) + '</td>' +
            '</tr>';
    }

    function sigLoad(newOffset) {
        if (newOffset !== undefined && newOffset !== null) offset = Math.max(0, newOffset);
        var body = $('sigTableBody');
        if (!body) return;
        body.innerHTML = '<tr><td colspan="10" class="th-empty">Đang tải…</td></tr>';
        return api(API + '?' + filterQuery(true).toString()).then(function (d) {
            total = d.total || 0;
            var items = d.items || [];
            body.innerHTML = items.length ? items.map(rowHtml).join('')
                : '<tr><td colspan="10" class="th-empty">Chưa có tín hiệu phù hợp bộ lọc — bấm <b>Quét thị trường</b> để tạo.</td></tr>';
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
            body.innerHTML = '<tr><td colspan="10" class="th-empty" style="color:#d0314e;">' + esc(err.message || 'Không tải được danh sách') + '</td></tr>';
        });
    }

    function sigReload() {
        setUpdated(null);
        return Promise.all([loadOverview(), sigLoad(0)]);
    }

    // ---------------- Quét ----------------
    function sigScan() {
        if (scanning) return;
        scanning = true;
        var btn = $('sigScanBtn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang quét…'; }
        api(API + '/scan', { method: 'POST', body: JSON.stringify({}) }).then(function (d) {
            var rot = $('sigRotation');
            if (rot) {
                var leaders = (d.rotation && d.rotation.leaders) || [];
                rot.innerHTML = '<b>Market Regime:</b> ' + esc((d.market || {}).regime || '—') +
                    ' · <b>Đã quét:</b> ' + (d.count || 0) + ' mã' +
                    ' · <b>TOP ' + ((d.rotation && d.rotation.portfolio_size) || 5) + ' (rotation Swing):</b> ' +
                    (leaders.length ? leaders.map(function (s) { return '<span class="sig-chip">' + esc(s) + '</span>'; }).join(' ')
                                    : '<span class="sig-chip weak">chưa đủ dữ liệu</span>');
            }
            setUpdated(d.generated_at);
            return Promise.all([loadOverview(), sigLoad(0)]);
        }).catch(function (err) {
            var rot = $('sigRotation');
            if (rot) rot.innerHTML = '<span class="sig-chip weak">Quét thất bại</span> ' + esc(err.message || '');
        }).then(function () {
            scanning = false;
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-satellite-dish"></i> Quét thị trường'; }
        });
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
                sigReload();
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
    window.sigScan = sigScan;
    window.sigReload = sigReload;
    window.sigOpenDetail = sigOpenDetail;
    window.sigCloseDetail = sigCloseDetail;
})();
