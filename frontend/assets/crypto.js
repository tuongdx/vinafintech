/* TuniX — Crypto (trang crypto.html)
   Dữ liệu Binance qua backend /api/crypto/* — frontend không gọi Binance trực tiếp.
   Cập nhật định kỳ bằng polling (15s) — không cần reload trang. */
(function () {
    'use strict';

    // Khung nến của biểu đồ chi tiết: NẾN NGÀY (1d) — 365 nến gần nhất; lịch sử cũ hơn được TẢI THÊM
    // khi lăn chuột để zoom rồi kéo về trái (đã bỏ dãy nút chọn kỳ hạn 1H/4H/1D/1W/1M/3M/1Y).
    var CHART_CFG = { interval: '1d', limit: 365 };
    var POLL_MS = 15000;
    // Danh sách crypto: hiện PAGE_FIRST mã trước, cuộn tới đáy thì nạp thêm PAGE_STEP mã/lần tới hết.
    var PAGE_FIRST = 20;
    var PAGE_STEP = 10;
    var PRELOAD_PX = 220;     // cách đáy ≤ 220px là nạp thêm
    var AUTO_MAX_STEPS = 20;  // chặn vòng lặp khi khung nhìn quá cao (mỗi bước +10 mã)

    var state = {
        items: [],
        quote: 'USDT',
        view: 'list',   // list | detail
        filter: 'top',
        sort: 'rank',
        q: '',
        sym: null,
        pendingSym: symbolFromUrl(),   // mã cần mở sẵn chi tiết (điều hướng từ trang khác)
        loading: false,
        visible: PAGE_FIRST,   // số dòng đang vẽ (cuộn tới đáy → +PAGE_STEP)
        rowsTotal: 0,          // tổng số dòng sau khi lọc
        shownCount: 0,         // số dòng vừa vẽ (do renderList ghi lại)
        rowList: [],           // danh sách đã lọc (để chèn thêm phần mới)
        source: 'binance',     // 'binance' | 'bybit' — sàn đang cung cấp dữ liệu giá
        asOf: null             // mốc cập nhật dữ liệu thị trường (ISO)
    };

    /* Trang khác (vd Trung tâm cảnh báo) điều hướng tới `crypto.html?symbol=BTC`
       → mở sẵn phần chi tiết của coin đó sau khi dữ liệu thị trường tải xong. */
    function symbolFromUrl() {
        try {
            var p = new URLSearchParams(window.location.search);
            var s = (p.get('symbol') || p.get('coin') || '').trim().toUpperCase();
            return s || null;
        } catch (e) { return null; }
    }
    function clearSymParam() {
        try {
            var u = new URL(window.location.href);
            if (!u.searchParams.has('symbol')) return;
            u.searchParams.delete('symbol');
            window.history.replaceState({}, '', u.pathname + (u.search || ''));
        } catch (e) {}
    }
    /* URL ĐẸP theo coin đang xem: /crypto/{MÃ} (null = về danh sách /crypto).
       - Nhúng trong shell → báo parent để shell đổi URL trên thanh địa chỉ.
       - Mở trực tiếp (không có parent) → tự replaceState. */
    function syncUrlForSymbol(sym) {
        var code = sym ? String(sym).trim().toUpperCase() : '';
        var path = code ? '/crypto/' + encodeURIComponent(code) : '/crypto';
        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'tunix-crypto-symbol', symbol: code || null }, '*');
            } else if (window.location.pathname !== path) {
                window.history.replaceState({}, '', path);
            }
        } catch (e) { /* ignore */ }
    }

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

    /* 1 dòng của bảng danh sách */
    function rowHtml(x) {
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
            '</tr>';
    }

    function renderList(noAuto) {
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
        $('crBody').innerHTML = showRows.map(rowHtml).join('');
        state.rowList = allRows;          // giữ danh sách đã lọc để NẠP THÊM chỉ chèn phần mới
        state.rowsTotal = allRows.length;
        state.shownCount = showRows.length;
        var wrap = $('crMoreWrap');
        if (wrap) {
            // Nhãn nút cố định "Xem thêm" (KHÔNG kèm số mã còn lại) — markup ở crypto.html #crMoreBtn.
            wrap.style.display = (allRows.length - showRows.length) > 0 ? '' : 'none';
        }
        paintShown();
        if (!noAuto) requestAnimationFrame(function () { autoFill(0); });
    }

    function paintShown() {
        var shown = $('crShown');
        if (shown) shown.innerHTML = 'hiển thị <b>' + (state.shownCount || 0) + '</b>/' + (state.rowList || []).length + ' mã';
    }

    /* Nạp thêm: CHỈ CHÈN các dòng mới vào cuối tbody (không dựng lại cả bảng) —
       nạp tới 698 dòng mà mỗi bước lại innerHTML toàn bộ thì rất nặng.
       Trả về true nếu có chèn thêm dòng. */
    function appendMore() {
        var list = state.rowList || [];
        var start = state.shownCount || 0;
        var end = Math.min(state.visible, list.length);
        if (start >= end) return false;
        $('crBody').insertAdjacentHTML('beforeend', list.slice(start, end).map(rowHtml).join(''));
        state.shownCount = end;
        var wrap = $('crMoreWrap');
        if (wrap) wrap.style.display = (list.length - end) > 0 ? '' : 'none';
        paintShown();
        return true;
    }

    /* ===== Nạp thêm khi cuộn (infinite scroll) =====
       Trang này được nhúng trong iframe mà shell kéo CAO BẰNG NỘI DUNG
       (index.html: 'tunix-embed-height') ⇒ iframe KHÔNG tự cuộn, thanh cuộn nằm ở TRANG CHA.
       Vì vậy KHÔNG thể dùng window.innerHeight bên trong iframe (lúc đó nó = toàn bộ
       chiều cao nội dung ⇒ mọi thứ luôn “trong tầm nhìn” và sẽ nạp hết ngay lập tức),
       phải quy đổi vị trí sang khung nhìn của trang cha qua window.frameElement. */
    function viewportRange() {
        var fe = null;
        try { fe = (window.self !== window.top) ? window.frameElement : null; } catch (e) { fe = null; }
        if (fe) {
            try {
                var fr = fe.getBoundingClientRect();
                var vh = window.parent.innerHeight || 0;
                // gốc toạ độ = đầu tài liệu của CHÍNH iframe
                if (vh) return { top: -fr.top, bottom: vh - fr.top };
            } catch (e) { /* khác origin → dùng khung nhìn của chính mình */ }
        }
        return { top: 0, bottom: window.innerHeight || 0 };
    }
    /* iframe đang bị ẩn (frame cache/prewarm của shell) → không tự nạp thêm */
    function pageHidden() {
        try {
            if (document.hidden) return true;
            var fe = (window.self !== window.top) ? window.frameElement : null;
            if (!fe) return false;
            var fr = fe.getBoundingClientRect();
            if (fr.width <= 0 || fr.height <= 0) return true;                       // display:none
            var vh = (window.parent && window.parent.innerHeight) || 0;
            if (vh && (fr.bottom <= 0 || fr.top >= vh)) return true;                // ngoài khung nhìn cha
        } catch (e) { /* ignore */ }
        return false;
    }
    function sentinelReached() {
        var el = $('crMoreWrap');
        if (!el) return false;
        var v = viewportRange();
        return (el.getBoundingClientRect().top - v.top) <= PRELOAD_PX;
    }
    function autoFill(step) {
        step = step || 0;
        if (state.view !== 'list' || state.loading) return;
        if (state.visible >= state.rowsTotal) return;
        if (step >= AUTO_MAX_STEPS || pageHidden() || !sentinelReached()) return;
        state.visible = Math.min(state.visible + PAGE_STEP, state.rowsTotal);
        appendMore();   // chèn thêm 10 dòng rồi ĐO LẠI ở frame sau (DOM cao lên thì đáy mới dịch xuống)
        requestAnimationFrame(function () { autoFill(step + 1); });
    }
    function bindInfiniteScroll() {
        var onScroll = function () {
            if (state.view !== 'list') return;
            requestAnimationFrame(function () { autoFill(0); });
        };
        window.addEventListener('scroll', onScroll, { passive: true });
        document.addEventListener('scroll', onScroll, { passive: true, capture: true });
        try {
            if (window.self !== window.top && window.parent) {
                // thanh cuộn thật nằm ở trang cha (shell)
                window.parent.addEventListener('scroll', onScroll, { passive: true });
            }
        } catch (e) { /* khác origin → chỉ còn nút "Xem thêm" */ }
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

    /* Shell yêu cầu đổi view khi KHÔNG nạp lại trang (Back/Forward, deep-link khi iframe đã tải sẵn):
       { type:'tunix-crypto-goto', symbol: 'BTC' | null }  → mở chi tiết coin / về danh sách. */
    window.addEventListener('message', function (ev) {
        var d = ev.data;
        if (!d || d.type !== 'tunix-crypto-goto') return;
        var sym = String(d.symbol || '').trim().toUpperCase();
        if (!sym) { state.sym = null; showList(); return; }
        if (!findItem(sym)) { state.pendingSym = sym; return; }   // chờ danh sách thị trường tải xong
        openDetail(sym);
    });

    function showList() {
        state.view = 'list';
        var hero = $('crHero');
        if (hero) hero.style.display = '';          // danh sách: GIỮ khối hero xanh
        $('crListView').style.display = '';
        $('crDetailView').style.display = 'none';
        // Vẽ lại thân bảng từ dữ liệu đã có: khi vào chi tiết bằng deep-link (?symbol=) thì lần tải thị trường
        // ĐẦU TIÊN mở chi tiết luôn và KHÔNG vẽ danh sách ⇒ bảng còn nguyên dòng “Đang tải dữ liệu…”.
        if (state.items && state.items.length) renderList();
        // Rời khỏi chi tiết → bỏ vạch vị thế + badge (xoá vạch cũ để không “dính” sang coin khác).
        crPos.data = null;
        renderCryptoPosition();
        notifyParentHeight();
    }
    function showDetail(sym) {
        state.view = 'detail';
        state.sym = sym;
        var hero = $('crHero');
        if (hero) hero.style.display = 'none';      // chi tiết: BỎ khối hero xanh
        var crumb = $('crCrumbSym');
        if (crumb) crumb.textContent = String(sym || '').toUpperCase();   // breadcrumb: Crypto › {MÃ}
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
            state.source = d.source || 'binance';
            state.asOf = d.as_of || null;
            var upd = $('crUpdated');
            if (upd) upd.innerHTML = '<i class="far fa-clock"></i> Cập nhật: <b>' + fmtVnTime(d.as_of) + '</b>';
            state.loading = false;
            if (state.pendingSym) {
                // Điều hướng từ trang khác (?symbol=) — chỉ mở khi coin có trong danh sách thị trường.
                var pending = state.pendingSym;
                state.pendingSym = null;
                if (findItem(pending)) { openDetail(pending); return; }
            }
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

    // ================= Thông tin cơ bản + giới thiệu coin =================
    // TÊN đầy đủ + đoạn giới thiệu lấy từ backend (/api/crypto/coin/{MÃ}, nguồn phụ CoinGecko,
    // Binance chỉ có mã). Cache ở client 6h cho mỗi mã để không gọi lại khi mở lại / polling.
    var INTRO_TTL_MS = 6 * 3600 * 1000;
    var INTRO_CLAMP = 420;                                  // dài hơn ngưỡng này → có nút "Xem thêm"
    var INTRO_TRIES = 12;                                    // AI dịch ở luồng nền → hỏi lại tối đa 12 lần
    var INTRO_RETRY_MS = 5000;                               // mỗi 5s (tổng ~60s)
    var introCache = {};                                     // MÃ → {name, description, original, source, translating, tries, at}
    var introFetching = {};
    var introTimer = null;

    function introTitleHtml(it, sym, note) {
        // Chỉ còn nhãn CẢNH BÁO (khi phải hiển thị bản gốc) — đã bỏ nhãn "Dịch bởi AI".
        var badge = note ? '<span class="cr-intro-tag warn"><i class="fas fa-info-circle"></i> ' + note + '</span>' : '';
        return '<h3 class="cr-intro-title">Giới thiệu về ' + esc(it.name || sym) + badge + '</h3>';
    }

    function introHtml(sym) {
        var it = introCache[sym];
        if (!it) return '<div class="cr-intro-load"><i class="fas fa-spinner fa-spin"></i> Đang tải giới thiệu…</div>';
        if (it.description) {
            var long = it.description.length > INTRO_CLAMP;
            return introTitleHtml(it, sym) +
                '<p class="cr-intro-text' + (long ? ' clamp' : '') + '" id="crIntroText">' + esc(it.description) + '</p>' +
                (long ? '<button type="button" class="cr-intro-toggle" id="crIntroToggle">Xem thêm</button>' : '');
        }
        if (it.translating && (it.tries || 0) < INTRO_TRIES) {
            return '<div class="cr-intro-load"><i class="fas fa-spinner fa-spin"></i> Đang dịch giới thiệu sang tiếng Việt bằng AI…</div>';
        }
        if (it.original) {   // AI không dịch được (thiếu key/lỗi) → hiển thị bản gốc
            return introTitleHtml(it, sym, 'Bản gốc — chưa dịch được') +
                '<p class="cr-intro-text clamp" id="crIntroText">' + esc(it.original) + '</p>' +
                '<button type="button" class="cr-intro-toggle" id="crIntroToggle">Xem thêm</button>';
        }
        return '<div class="cr-intro-empty">Chưa có nội dung giới thiệu cho mã này.</div>';
    }

    function loadCoinIntro(sym, force) {
        sym = String(sym || '').toUpperCase();
        if (!sym) return;
        if (introTimer) { clearTimeout(introTimer); introTimer = null; }
        var c = introCache[sym];
        if (!force && c && !c.translating && (Date.now() - c.at) < INTRO_TTL_MS) {
            paintIntroBox(sym);   // đã có (kể cả bản rỗng) → chỉ vẽ lại khung của mã đang xem
            return;
        }
        if (introFetching[sym]) return;
        introFetching[sym] = true;
        api('/coin/' + encodeURIComponent(sym)).then(function (d) {
            d = d || {};
            var tries = ((c && c.tries) || 0) + 1;
            introCache[sym] = {
                name: d.name || sym,
                description: d.description || '',
                original: d.original || (c && c.original) || '',
                source: d.source || '',
                translating: !!d.translating,
                tries: tries,
                at: Date.now()
            };
            var it = findItem(sym);
            if (it && d.name && it.name !== d.name) {
                it.name = d.name;                     // danh sách dùng luôn tên đầy đủ ở lần vẽ sau
                var h2 = document.querySelector('#crDetailInfo .cr-dname-row h2');
                if (h2 && state.sym && String(state.sym).toUpperCase() === sym) h2.textContent = d.name;
            }
            paintIntroBox(sym);
            // AI đang dịch ở backend → hỏi lại sau vài giây cho tới khi có bản tiếng Việt.
            if (d.translating && tries < INTRO_TRIES && state.sym && String(state.sym).toUpperCase() === sym) {
                introTimer = setTimeout(function () { loadCoinIntro(sym, true); }, INTRO_RETRY_MS);
            }
        }).catch(function () {
            introCache[sym] = { name: sym, description: '', original: (c && c.original) || '',
                                source: '', translating: false, tries: (c && c.tries) || 0, at: Date.now() };
            paintIntroBox(sym);
        }).then(function () { introFetching[sym] = false; });
    }

    /* Vẽ lại CHỈ khung #crIntro (không dựng lại cả block để không mất trạng thái polling). */
    function paintIntroBox(sym) {
        if (!state.sym || String(state.sym).toUpperCase() !== sym) return;
        var box = $('crIntro');
        if (!box) return;
        box.innerHTML = introHtml(sym);
        notifyParentHeight();
    }

    function renderDetailInfo(item) {
        var el = $('crDetailInfo');
        if (!item) {
            el.innerHTML = '<div class="cr-msg err">Không lấy được dữ liệu cho mã này. Vui lòng thử lại sau.</div>';
            var nc = $('crNewsCard');
            if (nc) nc.style.display = 'none';   // lỗi dữ liệu → giấu khối tin tức (tránh hiện tin của coin trước)
            var dc = $('crDcaCard');
            if (dc) dc.style.display = 'none';
            notifyParentHeight();
            return;
        }
        var ncShow = $('crNewsCard');
        if (ncShow) ncShow.style.display = '';
        var dcShow = $('crDcaCard');
        if (dcShow) dcShow.style.display = '';
        var c = pctCls(item.change_pct);
        var quote = state.quote || 'USDT';
        var srcName = state.source === 'bybit' ? 'Bybit' : 'Binance';
        el.innerHTML =
            '<div class="cr-detail-name">' + coinIconHtml(item.symbol, true) +
            '<div class="cr-dname"><div class="cr-dname-row"><h2>' + esc(item.name) + '</h2>' +
            '<span class="code">' + esc(item.symbol) + ' / ' + esc(item.pair) + '</span>' +
            '<span class="rank-tag">#' + item.rank + ' · Theo thanh khoản 24h</span></div>' +
            '<div class="cr-dmeta">' +
            '<span class="cr-meta-i"><i class="fas fa-server"></i> Sàn: <b>' + esc(srcName) + '</b></span>' +
            '<span class="cr-meta-i"><i class="fas fa-coins"></i> Đơn vị giá: <b>' + esc(quote) + '</b></span>' +
            '<span class="cr-meta-i"><i class="far fa-clock"></i> Cập nhật: <b>' + fmtVnTime(state.asOf) + '</b></span>' +
            '</div></div></div>' +
            '<div class="cr-price-big ' + c + '">' + fmtPrice(item.price) + ' <small>' + esc(quote) + '</small></div>' +
            '<div class="cr-kpis">' +
            '<div class="cr-kpi"><div class="k-label">Giá hiện tại (' + esc(quote) + ')</div><div class="k-val">' + fmtPrice(item.price) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Thay đổi 24h</div><div class="k-val ' + c + '">' + pctTxt(item.change_pct) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Cao 24h</div><div class="k-val">' + fmtPrice(item.high) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Thấp 24h</div><div class="k-val">' + fmtPrice(item.low) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Volume 24h (USDT)</div><div class="k-val">' + fmtBig(item.volume) + '</div></div>' +
            '</div>' +
            '<div class="cr-intro" id="crIntro">' + introHtml(item.symbol) + '</div>';
        // Polling cập nhật giá → cập nhật lại khối "Giả định đầu tư" nếu đang xem cùng coin.
        if (crDca.sym === String(item.symbol || '').toUpperCase() && crDca.cache &&
            Number(item.price) > 0 && Number(item.price) !== crDca.lastPrice) {
            renderDca(Number(item.price));
        }
        notifyParentHeight();
    }

    // ================= Tin tức Crypto (trang chi tiết coin) =================
    // Lấy 1 lần danh sách bài chuyên mục Crypto (slug tien-ao) rồi lọc theo coin đang xem.
    // Coin chưa có bài riêng → hiển thị tin Crypto mới nhất (khối không bao giờ rỗng).
    var NEWS_CAT = 'tien-ao';
    var NEWS_LIMIT = 40;      // số bài lấy về từ chuyên mục Crypto
    var NEWS_SHOW = 6;        // số tin hiển thị ban đầu (sau đó cân lại theo chiều cao khối Giả định đầu tư)
    var NEWS_MAX = 30;        // trần số tin có thể hiển thị
    var NEWS_GAP = 8;         // gap giữa các tin (khớp .cr-news-list trong crypto.css)
    // all: danh sách chuyên mục Crypto (chỉ tải 1 lần) · rel: bài liên quan coin ở MỌI chuyên mục
    // (chỉ dùng khi chuyên mục Crypto rỗng) — relSym cho biết rel đang là của coin nào.
    // list: tin ĐÃ xếp thứ tự để hiển thị · numHint: ghi chú cố định theo coin · show: số tin đang hiển thị.
    var crNews = { all: null, loading: false, sym: '', name: '', rel: null, relSym: '', list: null, numHint: '', show: NEWS_SHOW };
    var crFitTimer = null;

    function fmtNewsTime(iso) {
        if (!iso) return '';
        var d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        return pad2(d.getDate()) + '/' + pad2(d.getMonth() + 1) + '/' + d.getFullYear() +
            ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    }

    function newsHay(n) {
        return ((n.title || '') + ' ' + (n.summary || '') + ' ' + (n.tags || '')).toUpperCase();
    }

    /* Khớp theo TỪ cho mã coin (BTC khớp "mua BTC" nhưng KHÔNG khớp "BTCX") +
       khớp tên coin (Bitcoin) — tránh "ETH" dính vào "Tether" hay tên khác. */
    function newsMatchCoin(n, sym, name) {
        var hay = newsHay(n);
        var s = String(sym || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
        if (s.length >= 2) {
            var re = new RegExp('(^|[^A-Z0-9])' + s + '([^A-Z0-9]|$)');
            if (re.test(hay)) return true;
        }
        var nm = String(name || '').toUpperCase().trim();
        if (nm.length >= 4 && hay.indexOf(nm) >= 0) return true;
        return false;
    }

    function newsItemHtml(n) {
        var img = n.image_url
            ? '<img class="crn-thumb" src="' + esc(n.image_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '<span class="crn-thumb crn-thumb-fb"><i class="fas fa-coins"></i></span>';
        var time = fmtNewsTime(n.published_at || n.created_at);
        return '<button type="button" class="crn-item" data-id="' + esc(n.id) + '" data-slug="' + esc(n.slug || '') + '">' +
            img +
            '<span class="crn-body"><span class="crn-title">' + esc(n.title || '') + '</span>' +
            '<span class="crn-meta">' + (time ? '<span><i class="far fa-clock"></i> ' + esc(time) + '</span>' : '') +
            (n.source ? '<span class="crn-src">' + esc(n.source) + '</span>' : '') + '</span></span>' +
            '</button>';
    }

    function openNewsArticle(id, slug) {
        if (!id) return;
        if (window.parent && window.parent !== window) {
            // Nhúng trong shell → để shell mở trang bài viết (giữ URL đẹp + thanh header).
            window.parent.postMessage({ type: 'tunix-open-tintuc', id: Number(id), slug: slug || '' }, '*');
        } else {
            window.location.href = slug ? ('/' + slug + '-' + id + '.html') : ('/tintuc?id=' + id);
        }
    }

    /* Vẽ `count` tin đầu tiên của crNews.list (hàm thấp nhất của khối tin tức). */
    function paintCryptoNews(count) {
        var el = $('crNewsList');
        if (!el || !crNews.list) return;
        var list = crNews.list.slice(0, Math.max(1, count));
        crNews.show = list.length;
        el.innerHTML = list.map(newsItemHtml).join('') +
            (crNews.numHint ? '<div class="crn-hint">' + crNews.numHint + '</div>' : '');
        Array.prototype.forEach.call(el.querySelectorAll('.crn-item'), function (b) {
            b.addEventListener('click', function () {
                openNewsArticle(b.getAttribute('data-id'), b.getAttribute('data-slug'));
            });
        });
        notifyParentHeight();
    }

    /* Cân số tin để ĐÁY khối Tin tức trùng ĐÁY khối Giả định đầu tư (2 khối cùng hàng, align-items:start).
       Các tin có chiều cao như nhau (tiêu đề kẹp 2 dòng) nên tính trực tiếp được:
       cao danh sách = số tin × (cao 1 tin + gap) − gap  ⇒  số tin = (chỗ trống + gap) / (cao 1 tin + gap).
       Phần dư sau khi làm tròn số tin được BÙ bằng cách giãn nhẹ gap (có trần để không xấu). */
    function fitNewsToDca() {
        var dca = $('crDcaCard'), news = $('crNewsCard'), list = $('crNewsList');
        if (!dca || !news || !list || !crNews.list || !crNews.list.length) return;
        if (dca.style.display === 'none' || news.style.display === 'none') return;
        // Chỉ cân khi 2 khối NẰM CẠNH NHAU. Màn hẹp (.cr-duo xếp 1 cột) thì 2 khối trên/dưới nhau
        // → việc cân chiều cao vô nghĩa, trả về số tin mặc định.
        var sideBySide = Math.abs(dca.getBoundingClientRect().top - news.getBoundingClientRect().top) < 2;
        if (!sideBySide) {
            list.style.gap = '';
            if (crNews.show !== NEWS_SHOW) paintCryptoNews(NEWS_SHOW);
            return;
        }
        list.style.gap = '';                                   // đo với gap gốc
        var rows = list.querySelectorAll('.crn-item');
        if (!rows.length) return;
        var i, listH = 0;
        for (i = 0; i < rows.length; i++) listH += rows[i].offsetHeight;
        listH += NEWS_GAP * (rows.length - 1);
        var hint = list.querySelector('.crn-hint');
        if (hint) listH += hint.offsetHeight;               // ghi chú luôn có/không theo coin, không theo số tin
        var per = (listH + NEWS_GAP) / rows.length;         // chiều cao 1 tin + gap
        var chromeH = news.offsetHeight - listH;            // header + padding + viền của card
        var want = Math.floor((dca.offsetHeight - chromeH + NEWS_GAP) / per);
        want = Math.max(1, Math.min(want, crNews.list.length, NEWS_MAX));
        if (want !== crNews.show) paintCryptoNews(want);
        // Bù phần dư còn lại (do không thể thêm nửa tin) bằng cách giãn khoảng cách giữa các tin.
        var n = crNews.show;
        var extra = dca.offsetHeight - news.offsetHeight;
        if (n > 1 && extra > 1) {
            var itemH = (news.offsetHeight - chromeH - NEWS_GAP * (n - 1)) / n;
            var g2 = NEWS_GAP + extra / (n - 1);
            g2 = Math.min(g2, NEWS_GAP + 18, Math.max(NEWS_GAP, itemH * 0.6));
            list.style.gap = Math.max(NEWS_GAP, g2).toFixed(1) + 'px';
            notifyParentHeight();
        }
    }

    function renderCryptoNews() {
        var el = $('crNewsList');
        if (!el) return;
        var sym = crNews.sym;

        var all = crNews.all || [];
        var rel = all.length ? all.filter(function (n) { return newsMatchCoin(n, sym, crNews.name); }) : [];
        var list = null, hint = '';

        if (all.length) {
            // Ưu tiên bài đúng coin lên đầu, sau đó lấp đầy bằng các bài Crypto khác (giữ ĐỦ danh sách
            // để fitNewsToDca() hiển thị thêm cho tới khi đáy 2 khối bằng nhau).
            var others = all.filter(function (n) { return rel.indexOf(n) === -1; });
            list = rel.concat(others).slice(0, NEWS_MAX);
            if (!rel.length) {
                hint = 'Chưa có bài viết riêng cho <b>' + esc(sym) + '</b> — đang hiển thị tin Crypto mới nhất.';
            }
        } else if (crNews.rel && crNews.relSym === sym) {
            // Chuyên mục Crypto rỗng → dùng bài liên quan coin tìm ở mọi chuyên mục.
            list = crNews.rel.slice(0, NEWS_MAX);
            if (list.length) hint = 'Chuyên mục Crypto chưa có bài — đang hiển thị tin liên quan <b>' + esc(sym) + '</b>.';
        }

        if (list === null) {
            if (!all.length && !crNews.loading) {
                el.innerHTML = '<div class="cr-msg">Chưa có tin tức Crypto.</div>';
                notifyParentHeight();
            }
            return;
        }
        if (!list.length) {
            crNews.list = null;
            el.innerHTML = '<div class="cr-msg">Chưa có tin tức Crypto.</div>';
            notifyParentHeight();
            return;
        }
        crNews.list = list;
        crNews.numHint = hint;
        paintCryptoNews(crNews.show || NEWS_SHOW);
        fitNewsToDca();
    }

    /* Tìm bài LIÊN QUAN coin ở MỌI chuyên mục (server khớp mã/tên trong tiêu đề, tags, tóm tắt, nội dung).
       Dùng làm phương án dự phòng khi chuyên mục Crypto chưa có bài viết nào. */
    function fetchRelatedNews(sym) {
        var s = String(sym || '').toUpperCase();
        crNews.relSym = s;
        crNews.rel = null;
        if (!s) return Promise.resolve();
        var u = '/api/news/related?symbol=' + encodeURIComponent(s) + '&limit=' + NEWS_MAX;
        if (crNews.name && crNews.name.length >= 3) u += '&keyword=' + encodeURIComponent(crNews.name);
        return fetch(u, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (d) { crNews.rel = Array.isArray(d) ? d : []; })
            .catch(function () { crNews.rel = []; })
            .then(function () { renderCryptoNews(); });
    }

    function loadCryptoNews(sym, name) {
        var el = $('crNewsList');
        if (!el) return;
        crNews.sym = String(sym || '').toUpperCase();
        crNews.name = name || '';

        // Đã có danh sách chuyên mục → lọc lại theo coin ngay (không gọi API lại).
        if (crNews.all) {
            renderCryptoNews();
            if (!crNews.all.length && crNews.relSym !== crNews.sym) fetchRelatedNews(crNews.sym);
            return;
        }
        if (crNews.loading) return;   // đang tải lần đầu → .then() sẽ render với coin mới nhất
        crNews.loading = true;
        el.innerHTML = '<div class="cr-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải tin tức…</div>';
        fetch('/api/news?category=' + NEWS_CAT + '&limit=' + NEWS_LIMIT, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (d) {
                crNews.all = Array.isArray(d) ? d : [];
                // Chuyên mục Crypto chưa có bài → tìm tin liên quan coin ở mọi chuyên mục.
                if (crNews.all.length) return null;
                return fetchRelatedNews(crNews.sym);
            })
            .then(function () {
                crNews.loading = false;
                renderCryptoNews();
            })
            .catch(function () {
                crNews.loading = false;
                if (crNews.all && crNews.all.length) { renderCryptoNews(); return; }
                el.innerHTML = '<div class="cr-msg err">Không tải được tin tức Crypto.</div>';
                notifyParentHeight();
            });
    }

    // ================= Giả định đầu tư định kỳ (DCA) =================
    // Mỗi tháng nạp 5.000.000 VNĐ vào NGÀY 01, mua coin theo giá đóng cửa ngày đó,
    // trừ phí 0,1%/lần mua. Quy đổi VNĐ ⇄ USDT theo tỷ giá USD/VND tự động từ backend.
    var DCA_MONTHLY_VND = 5000000;
    var DCA_MONTHS = 12;
    var DCA_FEE_PCT = 0.1;   // % mỗi lần mua
    var crDca = { sym: '', cache: null, loading: false, fxAsOf: null, lastPrice: 0 };

    function fmtVnd(n) { return Math.round(Number(n) || 0).toLocaleString('vi-VN') + ' đ'; }
    /* Số tiền cho ô KPI: KHÔNG kèm đơn vị (đơn vị đưa lên nhãn để số gọn, đủ 1 hàng).
       Ô KPI rất hẹp: nếu số quá dài (≥ 1 tỷ ⇒ 13+ ký tự) thì rút gọn sang "tỷ" để không xuống dòng. */
    function fmtKpiVnd(n) {
        var v = Math.round(Number(n) || 0);
        if (Math.abs(v) >= 1e9) return (v / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + 'tỷ';
        return v.toLocaleString('vi-VN');
    }
    function fmtVndShort(v) {
        var n = Number(v) || 0, a = Math.abs(n);
        if (a >= 1e9) return (n / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + ' tỷ';
        if (a >= 1e6) return (n / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + ' tr';
        if (a >= 1e3) return (n / 1e3).toLocaleString('vi-VN', { maximumFractionDigits: 0 }) + 'K';
        return Math.round(n).toLocaleString('vi-VN');
    }
    function fmtCoin(qty) {
        var n = Number(qty) || 0;
        if (!n) return '0';
        var d = n >= 1000 ? 2 : n >= 1 ? 4 : 8;
        return n.toLocaleString('vi-VN', { maximumFractionDigits: d });
    }

    /* Tỷ giá USD/VND: ưu tiên /api/exchange/rate (cùng nguồn trang Tỷ giá),
       dự phòng /api/gold/world (fx.usd_vnd). Cả 2 đều public + có cache backend. */
    function fetchUsdVnd() {
        function viaGold() {
            return fetch('/api/gold/world', { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    var f = d && d.fx ? Number(d.fx.usd_vnd) : 0;
                    return f > 0 ? { fx: f, asOf: d.fx.as_of || null } : null;
                })
                .catch(function () { return null; });
        }
        return fetch('/api/exchange/rate?base=VND&quote=USD', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var f = d ? Number(d.rate) : 0;   // base=VND&quote=USD → rate = VND cho 1 USD
                return f > 0 ? { fx: f, asOf: d.updated_at || null } : viaGold();
            })
            .catch(function () { return viaGold(); });
    }

    // Mốc mua: ngày 01 (UTC) của 12 tháng gần nhất (tháng hiện tại tính ngược về 11 tháng).
    // `end` = mốc đầu tháng sau — dùng để biết tháng đó coin ĐÃ niêm yết hay chưa.
    function dcaBuyDates() {
        var now = new Date();
        var y = now.getUTCFullYear(), m = now.getUTCMonth();
        var out = [];
        for (var k = DCA_MONTHS - 1; k >= 0; k--) {
            out.push({ ts: Date.UTC(y, m - k, 1), end: Date.UTC(y, m - k + 1, 1) });
        }
        return out;
    }
    function firstBarAtOrAfter(bars, ts) {
        for (var i = 0; i < bars.length; i++) { if (bars[i].time >= ts) return bars[i]; }
        return null;
    }
    function dcaMonthLabel(ts) {
        var d = new Date(ts);
        return pad2(d.getUTCMonth() + 1) + '/' + String(d.getUTCFullYear()).slice(2);
    }

    function computeDca(bars, fx) {
        var rows = [], coins = 0, invested = 0, netUsdt = 0, missing = 0;
        dcaBuyDates().forEach(function (dt) {
            var bar = firstBarAtOrAfter(bars, dt.ts);
            // Nến tìm được phải NẰM TRONG đúng tháng đó; nếu không ⇒ coin chưa niêm yết ⇒ bỏ tháng này.
            if (!bar || bar.time >= dt.end || !(bar.close > 0)) { missing++; return; }
            var usdt = DCA_MONTHLY_VND / fx;                       // USDT mua được (trước phí)
            var net = usdt * (1 - DCA_FEE_PCT / 100);              // sau phí 0,1%
            var qty = net / bar.close;                             // số coin nhận được
            coins += qty; invested += DCA_MONTHLY_VND; netUsdt += net;
            rows.push({ time: bar.time, price: bar.close, qty: qty, coinsCum: coins, invested: invested });
        });
        return { rows: rows, coins: coins, investedVnd: invested, netUsdt: netUsdt, bars: bars, fx: fx, missing: missing };
    }

    // Chuỗi giá trị tích lũy theo từng mốc mua + điểm "Hôm nay" (theo giá hiện tại).
    function dcaSeries(d, currentPrice) {
        var series = d.rows.map(function (r) {
            return { label: dcaMonthLabel(r.time), value: r.coinsCum * r.price * d.fx, cost: r.invested };
        });
        if (d.rows.length) {
            var last = d.rows[d.rows.length - 1];
            series.push({ label: 'Hôm nay', value: last.coinsCum * currentPrice * d.fx, cost: last.invested });
        }
        return series;
    }

    function drawDcaChart(series) {
        var box = $('crDcaChart');
        if (!box || series.length < 2) return;
        var W = Math.max(320, box.clientWidth || 800);
        var H = box.clientHeight || 220;
        var padL = 6, padR = 62, padT = 16, padB = 24;
        var iw = W - padL - padR, ih = H - padT - padB;
        var vals = [];
        series.forEach(function (s) { vals.push(s.value); vals.push(s.cost); });
        var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
        var pad = (mx - mn) * 0.15 || Math.abs(mx) * 0.1 || 1;
        mn -= pad; mx += pad;
        if (mn < 0) mn = 0;
        if (mx <= mn) mx = mn + 1;
        var X = function (i) { return padL + (series.length === 1 ? iw / 2 : iw * i / (series.length - 1)); };
        var Y = function (v) { return padT + ih - (Number(v) - mn) / (mx - mn) * ih; };

        var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="Biểu đồ giá trị tích lũy">';
        out += '<defs><linearGradient id="crDcaFill" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#008080" stop-opacity="0.22"/><stop offset="100%" stop-color="#008080" stop-opacity="0"/></linearGradient></defs>';
        // lưới ngang + nhãn trục giá
        var grid = 'rgba(138,153,177,0.35)', tick = '#8a99b1';
        for (var g = 0; g <= 3; g++) {
            var gy = padT + ih * g / 3;
            var gv = mx - (mx - mn) * g / 3;
            out += '<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (padL + iw) + '" y2="' + gy.toFixed(1) + '" stroke="' + grid + '" stroke-width="1"/>';
            out += '<text x="' + (padL + iw + 6) + '" y="' + (gy + 3.5).toFixed(1) + '" fill="' + tick + '" font-size="10" font-weight="600">' + esc(fmtVndShort(gv)) + '</text>';
        }
        // đường vốn đã nạp (nét đứt) + vùng giá trị tích lũy
        var costPts = series.map(function (s, i) { return X(i).toFixed(1) + ',' + Y(s.cost).toFixed(1); }).join(' ');
        var valPts = series.map(function (s, i) { return X(i).toFixed(1) + ',' + Y(s.value).toFixed(1); }).join(' ');
        out += '<polygon points="' + valPts + ' ' + X(series.length - 1).toFixed(1) + ',' + (padT + ih) + ' ' + X(0).toFixed(1) + ',' + (padT + ih) + '" fill="url(#crDcaFill)"/>';
        out += '<polyline points="' + costPts + '" fill="none" stroke="#8a99b1" stroke-width="1.5" stroke-dasharray="5 4"/>';
        out += '<polyline points="' + valPts + '" fill="none" stroke="#008080" stroke-width="2.2" stroke-linejoin="round"/>';
        // điểm + tooltip native + nhãn trục tháng
        var lblStep = Math.max(1, Math.ceil(series.length / 5));
        series.forEach(function (s, i) {
            out += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(s.value).toFixed(1) + '" r="2.8" fill="#fff" stroke="#008080" stroke-width="1.8">' +
                '<title>' + esc(s.label) + ': ' + esc(fmtVnd(s.value)) + ' · Vốn ' + esc(fmtVnd(s.cost)) + '</title></circle>';
            if (i % lblStep === 0 || i === series.length - 1) {
                out += '<text x="' + X(i).toFixed(1) + '" y="' + (H - 8) + '" fill="' + tick + '" font-size="10" font-weight="600" text-anchor="middle">' + esc(s.label) + '</text>';
            }
        });
        out += '</svg>';
        box.innerHTML = out;
    }

    function currentCoinPrice(sym) {
        var it = findItem(sym);
        if (it && Number(it.price) > 0) return Number(it.price);
        var d = crDca.cache;
        return d && d.bars.length ? Number(d.bars[d.bars.length - 1].close) : 0;
    }

    function renderDca(currentPrice) {
        var body = $('crDcaBody');
        if (!body) return;
        var d = crDca.cache;
        if (!d || !d.rows.length) {
            body.innerHTML = '<div class="cr-msg">Chưa đủ dữ liệu giá (cần lịch sử 12 tháng) để tính toán.</div>';
            notifyParentHeight();
            return;
        }
        currentPrice = Number(currentPrice) || 0;
        if (!(currentPrice > 0)) currentPrice = d.bars[d.bars.length - 1].close;
        crDca.lastPrice = currentPrice;

        var curVnd = d.coins * currentPrice * d.fx;
        var pnl = curVnd - d.investedVnd;
        var pnlPct = d.investedVnd > 0 ? (pnl / d.investedVnd * 100) : 0;
        var avgUsdt = d.coins > 0 ? (d.netUsdt / d.coins) : 0;
        var cls = pctCls(pnl);
        var pnlTxt = (pnl > 0 ? '+' : (pnl < 0 ? '−' : '')) + fmtKpiVnd(Math.abs(pnl));

        var kpis =
            '<div class="cr-kpi"><div class="k-label">Vốn đã nạp (đ)</div><div class="k-val">' + fmtKpiVnd(d.investedVnd) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">' + esc(crDca.sym) + ' tích lũy</div><div class="k-val">' + fmtCoin(d.coins) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Giá vốn BQ (USDT)</div><div class="k-val">' + fmtPrice(avgUsdt) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Giá trị hiện tại (đ)</div><div class="k-val">' + fmtKpiVnd(curVnd) + '</div></div>' +
            '<div class="cr-kpi"><div class="k-label">Lãi / Lỗ (đ)</div><div class="k-val ' + cls + '">' + pnlTxt + '<small>' + pctTxt(pnlPct) + '</small></div></div>';

        var rowsHtml = d.rows.map(function (r) {
            var val = r.coinsCum * currentPrice * d.fx;
            var rp = val - r.invested;
            return '<tr>' +
                '<td>' + esc(dcaMonthLabel(r.time)) + '</td>' +
                '<td class="num">' + fmtPrice(r.price) + '</td>' +
                '<td class="num">' + fmtCoin(r.qty) + '</td>' +
                '<td class="num">' + fmtVnd(r.invested) + '</td>' +
                '<td class="num">' + fmtVnd(val) + '</td>' +
                '<td class="num ' + pctCls(rp) + '">' + (rp > 0 ? '+' : (rp < 0 ? '−' : '')) + fmtVnd(Math.abs(rp)) + '</td>' +
                '</tr>';
        }).join('');

        var fxTxt = 'tỷ giá 1 USD ≈ ' + Math.round(d.fx).toLocaleString('vi-VN') + ' VNĐ' +
            (crDca.fxAsOf ? ' (cập nhật ' + esc(fmtNewsTime(crDca.fxAsOf)) + ')' : '');
        var hint = 'Giả định mỗi tháng nạp <b>' + fmtVnd(DCA_MONTHLY_VND) + '</b> vào <b>ngày 01</b>, mua ' + esc(crDca.sym) +
            ' theo giá đóng cửa ngày đó, phí <b>' + String(DCA_FEE_PCT).replace('.', ',') + '%</b>/lần mua; ' + fxTxt +
            '. Chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.';
        // Coin mới niêm yết chưa đủ 12 tháng dữ liệu → nói rõ số tháng thực tế đã tính.
        if (d.rows.length < DCA_MONTHS) {
            hint += '<br>Lưu ý: coin mới niêm yết — chỉ có dữ liệu <b>' + d.rows.length + '/' + DCA_MONTHS +
                '</b> tháng gần nhất nên tổng vốn nạp là <b>' + fmtVnd(d.investedVnd) + '</b> thay vì ' +
                fmtVnd(DCA_MONTHLY_VND * DCA_MONTHS) + '.';
        }

        body.innerHTML =
            '<div class="cr-dca-kpis">' + kpis + '</div>' +
            '<div class="cr-dca-chart" id="crDcaChart"></div>' +
            '<div class="cr-legend"><span class="lg val"><i></i>Giá trị tích lũy</span><span class="lg cost"><i></i>Vốn đã nạp</span></div>' +
            '<div class="cr-dca-tblwrap"><table class="cr-dca-tbl"><thead><tr>' +
            '<th>Tháng</th><th class="num">Giá mua (USDT)</th><th class="num">Số coin</th>' +
            '<th class="num">Vốn</th><th class="num">Giá trị hiện tại</th><th class="num">Lãi/Lỗ</th>' +
            '</tr></thead><tbody>' + rowsHtml + '</tbody></table></div>' +
            '<div class="cr-dca-hint">' + hint + '</div>';
        drawDcaChart(dcaSeries(d, currentPrice));
        notifyParentHeight();
        fitNewsToDca();   // chiều cao khối vừa đổi → cân lại số tin ở khối Tin tức cho 2 đáy bằng nhau
    }

    function loadDca(sym) {
        var body = $('crDcaBody');
        if (!body) return;
        var S = String(sym || '').toUpperCase();
        var symEl = $('crDcaSym');
        if (symEl) symEl.textContent = S;
        if (crDca.sym === S && crDca.cache) { renderDca(currentCoinPrice(S)); return; }
        crDca.sym = S;
        crDca.cache = null;
        if (crDca.loading) return;   // đang tải → .then() sẽ render theo coin mới nhất
        crDca.loading = true;
        body.innerHTML = '<div class="cr-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tính toán…</div>';
        var it = findItem(S);
        var pair = it ? it.pair : (S + 'USDT');
        Promise.all([
            api('/klines', { symbol: pair, interval: '1d', limit: 400 }).catch(function () { return null; }),
            fetchUsdVnd()
        ]).then(function (res) {
            crDca.loading = false;
            var items = res[0] && Array.isArray(res[0].items) ? res[0].items : [];
            var bars = items.filter(function (b) { return b && Number(b.close) > 0; });
            var fxr = res[1];
            if (!bars.length || !fxr || !(fxr.fx > 0)) {
                body.innerHTML = '<div class="cr-msg err">Không lấy được dữ liệu giá / tỷ giá để tính toán.</div>';
                notifyParentHeight();
                return;
            }
            crDca.fxAsOf = fxr.asOf;
            crDca.cache = computeDca(bars, fxr.fx);
            renderDca(currentCoinPrice(crDca.sym));
        });
    }

    function openDetail(sym) {
        var item = findItem(sym);
        if (!item) { renderDetailInfo(null); showDetail(sym); syncUrlForSymbol(sym); return; }
        showDetail(sym);
        syncUrlForSymbol(item.symbol);   // URL → /crypto/{MÃ}
        $('crChartSym').textContent = item.symbol + '/' + (state.quote || 'USDT');
        // KHÔNG xoá #crChart ở đây: đây là khung của Lightweight Charts, xoá innerHTML sẽ phá chart
        // (canvas mất, instance vẫn tưởng còn) → chỉ đổi MÃ qua setData bên dưới.
        renderDetailInfo(item);
        loadCoinIntro(item.symbol);               // tên đầy đủ + giới thiệu (AI dịch sang tiếng Việt)
        loadDca(item.symbol);                     // giả định đầu tư định kỳ 5tr/tháng
        loadCryptoNews(item.symbol, item.name);   // tin tức liên quan coin này
        // Đổi coin → xoá vạch vị thế của coin TRƯỚC rồi nạp vị thế của coin này (nếu user đang nắm giữ).
        if (crPos.symbol !== String(item.symbol || '').toUpperCase()) { crPos.data = null; renderCryptoPosition(); }
        loadCryptoPosition(item.symbol);
        loadKlines();
    }

    function loadKlines() {
        var item = findItem(state.sym);
        if (!item) return;
        var p = CHART_CFG;
        var msgEl = $('crChartMsg');
        msgEl.innerHTML = '<div class="cr-msg load"><i class="fas fa-spinner fa-spin"></i> Đang tải biểu đồ…</div>';
        // Không xoá #crChart (khung Lightweight Charts) — chart tự cập nhật qua setData khi có dữ liệu mới.
        api('/klines', { symbol: item.pair, interval: p.interval, limit: p.limit }).then(function (d) {
            var arr = (d.items || []).filter(function (k) { return k && k.close > 0; });
            msgEl.innerHTML = '';
            if (arr.length < 2) {
                msgEl.innerHTML = '<div class="cr-msg err">Chưa đủ dữ liệu để vẽ biểu đồ.</div>';
                return;
            }
            // Bộ đệm lịch sử của (mã, kỳ) đang xem — lazy-load thêm khi kéo về quá khứ / zoom out.
            crHist = {
                key: item.symbol + '|' + p.interval, bars: arr, oldest: Number(arr[0].time),
                noMore: arr.length < p.limit, loading: false,
                userZoomOut: false,   // người dùng đã lăn chuột xuống (zoom out) → muốn xem thêm lịch sử
                interacted: false,    // đã thực sự chạm vào biểu đồ chưa (tránh nhận nhầm lúc khởi tạo)
                fill: false,          // lượt nạp này để LỘ DẦN lịch sử (canh lại khung nhìn) hay để giữ nguyên khung nhìn
                fillCount: 0, lastSpan: 0
            };
            drawChart(arr, item, true);   // true = canh lại khung nhìn (fitContent)
            notifyParentHeight();
        }).catch(function () {
            msgEl.innerHTML = '<div class="cr-msg err">Không lấy được dữ liệu biểu đồ từ Binance. Vui lòng thử lại sau.</div>';
            notifyParentHeight();
        });
    }

    // Tải THÊM lịch sử cũ hơn. Mỗi lần lấy tối đa 1000 nến trước mốc cũ nhất đang có.
    //  · fill = false (KÉO sang trái tới mép): giữ nguyên vùng đang xem (giống engine cổ phiếu).
    //  · fill = true  (ZOOM OUT quá khổ dữ liệu): sau khi nạp sẽ canh lại khung nhìn để lộ DẦN
    //    lịch sử cũ, rồi nạp tiếp cho tới khi hết dữ liệu.
    function loadMoreHistory() {
        if (!lwcChart || crHist.loading || crHist.noMore || !crHist.key) return;
        var item = findItem(state.sym);
        if (!item) return;
        var p = CHART_CFG;
        var fill = !!crHist.fill;
        crHist.fill = false;
        if (fill) crHist.fillCount++;
        crHist.loading = true;
        api('/klines', {
            symbol: item.pair, interval: p.interval, limit: 1000,
            end_time: Math.max(0, crHist.oldest - 1)
        }).then(function (d) {
            var older = (d.items || []).filter(function (k) {
                return k && k.close > 0 && Number(k.time) < crHist.oldest;
            });
            crHist.loading = false;
            if (!older.length) {
                crHist.noMore = true;
                drawChart(crHist.bars, item, false);   // vẽ lại lần cuối (đánh dấu đã hết lịch sử)
                notifyParentHeight();
                return;
            }
            var lr = null;
            if (!fill) {
                try { lr = lwcChart.timeScale().getVisibleLogicalRange(); } catch (e) { /* ignore */ }
            }
            crHist.bars = older.concat(crHist.bars);
            crHist.oldest = Number(crHist.bars[0].time);
            if (older.length < 1000) crHist.noMore = true;    // gói ngắn = đã hết dữ liệu lịch sử
            drawChart(crHist.bars, item, fill);              // fill → fitContent: lộ dần lịch sử vừa nạp
            if (!fill && lr) {
                try {
                    lwcChart.timeScale().setVisibleLogicalRange({ from: lr.from + older.length, to: lr.to + older.length });
                } catch (e) { /* ignore */ }
            } else if (fill) {
                maybeContinueFill();   // vẫn đang xem hết dữ liệu → nạp tiếp cho tới hết lịch sử
            }
            notifyParentHeight();
        }).catch(function () {
            crHist.loading = false;
        });
    }

    // Sau một lượt nạp ở chế độ "lộ dần" (do zoom out): nếu khung nhìn vẫn bao trọn TOÀN BỘ dữ liệu
    // đang có thì nạp tiếp lượt nữa → biểu đồ mở rộng dần về quá khứ cho tới khi hết lịch sử.
    function maybeContinueFill() {
        if (!lwcChart || crHist.loading || crHist.noMore || !crHist.key || !crHist.userZoomOut) return;
        if (crHist.fillCount >= 12) return;   // chốt an toàn: tối đa ~12.000 nến
        var lr = null;
        try { lr = lwcChart.timeScale().getVisibleLogicalRange(); } catch (e) { /* ignore */ }
        if (!lr) return;
        if ((lr.to - lr.from) >= crHist.bars.length - 1) { crHist.fill = true; loadMoreHistory(); }
    }

    // ===== Biểu đồ nến — DÙNG CHUNG thư viện Lightweight Charts v5.2.1 với trang chi tiết cổ phiếu =====
    // Nến + volume · lăn chuột để zoom · kéo để di chuyển · crosshair hiện OHLC ở dòng legend.
    var lwcChart = null, lwcCandle = null, lwcVol = null, lwcLastBar = null;
    // Bộ đệm lịch sử của (mã, kỳ) đang xem: { key, bars[], oldest, noMore, loading } — dùng cho lazy-load.
    var crHist = { key: null, bars: [], oldest: 0, noMore: true, loading: false, userZoomOut: false, interacted: false, fill: false, fillCount: 0, lastSpan: 0 };
    var crHistTimer = null;

    function lwcColors() {
        var dark = document.body.classList.contains('dark-mode');
        return {
            up: '#00a08a', down: '#d0314e',
            bg: dark ? '#0d1117' : '#ffffff',
            text: dark ? '#8b949e' : '#6b7a8f',
            grid: dark ? 'rgba(255,255,255,0.07)' : 'rgba(46,46,46,0.06)',
            border: dark ? '#30363d' : '#e2e8f0'
        };
    }
    // Giá crypto biến thiên rất rộng (BTC ~85.000 · XRP ~1,49) → chọn số chữ số thập phân theo giá.
    function pricePrecision(v) {
        var n = Math.abs(Number(v) || 0);
        if (n >= 1000) return 2;
        if (n >= 10) return 3;
        if (n >= 1) return 4;
        if (n >= 0.01) return 6;
        return 8;
    }
    // Tạo chart MỘT LẦN rồi tái sử dụng (đổi kỳ chỉ đổi data, không dựng lại → mượt hơn).
    function ensureChart() {
        if (lwcChart) return lwcChart;
        var el = $('crChart');
        if (!window.LightweightCharts || !el) return null;
        var c = lwcColors();
        el.innerHTML = '';
        var chart = LightweightCharts.createChart(el, {
            autoSize: true,
            layout: { background: { type: 'solid', color: c.bg }, textColor: c.text, fontSize: 11 },
            grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
            rightPriceScale: { borderColor: c.border },
            // minBarSpacing RẤT NHỎ: mặc định 0,5px/bến ⇒ trên bề rộng này chỉ thấy được ~1.700 nến,
            // không thể thu nhỏ tới mức xem hết lịch sử của coin lâu năm (BTC ~3.300 nến).
            timeScale: { borderColor: c.border, timeVisible: true, secondsVisible: false, rightOffset: 4, minBarSpacing: 0.02 },
            crosshair: { mode: 0 },
            // Lăn chuột = ZOOM (không pan ngang) → đúng yêu cầu "zoom in/out bằng chuột";
            // kéo chuột (pressedMouseMove) để di chuyển, như biểu đồ nến bên trang cổ phiếu.
            handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
            handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true }
        });
        lwcCandle = chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: c.up, downColor: c.down,
            borderUpColor: c.up, borderDownColor: c.down,
            wickUpColor: c.up, wickDownColor: c.down
        });
        lwcVol = chart.addSeries(LightweightCharts.HistogramSeries, {
            priceFormat: { type: 'volume' }, priceScaleId: ''
        });
        chart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.08, bottom: 0.26 } });
        // Rê chuột → hiện OHLC+KL của nến đang trỏ; ra ngoài → hiện nến cuối.
        chart.subscribeCrosshairMove(function (param) {
            var s = param && param.seriesData ? param.seriesData.get(lwcCandle) : null;
            var v = param && param.seriesData ? param.seriesData.get(lwcVol) : null;
            if (s) renderOhlcv({ time: param.time, open: s.open, high: s.high, low: s.low, close: s.close, volume: v ? v.value : null });
            else renderOhlcv(lwcLastBar);
        });
        lwcChart = chart;
        // Hook gỡ lỗi (như window.__tunixChartDebug của engine cổ phiếu). Dùng GETTER cho bộ đệm
        // lịch sử vì `crHist` được GÁN LẠI mỗi lần đổi mã/kỳ (không phải cùng một object).
        try {
            window.__tunixCryptoChartDebug = {
                chart: chart, candle: lwcCandle, vol: lwcVol,
                getHist: function () { return crHist; }
            };
        } catch (e) { /* ignore */ }
        // Người dùng THỰC SỰ tương tác với biểu đồ (chuột/kéo/chạm) — dùng để tránh nhận nhầm thay đổi
        // khung nhìn lúc khởi tạo/setData là ý định zoom out.
        var inChart = function (t) { return !!(t && t.closest && t.closest('#crChart')); };
        // Đặt ở document + capture: chạy TRƯỚC handler zoom của Lightweight Charts (gắn ở canvas con),
        // nếu không thì sự kiện đổi khung nhìn đầu tiên đã bắn ra khi cờ còn false → không nạp được gì.
        document.addEventListener('wheel', function (e) {
            if (!inChart(e.target)) return;
            crHist.interacted = true;
            if (e.deltaY > 0) crHist.userZoomOut = true;   // lăn xuống = zoom out = muốn xem thêm lịch sử
        }, { passive: true, capture: true });
        document.addEventListener('pointerdown', function (e) { if (inChart(e.target)) crHist.interacted = true; }, { passive: true, capture: true });
        document.addEventListener('touchstart', function (e) { if (inChart(e.target)) crHist.interacted = true; }, { passive: true, capture: true });
        // Nạp lịch sử cũ:
        //  (1) ZOOM OUT: khung nhìn rộng hơn/bằng toàn bộ dữ liệu đang có → nạp thêm rồi canh lại
        //      khung nhìn (lộ dần lịch sử), lặp cho tới khi hết dữ liệu.
        //  (2) KÉO sang trái tới sát mép dữ liệu → nạp thêm nhưng GIỮ NGUYÊN vùng đang xem.
        chart.timeScale().subscribeVisibleLogicalRangeChange(function (lr) {
            if (!lr || !crHist.key) return;
            var span = lr.to - lr.from, n = crHist.bars.length;
            // Khung nhìn RỘNG RA so với lần trước = đang zoom out (pinch / phím / trackpad) → đánh dấu.
            if (crHist.interacted && crHist.lastSpan && span > crHist.lastSpan * 1.02) crHist.userZoomOut = true;
            crHist.lastSpan = span;
            if (crHist.loading || crHist.noMore) return;
            if (crHist.userZoomOut && span >= n - 1) crHist.fill = true;
            // Chỉ nạp theo kiểu "cuộn vô hạn" khi người dùng ĐÃ tương tác — nếu không thì lúc mới mở
            // (khung nhìn đang ôm trọn 365 nến, from ≈ 0) sẽ tự nạp hết lịch sử ngay.
            else if (crHist.interacted && lr.from <= 8) crHist.fill = false;
            else return;
            clearTimeout(crHistTimer);
            crHistTimer = setTimeout(loadMoreHistory, 250);
        });
        return chart;
    }
    function applyChartTheme() {
        if (!lwcChart) return;
        var c = lwcColors();
        lwcChart.applyOptions({
            layout: { background: { type: 'solid', color: c.bg }, textColor: c.text },
            grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
            rightPriceScale: { borderColor: c.border },
            timeScale: { borderColor: c.border }
        });
        lwcCandle.applyOptions({ upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down, wickUpColor: c.up, wickDownColor: c.down });
    }
    // Thời gian (giờ VN) cho dòng legend — nhận cả unix giây và ms.
    function fmtAxisTime(t) {
        var ms = Number(t) || 0;
        if (ms > 1e12) ms = Math.floor(ms / 1000);
        if (!ms) return '';
        var vn = new Date(ms * 1000 + 7 * 3600 * 1000);
        var d = pad2(vn.getUTCDate()) + '/' + pad2(vn.getUTCMonth() + 1) + '/' + vn.getUTCFullYear();
        var intraday = !/[dwM]/.test(String(CHART_CFG.interval || ''));
        return intraday ? (d + ' ' + pad2(vn.getUTCHours()) + ':' + pad2(vn.getUTCMinutes())) : d;
    }
    function renderOhlcv(b) {
        var el = $('crOhlcv');
        if (!el) return;
        if (!b) { el.innerHTML = ''; return; }
        var up = Number(b.close) >= Number(b.open);
        var chg = Number(b.open) ? (Number(b.close) - Number(b.open)) / Number(b.open) * 100 : 0;
        var cls = up ? 'up' : 'down';
        el.innerHTML =
            '<span class="cr-oh-time">' + esc(fmtAxisTime(b.time)) + '</span>' +
            '<span>Mở <b>' + fmtPrice(b.open) + '</b></span>' +
            '<span>Cao <b>' + fmtPrice(b.high) + '</b></span>' +
            '<span>Thấp <b>' + fmtPrice(b.low) + '</b></span>' +
            '<span>Đóng <b class="' + cls + '">' + fmtPrice(b.close) + '</b> <i class="' + cls + '">' + (up ? '+' : '') + chg.toFixed(2) + '%</i></span>' +
            (b.volume != null ? '<span>KL <b>' + fmtBig(b.volume) + '</b></span>' : '');
    }

    /* ===== Vạch vị thế (đường giá vốn) — chỉ vẽ khi user CÓ vị thế coin này =====
       Nguồn: GET /api/assets/position/crypto/{MÃ} (bảng user_assets, label = MÃ coin,
       unit_price = giá vốn BÌNH QUÂN theo USDT/USDC — cùng đơn vị với giá trên biểu đồ).
       Chưa đăng nhập / không có vị thế → không vẽ gì (không có vạch, không có badge). */
    var crPos = { line: null, series: null, data: null, symbol: '', seq: 0, fetchedAt: 0 };
    var CR_POS_TTL = 60000;   // cache kết quả 60s/mã

    function crToken() {
        try { return localStorage.getItem('tunix_access_token') || ''; } catch (e) { return ''; }
    }
    function crPosFmt(n, d) {
        var v = Number(n) || 0;
        return v.toLocaleString('vi-VN', { maximumFractionDigits: d == null ? 2 : d });
    }
    // Nạp vị thế của coin (cache 60s). 401 / không có token → coi như không có vị thế.
    function loadCryptoPosition(sym) {
        var s = String(sym || '').trim().toUpperCase();
        if (!s) return;
        if (crPos.symbol === s && (Date.now() - crPos.fetchedAt) < CR_POS_TTL) { renderCryptoPosition(); return; }
        var token = crToken();
        // Chưa đăng nhập → không có vị thế. KHÔNG cache kết quả này (fetchedAt = 0) để nếu user đăng nhập
        // sau đó (vd ở tab khác) thì lần mở coin kế tiếp sẽ thử lại ngay, không phải chờ hết 60s.
        if (!token) { crPos.symbol = s; crPos.fetchedAt = 0; crPos.data = null; renderCryptoPosition(); return; }
        var seq = ++crPos.seq;
        fetch('/api/assets/position/crypto/' + encodeURIComponent(s), {
            headers: { Authorization: 'Bearer ' + token }, cache: 'no-store'
        }).then(function (res) {
            if (res.status === 401) return null;
            return res.ok ? res.json() : null;
        }).then(function (d) {
            if (seq !== crPos.seq) return;                  // đã đổi coin trong lúc chờ → bỏ kết quả cũ
            crPos.symbol = s; crPos.fetchedAt = Date.now();
            crPos.data = (d && d.has_position) ? d : null;
            renderCryptoPosition();
        }).catch(function () {
            if (seq !== crPos.seq) return;
            crPos.symbol = s; crPos.fetchedAt = Date.now(); crPos.data = null; renderCryptoPosition();
        });
    }
    // Vẽ/cập nhật vạch giá vốn + badge. Màu: xanh khi giá ≥ giá vốn, đỏ khi dưới giá vốn.
    function renderCryptoPosition() {
        var badge = $('crPosBadge');
        var d = crPos.data;
        var avg = d ? (Number(d.avg_price) || 0) : 0;
        if (!lwcCandle || !d || !(avg > 0)) {
            removeCryptoPosLine();
            if (badge) { badge.style.display = 'none'; badge.innerHTML = ''; badge.removeAttribute('title'); }
            return;
        }
        var last = lwcLastBar ? (Number(lwcLastBar.close) || 0) : 0;
        var up = !last || last >= avg;
        var color = up ? '#00a08a' : '#d0314e';
        // Series bị tạo lại (khởi tạo chart) → price line cũ không còn thuộc series này.
        if (crPos.line && crPos.series !== lwcCandle) removeCryptoPosLine();
        if (!crPos.line) {
            try {
                crPos.line = lwcCandle.createPriceLine({
                    price: avg, color: color, lineWidth: 1,
                    lineStyle: 3,      // 3 = nét đứt DÀI (LargeDashed) — phân biệt với vạch “giá mới nhất”
                    axisLabelVisible: true, title: 'Vị thế'
                });
                crPos.series = lwcCandle;
            } catch (e) { crPos.line = null; crPos.series = null; }
        } else {
            try { crPos.line.applyOptions({ price: avg, color: color, axisLabelVisible: true, title: 'Vị thế' }); } catch (e) { /* ignore */ }
        }
        if (!badge) return;
        var qty = Number(d.quantity) || 0;
        var cur = d.currency || 'USDT';
        var pct = (last > 0 && avg > 0) ? ((last - avg) / avg * 100) : null;
        var pctTxt = pct == null ? '--' : ((pct >= 0 ? '+' : '') + pct.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + '%');
        badge.innerHTML =
            '<span class="crp-dot" style="background:' + color + '"></span>' +
            '<span class="crp-label">Vị thế</span>' +
            '<span class="crp-qty">' + crPosFmt(qty, 8) + ' ' + esc(d.symbol || crPos.symbol) + '</span>' +
            '<span class="crp-sep">·</span>' +
            '<span>Giá vốn <b>' + crPosFmt(avg, 8) + '</b> ' + esc(cur) + '</span>' +
            '<span class="crp-sep">·</span>' +
            '<span class="crp-pct" style="color:' + color + '">' + pctTxt + '</span>';
        badge.title = 'Vị thế ' + (d.symbol || crPos.symbol) + ': ' + crPosFmt(qty, 8) + ' coin'
            + ' · giá vốn ' + crPosFmt(avg, 8) + ' ' + cur
            + ' · tổng đầu tư ' + crPosFmt(d.amount, 2) + ' ' + cur
            + (last > 0 ? ' · giá hiện tại ' + crPosFmt(last, 8) + ' USDT' : '');
        badge.style.display = 'inline-flex';
    }
    function removeCryptoPosLine() {
        var target = crPos.series || lwcCandle;
        if (target && crPos.line) { try { target.removePriceLine(crPos.line); } catch (e) { /* ignore */ } }
        crPos.line = null; crPos.series = null;
    }

    function drawChart(bars, item, fit) {
        var chart = ensureChart();
        var msgEl = $('crChartMsg');
        if (!chart) {
            msgEl.innerHTML = '<div class="cr-msg err">Không nạp được thư viện biểu đồ.</div>';
            return;
        }
        msgEl.innerHTML = '';
        var prec = pricePrecision(bars[bars.length - 1].close);
        lwcCandle.applyOptions({ priceFormat: { type: 'price', precision: prec, minMove: Math.pow(10, -prec) } });
        var candles = [], vols = [];
        for (var i = 0; i < bars.length; i++) {
            var b = bars[i];
            var t = Math.floor(Number(b.time) / 1000);   // Binance trả ms → Lightweight Charts cần GIÂY
            if (!isFinite(t) || t <= 0) continue;
            var o = Number(b.open), h = Number(b.high), l = Number(b.low), cl = Number(b.close);
            candles.push({ time: t, open: o, high: h, low: l, close: cl });
            vols.push({ time: t, value: Number(b.volume) || 0, color: cl >= o ? 'rgba(0,160,138,0.5)' : 'rgba(208,49,78,0.5)' });
        }
        if (candles.length < 2) { msgEl.innerHTML = '<div class="cr-msg err">Chưa đủ dữ liệu để vẽ biểu đồ.</div>'; return; }
        lwcCandle.setData(candles);
        lwcVol.setData(vols);
        if (fit) chart.timeScale().fitContent();    // lần đầu / đổi kỳ / đổi mã → canh lại khung nhìn
        var last = bars[bars.length - 1];
        lwcLastBar = {
            time: Math.floor(Number(last.time) / 1000),
            open: Number(last.open), high: Number(last.high), low: Number(last.low), close: Number(last.close),
            volume: Number(last.volume) || null
        };
        renderOhlcv(lwcLastBar);
        renderCryptoPosition();   // vẽ lại vạch vị thế (màu/% lãi-lỗ theo giá nến cuối vừa có)
    }

    // ================= Bind =================
    function bind() {
        $('crSearch').addEventListener('input', function () { state.q = $('crSearch').value; state.visible = PAGE_FIRST; renderList(); });
        Array.prototype.forEach.call(document.querySelectorAll('#crChips .cr-chip'), function (b) {
            b.addEventListener('click', function () {
                state.filter = b.getAttribute('data-filter');
                Array.prototype.forEach.call(document.querySelectorAll('#crChips .cr-chip'), function (x) { x.classList.toggle('on', x === b); });
                state.visible = PAGE_FIRST;
                syncSortSelect();
                renderList();
            });
        });
        $('crSort').addEventListener('change', function () { state.sort = $('crSort').value; state.visible = PAGE_FIRST; renderList(); });
        var moreBtn = $('crMoreBtn');
        if (moreBtn) moreBtn.addEventListener('click', function () {
            state.visible = Math.min(state.visible + PAGE_STEP, state.rowsTotal);
            if (!appendMore()) renderList();   // chỉ chèn 10 dòng; nếu DOM lệch thì dựng lại
        });
        bindInfiniteScroll();
        $('crBody').addEventListener('click', function (e) {
            var tr = e.target.closest('tr[data-symbol]');
            if (tr) openDetail(tr.getAttribute('data-symbol'));
        });
        // Nút "Xem thêm / Thu gọn" của khối Giới thiệu (nội dung được vẽ lại sau mỗi lần polling)
        var di = $('crDetailInfo');
        if (di) di.addEventListener('click', function (e) {
            var btn = e.target.closest('#crIntroToggle');
            if (!btn) return;
            var p = $('crIntroText');
            if (!p) return;
            var clamped = p.classList.toggle('clamp');
            btn.textContent = clamped ? 'Xem thêm' : 'Thu gọn';
            notifyParentHeight();
        });
        var crumbHome = $('crCrumbHome');
        if (crumbHome) crumbHome.addEventListener('click', function (e) {
            e.preventDefault();
            state.sym = null; clearSymParam(); syncUrlForSymbol(null); showList();
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && state.view === 'detail') { state.sym = null; clearSymParam(); syncUrlForSymbol(null); showList(); }
        });

        // "Xem tất cả" → trang Tin tức chuyên mục Crypto (/tien-ao)
        var newsAll = $('crNewsAll');
        if (newsAll) newsAll.addEventListener('click', function () {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'tunix-open-page', url: '/tien-ao' }, '*');
            } else {
                window.location.href = '/tien-ao';
            }
        });
    }

    // ===== Embed / chiều cao / theme =====
    if (window.self !== window.top) document.body.classList.add('embedded');
    function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); applyChartTheme(); }
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
    window.addEventListener('resize', function () {
        notifyParentHeight();
        clearTimeout(crFitTimer);
        crFitTimer = setTimeout(fitNewsToDca, 250);   // đổi bề rộng → chiều cao 2 khối đổi → cân lại số tin
    });
    if (window.ResizeObserver) {
        var obs = new ResizeObserver(notifyParentHeight);
        obs.observe(document.querySelector('.cr-page') || document.body);
    }
    applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
    notifyParentHeight();
})();
