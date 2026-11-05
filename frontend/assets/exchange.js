/* VinaFintech — Tỷ giá ngoại tệ (exchange.html) — logic frontend.
   Dữ liệu lấy từ VinaFintech backend (/api/exchange/rates) — KHÔNG gọi API ngoài trực tiếp.
   Mọi chuyển đổi tính ngay trên máy (dữ liệu đã cache) để không spam request. */
(function () {
    'use strict';
    if (window.self !== window.top) document.body.classList.add('embedded');

    // Các phần tử
    var fromSel = document.getElementById('fxFrom');
    var toSel = document.getElementById('fxTo');
    var fromFlag = document.getElementById('fxFromFlag');
    var toFlag = document.getElementById('fxToFlag');
    var amtIn = document.getElementById('fxAmt');
    var outIn = document.getElementById('fxOut');
    var swapBtn = document.getElementById('fxSwap');
    var feeOn = document.getElementById('fxFeeOn');
    var feeCtrl = document.getElementById('fxFeeCtrl');
    var feePctIn = document.getElementById('fxFeePct');
    var feeChipsEl = document.getElementById('fxFeeChips');
    var resEl = document.getElementById('fxRes');
    var gridEl = document.getElementById('fxGrid');
    var ratesMsg = document.getElementById('fxRatesMsg');
    var pillEl = document.getElementById('fxPill');
    var refreshBtn = document.getElementById('fxRefresh');

    // Trạng thái
    var CURR = [];   // [{code,name,digits,vnd_rate,per_usd}] (gồm VND)
    var VND = {};    // code -> vnd_rate (VND=1)
    var DIG = {};    // code -> decimals hiển thị
    var META = { updated_at: '', source: '', stale: false };
    var GRID_ORDER = ['USD', 'JPY', 'KRW', 'CNY', 'SGD', 'EUR', 'GBP', 'TWD', 'AUD'];
    var FEES = [0, 0.1, 0.25, 0.5, 1, 2];
    var feeState = { on: false, pct: 0.5 };
    var amount = NaN;
    var fxFrom = 'VND', fxTo = 'USD';

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function digitsOf(code) { return DIG[code] != null ? DIG[code] : 2; }
    function codeName(code) {
        for (var i = 0; i < CURR.length; i++) if (CURR[i].code === code) return CURR[i].name || code;
        return code;
    }
    function flagOf(code) {
        for (var i = 0; i < CURR.length; i++) if (CURR[i].code === code) return CURR[i].flag || '';
        return '';
    }
    function flagUrl(code) {
        var f = flagOf(code);
        return f ? 'https://flagcdn.com/w40/' + f.toLowerCase() + '.png' : '';
    }
    function setFlagImgs() {
        if (fromFlag) fromFlag.src = flagUrl(fromSel.value);
        if (toFlag) toFlag.src = flagUrl(toSel.value);
    }
    function vndOf(code) {
        var v = VND[code];
        return (v != null && isFinite(v)) ? v : null;
    }

    // ---------- Định dạng số ----------
    function fmt(n, code) {
        n = Number(n);
        if (!isFinite(n)) return '—';
        var d = digitsOf(code);
        var neg = n < 0 ? '-' : '';
        n = Math.abs(n);
        var s = n.toFixed(d);
        var parts = s.split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        return neg + (d > 0 ? parts.join(',') : parts[0]);
    }
    function parseMoney(s) {
        s = String(s == null ? '' : s).trim().replace(/\./g, '').replace(',', '.');
        if (!s) return NaN;
        var n = parseFloat(s);
        return isFinite(n) ? n : NaN;
    }
    function parsePct(s) {
        s = String(s == null ? '' : s).trim().replace(/,/g, '.');
        if (!s) return NaN;
        var n = parseFloat(s);
        return isFinite(n) ? n : NaN;
    }
    function moneyFmt(n) {
        if (!isFinite(n)) return '';
        var s = n.toFixed(8).replace(/\.?0+$/, '');
        var neg = s.charAt(0) === '-' ? '-' : '';
        if (neg) s = s.slice(1);
        var p = s.split('.');
        p[0] = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        return neg + p.join(',');
    }

    // ---------- Render bảng tỷ giá ----------
    function renderGrid() {
        var html = '';
        for (var i = 0; i < GRID_ORDER.length; i++) {
            var code = GRID_ORDER[i];
            var v = vndOf(code);
            var name = codeName(code);
            if (code === 'VND') continue;
            if (v == null) {
                html += '<div class="fx-tile" data-code="' + code + '">' +
                    '<div class="fx-tile-top">' + (flagUrl(code) ? '<img class="fx-flag" src="' + flagUrl(code) + '" alt="' + code + '" />' : '') + '<span class="fx-tcode">' + code + '</span><span class="fx-tname">' + esc(name) + '</span></div>' +
                    '<div class="fx-trate">1 ' + code + ' = <b>—</b> VND</div>' +
                    '<div class="fx-tnote">Nguồn chưa cung cấp</div></div>';
                continue;
            }
            html += '<div class="fx-tile" data-code="' + code + '" title="Chọn để hoán đổi VND → ' + code + '">' +
                '<div class="fx-tile-top">' + (flagUrl(code) ? '<img class="fx-flag" src="' + flagUrl(code) + '" alt="' + code + '" />' : '') + '<span class="fx-tcode">' + code + '</span><span class="fx-tname">' + esc(name) + '</span></div>' +
                '<div class="fx-trate">1 ' + code + ' = <b>' + fmt(v, 'VND') + '</b> VND</div></div>';
        }
        gridEl.innerHTML = html;
        Array.prototype.forEach.call(gridEl.querySelectorAll('.fx-tile'), function (t) {
            t.addEventListener('click', function () {
                var code = t.getAttribute('data-code');
                if (!code) return;
                fxFrom = 'VND'; fxTo = code;
                fromSel.value = fxFrom; toSel.value = fxTo;
                setFlagImgs();
                recalc();
                amtIn.focus();
            });
        });
    }

    function renderOptions() {
        var build = function (sel, keep) {
            var cur = sel.value || keep;
            var html = '';
            for (var i = 0; i < CURR.length; i++) {
                var c = CURR[i];
                html += '<option value="' + c.code + '">' + c.code + ' — ' + esc(c.name) + '</option>';
            }
            sel.innerHTML = html;
            if (cur && CURR.some(function (c) { return c.code === cur; })) sel.value = cur;
        };
        build(fromSel, fxFrom);
        build(toSel, fxTo);
        if (fromSel.value === toSel.value) { toSel.value = (fxFrom === 'VND' ? 'USD' : 'VND'); fxTo = toSel.value; }
        fxFrom = fromSel.value; fxTo = toSel.value;
        setFlagImgs();
    }

    // ---------- Phí ----------
    function renderFeeChips() {
        var html = '';
        for (var i = 0; i < FEES.length; i++) {
            var val = FEES[i];
            html += '<button type="button" class="fx-chip" data-fee="' + val + '">' + (val === 0 ? '0%' : val + '%') + '</button>';
        }
        feeChipsEl.innerHTML = html;
        Array.prototype.forEach.call(feeChipsEl.querySelectorAll('.fx-chip'), function (c) {
            c.addEventListener('click', function () {
                feeState.pct = parseFloat(c.getAttribute('data-fee'));
                feePctIn.value = String(feeState.pct);
                paintChips();
                recalc();
            });
        });
        paintChips();
    }
    function paintChips() {
        var target = String(feeState.pct);
        Array.prototype.forEach.call(feeChipsEl.querySelectorAll('.fx-chip'), function (c) {
            c.classList.toggle('on', String(c.getAttribute('data-fee')) === target);
        });
    }

    function rateLabel(from, to) {
        var vf = vndOf(from), vt = vndOf(to);
        if (vf == null || vt == null) return null;
        if (from === 'VND') return '1 ' + to + ' = ' + fmt(vt, 'VND') + ' VND';
        if (to === 'VND') return '1 ' + from + ' = ' + fmt(vf, 'VND') + ' VND';
        var r = vf / vt;
        return '1 ' + from + ' = ' + fmt(r, to) + ' ' + to;
    }

    // ---------- Tính toán ----------
    function recalc() {
        fxFrom = fromSel.value;
        fxTo = toSel.value;
        if (fxFrom === fxTo) {
            outIn.value = '';
            resEl.innerHTML = '<div class="fx-res-err">Hãy chọn 2 loại tiền khác nhau để hoán đổi.</div>';
            return;
        }
        var a = parseMoney(amtIn.value);
        amount = a;
        amtIn.value = moneyFmt(a);
        if (!isFinite(a) || a <= 0) {
            outIn.value = '';
            resEl.innerHTML = '<div class="fx-res-err">Vui lòng nhập số tiền hợp lệ (lớn hơn 0) để chuyển đổi.</div>';
            return;
        }
        var vf = vndOf(fxFrom), vt = vndOf(fxTo);
        if (vf == null || vt == null) {
            outIn.value = '';
            resEl.innerHTML = '<div class="fx-res-err">Dữ liệu tỷ giá đang thiếu cho cặp này — vui lòng thử lại sau.</div>';
            return;
        }
        var pct = 0;
        if (feeState.on) {
            var p = parsePct(feePctIn.value);
            pct = (isFinite(p) && p >= 0 && p <= 100) ? p : 0;
            feePctIn.value = String(pct);
        }
        var fee = a * pct / 100;
        var net = a - fee;
        var rate = vf / vt; // 1 from = rate to
        var result = net * rate;
        outIn.value = fmt(result, fxTo);

        var rows = [];
        rows.push(['Số tiền ban đầu', fmt(a, fxFrom) + ' ' + fxFrom]);
        if (feeState.on) {
            rows.push(['Phí (' + pct + '%)', '− ' + fmt(fee, fxFrom) + ' ' + fxFrom]);
            rows.push(['Số tiền sau phí', fmt(net, fxFrom) + ' ' + fxFrom]);
        }
        var rl = rateLabel(fxFrom, fxTo);
        if (rl) rows.push(['Tỷ giá', rl]);
        rows.push(['Số tiền nhận được', fmt(result, fxTo) + ' ' + fxTo]);
        var note = 'Tỷ giá: ' + (META.source || '') + (feeState.on ? ' · Phí được trừ trên số tiền nguồn trước khi quy đổi.' : '') + ' · ' + (META.updated_at ? 'cập nhật ' + fmtIso(META.updated_at) : '');
        if (META.stale) note = '<span style="color:#c98a16;">Dữ liệu đang hiển thị là bản cũ (nguồn tạm lỗi), cập nhật: ' + fmtIso(META.updated_at) + '.</span>';
        resEl.innerHTML =
            '<div class="fx-summary">' +
                '<div class="fx-sum-main"><span class="fx-sum-label">Số tiền nhận được</span>' +
                '<span class="fx-sum-value">' + fmt(result, fxTo) + ' ' + fxTo + '</span></div>' +
                '<div class="fx-break">' + rows.map(function (r) {
                    return '<div class="fx-brow"><span class="fx-bk">' + r[0] + '</span><span class="fx-bv">' + r[1] + '</span></div>';
                }).join('') + '</div>' +
                '<div class="fx-note">' + note + '</div>' +
            '</div>';
    }

    function fmtIso(iso) {
        try {
            var d = new Date(iso);
            if (isNaN(d.getTime())) return '';
            var p = function (x) { return (x < 10 ? '0' : '') + x; };
            return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear() + ' · ' + p(d.getHours()) + ':' + p(d.getMinutes());
        } catch (e) { return ''; }
    }

    // ---------- Tải dữ liệu ----------
    function setPill() {
        if (!pillEl) return; // khi nhúng vào trang Tiện ích không có pill riêng
        if (!META.updated_at) { pillEl.innerHTML = '<i class="far fa-clock"></i> Cập nhật lần cuối: <b>…</b>'; return; }
        var txt = '<i class="far fa-clock"></i> Cập nhật lần cuối: <b>' + fmtIso(META.updated_at) + '</b>';
        if (META.stale) txt += ' <i class="fas fa-triangle-exclamation"></i> (bản cũ do nguồn tạm lỗi)';
        pillEl.innerHTML = txt;
    }

    function load(refresh, silent) {
        var q = refresh ? '?refresh=true' : '';
        return fetch('/api/exchange/rates' + q, { cache: 'no-store' })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (e) { throw new Error((e && e.detail) || 'Lỗi tải tỷ giá.'); });
                return r.json();
            })
            .then(function (d) {
                CURR = d.currencies || [];
                VND = {};
                DIG = {};
                for (var i = 0; i < CURR.length; i++) {
                    var c = CURR[i];
                    VND[c.code] = c.vnd_rate != null ? Number(c.vnd_rate) : null;
                    DIG[c.code] = c.digits != null ? c.digits : 2;
                }
                META.updated_at = d.updated_at || d.fetched_at || '';
                META.source = d.source || '';
                META.stale = !!d.stale;
                if (ratesMsg) ratesMsg.innerHTML = '';
                renderOptions();
                renderGrid();
                renderFeeChips();
                setPill();
                recalc();
            })
            .catch(function (err) {
                ratesMsg.innerHTML = '<div class="fx-msg err">Dữ liệu tỷ giá tạm thời không khả dụng (nguồn: ' + esc(META.source || '—') + '). Không hiển thị tỷ giá giả.<br/>' +
                    '<button type="button" class="fx-retry" id="fxRetry"><i class="fas fa-sync-alt"></i> Thử lại</button></div>';
                var rb = document.getElementById('fxRetry');
                if (rb) rb.addEventListener('click', function () { load(true, false); });
                gridEl.innerHTML = '';
                if (!silent) resEl.innerHTML = '<div class="fx-res-err">' + esc(err && err.message || 'Lỗi') + '</div>';
            });
    }

    // ---------- Bind sự kiện ----------
    function bind() {
        amtIn.addEventListener('input', function () {
            var n = parseMoney(amtIn.value);
            amtIn.value = moneyFmt(n);
            recalc();
        });
        fromSel.addEventListener('change', function () {
            fxFrom = fromSel.value;
            if (fromSel.value === toSel.value) { toSel.value = fxFrom === 'VND' ? 'USD' : 'VND'; }
            fxTo = toSel.value;
            setFlagImgs();
            recalc();
        });
        toSel.addEventListener('change', function () {
            fxTo = toSel.value;
            if (fromSel.value === toSel.value) { fromSel.value = fxTo === 'VND' ? 'USD' : 'VND'; }
            fxFrom = fromSel.value;
            setFlagImgs();
            recalc();
        });
        swapBtn.addEventListener('click', function () {
            var oldFrom = fxFrom, oldTo = fxTo;
            var prevRes = parseMoney(outIn.value);
            fxFrom = oldTo; fxTo = oldFrom;
            fromSel.value = fxFrom; toSel.value = fxTo;
            if (isFinite(prevRes) && prevRes > 0) { amtIn.value = moneyFmt(prevRes); amount = prevRes; }
            else { amtIn.value = ''; amount = NaN; }
            setFlagImgs();
            recalc();
        });
        feeOn.addEventListener('change', function () {
            feeState.on = feeOn.checked;
            feeCtrl.style.display = feeState.on ? 'flex' : 'none';
            recalc();
        });
        feePctIn.addEventListener('input', function () {
            var p = parsePct(feePctIn.value);
            feeState.pct = isFinite(p) ? p : feeState.pct;
            paintChips();
            recalc();
        });
        feePctIn.addEventListener('blur', function () {
            if (!isFinite(parsePct(feePctIn.value))) feePctIn.value = String(feeState.pct);
        });
        if (refreshBtn) refreshBtn.addEventListener('click', function () {
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang cập nhật…';
            load(true, false).finally(function () {
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = '<i class="fas fa-sync-alt"></i> Làm mới';
            });
        });
    }

    // ---------- Embed / theme / chiều cao ----------
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
    function notifyHeight() {
        try {
            var el = document.querySelector('.fx-page') || document.body;
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

    // ---------- Khởi tạo ----------
    function init() {
        amtIn.value = '10.000.000'; // ví dụ mặc định
        bind();
        load(false, false);
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
        obs.observe(document.querySelector('.fx-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
    notifyHeight();
})();
