/**
 * TuniX — Global Context Menu (toàn dự án)
 * =========================================
 * Menu chuột phải dùng chung cho MỌI trang frontend (thay menu mặc định trình duyệt).
 *
 * Đặc điểm:
 *  - Tự tiêm CSS (hỗ trợ light + dark-mode qua class `dark-mode` trên <body>),
 *    KHÔNG phụ thuộc styles.css → hoạt động trên mọi trang kể cả trang độc lập.
 *  - Không phá các menu chuột phải chuyên biệt đã có:
 *      + Nếu một thành phần khác đã xử lý sự kiện (preventDefault hoặc
 *        stopPropagation — ví dụ menu drawing trong StockDetail) thì bỏ qua.
 *      + Listener mở menu đặt ở pha bubble, listener đóng menu ở pha capture.
 *  - Đóng menu khi: click chuột trái bên ngoài, bấm Escape, cuộn trang,
 *    resize, mất focus.
 *  - Hỗ trợ bàn phím: ↑/↓ di chuyển, Enter kích hoạt, Home/End, Escape đóng.
 *  - Menu theo ngữ cảnh: liên kết, hình ảnh, ô nhập liệu, văn bản đang chọn,
 *    điều hướng trang, in, xem mã nguồn, chuyển giao diện sáng/tối.
 *  - API mở rộng cho từng trang:
 *      window.TuniXContextMenu.register(rule, factory)
 *        rule(target, event) -> boolean (có áp dụng không)
 *        factory(target, event) -> [{ label, icon, shortcut, action, disabled } | { divider: true }]
 *      window.TuniXContextMenu.open(items, x, y) / .close()
 *
 * Cách dùng: thêm trước </body> của mỗi trang:
 *   <script src="/static/context-menu.js"></script>
 */
