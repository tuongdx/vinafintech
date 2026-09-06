/* TuniX — Crypto (trang crypto.html)
   Dữ liệu Binance qua backend /api/crypto/* — frontend không gọi Binance trực tiếp.
   Cập nhật định kỳ bằng polling (15s) — không cần reload trang. */
(function () {
    'use strict';

    var PERIODS = {
        '1H': { interval: '1m', limit: 60, label: '1H' },
        '4H': { interval: '5m', limit: 48, label: '4H' },
        '1D': { interval: '15m', limit: 96, label: '1D' },
        '1W': { interval: '1h', limit: 168, label: '1W' },
        '1M': { interval: '4h', limit: 180, label: '1M' },
        '3M': { interval: '1d', limit: 90, label: '3M' },
        '1Y': { interval: '1d', limit: 365, label: '1Y' }
    };
    var DEFAULT_PERIOD = '1D';
    var POLL_MS = 15000;

    var state = {
        items: [],
        quote: 'USDT',
        view: 'list',   // list | detail
        filter: 'top',
        sort: 'rank',
        q: '',
        sym: null,
        period: DEFAULT_PERIOD,
        loading: false,
        visible: 50
    };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    function fmtVnTime(iso) {
        if (!iso) return '--:--:--';
        try {
            var t = new Date(iso).getTime();
            if (isNaN(t)) return '--:--:--';
            var vn = new Date(t + 7 * 3600 * 1000);
            return pad2(vn.getUTCHours()) + ':' + pad2(vn.getUTCMinutes()) + ':' + pad2(vn.getUTCSeconds());
        } catch (e) { return '--:--:--'; }
    }
    function fmtPrice(p) {
        var n = Number(p);
        if (!isFinite(n) || n === 0) return '—';
        var d = n >= 1000 ? 2 : n >= 1 ? 4 : 6;
        return n.toLocaleString('vi-VN', { maximumFractionDigits: d });
    }
    function fmtBig(v) {
        var n = Number(v) || 0;
        if (!n) return '—';
        var a = Math.abs(n);
        if (a >= 1e12) return (n / 1e12).toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + 'T';
        if (a >= 1e9) return (n / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + 'B';
        if (a >= 1e6) return (n / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + 'M';
        if (a >= 1e3) return (n / 1e3).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + 'K';
        return n.toLocaleString('vi-VN', { maximumFractionDigits: 0 });
    }
    function pctTxt(p) {
        var n = Number(p) || 0;
        return (n >= 0 ? '+' : '') + n.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + '%';
    }
    function pctCls(p) { var n = Number(p) || 0; return n > 0 ? 'up' : (n < 0 ? 'down' : 'flat'); }
    function api(path, params) {
        var url = '/api/crypto' + path;
        if (params) {
            var qs = Object.keys(params).filter(function (k) { return params[k] !== '' && params[k] != null; })
                .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
            if (qs) url += '?' + qs;
        }
        return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    // ================= Render list =================
    function filteredList() {
        var arr = state.items.slice();
        var f = state.filter;
        if (f === 'gainers') arr = arr.filter(function (x) { return (x.change_pct || 0) > 0; });
        else if (f === 'losers') arr = arr.filter(function (x) { return (x.change_pct || 0) < 0; });
        var q = (state.q || '').trim().toLowerCase();
        if (q) {
            arr = arr.filter(function (x) {
                return (x.name || '').toLowerCase().indexOf(q) >= 0 || (x.symbol || '').toLowerCase().indexOf(q) >= 0 || (x.pair || '').toLowerCase().indexOf(q) >= 0;
            });
        }
        var s = state.sort;
        if (f === 'gainers' && s === 'rank') s = 'change';
        if (f === 'losers' && s === 'rank') s = 'change';
        if (f === 'volume' && s === 'rank') s = 'volume';
        var dir = s === 'rank' ? 1 : -1;
        arr.sort(function (a, b) {
            if (s === 'rank') return a.rank - b.rank;
            var va = Number(a[s] || 0), vb = Number(b[s] || 0);
            return (va - vb) * dir;
        });
        return arr;
    }

    function coinIconHtml(sym, big) {
        var s = String(sym == null ? '' : sym).trim().toLowerCase();
        var letter = String(sym == null ? '' : sym).trim().charAt(0).toUpperCase() || '?';
        var b = big ? ' big' : '';
        return '<span class="cr-ico-wrap' + b + '">' +
            '<img class="cr-ico" loading="lazy" alt="' + esc(sym) + '" src="https://assets.coincap.io/assets/icons/' + s + '@2x.png" onerror="this.classList.add(\'broken\')">' +
            '<span class="cr-ico-fb' + b + '">' + esc(letter) + '</span>' +
            '</span>';
    }

    function renderList() {
        var rows = filteredList();
        var msg = $('crMsg');
        if (state.loading && !rows.length) {
            msg.innerHTML = '<div class="cr-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải dữ liệu…</div>';
        } else if (!state.items.length) {
            msg.innerHTML = '<div class="cr-msg">Không có dữ liệu.</div>';
        } else if (!rows.length) {
            msg.innerHTML = '<div class="cr-msg">Không tìm thấy crypto phù hợp với “' + esc(state.q) + '”.</div>';
        } else {
            msg.innerHTML = '';
        }
        var allRows = rows;
        var showRows = rows.slice(0, state.visible);
        var body = showRows.map(function (x) {
            var c = pctCls(x.change_pct);
            return '<tr data-symbol="' + esc(x.symbol) + '" data-pair="' + esc(x.pair) + '">' +
                '<td class="rank">' + x.rank + '</td>' +
                '<td class="left"><div class="cr-symcell">' + coinIconHtml(x.symbol) +
                '<div class="cr-symtxt"><span class="sym">' + esc(x.name) + '</span>' +
                '<span class="nm">' + esc(x.symbol) + ' · ' + esc(x.pair) + '</span></div></div></td>' +
                '<td class="right num">' + fmtPrice(x.price) + '</td>' +
                '<td class="right num ' + c + '"><span class="cr-badge ' + c + '">' + pctTxt(x.change_pct) + '</span></td>' +
                '<td class="right num">' + fmtPrice(x.high) + '</td>' +
                '<td class="right num">' + fmtPrice(x.low) + '</td>' +
                '<td class="right num">' + fmtBig(x.volume) + '</td>' +
                '<td class="right num">' + (x.market_cap ? '$' + fmtBig(x.market_cap) : '<span class="na">—</span>') + '</td>' +
                '</tr>';
        }).join('');
        $('crBody').innerHTML = body;
        var wrap = $('crMoreWrap');
        if (wrap) {
            var more = allRows.length - showRows.length;
            if (more > 0) {
                wrap.style.display = '';
                var btn = $('crMoreBtn');
                if (btn) btn.innerHTML = '<i class="fas fa-chevron-down"></i> Xem thêm (' + more + ' mã còn lại)';
            } else {
                wrap.style.display = 'none';
            }
        }
    }

    function syncSortSelect() {
        var sel = $('crSort');
        var f = state.filter;
        var v = f === 'gainers' || f === 'losers' ? 'change' : (f === 'volume' ? 'volume' : state.sort);
        for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === v) { sel.value = v; break; }
        }
        state.sort = sel.value;
    }

    function showList() {
        state.view = 'list';
        $('crListView').style.display = '';
        $('crDetailView').style.display = 'none';
        notifyParentHeight();
    }
    function showDetail(sym) {
        state.view = 'detail';
        state.sym = sym;
        $('crListView').style.display = 'none';
        $('crDetailView').style.display = '';
        notifyParentHeight();
    }

    function load(opts) {
        opts = opts || {};
        state.loading = true;
        if (!opts.silent) renderList();
        api('/markets').then(function (d) {
            state.items = (d.items || []);
            state.quote = d.quote || 'USDT';
            var upd = $('crUpdated');
            if (upd) upd.innerHTML = '<i class="far fa-clock"></i> Cập nhật: <b>' + fmtVnTime(d.as_of) + '</b>';
            state.loading = false;
            if (state.view === 'list') {
                renderList();
            } else if (state.sym) {
                // cập nhật lại giá hiện tại của coin đang xem (nếu còn trong danh sách)
                var cur = findItem(state.sym);
                if (cur) renderDetailInfo(cur);
            }
        }).catch(function () {
            state.loading = false;
            if (state.view === 'list') {
                $('crMsg').innerHTML = '<div class="cr-msg err">Không lấy được dữ liệu từ Binance. Vui lòng thử lại sau.</div>';
                $('crBody').innerHTML = '';
            } else {
                renderDetailInfo(null);
            }
            notifyParentHeight();
        });
    }

    // ================= Detail =================
    function findItem(sym) {
        sym = String(sym || '').toUpperCase();
        for (var i = 0; i < state.items.length; i++) {
            if (state.items[i].symbol === sym || state.items[i].pair === sym + state.quote) return state.items[i];
        }
        return null;
    }

    function renderDetailInfo(item) {
        var el = $('crDetailInfo');
        if (!item) {
            el.innerHTML = '<div class="cr-msg err">Không lấy được dữ liệu cho mã này. Vui lòng thử lại sau.</div>';
            notifyParentHeight();
            return;
        }
        var c = pctCls(item.change_pct);
        el.innerHTML =
            '<div class="cr-detail-name">' + coinIconHtml(item.symbol, true) +
            '<div><div class="cr-dname-row"><h2>' + esc(item.name) + '</h2>' +
            '<span class="code">' + esc(item.symbol) + ' / ' + esc(item.pair) + '</span>' +
            '<span class="rank-tag">#' + item.rank + ' · Top vốn hóa</span></div></div></div>' +
            '<div class="cr-price-big ' + c + '">' + fmtPrice(item.price) + ' <small>USDT</small></div>' +            '<div class="cr-kpis">' +
            '<div class="cr-kpi"><div class="k-label">Thay đổi 24h</div><div class="k-val ' + c + '">' + pctTxt(item.change_pct) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Cao 24h</div><div class="k-val">' + fmtPrice(item.high) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Thấp 24h</div><div class="k-val">' + fmtPrice(item.low) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Volume 24h (USDT)</div><div class="k-val">' + fmtBig(item.volume) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Vốn hóa (USD)</div><div class="k-val">' + (item.market_cap ? '$' + fmtBig(item.market_cap) : '<span class="na">—</span>') + '</div></div>' +
            '</div>';
        notifyParentHeight();
    }

    function openDetail(sym) {
        var item = findItem(sym);
        if (!item) { renderDetailInfo(null); showDetail(sym); return; }
        state.period = DEFAULT_PERIOD;
        showDetail(sym);
        $('crChartSym').textContent = item.symbol + '/' + (state.quote || 'USDT');
        $('crChart').innerHTML = '';
        renderDetailInfo(item);
        renderPeriods();
        loadKlines();
    }

    function renderPeriods() {
        var wrap = $('crPeriods');
        wrap.innerHTML = Object.keys(PERIODS).map(function (k) {
            return '<button type="button" class="cr-period' + (k === state.period ? ' on' : '') + '" data-period="' + k + '">' + PERIODS[k].label + '</button>';
        }).join('');
    }

    function loadKlines() {
        var item = findItem(state.sym);
        if (!item) return;
        var p = PERIODS[state.period] || PERIODS[DEFAULT_PERIOD];
        var msgEl = $('crChartMsg');
        msgEl.innerHTML = '<div class="cr-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải biểu đồ…</div>';
        $('crChart').innerHTML = '';
        api('/klines', { symbol: item.pair, interval: p.interval, limit: p.limit }).then(function (d) {
            var arr = (d.items || []).filter(function (k) { return k && k.close > 0; });
            msgEl.innerHTML = '';
            if (arr.length < 2) {
                msgEl.innerHTML = '<div class="cr-msg err">Chưa đủ dữ liệu để vẽ biểu đồ.</div>';
            } else {
                drawChart(arr, item);
            }
            notifyParentHeight();
        }).catch(function () {
            msgEl.innerHTML = '<div class="cr-msg err">Không lấy được dữ liệu biểu đồ từ Binance. Vui lòng thử lại sau.</div>';
            notifyParentHeight();
        });
    }

    function drawChart(bars, item) {
        var W = 760, H = 300, padL = 46, padR = 12, padT = 12, padB = 26;
        var closes = bars.map(function (b) { return b.close; });
        var min = Math.min.apply(null, closes), max = Math.max.apply(null, closes);
        var range = (max - min) || 1;
        var up = closes[closes.length - 1] >= closes[0];
        var col = up ? '#00a08a' : '#d0314e';
        var n = bars.length;
        var iw = W - padL - padR, ih = H - padT - padB;
        function X(i) { return padL + (n === 1 ? iw / 2 : (i / (n - 1)) * iw); }
        function Y(v) { return padT + ih - ((v - min) / range) * ih; }
        var pts = closes.map(function (v, i) { return [X(i), Y(v)]; });
        var line = pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
        var area = 'M' + X(0).toFixed(1) + ',' + (padT + ih).toFixed(1) + ' L' + pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' L ') + ' L' + X(n - 1).toFixed(1) + ',' + (padT + ih).toFixed(1) + ' Z';

        // trục giá
        var gridVals = [];
        for (var g = 0; g <= 4; g++) {
            var v = max - (range * g / 4);
            gridVals.push('<text x="' + (padL - 6) + '" y="' + (Y(v) + 4) + '" text-anchor="end" font-size="10" fill="#8a99b1">' + esc(fmtAxisNum(v)) + '</text>');
        }
        // nhãn thời gian
        function lbl(i) {
            var b = bars[i];
            var dt = new Date(b.time);
            if (isNaN(dt)) return '';
            var vn = new Date(dt.getTime() + 7 * 3600 * 1000);
            var span = bars[n - 1].time - bars[0].time;
            if (span <= 2 * 3600 * 1000) return pad2(vn.getUTCHours()) + ':' + pad2(vn.getUTCMinutes());
            return pad2(vn.getUTCDate()) + '/' + pad2(vn.getUTCMonth() + 1);
        }
        var mid = Math.floor(n / 2);
        var timeLbls = [0, mid, n - 1].map(function (idx) {
            return '<text x="' + X(idx) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="#8a99b1">' + esc(lbl(idx)) + '</text>';
        }).join('');
        var lastVal = closes[n - 1];
        $('crChart').innerHTML =
            '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Biểu đồ giá ' + esc(item.symbol) + '">' +
            '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="transparent"/>' +
            '<path d="' + area + '" fill="' + col + '" opacity="0.10"/>' +
            '<polyline points="' + line + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
            gridVals.join('') + timeLbls +
            '<text x="' + (W - padR) + '" y="' + (padT + 4) + '" text-anchor="end" font-size="12" font-weight="700" fill="' + col + '">' + esc(fmtPrice(lastVal)) + '</text>' +
            '</svg>';
        var from = new Date(bars[0].time), to = new Date(bars[n - 1].time);
        function d(l, h) { return l.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ' ' + h.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' }); }
        $('crChartHint').innerHTML = 'Khoảng: ' + d(from, from) + ' → ' + d(to, to) + ' · ' + PERIODS[state.period].label + ' · Giá cuối ' + fmtPrice(lastVal) + ' USDT';
    }
    function fmtAxisNum(v) {
        if (v >= 1000) return v.toLocaleString('vi-VN', { maximumFractionDigits: 0 });
        if (v >= 1) return v.toLocaleString('vi-VN', { maximumFractionDigits: 2 });
        return v.toLocaleString('vi-VN', { maximumFractionDigits: 4 });
    }

    // ================= Bind =================
    function bind() {
        $('crSearch').addEventListener('input', function () { state.q = $('crSearch').value; state.visible = 50; renderList(); });
        Array.prototype.forEach.call(document.querySelectorAll('#crChips .cr-chip'), function (b) {
            b.addEventListener('click', function () {
                state.filter = b.getAttribute('data-filter');
                Array.prototype.forEach.call(document.querySelectorAll('#crChips .cr-chip'), function (x) { x.classList.toggle('on', x === b); });
                state.visible = 50;
                syncSortSelect();
                renderList();
            });
        });
        $('crSort').addEventListener('change', function () { state.sort = $('crSort').value; state.visible = 50; renderList(); });
        var moreBtn = $('crMoreBtn');
        if (moreBtn) moreBtn.addEventListener('click', function () { state.visible += 50; renderList(); });
        $('crBody').addEventListener('click', function (e) {
            var tr = e.target.closest('tr[data-symbol]');
            if (tr) openDetail(tr.getAttribute('data-symbol'));
        });
        $('crBack').addEventListener('click', function () { state.sym = null; showList(); });
        $('crPeriods').addEventListener('click', function (e) {
            var b = e.target.closest('.cr-period[data-period]');
            if (!b) return;
            state.period = b.getAttribute('data-period');
            Array.prototype.forEach.call(document.querySelectorAll('.cr-period'), function (x) { x.classList.toggle('on', x.getAttribute('data-period') === state.period); });
            loadKlines();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && state.view === 'detail') { state.sym = null; showList(); }
        });
    }

    // ===== Embed / chiều cao / theme =====
    if (window.self !== window.top) document.body.classList.add('embedded');
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
    function notifyParentHeight() {
        try {
            var el = document.querySelector('.cr-page') || document.body;
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

    // ===== Init =====
    function init() {
        bind();
        load({});
        setInterval(function () { load({ silent: true }); }, POLL_MS);
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
        obs.observe(document.querySelector('.cr-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
    notifyParentHeight();
})();
