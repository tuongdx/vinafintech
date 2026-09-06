/* TuniX — Giá Vàng/Bạc (trang gold.html)
   Dữ liệu qua backend /api/gold/* — frontend không gọi nguồn ngoài trực tiếp.
   Chỉ hiển thị: bảng giá trong nước, giá thế giới, biểu đồ, so sánh. */
(function () {
    'use strict';

    var PERIODS = ['1D', '1W', '1M', '3M', '1Y', '5Y'];
    var DEFAULT_PERIOD = '1W';
    var WORLD_POLL_MS = 30000;
    var DOMESTIC_POLL_MS = 120000;

    var state = {
        period: DEFAULT_PERIOD,
        world: null,
        domestic: null,
        chartData: null
    };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    function fmtVnd(n) {
        var v = Number(n);
        if (!isFinite(v)) return '—';
        return v.toLocaleString('vi-VN', { maximumFractionDigits: 0 });
    }
    function fmtUsd(n, dec) {
        var v = Number(n);
        if (!isFinite(v)) return '—';
        if (dec == null) dec = 2;
        return v.toLocaleString('vi-VN', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }
    function fmtAxis(v) {
        var n = Number(v);
        if (!isFinite(n)) return '';
        var dec = Math.abs(n) >= 1000 ? 1 : 2;
        return n.toLocaleString('vi-VN', { maximumFractionDigits: dec });
    }
    function fmtPct(p) {
        var n = Number(p) || 0;
        return (n >= 0 ? '+' : '') + n.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + '%';
    }
    function fmtTimeFull(sec) {
        try {
            var t = new Date(Number(sec) * 1000);
            if (isNaN(t)) return '—';
            var vn = new Date(t.getTime() + 7 * 3600 * 1000);
            return pad2(vn.getUTCDate()) + '/' + pad2(vn.getUTCMonth() + 1) + '/' + vn.getUTCFullYear() +
                ' · ' + pad2(vn.getUTCHours()) + ':' + pad2(vn.getUTCMinutes());
        } catch (e) { return '—'; }
    }
    function arrowHtml(d, suffix) {
        if (d == null) return '<span class="gv-delta none">—</span>';
        var n = Number(d);
        if (n > 0) return '<span class="gv-delta up"><i class="fas fa-caret-up"></i> +' + fmtVnd(n) + (suffix || '') + '</span>';
        if (n < 0) return '<span class="gv-delta down"><i class="fas fa-caret-down"></i> −' + fmtVnd(Math.abs(n)) + (suffix || '') + '</span>';
        return '<span class="gv-delta same">= 0</span>';
    }
    function chgText(v, pct) {
        if (v == null) return '—';
        var n = Number(v);
        var cls = n > 0 ? 'up' : (n < 0 ? 'down' : 'same');
        var sign = n > 0 ? '+' : (n < 0 ? '−' : '');
        return '<span class="' + cls + '">' + sign + fmtUsd(Math.abs(n)) +
            (pct != null ? ' <span class="gw-pct">(' + fmtPct(pct).replace('-', '−') + ')</span>' : '') + '</span>';
    }
    function api(path, params) {
        var url = '/api/gold' + path;
        if (params) {
            var qs = Object.keys(params).filter(function (k) { return params[k] !== '' && params[k] != null; })
                .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
            if (qs) url += '?' + qs;
        }
        return fetch(url, { headers: { 'Accept': 'application/json' }, cache: 'no-store' }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }
    function logoHtml(b) {
        var letter = String(b.name || '?').charAt(0).toUpperCase();
        return '<span class="gv-logo"><img loading="lazy" alt="' + esc(b.name) + '" src="' + esc(b.logo) + '" onerror="this.classList.add(\'broken\')"><span class="gv-lfb">' + esc(letter) + '</span></span>';
    }

    // ================= 1) Bảng giá vàng trong nước =================
    function renderDomestic(d) {
        var body = $('gvDBody');
        var html = '';
        (d.brands || []).forEach(function (b) {
            html += '<tr class="gv-brand"><td colspan="4"><div class="gv-brand-row">' + logoHtml(b) +
                '<span class="gv-bname">' + esc(b.name) + '</span></div></td></tr>';
            (b.products || []).forEach(function (p) {
                html += '<tr>' +
                    '<td class="left"><div class="gv-pname">' + esc(p.name) + '</div><div class="gv-pkind">' + esc(p.kind) + '</div></td>' +
                    '<td class="right"><div class="gv-price">' + fmtVnd(p.buy) + '</div>' + arrowHtml(p.buy_change) + '</td>' +
                    '<td class="right"><div class="gv-price">' + fmtVnd(p.sell) + '</div>' + arrowHtml(p.sell_change) + '</td>' +
                    '<td class="right"><span class="gv-spread">' + fmtVnd((Number(p.sell) || 0) - (Number(p.buy) || 0)) + '</span></td>' +
                    '</tr>';
            });
        });
        body.innerHTML = html;
        $('gvVnWrap').style.display = '';
        setPill();
    }
    function setPill() {
        var times = [];
        if (state.world && state.world.as_of) times.push(state.world.as_of);
        if (state.domestic && state.domestic.as_of) times.push(state.domestic.as_of);
        var mx = times.length ? Math.max.apply(null, times) : null;
        $('gvUpdated').innerHTML = '<i class="far fa-clock"></i> Cập nhật lần cuối: <b>' + (mx ? fmtTimeFull(mx) : '…') + '</b>';
    }
    function loadDomestic(silent) {
        var msg = $('gvVnMsg');
        if (!silent) { msg.innerHTML = '<div class="gv-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải dữ liệu…</div>'; }
        api('/domestic').then(function (d) {
            if (d.error) throw new Error(d.message || 'Lỗi nguồn dữ liệu');
            state.domestic = d;
            msg.innerHTML = '';
            renderDomestic(d);
            notifyHeight();
        }).catch(function (e) {
            msg.innerHTML = '<div class="gv-msg err"><i class="fas fa-exclamation-triangle"></i> Không tải được giá vàng trong nước.<br><button type="button" class="gv-retry" onclick="window.tunixReloadDomestic()"><i class="fas fa-sync-alt"></i> Thử lại</button></div>';
        });
    }
    window.tunixReloadDomestic = function () { loadDomestic(false); };

    // ================= 2) Giá thế giới + 4) So sánh =================
    function byKey(items, k) {
        for (var i = 0; i < items.length; i++) if (items[i].key === k) return items[i];
        return null;
    }
    function worldIconHtml(k) {
        var ico = k === 'xau' ? 'fa-coins gold' : 'fa-coins silver';
        return '<span class="gw-ico ' + (k === 'xau' ? 'gold' : 'silver') + '"><i class="fas fa-coins"></i></span>';
    }
    function renderWorld(d) {
        var box = $('gvWorld');
        var items = d.items || [];
        var html = '<div class="gv-world">';
        items.forEach(function (it) {
            var chg;
            if (it.change_24h == null) chg = '<div class="gw-chg same">—</div>';
            else {
                var n = Number(it.change_24h), cls = n > 0 ? 'up' : (n < 0 ? 'down' : 'same'), sgn = n > 0 ? '+' : (n < 0 ? '−' : '');
                chg = '<div class="gw-chg ' + cls + '">' + sgn + fmtUsd(Math.abs(n)) + ' <span class="gw-pct">(' + (it.change_24h_pct != null ? fmtPct(it.change_24h_pct).replace('-', '−') : '—') + ')</span></div>';
            }
            html += '<div class="gv-wcard" data-price="' + esc(it.price) + '">' +
                '<div class="gw-top"><span class="gw-name">' + worldIconHtml(it.key) + esc(it.name) + '</span><span class="gw-sym">' + esc(it.symbol) + '</span></div>' +
                '<div class="gw-price">' + fmtUsd(it.price) + '<span class="gw-unit">' + esc(it.unit) + '</span></div>' +
                chg +
                '</div>';
        });
        html += '</div>';
        box.innerHTML = html;
        renderCompare(d);
        setPill();
        refreshFx();
    }
    function refreshFx() {
        // Luôn quy đổi & hiển thị giá VND bên dưới mỗi card (khi có tỷ giá).
        var fx = state.world && state.world.fx;
        var rateEl = $('gvFxRate');
        var ok = !!(fx && fx.usd_vnd);
        if (rateEl) rateEl.innerHTML = ok ? ('1 USD ≈ <b>' + fmtVnd(fx.usd_vnd) + '</b> VND') : '';
        Array.prototype.forEach.call(document.querySelectorAll('#gvWorld .gv-wcard'), function (card) {
            var old = card.querySelector('.gw-vnd');
            if (old) old.remove();
            if (ok) {
                var price = Number(card.getAttribute('data-price')) || 0;
                if (price > 0) {
                    var div = document.createElement('div');
                    div.className = 'gw-vnd';
                    div.innerHTML = '≈ ' + fmtVnd(price * fx.usd_vnd) + ' <span>VND/ounce</span>';
                    card.appendChild(div);
                }
            }
        });
    }
    function renderCompare(d) {
        var g = byKey(d.items || [], 'xau');
        var s = byKey(d.items || [], 'xag');
        if (!g || !s) { $('gvCmpMsg').innerHTML = '<div class="gv-msg err">Chưa có đủ dữ liệu so sánh.</div>'; return; }
        var ratio = (Number(g.price) || 0) / (Number(s.price) || 1);
        function row(label, gv, sv, ratioTxt) {
            return '<tr><td class="left">' + label + '</td>' +
                '<td class="right num">' + (gv == null ? '—' : gv) + '</td>' +
                '<td class="right num">' + (sv == null ? '—' : sv) + '</td>' +
                '<td class="right num cmp-ratio">' + (ratioTxt == null ? '—' : ratioTxt) + '</td></tr>';
        }
        var rows = '';
        rows += row('Giá hiện tại', fmtUsd(g.price) + ' USD/oz', fmtUsd(s.price) + ' USD/oz', fmtUsd(ratio));
        rows += row('Thay đổi 24h', chgText(g.change_24h, g.change_24h_pct), chgText(s.change_24h, s.change_24h_pct));
        rows += row('Thay đổi 1 tuần', chgText(g.change_1w), chgText(s.change_1w));
        rows += row('Thay đổi 1 tháng', chgText(g.change_1m), chgText(s.change_1m));
        rows += row('Thay đổi 1 năm', chgText(g.change_1y, g.change_1y_pct), chgText(s.change_1y, s.change_1y_pct));
        $('gvCmpBody').innerHTML = rows;
        $('gvCmpMsg').innerHTML = '';
        $('gvCmpWrap').style.display = '';
    }
    function loadWorld(silent) {
        var msg = $('gvWorldMsg');
        if (!silent) { msg.innerHTML = '<div class="gv-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải dữ liệu…</div>'; }
        api('/world').then(function (d) {
            if (d.error) throw new Error(d.message || 'Lỗi nguồn dữ liệu');
            state.world = d;
            msg.innerHTML = '';
            renderWorld(d);
            renderDomestic(state.domestic || { brands: [], as_of: null });
            notifyHeight();
        }).catch(function () {
            msg.innerHTML = '<div class="gv-msg err"><i class="fas fa-exclamation-triangle"></i> Không tải được giá vàng/bạc thế giới.<br><button type="button" class="gv-retry" onclick="window.tunixReloadWorld()"><i class="fas fa-sync-alt"></i> Thử lại</button></div>';
        });
    }
    window.tunixReloadWorld = function () { loadWorld(false); };

    // ================= 3) Biểu đồ vàng thế giới =================
    var CHART_W = 780, CHART_H = 340, CH_PAD = { l: 70, r: 14, t: 16, b: 28 };
    function loadChart(silent) {
        var msg = $('gvChartMsg');
        if (!silent) { msg.innerHTML = '<div class="gv-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải biểu đồ…</div>'; }
        api('/chart', { period: state.period, metal: 'xau' }).then(function (d) {
            if (d.error) throw new Error(d.message || 'Lỗi nguồn dữ liệu');
            state.chartData = d;
            msg.innerHTML = '';
            drawChart(d);
            notifyHeight();
        }).catch(function () {
            msg.innerHTML = '<div class="gv-msg err"><i class="fas fa-exclamation-triangle"></i> Không tải được biểu đồ.<br><button type="button" class="gv-retry" onclick="window.tunixReloadChart()"><i class="fas fa-sync-alt"></i> Thử lại</button></div>';
        });
    }
    window.tunixReloadChart = function () { loadChart(false); };

    function fmtAxisT(t, period) {
        var d = new Date(t);
        if (isNaN(d)) return '';
        var vn = new Date(d.getTime() + 7 * 3600 * 1000);
        if (period === '1D') return pad2(vn.getUTCHours()) + ':' + pad2(vn.getUTCMinutes());
        if (period === '1W') return pad2(vn.getUTCDate()) + '/' + pad2(vn.getUTCMonth() + 1);
        return pad2(vn.getUTCDate()) + '/' + pad2(vn.getUTCMonth() + 1) + (period === '5Y' ? '/' + String(vn.getUTCFullYear()).slice(2) : '');
    }
    function drawChart(d) {
        var pts = d.points || [];
        var box = $('gvChartBox');
        if (!pts.length) {
            box.innerHTML = '<div class="gv-msg err">Không có dữ liệu cho khoảng thời gian này.</div>';
            $('gvChartHint').textContent = '';
            return;
        }
        var n = pts.length;
        var minP = Infinity, maxP = -Infinity;
        pts.forEach(function (p) {
            var v = Number(p.v);
            if (v < minP) minP = v;
            if (v > maxP) maxP = v;
        });
        var span = (maxP - minP) || 1;
        minP -= span * 0.06; maxP += span * 0.06;
        // Đo kích thước THẬT của vùng biểu đồ để SVG fill đúng toàn block (không letterbox ngang).
        var W = CHART_W, H = CHART_H;
        try {
            var bw = box.clientWidth, bh = box.clientHeight;
            if (bw >= 380 && bh >= 150) { W = bw - 12; H = bh - 12; }
        } catch (e) {}
        var iw = W - CH_PAD.l - CH_PAD.r, ih = H - CH_PAD.t - CH_PAD.b;
        function X(i) { return CH_PAD.l + (n <= 1 ? 0 : (i / (n - 1)) * iw); }
        function Y(v) { return CH_PAD.t + ((maxP - v) / (maxP - minP)) * ih; }
        var up = Number(pts[n - 1].v) >= Number(pts[0].v);
        var col = up ? '#00a08a' : '#d0314e';
        var line = '', area = '';
        pts.forEach(function (p, i) {
            var x = X(i), y = Y(Number(p.v));
            line += (i ? ' L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
            area += (i ? ' L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
        });
        area += ' L' + X(n - 1).toFixed(1) + ' ' + (H - CH_PAD.b) + ' L' + X(0).toFixed(1) + ' ' + (H - CH_PAD.b) + ' Z';
        // grid + nhãn trục giá
        var gHtml = '';
        for (var g = 0; g <= 4; g++) {
            var v = maxP - ((maxP - minP) * g / 4);
            var yv = Y(v);
            gHtml += '<line x1="' + CH_PAD.l + '" y1="' + yv + '" x2="' + (W - CH_PAD.r) + '" y2="' + yv + '" stroke="#eef1f5" stroke-width="1"/>';
            gHtml += '<text x="' + (CH_PAD.l - 6) + '" y="' + (yv + 4) + '" text-anchor="end" font-size="10" fill="#8a99b1">' + esc(fmtAxis(v)) + '</text>';
        }
        // nhãn thời gian
        var labelCount = Math.min(6, n);
        var step = Math.max(1, Math.floor((n - 1) / (labelCount - 1)));
        for (var k = 0; k < n; k += step) {
            var xt = X(k), vn2 = new Date(pts[k].t);
            if (isNaN(vn2)) continue;
            gHtml += '<text x="' + xt + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="#8a99b1">' + esc(fmtAxisT(pts[k].t, d.period)) + '</text>';
        }
        var last = Number(pts[n - 1].v);
        var oldSvg = box.querySelector('svg');
        if (oldSvg) oldSvg.remove();
        box.insertAdjacentHTML('afterbegin',
            '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Biểu đồ giá vàng thế giới">' +
            '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="transparent"/>' +
            gHtml +
            '<path d="' + area + '" fill="' + col + '" opacity="0.10"/>' +
            '<path d="' + line + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
            '<text x="' + (W - CH_PAD.r) + '" y="' + (CH_PAD.t + 4) + '" text-anchor="end" font-size="12" font-weight="700" fill="' + col + '">' + esc(fmtUsd(last)) + '</text>' +
            '</svg>');
        var vline = $('gvVline'), tip = $('gvTip');
        // tooltip
        box.onmousemove = function (e) {
            var rect = box.getBoundingClientRect();
            if (!rect.width) return;
            var px = e.clientX - rect.left;
            var ratio = Math.min(1, Math.max(0, px / rect.width));
            var idx = Math.min(n - 1, Math.max(0, Math.round(ratio * (n - 1))));
            vline.style.left = (px) + 'px';
            vline.style.display = 'block';
            var p = pts[idx];
            tip.innerHTML = '<span class="tt-t">' + esc(fmtAxisT(p.t, d.period)) + '</span><br><span class="tt-v">' + esc(fmtUsd(Number(p.v))) + ' USD/oz</span>';
            var tw = tip.offsetWidth || 120;
            tip.style.display = 'block';
            var left = px - tw / 2;
            left = Math.max(4, Math.min(rect.width - tw - 4, left));
            tip.style.left = left + 'px';
            var by = Math.min(8, rect.height - 60);
            tip.style.top = by + 'px';
        };
        box.onmouseleave = function () { vline.style.display = 'none'; tip.style.display = 'none'; };
        $('gvChartHint').innerHTML = 'Khoảng: ' + fmtAxisT(pts[0].t, d.period) + ' → ' + fmtAxisT(pts[n - 1].t, d.period) +
            ' · Giá cuối ' + fmtUsd(last) + ' USD/oz';
        state.chartData = d;
    }
    // Vẽ lại khi đổi kích thước → biểu đồ luôn fill đúng bề rộng của block.
    var _chartResizeT = null;
    window.addEventListener('resize', function () {
        if (!state.chartData || !$('gvChartBox')) return;
        clearTimeout(_chartResizeT);
        _chartResizeT = setTimeout(function () { drawChart(state.chartData); }, 250);
    });

    // ================= Bind =================
    function bindPeriods() {
        var wrap = $('gvPeriods');
        wrap.innerHTML = PERIODS.map(function (p) {
            return '<button type="button" class="gv-period' + (p === state.period ? ' on' : '') + '" data-period="' + p + '">' + p + '</button>';
        }).join('');
        wrap.addEventListener('click', function (e) {
            var b = e.target.closest('.gv-period[data-period]');
            if (!b) return;
            state.period = b.getAttribute('data-period');
            Array.prototype.forEach.call(wrap.querySelectorAll('.gv-period'), function (x) { x.classList.toggle('on', x === b); });
            loadChart(false);
        });
    }

    // ===== Embed / chiều cao / theme =====
    if (window.self !== window.top) document.body.classList.add('embedded');
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
    function notifyHeight() {
        try {
            var el = document.querySelector('.gv-page') || document.body;
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
        setTimeout(notifyHeight, 50);
    });

    function init() {
        bindPeriods();
        loadWorld(false);
        loadDomestic(false);
        loadChart(false);
        setInterval(function () { loadWorld(true); }, WORLD_POLL_MS);
        setInterval(function () { loadDomestic(true); }, DOMESTIC_POLL_MS);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.addEventListener('load', function () {
        applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
        setTimeout(notifyHeight, 60);
        setTimeout(notifyHeight, 400);
    });
    window.addEventListener('resize', notifyHeight);
    if (window.ResizeObserver) {
        var obs = new ResizeObserver(function () { notifyHeight(); });
        obs.observe(document.querySelector('.gv-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
    notifyHeight();
})();