(function () {
    'use strict';

    var STYLE_ID = 'tunix-ctx-style';

    /* ------------------------------------------------------------------
     * CSS — tự tiêm 1 lần (đủ mọi trang)
     * ------------------------------------------------------------------ */
    var CSS = [
        '#tunix-ctx-style{display:none}',
        '.tunix-ctx{position:fixed;z-index:2147483000;min-width:212px;max-width:300px;padding:6px;',
        '  background:#fff;border:1px solid #e2e8f0;border-radius:10px;',
        '  box-shadow:0 8px 30px rgba(15,23,42,.16),0 2px 8px rgba(15,23,42,.08);',
        '  font-family:\'Inter\',-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;',
        '  font-size:13px;line-height:1.35;color:#1f2937;box-sizing:border-box;',
        '  user-select:none;-webkit-user-select:none;',
        '  animation:tunix-ctx-in .12s ease-out;transform-origin:top left;}',
        '@keyframes tunix-ctx-in{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:scale(1)}}',
        '.tunix-ctx-item{display:flex;align-items:center;gap:10px;width:100%;padding:7px 10px;',
        '  border:0;background:transparent;color:inherit;border-radius:6px;cursor:pointer;',
        '  text-align:left;font:inherit;line-height:1.35;white-space:nowrap;}',
        '.tunix-ctx-item i{width:16px;text-align:center;font-size:13px;color:#64748b;flex:none;}',
        '.tunix-ctx-item span{overflow:hidden;text-overflow:ellipsis;}',
        '.tunix-ctx-item:hover,.tunix-ctx-item.is-active{background:#f0f4f8;color:#008080;}',
        '.tunix-ctx-item:hover i,.tunix-ctx-item.is-active i{color:#008080;}',
        '.tunix-ctx-item.is-disabled{opacity:.45;cursor:default;pointer-events:none;}',
        '.tunix-ctx-kbd{margin-left:auto;padding:0 5px;font-size:11px;color:#94a3b8;flex:none;',
        '  border:1px solid #e2e8f0;border-radius:4px;background:#f8fafc;}',
        '.tunix-ctx-sep{height:1px;background:#e5eaf0;margin:5px 6px;}',
        /* Dark mode */
        'body.dark-mode .tunix-ctx{background:#161b22;border-color:#30363d;color:#e6edf3;',
        '  box-shadow:0 8px 30px rgba(0,0,0,.5);}',
        'body.dark-mode .tunix-ctx-item i{color:#8b949e;}',
        'body.dark-mode .tunix-ctx-item:hover,body.dark-mode .tunix-ctx-item.is-active{background:#21262d;color:#58a6ff;}',
        'body.dark-mode .tunix-ctx-item:hover i,body.dark-mode .tunix-ctx-item.is-active i{color:#58a6ff;}',
        'body.dark-mode .tunix-ctx-kbd{color:#6e7681;border-color:#30363d;background:#21262d;}',
        'body.dark-mode .tunix-ctx-sep{background:#30363d;}'
    ].join('\n');

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var st = document.createElement('style');
        st.id = STYLE_ID;
        st.textContent = CSS;
        (document.head || document.documentElement).appendChild(st);
    }

    /* ------------------------------------------------------------------
     * Tiện ích
     * ------------------------------------------------------------------ */
    var NON_TEXT_TYPES = ['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range', 'color', 'image', 'hidden'];

    function isTextEditable(el) {
        if (!el) return false;
        if (el.isContentEditable) return true;
        var tag = (el.tagName || '').toUpperCase();
        if (tag === 'TEXTAREA') return true;
        if (tag === 'SELECT') return true;
        if (tag === 'INPUT') {
            return NON_TEXT_TYPES.indexOf(String(el.type || 'text').toLowerCase()) === -1;
        }
        return false;
    }

    function hasInputSelection(el) {
        try {
            if (el && el.selectionStart != null) return el.selectionStart !== el.selectionEnd;
            var s = window.getSelection();
            return !!s && s.toString().length > 0;
        } catch (err) { return false; }
    }

    function getSelectionText() {
        try { return (window.getSelection() || {}).toString() || ''; } catch (err) { return ''; }
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function runCmd(cmd) {
        try { document.execCommand(cmd); } catch (err) { /* ignore */ }
    }

    function fallbackCopy(text) {
        try {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none;';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        } catch (err) { /* ignore */ }
    }

    function copyText(text) {
        text = String(text == null ? '' : text);
        if (!text) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).catch(function () { fallbackCopy(text); });
        } else {
            fallbackCopy(text);
        }
    }

    function selectAllPage() {
        try {
            var r = document.createRange();
            r.selectNodeContents(document.body);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(r);
        } catch (err) { /* ignore */ }
    }

    function toggleTheme() {
        var dark = !document.body.classList.contains('dark-mode');
        document.body.classList.toggle('dark-mode', dark);
        try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch (err) { /* ignore */ }
        var mode = dark ? 'dark' : 'light';
        // Đồng bộ chéo khung: nếu đang ở iframe → báo shell áp dụng + phát lại cho mọi frame;
        // nếu ở shell → gửi tới các frame nhúng.
        try {
            window.postMessage({ type: 'tunix-set-theme', mode: mode }, '*');
            window.postMessage({ type: 'tunix-theme', mode: mode }, '*');
        } catch (err) { /* ignore */ }
        // Cập nhật icon nút theme hiện có (nếu trang có)
        var t = document.getElementById('themeToggle');
        if (t) t.className = dark ? 'fas fa-sun theme-toggle' : 'fas fa-moon theme-toggle';
        var tm = document.getElementById('themeToggleMini');
        if (tm) { var i = tm.querySelector('i'); if (i) i.className = dark ? 'fas fa-sun' : 'fas fa-moon'; }
    }

    /* Mở liên kết trong cửa sổ mới (popup kích thước cố định, căn giữa theo cửa sổ hiện tại) */
    function openNewWindow(url) {
        var w = 1180, h = 780;
        var left = Math.max(0, Math.round((window.screenX || 0) + ((window.outerWidth || w) - w) / 2));
        var top = Math.max(0, Math.round((window.screenY || 0) + 40));
        window.open(url, '_blank',
            'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top +
            ',resizable=yes,scrollbars=yes,menubar=yes,toolbar=yes,status=yes,noopener,noreferrer');
    }

    /* Mở liên kết ở dạng xem phân tách: cửa sổ mới chiếm 1 nửa màn hình, đặt cạnh
       cửa sổ hiện tại → chia đôi màn hình để xem song song. */
    function openSplitView(url) {
        var screenW = window.screen.width || Math.round((window.outerWidth || 1200) * 2);
        var screenH = window.screen.height || (window.outerHeight || 800);
        var w = Math.max(360, Math.round(screenW / 2));
        var h = Math.max(480, Math.round(screenH * 0.94));
        var left = Math.round((window.screenX || 0) + (window.outerWidth || w));
        left = Math.max(0, Math.min(screenW - w, left));
        var top = Math.round(window.screenY || 0);
        window.open(url, '_blank',
            'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top +
            ',resizable=yes,scrollbars=yes,status=yes,noopener,noreferrer');
    }

    /* "Quay lại trang trước" — tránh rời khỏi web: ưu tiên quay lại trang trước
       trong-app (shell), nếu không thì không rời site. */
    function goBackPage() {
        // Đang ở trang nhúng (iframe trong shell) → nhờ shell điều hướng về trang trước trong-app
        if (window.self !== window.top) {
            try { window.parent.postMessage({ type: 'tunix-go-back' }, '*'); } catch (e) { /* ignore */ }
            return;
        }
        // Shell hoặc trang có API in-app do shell cung cấp
        if (typeof window.tunixGoBackInApp === 'function') {
            window.tunixGoBackInApp();
            return;
        }
        // Trang độc lập (Login, register...): về trang trước cùng site nếu có;
        // nếu entry trước là ngoài site → về trang chủ thay vì rời web.
        try {
            var ref = document.referrer || '';
            if (ref && (new URL(ref)).origin === location.origin) { history.back(); return; }
        } catch (e) { /* ignore */ }
        window.location.href = (location.origin || '') + '/';
    }

    /* ------------------------------------------------------------------
     * Trạng thái menu
     * ------------------------------------------------------------------ */
    var menuEl = null;
    var items = [];
    var itemEls = [];
    var activeIndex = -1;
    var customRules = [];

    function setActive(i) {
        if (!menuEl) return;
        if (activeIndex >= 0 && itemEls[activeIndex]) itemEls[activeIndex].classList.remove('is-active');
        activeIndex = i;
        if (itemEls[i]) {
            itemEls[i].classList.add('is-active');
            try { itemEls[i].scrollIntoView({ block: 'nearest' }); } catch (err) { /* ignore */ }
        }
    }

    function renderItem(it) {
        if (it && it.divider) {
            var sep = document.createElement('div');
            sep.className = 'tunix-ctx-sep';
            return sep;
        }
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tunix-ctx-item' + (it.disabled ? ' is-disabled' : '');
        btn.innerHTML = '<i class="fa-solid ' + esc(it.icon || 'fa-circle') + '" aria-hidden="true"></i>' +
            '<span>' + esc(it.label) + '</span>' +
            (it.shortcut ? '<span class="tunix-ctx-kbd">' + esc(it.shortcut) + '</span>' : '');
        if (!it.disabled) {
            itemEls.push(btn);
            btn.addEventListener('click', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                var d = it;
                close();
                if (d && d.action) d.action();
            });
            btn.addEventListener('pointerenter', function () { setActive(itemEls.indexOf(btn)); });
        }
        return btn;
    }

    function placeMenu(x, y) {
        if (!menuEl) return;
        menuEl.style.visibility = 'hidden';
        menuEl.style.left = '0px';
        menuEl.style.top = '0px';
        var r = menuEl.getBoundingClientRect();
        var vw = document.documentElement.clientWidth;
        var vh = document.documentElement.clientHeight;
        var pad = 8;
        var left = x;
        var top = y;
        if (left + r.width + pad > vw) left = x - r.width;
        if (top + r.height + pad > vh) top = y - r.height;
        left = Math.max(pad, Math.min(left, vw - r.width - pad));
        top = Math.max(pad, Math.min(top, vh - r.height - pad));
        menuEl.style.left = left + 'px';
        menuEl.style.top = top + 'px';
        menuEl.style.visibility = 'visible';
    }

    function open(itemList, x, y) {
        close();
        ensureStyle();
        var el = document.createElement('div');
        el.className = 'tunix-ctx';
        el.setAttribute('role', 'menu');
        items = (itemList || []).filter(Boolean);
        itemEls = [];
        activeIndex = -1;
        for (var i = 0; i < items.length; i++) el.appendChild(renderItem(items[i]));
        document.body.appendChild(el);
        menuEl = el;
        placeMenu(x, y);
        // Focus để nhận phím (a11y), không cuộn trang
        try { el.setAttribute('tabindex', '-1'); el.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
    }

    function close() {
        if (menuEl) { try { menuEl.remove(); } catch (err) { /* ignore */ } menuEl = null; }
        items = [];
        itemEls = [];
        activeIndex = -1;
    }

    /* ------------------------------------------------------------------
     * Xây dựng danh sách mục theo ngữ cảnh
     * ------------------------------------------------------------------ */
    function buildItems(e) {
        var out = [];
        var t = e.target;
        var linkEl = t && t.closest ? t.closest('a[href]') : null;
        // Bỏ link dạng javascript:void(0) (nút hành động) — không mở được
        var linkHref = linkEl ? String(linkEl.getAttribute('href') || '') : '';
        var link = (/^\s*javascript:/i.test(linkHref) || !linkHref) ? null : linkEl;
        var img = t && t.closest ? t.closest('img') : null;
        var editable = isTextEditable(t);
        var selText = getSelectionText().trim();

        // 1) Mục mở rộng từ từng trang (custom rules) — ưu tiên đầu menu
        for (var c = 0; c < customRules.length; c++) {
            var rule = customRules[c];
            if (rule.rule(t, e)) {
                var extra = rule.factory(t, e) || [];
                if (extra.length) {
                    for (var j = 0; j < extra.length; j++) if (extra[j]) out.push(extra[j]);
                    out.push({ divider: true });
                }
            }
        }

        // 2) Liên kết
        if (link) {
            out.push({
                label: 'Mở liên kết trong tab mới', icon: 'fa-external-link-alt',
                action: function () { window.open(link.href, '_blank', 'noopener,noreferrer'); }
            });
            out.push({
                label: 'Mở liên kết trong cửa sổ mới', icon: 'fa-window-restore',
                action: function () { openNewWindow(link.href); }
            });
            out.push({
                label: 'Mở liên kết ở dạng xem phân tách', icon: 'fa-columns',
                action: function () { openSplitView(link.href); }
            });
            out.push({ divider: true });
            out.push({ label: 'Sao chép địa chỉ liên kết', icon: 'fa-link', action: function () { copyText(link.href); } });
            out.push({ divider: true });
        }

        // 3) Hình ảnh
        if (img) {
            out.push({
                label: 'Mở hình ảnh trong tab mới', icon: 'fa-image',
                action: function () { window.open(img.currentSrc || img.src, '_blank', 'noopener,noreferrer'); }
            });
            out.push({ divider: true });
        }

        // 4) Ô nhập liệu → menu soạn thảo
        if (editable) {
            var hasSel = hasInputSelection(t);
            out.push({ label: 'Cắt', icon: 'fa-scissors', shortcut: 'Ctrl+X', disabled: !hasSel, action: function () { runCmd('cut'); } });
            out.push({ label: 'Sao chép', icon: 'fa-copy', shortcut: 'Ctrl+C', disabled: !hasSel, action: function () { runCmd('copy'); } });
            out.push({ label: 'Dán', icon: 'fa-paste', shortcut: 'Ctrl+V', action: function () { runCmd('paste'); } });
            out.push({ label: 'Xóa', icon: 'fa-trash', shortcut: 'Del', disabled: !hasSel, action: function () { runCmd('delete'); } });
            out.push({ divider: true });
            out.push({
                label: 'Chọn tất cả', icon: 'fa-edit', shortcut: 'Ctrl+A',
                action: function () { try { t.focus(); t.select(); } catch (err) { /* ignore */ } }
            });
        } else if (selText) {
            // 5) Văn bản đang chọn (ngoài ô nhập)
            out.push({ label: 'Sao chép', icon: 'fa-copy', shortcut: 'Ctrl+C', action: function () { copyText(selText); } });
            out.push({ divider: true });
        }

        // 6) Điều hướng trang
        out.push({ label: 'Tải lại trang', icon: 'fa-sync', shortcut: 'F5', action: function () { location.reload(); } });
        out.push({
            label: 'Quay lại trang trước', icon: 'fa-arrow-left', shortcut: 'Alt+←',
            action: goBackPage
        });
        out.push({ label: 'Tiến tới trang sau', icon: 'fa-arrow-right', shortcut: 'Alt+→', action: function () { history.forward(); } });

        // 7) Chung
        out.push({ divider: true });
        if (!editable) out.push({ label: 'Chọn tất cả', icon: 'fa-edit', shortcut: 'Ctrl+A', action: selectAllPage });
        out.push({ label: 'In trang', icon: 'fa-print', shortcut: 'Ctrl+P', action: function () { window.print(); } });
        out.push({ label: 'Xem mã nguồn', icon: 'fa-code', action: function () { window.open('view-source:' + location.href, '_blank', 'noopener'); } });

        // 8) Giao diện
        out.push({ divider: true });
        var isDark = document.body.classList.contains('dark-mode');
        out.push({ label: isDark ? 'Chế độ sáng' : 'Chế độ tối', icon: isDark ? 'fa-sun' : 'fa-moon', action: toggleTheme });

        return out;
    }

    /* ------------------------------------------------------------------
     * Sự kiện toàn cục
     * ------------------------------------------------------------------ */
    // Pha capture: chỉ đóng menu cũ trước khi mọi handler khác chạy
    function onCtxCapture() { close(); }

    // Pha bubble: mở menu nếu chưa có thành phần khác xử lý
    function onContextMenu(e) {
        if (e.defaultPrevented) return; // thành phần khác đã xử lý (vd: menu drawing)
        e.preventDefault();
        var list = buildItems(e);
        if (!list.length) return;
        open(list, e.clientX, e.clientY);
    }

    function onPointerDown(e) {
        if (!menuEl) return;
        if (e.button === 2) { close(); return; } // chuột phải → đóng menu cũ
        if (e.target && menuEl.contains(e.target)) return; // click trong menu → giữ
        close();
    }

    function onKeyDown(e) {
        if (!menuEl || !itemEls.length) return;
        if (e.key === 'Escape') { e.preventDefault(); close(); return; }
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            var dir = e.key === 'ArrowDown' ? 1 : -1;
            var next = activeIndex + dir;
            if (next >= itemEls.length) next = 0;
            if (next < 0) next = itemEls.length - 1;
            setActive(next);
            return;
        }
        if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault();
            setActive(e.key === 'Home' ? 0 : itemEls.length - 1);
            return;
        }
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (activeIndex >= 0 && activeIndex < itemEls.length) itemEls[activeIndex].click();
        }
    }

    function onBlur() { close(); }

    /* ------------------------------------------------------------------
     * API công khai
     * ------------------------------------------------------------------ */
    function register(rule, factory) {
        if (typeof rule !== 'function') return;
        customRules.push({ rule: rule, factory: typeof factory === 'function' ? factory : function () { return []; } });
    }

    window.TuniXContextMenu = {
        open: open,
        close: close,
        register: register
    };

    /* ------------------------------------------------------------------
     * Khởi tạo
     * ------------------------------------------------------------------ */
    ensureStyle();
    document.addEventListener('contextmenu', onContextMenu, false);
    document.addEventListener('contextmenu', onCtxCapture, true);
    window.addEventListener('pointerdown', onPointerDown, true);
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('blur', onBlur);
    window.addEventListener('resize', onBlur);
    window.addEventListener('scroll', onBlur, true);
})();
