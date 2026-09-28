/**
 * TuniX — CUSTOM CONTEXT MENU (menu chuột phải dùng chung toàn site)
 * ==================================================================
 * ⚠️ MẶC ĐỊNH TẮT (user chốt 2026-09-28): website dùng MENU NGẦM ĐỊNH CỦA TRÌNH DUYỆT.
 * File này chỉ còn là thư viện tuỳ chọn — KHÔNG tự gắn listener khi nạp nữa.
 * Bật lại: đặt `window.TUNIX_CONTEXT_MENU_AUTO = true` TRƯỚC thẻ <script> này,
 * hoặc gọi `window.TuniXContextMenu.install()` sau khi trang đã tải.
 *
 * (Bản gốc) Thay menu chuột phải MẶC ĐỊNH của trình duyệt bằng menu của TuniX, hiển thị tại vị trí
 * con trỏ, giao diện kiểu Chrome (light + dark-mode), logic theo NGỮ CẢNH click.
 *
 * KIẾN TRÚC (1 file, JavaScript thuần — KHÔNG dùng framework vì toàn bộ frontend của dự án
 * là HTML/CSS/JS thuần nhúng vào shell iframe; không đổi kiến trúc hiện có):
 *
 *   useContextMenu()        → install(): gắn listener toàn cục ĐÚNG 1 LẦN, cleanup khi cần
 *   detectContext(event)    → phân loại ngữ cảnh → ContextInfo
 *   buildContextMenu(ctx)   → dựng danh sách item TỪ CONFIG (không hard-code UI)
 *   ContextMenuItem / ContextMenuSeparator → factory tạo DOM node (role="menuitem")
 *   contextMenuActions      → bảng action theo id (executeAction)
 *   contextMenuUtils        → tiện ích: escape, copy, download, clamp vị trí, toast…
 *
 * Flow: right-click → preventDefault → detectContext → buildContextMenu → placeMenu
 *       → render → người dùng chọn item → executeAction → close.
 *
 * DATA MODEL:
 *   item      = { id, label, icon, shortcut, disabled, visible, hint, action }
 *   separator = { divider: true }
 *   ContextInfo = {
 *       type: 'PAGE_BACKGROUND'|'LINK'|'IMAGE'|'TEXT_SELECTION'|'INPUT'|'TEXTAREA'|'VIDEO'|'AUDIO'|'OTHER_ELEMENT',
 *       target, linkUrl, imageUrl, selectedText, pageUrl, pageTitle,
 *       elementType, elementText
 *   }
 *
 * NGUYÊN TẮC "KHÔNG GIẢ LẬP CHỨC NĂNG TRÌNH DUYỆT": những việc website KHÔNG được phép làm
 * (DevTools, Cast/Truyền, Gửi đến thiết bị…) sẽ hiển thị ở trạng thái DISABLED kèm lý do,
 * thay vì giả vờ thực hiện. Các việc có thể làm trong phạm vi website (xem nguồn HTML, chế độ
 * đọc, QR, dịch, lưu tệp HTML) thì làm thật bằng API của trình duyệt.
 *
 * Dùng cho MỌI trang — thêm trước </body>:
 *   <script src="/static/context-menu.js?v=20260928a"></script>
 *
 * API mở rộng cho từng trang (giữ tương thích bản cũ):
 *   window.TuniXContextMenu.register(rule, factory)
 *     rule(target, event) -> boolean
 *     factory(target, event) -> [item | {divider:true}]
 *   window.TuniXContextMenu.open(items, x, y) / .close()
 *   window.TuniXContextMenu.detectContext(event) / .buildContextMenu(ctx)
 *   window.TuniXContextMenu.actions / .utils / .config / .version
 */
(function () {
    'use strict';

    var VERSION = '20260928a';
    var STYLE_ID = 'tunix-ctx-style';
    var TOAST_ID = 'tunix-ctx-toast';
    var MENU_CLASS = 'tunix-ctx';
    var Z_INDEX = 2147483000;

    /* ══════════════════════════════════════════════════════════════════
     * 1) contextMenuUtils — tiện ích dùng chung
     * ══════════════════════════════════════════════════════════════════ */
    var contextMenuUtils = (function () {
        var NON_TEXT_TYPES = ['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range', 'color', 'image', 'hidden'];
        var BLOCK_TAGS = ['HTML', 'BODY', 'DIV', 'SECTION', 'MAIN', 'ARTICLE', 'ASIDE', 'UL', 'OL', 'TABLE', 'TBODY', 'TR', 'TD', 'FORM', 'HEADER', 'FOOTER', 'NAV'];

        function assign(target) {
            for (var i = 1; i < arguments.length; i++) {
                var src = arguments[i] || {};
                for (var k in src) if (Object.prototype.hasOwnProperty.call(src, k)) target[k] = src[k];
            }
            return target;
        }

        function esc(s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        }

        function clamp(v, min, max) { return Math.max(min, Math.min(v, max)); }

        function shortText(s, max) {
            var t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
            max = max || 40;
            return t.length > max ? t.slice(0, max - 1) + '…' : t;
        }

        function isTextEditable(el) {
            if (!el || !el.tagName) return false;
            if (el.isContentEditable) return true;
            var tag = el.tagName.toUpperCase();
            if (tag === 'TEXTAREA') return true;
            if (tag === 'SELECT') return true;
            if (tag === 'INPUT') return NON_TEXT_TYPES.indexOf(String(el.type || 'text').toLowerCase()) === -1;
            return false;
        }

        function closestEditable(el) {
            var node = el;
            while (node && node !== document.body) {
                if (isTextEditable(node)) return node;
                node = node.parentElement;
            }
            return null;
        }

        function hasInputSelection(el) {
            try {
                if (el && el.selectionStart != null && el.selectionEnd != null) return el.selectionStart !== el.selectionEnd;
                var s = window.getSelection();
                return !!s && s.toString().length > 0;
            } catch (err) { return false; }
        }

        function getSelectionText() {
            try { return String((window.getSelection() || {}).toString() || '').trim(); } catch (err) { return ''; }
        }

        function isBlankTarget(el) {
            if (!el || !el.tagName) return true;
            var tag = el.tagName.toUpperCase();
            if (tag === 'HTML' || tag === 'BODY') return true;
            if (BLOCK_TAGS.indexOf(tag) === -1) return false;
            var txt = String(el.textContent || '').replace(/\s+/g, '');
            return txt.length === 0;
        }

        function absUrl(href) {
            try { return new URL(String(href || ''), location.href).href; } catch (err) { return String(href || ''); }
        }

        function isSameOrigin(url) {
            try { return new URL(url, location.href).origin === location.origin; } catch (err) { return false; }
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
                return true;
            } catch (err) { return false; }
        }

        function copyText(text, okMsg) {
            text = String(text == null ? '' : text);
            if (!text) return;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function () {
                    toast(okMsg || 'Đã sao chép');
                }, function () {
                    fallbackCopy(text);
                    toast(okMsg || 'Đã sao chép');
                });
            } else {
                fallbackCopy(text);
                toast(okMsg || 'Đã sao chép');
            }
        }

        function runCmd(cmd) {
            try { return document.execCommand(cmd); } catch (err) { return false; }
        }

        function downloadBlob(content, mime, filename) {
            try {
                var blob = content instanceof Blob ? content : new Blob([content], { type: mime || 'application/octet-stream' });
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = filename || 'tunix-download';
                a.style.display = 'none';
                document.body.appendChild(a);
                a.click();
                setTimeout(function () {
                    try { a.remove(); URL.revokeObjectURL(url); } catch (e) { /* ignore */ }
                }, 4000);
                return true;
            } catch (err) { return false; }
        }

        /** Tải 1 URL về máy (cùng origin / có CORS) — fallback: mở tab mới để trình duyệt tự lưu. */
        function downloadUrl(url, filename) {
            fetch(url, { credentials: 'same-origin' })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob(); })
                .then(function (blob) {
                    downloadBlob(blob, blob.type || 'application/octet-stream', filename || 'tunix-file');
                    toast('Đã tải xuống');
                })
                .catch(function () {
                    window.open(url, '_blank', 'noopener');
                    toast('Không tải trực tiếp được — đã mở ở tab mới để tự lưu', false);
                });
        }

        /** Sao chép ẢNH vào clipboard (PNG). Ảnh khác miền có thể bị chặn → sao chép URL thay thế. */
        function copyImage(el) {
            var url = (el && (el.currentSrc || el.src)) || '';
            if (!url) return;
            if (!navigator.clipboard || !navigator.clipboard.write || typeof window.ClipboardItem === 'undefined') {
                copyText(url, 'Đã sao chép địa chỉ ảnh');
                return;
            }
            fetch(url, { credentials: 'same-origin' })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob(); })
                .then(function (blob) {
                    var type = blob.type && /^image\//.test(blob.type) ? blob.type : 'image/png';
                    var data = {};
                    data[type] = blob;
                    return navigator.clipboard.write([new window.ClipboardItem(data)]);
                })
                .then(function () { toast('Đã sao chép hình ảnh'); })
                .catch(function () { copyText(url, 'Không sao chép được ảnh — đã sao chép địa chỉ ảnh'); });
        }

        function screenshotViaCanvas(img) {
            try {
                var c = document.createElement('canvas');
                c.width = img.naturalWidth || 0;
                c.height = img.naturalHeight || 0;
                if (!c.width || !c.height) return null;
                c.getContext('2d').drawImage(img, 0, 0);
                return c;
            } catch (err) { return null; }
        }

        /** Thông báo nhỏ (tự tiêm) — dùng cho phản hồi hành động. */
        var toastTimer = null;
        function toast(msg, ok) {
            try {
                var el = document.getElementById(TOAST_ID);
                if (!el) {
                    el = document.createElement('div');
                    el.id = TOAST_ID;
                    document.body.appendChild(el);
                }
                el.className = 'tunix-ctx-toast' + (ok === false ? ' is-warn' : '');
                el.innerHTML = '<i class="fa-solid ' + (ok === false ? 'fa-triangle-exclamation' : 'fa-circle-check') + '"></i><span>' + esc(msg) + '</span>';
                el.classList.add('show');
                if (toastTimer) clearTimeout(toastTimer);
                toastTimer = setTimeout(function () { el.classList.remove('show'); }, 2600);
            } catch (err) { /* ignore */ }
        }

        return {
            assign: assign, esc: esc, clamp: clamp, shortText: shortText,
            isTextEditable: isTextEditable, closestEditable: closestEditable,
            hasInputSelection: hasInputSelection, getSelectionText: getSelectionText,
            isBlankTarget: isBlankTarget, absUrl: absUrl, isSameOrigin: isSameOrigin,
            copyText: copyText, fallbackCopy: fallbackCopy, runCmd: runCmd,
            downloadBlob: downloadBlob, downloadUrl: downloadUrl,
            copyImage: copyImage, screenshotViaCanvas: screenshotViaCanvas, toast: toast
        };
    })();

    var utils = contextMenuUtils;
    var esc = utils.esc;

    /* ══════════════════════════════════════════════════════════════════
     * 2) CSS — tự tiêm (light + dark mode), giống Chrome
     * ══════════════════════════════════════════════════════════════════ */
    var CSS = [
        '.tunix-ctx{position:fixed;z-index:' + Z_INDEX + ';width:300px;min-width:240px;max-width:300px;',
        '  padding:6px;background:#fff;border:1px solid #e2e8f0;border-radius:10px;',
        '  box-shadow:0 10px 34px rgba(15,23,42,.18),0 2px 8px rgba(15,23,42,.08);',
        '  font-family:\'Inter\',-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;',
        '  font-size:13.5px;line-height:1.35;color:#1f2937;box-sizing:border-box;',
        '  user-select:none;-webkit-user-select:none;overflow-y:auto;overflow-x:hidden;',
        '  animation:tunix-ctx-in .12s ease-out;transform-origin:top left;}',
        '@keyframes tunix-ctx-in{from{opacity:0;transform:scale(.97)}to{opacity:1;transform:scale(1)}}',
        '.tunix-ctx:focus{outline:none;}',
        /* Chiều cao/kích thước CỐ ĐỊNH → vị trí menu không lệch khi font/icon load muộn */
        '.tunix-ctx-item{display:flex;align-items:center;gap:11px;width:100%;height:34px;padding:0 10px;',
        '  border:0;background:transparent;color:inherit;border-radius:7px;cursor:pointer;',
        '  text-align:left;font:inherit;white-space:nowrap;box-sizing:border-box;}',
        '.tunix-ctx-item i{width:17px;height:17px;display:inline-flex;align-items:center;justify-content:center;',
        '  font-size:13px;color:#64748b;flex:none;}',
        '.tunix-ctx-item .tunix-ctx-label{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
        '.tunix-ctx-item:hover,.tunix-ctx-item.is-active{background:#f0f4f8;color:#008080;}',
        '.tunix-ctx-item:hover i,.tunix-ctx-item.is-active i{color:#008080;}',
        '.tunix-ctx-item.is-disabled{opacity:.42;cursor:default;}',
        '.tunix-ctx-item.is-disabled:hover{background:transparent;color:inherit;}',
        '.tunix-ctx-item.is-disabled:hover i{color:#64748b;}',
        '.tunix-ctx-kbd{margin-left:auto;padding:0 6px;height:18px;line-height:16px;font-size:11px;color:#94a3b8;flex:none;',
        '  border:1px solid #e2e8f0;border-radius:5px;background:#f8fafc;font-variant-numeric:tabular-nums;}',
        '.tunix-ctx-sep{height:1px;background:#e8edf3;margin:5px 6px;}',
        '.tunix-ctx-note{padding:4px 10px 2px;font-size:10.5px;color:#94a3b8;text-transform:uppercase;letter-spacing:.4px;}',
        /* Toast */
        '.tunix-ctx-toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,14px);z-index:' + (Z_INDEX + 10) + ';',
        '  display:flex;align-items:center;gap:8px;max-width:82vw;padding:10px 16px;border-radius:10px;',
        '  background:#0f172a;color:#fff;font:600 12.5px \'Inter\',system-ui,sans-serif;',
        '  box-shadow:0 10px 30px rgba(15,23,42,.3);opacity:0;pointer-events:none;transition:opacity .18s,transform .18s;}',
        '.tunix-ctx-toast.show{opacity:1;transform:translate(-50%,0);}',
        '.tunix-ctx-toast i{color:#5fd6cf;}',
        '.tunix-ctx-toast.is-warn{background:#7a3b12;}',
        '.tunix-ctx-toast.is-warn i{color:#ffd28a;}',
        /* Modal QR */
        '.tunix-ctx-modal{position:fixed;inset:0;z-index:' + (Z_INDEX + 20) + ';display:flex;align-items:center;justify-content:center;',
        '  background:rgba(15,23,42,.5);backdrop-filter:blur(2px);padding:16px;}',
        '.tunix-ctx-modal-card{background:#fff;color:#1f2937;border-radius:14px;padding:18px;max-width:330px;width:100%;',
        '  box-shadow:0 18px 50px rgba(15,23,42,.35);text-align:center;font-family:\'Inter\',system-ui,sans-serif;}',
        '.tunix-ctx-modal-card h3{margin:0 0 4px;font-size:15px;}',
        '.tunix-ctx-modal-card p{margin:0 0 12px;font-size:12px;color:#64748b;word-break:break-all;}',
        '.tunix-ctx-modal-card img{width:240px;height:240px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;}',
        '.tunix-ctx-modal-actions{display:flex;gap:8px;justify-content:center;margin-top:14px;flex-wrap:wrap;}',
        '.tunix-ctx-btn{border:1px solid #dbe4ec;background:#fff;color:#1f2937;border-radius:8px;padding:8px 14px;',
        '  font:600 12.5px inherit;cursor:pointer;}',
        '.tunix-ctx-btn.primary{background:#008080;border-color:#008080;color:#fff;}',
        '.tunix-ctx-btn:hover{filter:brightness(.97);}',
        /* Dark mode */
        'body.dark-mode .tunix-ctx{background:#161b22;border-color:#30363d;color:#e6edf3;box-shadow:0 12px 40px rgba(0,0,0,.55);}',
        'body.dark-mode .tunix-ctx-item i{color:#8b949e;}',
        'body.dark-mode .tunix-ctx-item:hover,body.dark-mode .tunix-ctx-item.is-active{background:#21262d;color:#58a6ff;}',
        'body.dark-mode .tunix-ctx-item:hover i,body.dark-mode .tunix-ctx-item.is-active i{color:#58a6ff;}',
        'body.dark-mode .tunix-ctx-kbd{color:#6e7681;border-color:#30363d;background:#21262d;}',
        'body.dark-mode .tunix-ctx-sep{background:#30363d;}',
        'body.dark-mode .tunix-ctx-note{color:#6e7681;}',
        'body.dark-mode .tunix-ctx-modal-card{background:#161b22;color:#e6edf3;border:1px solid #30363d;}',
        'body.dark-mode .tunix-ctx-modal-card p{color:#8b949e;}',
        'body.dark-mode .tunix-ctx-btn{background:#21262d;border-color:#30363d;color:#e6edf3;}',
        'body.dark-mode .tunix-ctx-btn.primary{background:#008080;border-color:#008080;color:#fff;}'
    ].join('\n');

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var st = document.createElement('style');
        st.id = STYLE_ID;
        st.textContent = CSS;
        (document.head || document.documentElement).appendChild(st);
    }

    /* ══════════════════════════════════════════════════════════════════
     * 3) detectContext — phân loại ngữ cảnh
     * ══════════════════════════════════════════════════════════════════ */
    var JS_VOID_HREF = /^\s*javascript:/i;

    function closestLink(el) {
        var node = el;
        while (node && node.tagName) {
            if (node.tagName.toUpperCase() === 'A') {
                var href = node.getAttribute('href') || '';
                if (href && !JS_VOID_HREF.test(href) && href.charAt(0) !== '#') return node;
                return null;
            }
            node = node.parentElement;
        }
        return null;
    }

    function detectContext(e) {
        var t = (e && e.target) || document.body;
        var linkEl = closestLink(t);
        var imgEl = (t.closest && t.closest('img')) || null;
        var videoEl = (t.closest && (t.closest('video') || t.closest('.video-js'))) || null;
        var audioEl = (t.closest && t.closest('audio')) || null;
        var editableEl = utils.closestEditable(t);
        var selectedText = utils.getSelectionText();
        var tag = (t.tagName || '').toUpperCase();

        var type = 'OTHER_ELEMENT';
        if (editableEl) {
            type = tag === 'TEXTAREA' ? 'TEXTAREA' : 'INPUT';
        } else if (selectedText) {
            type = 'TEXT_SELECTION';
        } else if (linkEl) {
            type = 'LINK';
        } else if (imgEl) {
            type = 'IMAGE';
        } else if (videoEl) {
            type = 'VIDEO';
        } else if (audioEl) {
            type = 'AUDIO';
        } else if (utils.isBlankTarget(t)) {
            type = 'PAGE_BACKGROUND';
        }

        var elementText = '';
        try {
            var host = linkEl || imgEl || videoEl || audioEl || editableEl || t;
            elementText = String(host.innerText || host.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300);
        } catch (err) { elementText = ''; }

        return {
            type: type,
            target: t,
            linkEl: linkEl,
            linkUrl: linkEl ? utils.absUrl(linkEl.getAttribute('href')) : '',
            imageEl: imgEl,
            imageUrl: imgEl ? utils.absUrl(imgEl.currentSrc || imgEl.src || '') : '',
            videoEl: videoEl,
            audioEl: audioEl,
            mediaEl: videoEl || audioEl || null,
            editableEl: editableEl,
            selectedText: selectedText,
            pageUrl: location.href,
            pageTitle: String(document.title || '').trim(),
            elementType: tag ? tag.toLowerCase() : 'page',
            elementText: elementText,
            hasInputSelection: editableEl ? utils.hasInputSelection(editableEl) : false
        };
    }

    /* ══════════════════════════════════════════════════════════════════
     * 4) contextMenuActions — thực thi hành động
     *    (mỗi action nhận ctx, KHÔNG tự đóng menu — ContextMenu lo việc đó)
     * ══════════════════════════════════════════════════════════════════ */

    /* Mở TuniAI với ngữ cảnh: ưu tiên điền sẵn vào ô chat nếu đang ở trang TuniAI. */
    function askAi(ctx, kind) {
        var box = document.getElementById('taInput');
        var prompt = buildAiPrompt(ctx, kind);
        if (box) {
            box.value = prompt;
            box.focus();
            try { box.dispatchEvent(new Event('input', { bubbles: true })); } catch (err) { /* ignore */ }
            utils.toast('Đã điền câu hỏi vào TuniAI');
            return;
        }
        // Nhúng trong shell → nhờ shell chuyển trang + gửi câu hỏi
        if (window.self !== window.top) {
            try { window.parent.postMessage({ type: 'tunix-ai-open', prompt: prompt, context: aiContext(ctx, kind) }, '*'); return; } catch (err) { /* ignore */ }
        }
        // Có API điều hướng của shell
        if (typeof window.openTuniAiPage === 'function') {
            try { sessionStorage.setItem('tunix_ai_prompt', prompt); } catch (err) { /* ignore */ }
            window.openTuniAiPage();
            return;
        }
        // Trang độc lập: lưu prompt rồi sang trang /tuniai
        try { sessionStorage.setItem('tunix_ai_prompt', prompt); } catch (err) { /* ignore */ }
        location.href = '/tuniai';
    }

    function aiContext(ctx, kind) {
        var c = {
            pageUrl: ctx.pageUrl,
            pageTitle: ctx.pageTitle,
            selectedText: '',
            elementType: '',
            elementText: '',
            imageUrl: '',
            linkUrl: ''
        };
        if (kind === 'text') { c.selectedText = ctx.selectedText; c.elementType = 'text'; }
        else if (kind === 'link') { c.linkUrl = ctx.linkUrl; c.elementType = 'link'; c.elementText = ctx.elementText; }
        else if (kind === 'image') { c.imageUrl = ctx.imageUrl; c.elementType = 'image'; c.elementText = ctx.elementText; }
        else if (kind === 'video' || kind === 'audio') { c.elementType = kind; c.elementText = ctx.elementText; c.linkUrl = mediaSrc(ctx.mediaEl); }
        else if (kind === 'element') { c.elementType = ctx.elementType; c.elementText = ctx.elementText; }
        else { c.elementType = 'page'; c.elementText = ctx.selectedText || ctx.pageTitle; }
        return c;
    }

    function buildAiPrompt(ctx, kind) {
        var c = aiContext(ctx, kind);
        var lines = ['[Hỏi AI từ menu chuột phải TuniX]'];
        lines.push('pageUrl: ' + c.pageUrl);
        if (c.pageTitle) lines.push('pageTitle: ' + c.pageTitle);
        if (c.selectedText) lines.push('selectedText: ' + c.selectedText);
        if (c.elementType) lines.push('elementType: ' + c.elementType);
        if (c.elementText) lines.push('elementText: ' + c.elementText);
        if (c.linkUrl) lines.push('linkUrl: ' + c.linkUrl);
        if (c.imageUrl) lines.push('imageUrl: ' + c.imageUrl);
        lines.push('');
        if (kind === 'text') lines.push('Yêu cầu: giải thích/đánh giá giúp tôi nội dung được chọn ở trên (bối cảnh chứng khoán – tài chính nếu liên quan).');
        else if (kind === 'link') lines.push('Yêu cầu: cho biết liên kết này nói về gì và điểm đáng chú ý (dựa trên URL/ngữ cảnh, nêu rõ nếu không đọc được nội dung bên trong).');
        else if (kind === 'image') lines.push('Yêu cầu: mô tả/thông tin về hình ảnh này và ý nghĩa của nó trên trang.');
        else if (kind === 'video' || kind === 'audio') lines.push('Yêu cầu: cho biết thông tin về nội dung media này và cách đối chiếu với dữ liệu TuniX.');
        else if (kind === 'element') lines.push('Yêu cầu: giải thích phần tử/khối nội dung này trên trang và ý nghĩa của nó.');
        else lines.push('Yêu cầu: tôi đang xem trang này, hãy gợi ý tôi nên xem/chú ý gì tiếp theo.');
        return lines.join('\n');
    }

    function fileNameForPage() {
        var slug = String(document.title || 'tunix').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').toLowerCase();
        var d = new Date();
        var stamp = d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
        return 'tunix-' + (slug || 'page').slice(0, 60) + '-' + stamp + '.html';
    }

    function mediaSrc(el) {
        if (!el) return '';
        if (el.currentSrc) return el.currentSrc;
        if (el.src) return el.src;
        var s = el.querySelector && el.querySelector('source');
        return (s && (s.src || s.getAttribute('src'))) || '';
    }

    function readerHtml() {
        var candidates = ['article', 'main', '.article-container', '.article-content', '#dynamicContent', '.content', '#content', '.post-content'];
        var root = null;
        for (var i = 0; i < candidates.length; i++) {
            var el = document.querySelector(candidates[i]);
            if (el && String(el.innerText || '').trim().length > 400) { root = el; break; }
        }
        if (!root) root = document.body;
        var clone = root.cloneNode(true);
        var strip = clone.querySelectorAll('script,style,noscript,iframe,nav,aside,footer,header,form,button,svg,.no-reader,.advertisement');
        for (var j = 0; j < strip.length; j++) if (strip[j].parentNode) strip[j].parentNode.removeChild(strip[j]);
        var body = clone.innerHTML;
        var title = utils.esc(document.title || 'TuniX');
        var src = utils.esc(location.href);
        return '<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8" />' +
            '<meta name="viewport" content="width=device-width,initial-scale=1" />' +
            '<base href="' + utils.esc(location.href) + '" />' +
            '<title>' + title + '</title><style>' +
            'body{margin:0;background:#f6f8fa;color:#1f2937;font:17px/1.75 Georgia,"Times New Roman",serif;}' +
            '.rd-wrap{max-width:760px;margin:0 auto;padding:32px 20px 80px;background:#fff;min-height:100vh;' +
            'box-shadow:0 0 40px rgba(15,23,42,.06);}' +
            '.rd-bar{font:600 12px system-ui,sans-serif;color:#64748b;border-bottom:1px solid #e8edf3;padding-bottom:10px;margin-bottom:22px;' +
            'display:flex;gap:10px;flex-wrap:wrap;align-items:center;}' +
            '.rd-bar a{color:#008080;text-decoration:none;}' +
            '.rd-wrap h1{font-size:30px;line-height:1.3;margin:0 0 8px;} .rd-wrap h2{font-size:23px;margin:28px 0 10px;}' +
            '.rd-wrap h3{font-size:19px;margin:22px 0 8px;} .rd-wrap img{max-width:100%;height:auto;border-radius:8px;}' +
            '.rd-wrap a{color:#0f766e;} .rd-wrap blockquote{border-left:3px solid #0f766e;margin:16px 0;padding:6px 14px;color:#475569;}' +
            '.rd-wrap table{width:100%;border-collapse:collapse;font:14px system-ui,sans-serif;} .rd-wrap td,.rd-wrap th{border:1px solid #e2e8f0;padding:6px 8px;}' +
            '@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}.rd-wrap{background:#161b22}}' +
            '</style></head><body><div class="rd-wrap"><div class="rd-bar"><i>📖</i> <span>Chế độ đọc TuniX</span>' +
            ' · <a href="' + src + '">Mở trang gốc</a></div>' + body + '</div></body></html>';
    }

    var contextMenuActions = {
        goBack: function () { goBackPage(); },
        goForward: function () { goForwardPage(); },
        reload: function () { location.reload(); },
        askAi: function (ctx, kind) { askAi(ctx, kind); },

        openLinkSame: function (ctx) { if (ctx.linkUrl) location.href = ctx.linkUrl; },
        openLinkTab: function (ctx) { if (ctx.linkUrl) window.open(ctx.linkUrl, '_blank', 'noopener,noreferrer'); },
        openLinkWindow: function (ctx) { if (ctx.linkUrl) openNewWindow(ctx.linkUrl); },
        openLinkSplit: function (ctx) { if (ctx.linkUrl) openSplitView(ctx.linkUrl); },
        copyLink: function (ctx) { utils.copyText(ctx.linkUrl, 'Đã sao chép địa chỉ liên kết'); },

        openImageTab: function (ctx) { if (ctx.imageUrl) window.open(ctx.imageUrl, '_blank', 'noopener,noreferrer'); },
        saveImage: function (ctx) { utils.downloadUrl(ctx.imageUrl, (ctx.imageUrl.split('/').pop() || 'image').split('?')[0]); },
        copyImage: function (ctx) { utils.copyImage(ctx.imageEl); },
        copyImageUrl: function (ctx) { utils.copyText(ctx.imageUrl, 'Đã sao chép địa chỉ hình ảnh'); },
        searchImage: function (ctx) {
            window.open('https://lens.google.com/uploadbyurl?url=' + encodeURIComponent(ctx.imageUrl), '_blank', 'noopener,noreferrer');
        },

        copySelection: function (ctx) { utils.copyText(ctx.selectedText, 'Đã sao chép văn bản'); },
        searchSelection: function (ctx) { googleSearch(ctx.selectedText); },
        translateSelection: function (ctx) {
            window.open('https://translate.google.com/?sl=auto&tl=vi&op=translate&text=' + encodeURIComponent(utils.shortText(ctx.selectedText, 1200)), '_blank', 'noopener,noreferrer');
        },

        undo: function (ctx) { focusEditable(ctx); utils.runCmd('undo'); },
        redo: function (ctx) { focusEditable(ctx); utils.runCmd('redo'); },
        cut: function (ctx) { focusEditable(ctx); utils.runCmd('cut'); },
        copyInput: function (ctx) { focusEditable(ctx); utils.runCmd('copy'); },
        paste: function (ctx) { pasteInto(ctx); },
        del: function (ctx) { focusEditable(ctx); utils.runCmd('delete'); },
        selectAllInput: function (ctx) {
            var el = ctx.editableEl;
            if (!el) return;
            try { el.focus({ preventScroll: true }); } catch (err) { try { el.focus(); } catch (e2) { /* ignore */ } }
            if (el.select) { try { el.select(); return; } catch (err) { /* ignore */ } }
            utils.runCmd('selectAll');
        },
        selectAllPage: function () {
            try {
                var r = document.createRange();
                r.selectNodeContents(document.body);
                var sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(r);
            } catch (err) { /* ignore */ }
        },

        savePage: function () {
            fetch(location.href, { credentials: 'same-origin' })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
                .then(function (html) {
                    utils.downloadBlob(html, 'text/html;charset=utf-8', fileNameForPage());
                    utils.toast('Đã lưu tệp HTML của trang');
                })
                .catch(function () {
                    utils.toast('Không lưu được tệp — hãy dùng Ctrl+S của trình duyệt', false);
                });
        },
        print: function () { window.print(); },
        readerMode: function () {
            var html = readerHtml();
            var url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
            window.open(url, '_blank', 'noopener,noreferrer');
            utils.toast('Đã mở chế độ đọc (bản TuniX)');
        },
        searchPage: function (ctx) { googleSearch(ctx.selectedText || ctx.pageTitle || ctx.pageUrl); },
        translatePage: function (ctx) {
            window.open('https://translate.google.com/translate?sl=auto&tl=vi&u=' + encodeURIComponent(ctx.pageUrl), '_blank', 'noopener,noreferrer');
        },
        qrPage: function (ctx) { showQrModal(ctx.pageUrl); },
        viewSource: function (ctx) {
            var url = location.href;
            fetch(url, { credentials: 'same-origin' })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
                .then(function (html) {
                    var blob = new Blob(['<!-- Nguồn: ' + url + ' -->\n' + html], { type: 'text/plain;charset=utf-8' });
                    window.open(URL.createObjectURL(blob), '_blank', 'noopener,noreferrer');
                })
                .catch(function () { utils.toast('Không đọc được mã nguồn (trang khác miền)', false); });
        },

        mediaPlayPause: function (ctx) { var m = ctx.mediaEl; if (!m) return; if (m.paused) m.play(); else m.pause(); },
        mediaMute: function (ctx) { var m = ctx.mediaEl; if (!m) return; m.muted = !m.muted; },
        mediaPip: function (ctx) {
            var m = ctx.mediaEl;
            if (!m) return;
            try {
                if (document.pictureInPictureElement) document.exitPictureInPicture();
                else if (m.requestPictureInPicture) m.requestPictureInPicture();
                else utils.toast('Trình duyệt không hỗ trợ ảnh-trong-ảnh', false);
            } catch (err) { utils.toast('Không bật được ảnh-trong-ảnh', false); }
        },
        mediaFullscreen: function (ctx) {
            var m = ctx.mediaEl;
            if (!m) return;
            try {
                if (document.fullscreenElement) document.exitFullscreen();
                else if (m.requestFullscreen) m.requestFullscreen();
                else if (m.webkitEnterFullscreen) m.webkitEnterFullscreen();
                else utils.toast('Trình duyệt không hỗ trợ toàn màn hình cho media', false);
            } catch (err) { utils.toast('Không bật được toàn màn hình', false); }
        },
        copyMediaUrl: function (ctx) { utils.copyText(mediaSrc(ctx.mediaEl), 'Đã sao chép địa chỉ media'); },
        saveMedia: function (ctx) {
            var src = mediaSrc(ctx.mediaEl);
            if (!src) { utils.toast('Không tìm thấy địa chỉ media', false); return; }
            utils.downloadUrl(src, (src.split('/').pop() || 'media').split('?')[0]);
        },

        toggleTheme: function () { toggleTheme(); },
        disabledBrowserFeature: function (ctx, item) { utils.toast((item && item.hint) || 'Trình duyệt không cho trang web thực hiện việc này', false); }
    };

    function focusEditable(ctx) {
        var el = ctx && ctx.editableEl;
        if (!el) return;
        try { el.focus({ preventScroll: true }); } catch (err) { try { el.focus(); } catch (e2) { /* ignore */ } }
    }

    function pasteInto(ctx) {
        var el = ctx && ctx.editableEl;
        if (!el) return;
        focusEditable(ctx);
        if (navigator.clipboard && navigator.clipboard.readText) {
            navigator.clipboard.readText().then(function (text) {
                if (!text) return;
                if (el.isContentEditable) {
                    try { document.execCommand('insertText', false, text); } catch (err) { /* ignore */ }
                } else {
                    var start = el.selectionStart != null ? el.selectionStart : el.value.length;
                    var end = el.selectionEnd != null ? el.selectionEnd : el.value.length;
                    var val = String(el.value || '');
                    el.value = val.slice(0, start) + text + val.slice(end);
                    try { el.selectionStart = el.selectionEnd = start + text.length; } catch (err) { /* ignore */ }
                    try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (err) { /* ignore */ }
                }
            }).catch(function () {
                if (!utils.runCmd('paste')) utils.toast('Hãy dùng Ctrl+V để dán', false);
            });
        } else if (!utils.runCmd('paste')) {
            utils.toast('Hãy dùng Ctrl+V để dán', false);
        }
    }

    function googleSearch(query) {
        query = String(query || '').replace(/\s+/g, ' ').trim();
        if (!query) { utils.toast('Không có nội dung để tìm kiếm', false); return; }
        window.open('https://www.google.com/search?q=' + encodeURIComponent(query.slice(0, 300)), '_blank', 'noopener,noreferrer');
    }

    function goBackPage() {
        if (window.self !== window.top) {
            try { window.parent.postMessage({ type: 'tunix-go-back' }, '*'); return; } catch (e) { /* ignore */ }
        }
        if (typeof window.tunixGoBackInApp === 'function') { window.tunixGoBackInApp(); return; }
        try {
            var ref = document.referrer || '';
            if (ref && (new URL(ref)).origin === location.origin) { history.back(); return; }
        } catch (e) { /* ignore */ }
        location.href = (location.origin || '') + '/';
    }

    function goForwardPage() {
        if (window.self !== window.top) {
            try { window.parent.postMessage({ type: 'tunix-go-forward' }, '*'); return; } catch (e) { /* ignore */ }
        }
        if (typeof window.tunixGoForwardInApp === 'function') { window.tunixGoForwardInApp(); return; }
        history.forward();
    }

    function openNewWindow(url) {
        var w = 1180, h = 780;
        var left = Math.max(0, Math.round((window.screenX || 0) + ((window.outerWidth || w) - w) / 2));
        var top = Math.max(0, Math.round((window.screenY || 0) + 40));
        window.open(url, '_blank', 'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top +
            ',resizable=yes,scrollbars=yes,menubar=yes,toolbar=yes,status=yes,noopener,noreferrer');
    }

    function openSplitView(url) {
        var screenW = window.screen.width || Math.round((window.outerWidth || 1200) * 2);
        var screenH = window.screen.height || (window.outerHeight || 800);
        var w = Math.max(360, Math.round(screenW / 2));
        var h = Math.max(480, Math.round(screenH * 0.94));
        var left = Math.round((window.screenX || 0) + (window.outerWidth || w));
        left = Math.max(0, Math.min(screenW - w, left));
        var top = Math.round(window.screenY || 0);
        window.open(url, '_blank', 'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top +
            ',resizable=yes,scrollbars=yes,status=yes,noopener,noreferrer');
    }

    function toggleTheme() {
        var dark = !document.body.classList.contains('dark-mode');
        document.body.classList.toggle('dark-mode', dark);
        try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch (err) { /* ignore */ }
        var mode = dark ? 'dark' : 'light';
        try {
            window.postMessage({ type: 'tunix-set-theme', mode: mode }, '*');
            window.postMessage({ type: 'tunix-theme', mode: mode }, '*');
        } catch (err) { /* ignore */ }
        var t = document.getElementById('themeToggle');
        if (t) t.className = dark ? 'fas fa-sun theme-toggle' : 'fas fa-moon theme-toggle';
        var tm = document.getElementById('themeToggleMini');
        if (tm) { var i = tm.querySelector('i'); if (i) i.className = dark ? 'fas fa-sun' : 'fas fa-moon'; }
    }

    /* Modal QR (tự dựng, không cần thư viện ngoài) */
    var qrModal = null;
    function showQrModal(url) {
        closeQrModal();
        var qrSrc = 'https://api.qrserver.com/v1/create-qr-code/?size=480x480&margin=8&data=' + encodeURIComponent(url);
        var wrap = document.createElement('div');
        wrap.className = 'tunix-ctx-modal';
        wrap.setAttribute('role', 'dialog');
        wrap.setAttribute('aria-modal', 'true');
        wrap.innerHTML =
            '<div class="tunix-ctx-modal-card">' +
                '<h3>Mã QR cho trang này</h3>' +
                '<p>' + esc(utils.shortText(url, 120)) + '</p>' +
                '<img alt="Mã QR của trang" src="' + esc(qrSrc) + '" />' +
                '<div class="tunix-ctx-modal-actions">' +
                    '<button type="button" class="tunix-ctx-btn primary" data-qr-copy><i class="fa-solid fa-copy"></i> Sao chép địa chỉ</button>' +
                    '<button type="button" class="tunix-ctx-btn" data-qr-close>Đóng</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(wrap);
        qrModal = wrap;
        var img = wrap.querySelector('img');
        img.addEventListener('error', function () {
            img.replaceWith((function () {
                var p = document.createElement('p');
                p.style.marginTop = '10px';
                p.textContent = 'Không tải được mã QR (cần internet). Bạn vẫn có thể sao chép địa chỉ bên dưới.';
                return p;
            })());
        });
        wrap.addEventListener('mousedown', function (ev) { if (ev.target === wrap) closeQrModal(); });
        wrap.addEventListener('contextmenu', function (ev) { ev.preventDefault(); });
        wrap.querySelector('[data-qr-close]').addEventListener('click', closeQrModal);
        wrap.querySelector('[data-qr-copy]').addEventListener('click', function () { utils.copyText(url, 'Đã sao chép địa chỉ trang'); });
        document.addEventListener('keydown', onQrKey, true);
    }

    function onQrKey(ev) { if (ev.key === 'Escape') { ev.preventDefault(); closeQrModal(); } }

    function closeQrModal() {
        document.removeEventListener('keydown', onQrKey, true);
        if (qrModal) { try { qrModal.remove(); } catch (err) { /* ignore */ } qrModal = null; }
    }

    /* ══════════════════════════════════════════════════════════════════
     * 5) CONFIG — khai báo item + nhóm menu theo ngữ cảnh (không hard-code UI)
     * ══════════════════════════════════════════════════════════════════ */
    var ICON = {
        back: 'fa-arrow-left', forward: 'fa-arrow-right', reload: 'fa-sync-alt', ai: 'fa-robot',
        save: 'fa-download', print: 'fa-print', cast: 'fa-tv', search: 'fa-search', reader: 'fa-book-open',
        device: 'fa-mobile-alt', qr: 'fa-qrcode', translate: 'fa-language', source: 'fa-code', inspect: 'fa-bug',
        copy: 'fa-copy', cut: 'fa-cut', paste: 'fa-paste', del: 'fa-trash-alt', undo: 'fa-undo', redo: 'fa-redo',
        selectAll: 'fa-i-cursor', link: 'fa-link', linkTab: 'fa-external-link-alt', window: 'fa-window-restore',
        split: 'fa-columns', image: 'fa-image', clone: 'fa-clone', lens: 'fa-search',
        play: 'fa-play', mute: 'fa-volume-mute', pip: 'fa-window-restore', fullscreen: 'fa-expand',
        moon: 'fa-moon', sun: 'fa-sun'
    };

    var ITEMS = {
        back: { id: 'back', label: 'Quay lại', icon: ICON.back, shortcut: 'Alt+←', run: contextMenuActions.goBack },
        forward: { id: 'forward', label: 'Tiến tới', icon: ICON.forward, shortcut: 'Alt+→', run: contextMenuActions.goForward },
        reload: { id: 'reload', label: 'Tải lại', icon: ICON.reload, shortcut: 'Ctrl+R', run: contextMenuActions.reload },

        askAi: {
            id: 'askAi', label: 'Hỏi AI', icon: ICON.ai,
            dynamic: function (ctx) {
                if (ctx.type === 'TEXT_SELECTION') return { label: 'Hỏi AI về “' + utils.shortText(ctx.selectedText, 26) + '”', run: function (c) { askAi(c, 'text'); } };
                if (ctx.type === 'LINK') return { label: 'Hỏi AI về liên kết này', run: function (c) { askAi(c, 'link'); } };
                if (ctx.type === 'IMAGE') return { label: 'Hỏi AI về hình ảnh', run: function (c) { askAi(c, 'image'); } };
                if (ctx.type === 'VIDEO' || ctx.type === 'AUDIO') return { label: 'Hỏi AI về media này', run: function (c) { askAi(c, c.type.toLowerCase()); } };
                if (ctx.type === 'INPUT' || ctx.type === 'TEXTAREA') return { label: 'Hỏi AI về ô nhập liệu', run: function (c) { askAi(c, 'element'); } };
                if (ctx.type === 'OTHER_ELEMENT' && ctx.elementText) return { label: 'Hỏi AI về nội dung này', run: function (c) { askAi(c, 'element'); } };
                return null;
            },
            run: function (ctx) { askAi(ctx, 'page'); }
        },

        savePage: {
            id: 'savePage', label: 'Lưu thành tệp (.html)', icon: ICON.save, shortcut: 'Ctrl+S',
            hint: 'Trình duyệt không cho trang web mở hộp thoại “Lưu thành…” — TuniX tải tệp HTML của trang này về máy.',
            run: contextMenuActions.savePage
        },
        print: { id: 'print', label: 'In…', icon: ICON.print, shortcut: 'Ctrl+P', run: contextMenuActions.print },
        cast: {
            id: 'cast', label: 'Truyền…', icon: ICON.cast, disabled: true,
            hint: 'Tính năng của trình duyệt (Chrome Cast) — trang web không thể gọi. Hãy dùng menu trình duyệt.'
        },
        sendDevice: {
            id: 'sendDevice', label: 'Gửi đến thiết bị của bạn', icon: ICON.device, disabled: true,
            hint: 'Tính năng của trình duyệt — trang web không thể gọi. Dùng “Tạo mã QR cho trang này” để mở trên điện thoại.'
        },
        searchPage: { id: 'searchPage', label: 'Tìm kiếm trên trang này bằng Google', icon: ICON.search, run: contextMenuActions.searchPage },
        searchSelection: {
            id: 'searchSelection', icon: ICON.search,
            dynamic: function (ctx) { return { label: 'Tìm kiếm Google cho “' + utils.shortText(ctx.selectedText, 26) + '”' }; },
            run: contextMenuActions.searchSelection
        },
        reader: { id: 'reader', label: 'Mở ở chế độ đọc', icon: ICON.reader, run: contextMenuActions.readerMode },
        qr: { id: 'qr', label: 'Tạo mã QR cho trang này', icon: ICON.qr, run: contextMenuActions.qrPage },
        translatePage: { id: 'translatePage', label: 'Dịch sang Tiếng Việt', icon: ICON.translate, run: contextMenuActions.translatePage },
        viewSource: {
            id: 'viewSource', label: 'Xem nguồn trang (HTML)', icon: ICON.source, shortcut: 'Ctrl+U',
            hint: 'Mở bản HTML của trang do TuniX tải về (trình duyệt không cho trang web mở view-source:).',
            run: contextMenuActions.viewSource
        },
        inspect: {
            id: 'inspect', label: 'Kiểm tra (DevTools)', icon: ICON.inspect, disabled: true,
            hint: 'Vì lý do bảo mật, trình duyệt KHÔNG cho trang web mở DevTools. Hãy dùng F12 / Ctrl+Shift+I.'
        },

        openLinkSame: { id: 'openLinkSame', label: 'Mở liên kết', icon: ICON.link, run: contextMenuActions.openLinkSame },
        openLinkTab: { id: 'openLinkTab', label: 'Mở liên kết trong tab mới', icon: ICON.linkTab, run: contextMenuActions.openLinkTab },
        openLinkWindow: { id: 'openLinkWindow', label: 'Mở liên kết trong cửa sổ mới', icon: ICON.window, run: contextMenuActions.openLinkWindow },
        openLinkSplit: { id: 'openLinkSplit', label: 'Mở liên kết ở dạng xem phân tách', icon: ICON.split, run: contextMenuActions.openLinkSplit },
        copyLink: { id: 'copyLink', label: 'Sao chép địa chỉ liên kết', icon: ICON.copy, run: contextMenuActions.copyLink },

        openImageTab: { id: 'openImageTab', label: 'Mở hình ảnh trong tab mới', icon: ICON.image, run: contextMenuActions.openImageTab },
        saveImage: { id: 'saveImage', label: 'Lưu hình ảnh', icon: ICON.save, run: contextMenuActions.saveImage },
        copyImage: { id: 'copyImage', label: 'Sao chép hình ảnh', icon: ICON.clone, run: contextMenuActions.copyImage },
        copyImageUrl: { id: 'copyImageUrl', label: 'Sao chép địa chỉ hình ảnh', icon: ICON.link, run: contextMenuActions.copyImageUrl },
        searchImage: { id: 'searchImage', label: 'Tìm kiếm hình ảnh bằng Google', icon: ICON.lens, run: contextMenuActions.searchImage },

        copySelection: { id: 'copySelection', label: 'Sao chép', icon: ICON.copy, shortcut: 'Ctrl+C', run: contextMenuActions.copySelection },
        translateSelection: {
            id: 'translateSelection',
            dynamic: function (ctx) { return { label: 'Dịch “' + utils.shortText(ctx.selectedText, 24) + '”' }; },
            icon: ICON.translate, run: contextMenuActions.translateSelection
        },
        selectAllPage: { id: 'selectAllPage', label: 'Chọn tất cả', icon: ICON.selectAll, shortcut: 'Ctrl+A', run: contextMenuActions.selectAllPage },
        toggleTheme: {
            id: 'toggleTheme', icon: ICON.moon,
            dynamic: function () {
                var dark = document.body.classList.contains('dark-mode');
                return { label: dark ? 'Chuyển giao diện sáng' : 'Chuyển giao diện tối', icon: dark ? ICON.sun : ICON.moon };
            },
            run: contextMenuActions.toggleTheme
        },

        undo: { id: 'undo', label: 'Hoàn tác', icon: ICON.undo, shortcut: 'Ctrl+Z', run: contextMenuActions.undo },
        redo: { id: 'redo', label: 'Làm lại', icon: ICON.redo, shortcut: 'Ctrl+Y', run: contextMenuActions.redo },
        cut: { id: 'cut', label: 'Cắt', icon: ICON.cut, shortcut: 'Ctrl+X', disabled: function (ctx) { return !ctx.hasInputSelection; }, run: contextMenuActions.cut },
        copyInput: { id: 'copyInput', label: 'Sao chép', icon: ICON.copy, shortcut: 'Ctrl+C', disabled: function (ctx) { return !ctx.hasInputSelection; }, run: contextMenuActions.copyInput },
        paste: { id: 'paste', label: 'Dán', icon: ICON.paste, shortcut: 'Ctrl+V', run: contextMenuActions.paste },
        del: { id: 'del', label: 'Xóa', icon: ICON.del, shortcut: 'Del', disabled: function (ctx) { return !ctx.hasInputSelection; }, run: contextMenuActions.del },
        selectAllInput: { id: 'selectAllInput', label: 'Chọn tất cả', icon: ICON.selectAll, shortcut: 'Ctrl+A', run: contextMenuActions.selectAllInput },

        mediaPlayPause: { id: 'mediaPlayPause', label: 'Phát / Tạm dừng', icon: ICON.play, run: contextMenuActions.mediaPlayPause },
        mediaMute: { id: 'mediaMute', label: 'Bật / Tắt tiếng', icon: ICON.mute, run: contextMenuActions.mediaMute },
        mediaPip: { id: 'mediaPip', label: 'Ảnh trong ảnh', icon: ICON.pip, run: contextMenuActions.mediaPip },
        mediaFullscreen: { id: 'mediaFullscreen', label: 'Toàn màn hình', icon: ICON.fullscreen, run: contextMenuActions.mediaFullscreen },
        copyMediaUrl: { id: 'copyMediaUrl', label: 'Sao chép địa chỉ media', icon: ICON.link, run: contextMenuActions.copyMediaUrl },
        saveMedia: { id: 'saveMedia', label: 'Lưu media', icon: ICON.save, run: contextMenuActions.saveMedia }
    };

    function SEP() { return { divider: true }; }

    /** Bảng ngữ cảnh → danh sách id item (thứ tự hiển thị). */
    var CONTEXT_MENU_MAP = {
        PAGE_BACKGROUND: ['back', 'forward', 'reload', SEP, 'askAi', SEP, 'savePage', 'print', 'cast', SEP,
            'searchPage', 'reader', SEP, 'sendDevice', 'qr', SEP, 'translatePage', SEP, 'viewSource', 'inspect', SEP, 'toggleTheme'],
        OTHER_ELEMENT: ['back', 'forward', 'reload', SEP, 'askAi', SEP, 'savePage', 'print', 'cast', SEP,
            'searchPage', 'reader', SEP, 'sendDevice', 'qr', SEP, 'translatePage', SEP, 'viewSource', 'inspect', SEP, 'toggleTheme'],
        LINK: ['openLinkSame', 'openLinkTab', 'openLinkWindow', 'openLinkSplit', SEP, 'copyLink', SEP,
            'reload', 'savePage', 'print', SEP, 'askAi'],
        IMAGE: ['openImageTab', 'saveImage', 'copyImage', 'copyImageUrl', SEP, 'searchImage', SEP, 'askAi'],
        TEXT_SELECTION: ['copySelection', SEP, 'searchSelection', 'askAi', 'translateSelection'],
        INPUT: ['undo', 'redo', SEP, 'cut', 'copyInput', 'paste', 'del', SEP, 'selectAllInput', SEP, 'askAi'],
        TEXTAREA: ['undo', 'redo', SEP, 'cut', 'copyInput', 'paste', 'del', SEP, 'selectAllInput', SEP, 'askAi'],
        VIDEO: ['mediaPlayPause', 'mediaMute', 'mediaPip', 'mediaFullscreen', SEP, 'copyMediaUrl', 'saveMedia', SEP, 'askAi'],
        AUDIO: ['mediaPlayPause', 'mediaMute', SEP, 'copyMediaUrl', 'saveMedia', SEP, 'askAi']
    };

    function buildContextMenu(ctx) {
        var out = [];
        var defs = CONTEXT_MENU_MAP[ctx.type] || CONTEXT_MENU_MAP.PAGE_BACKGROUND;
        for (var i = 0; i < defs.length; i++) {
            var d = defs[i];
            // `SEP` trong CONFIG_MENU_MAP là hàm; cũng chấp nhận {divider:true} trực tiếp
            if (typeof d === 'function' || (d && d.divider)) { out.push(SEP()); continue; }
            var base = ITEMS[d];
            if (!base) continue;
            var item = utils.assign({}, base);
            delete item.run;
            delete item.dynamic;
            var dyn = typeof base.dynamic === 'function' ? (base.dynamic(ctx) || {}) : {};
            item.label = dyn.label || item.label;
            item.icon = dyn.icon || item.icon;
            var run = typeof dyn.run === 'function' ? dyn.run : base.run;
            // `disabled` trong CONFIG có thể là boolean HOẶC hàm nhận ctx → LUÔN quy về boolean
            var disabled = typeof base.disabled === 'function' ? !!base.disabled(ctx) : !!base.disabled;
            if (typeof dyn.disabled === 'boolean') disabled = dyn.disabled;
            item.disabled = disabled;
            if (disabled) {
                item.action = (function (hint) {
                    return function () { contextMenuActions.disabledBrowserFeature(ctx, { hint: hint }); };
                })(item.hint || '');
            } else {
                item.action = (function (fn) { return function () { fn(ctx); }; })(run);
            }
            if (item.label) out.push(item);
        }
        // Mục mở rộng do từng trang đăng ký (API cũ) — đưa lên đầu menu
        for (var c = 0; c < customRules.length; c++) {
            var rule = customRules[c];
            try {
                if (!rule.rule(ctx.target, ctx.event)) continue;
                var extra = rule.factory(ctx.target, ctx.event) || [];
                if (!extra.length) continue;
                var block = extra.filter(Boolean);
                block.push(SEP());
                out = block.concat(out);
            } catch (err) { /* ignore rule lỗi */ }
        }
        return out;
    }

    /* ══════════════════════════════════════════════════════════════════
     * 6) ContextMenu — render + vị trí + bàn phím (a11y)
     * ══════════════════════════════════════════════════════════════════ */
    var menuEl = null;
    var itemEls = [];
    var activeIndex = -1;
    var customRules = [];
    var lastFocus = null;

    function ContextMenuSeparator() {
        var sep = document.createElement('div');
        sep.className = 'tunix-ctx-sep';
        sep.setAttribute('role', 'separator');
        return sep;
    }

    function ContextMenuItem(item, onActivate) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tunix-ctx-item' + (item.disabled ? ' is-disabled' : '');
        btn.setAttribute('role', 'menuitem');
        btn.setAttribute('tabindex', '-1');
        if (item.disabled) btn.setAttribute('aria-disabled', 'true');
        if (item.hint) btn.title = item.hint;
        btn.innerHTML = '<i class="fa-solid ' + esc(item.icon || 'fa-circle') + '" aria-hidden="true"></i>' +
            '<span class="tunix-ctx-label">' + esc(item.label) + '</span>' +
            (item.shortcut ? '<span class="tunix-ctx-kbd">' + esc(item.shortcut) + '</span>' : '');
        if (!item.disabled) {
            itemEls.push(btn);                       // đăng ký để điều hướng bằng bàn phím
            btn.addEventListener('click', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                onActivate(item);
            });
            btn.addEventListener('pointerenter', function () { setActive(itemEls.indexOf(btn)); });
        } else {
            // Item bị vô hiệu (tính năng trình duyệt không cho làm từ web) → báo LÝ DO, giữ menu mở
            btn.addEventListener('click', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                if (item.action) item.action();
            });
        }
        return btn;
    }

    function setActive(i) {
        if (!menuEl) return;
        if (activeIndex >= 0 && itemEls[activeIndex]) itemEls[activeIndex].classList.remove('is-active');
        activeIndex = i;
        if (itemEls[i]) {
            itemEls[i].classList.add('is-active');
            try { itemEls[i].scrollIntoView({ block: 'nearest' }); } catch (err) { /* ignore */ }
        }
    }

    /** Vị trí: ưu tiên dưới-phải con trỏ, tự lật sang trái/trên, luôn nằm trong viewport. */
    function fitMenu(x, y) {
        if (!menuEl) return;
        var pad = 8;
        var vw = document.documentElement.clientWidth || window.innerWidth;
        var vh = document.documentElement.clientHeight || window.innerHeight;
        // Dùng offsetWidth/Height (kích thước LAYOUT) — không bị ảnh hưởng bởi animation scale(.97)
        // như getBoundingClientRect, nên vị trí không bị lệch ~3%.
        var w = menuEl.offsetWidth || 300;
        var h = menuEl.offsetHeight || 300;
        var left = x;
        var top = y;
        if (left + w + pad > vw) left = x - w;      // lật sang trái con trỏ
        if (top + h + pad > vh) top = y - h;        // lật lên trên con trỏ
        left = utils.clamp(left, pad, Math.max(pad, vw - w - pad));
        top = utils.clamp(top, pad, Math.max(pad, vh - h - pad));
        menuEl.style.left = Math.round(left) + 'px';
        menuEl.style.top = Math.round(top) + 'px';
    }

    function placeMenu(x, y) {
        if (!menuEl) return;
        var vh = document.documentElement.clientHeight || window.innerHeight;
        menuEl.style.visibility = 'hidden';
        menuEl.style.left = '0px';
        menuEl.style.top = '0px';
        menuEl.style.maxHeight = Math.max(140, vh - 16) + 'px';
        fitMenu(x, y);
        menuEl.style.visibility = 'visible';
        // Font/icon của trang có thể load MUỘN làm kích thước menu đổi sau khi đặt vị trí
        // → căn lại ở frame kế tiếp và mỗi khi menu bị đổi kích thước (tránh tràn viewport).
        try { requestAnimationFrame(function () { fitMenu(x, y); }); } catch (err) { /* ignore */ }
        if (typeof ResizeObserver !== 'undefined') {
            try {
                var ro = new ResizeObserver(function () { fitMenu(x, y); });
                ro.observe(menuEl);
                menuEl.__ro = ro;
            } catch (err) { /* ignore */ }
        }
    }

    function openExport(items, x, y, ctx) {
        // Không bao giờ có 2 menu cùng lúc
        removeMenu();
        ensureStyle();
        var el = document.createElement('div');
        el.className = MENU_CLASS;
        el.setAttribute('role', 'menu');
        el.setAttribute('aria-label', 'Menu chuột phải TuniX');
        el.setAttribute('tabindex', '-1');
        itemEls = [];
        activeIndex = -1;
        var list = (items || []).filter(Boolean);
        for (var i = 0; i < list.length; i++) {
            var it = list[i];
            if (it.divider) { el.appendChild(ContextMenuSeparator()); continue; }
            if (it.visible === false) continue;
            el.appendChild(ContextMenuItem(it, activate));
        }
        document.body.appendChild(el);
        menuEl = el;
        menuEl.__ctx = ctx || null;
        placeMenu(x, y);
        try { lastFocus = document.activeElement; } catch (err) { lastFocus = null; }
        try { el.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
    }

    function activate(item) {
        var ctx = (menuEl && menuEl.__ctx) || null;
        // Đóng TRƯỚC khi chạy action (theo yêu cầu: menu không được tồn tại sau khi action xong)
        removeMenu();
        try { if (item && item.action) item.action(ctx); } catch (err) { console.warn('[context-menu]', err); }
    }

    function removeMenu() {
        if (menuEl) {
            try { if (menuEl.__ro) menuEl.__ro.disconnect(); } catch (err) { /* ignore */ }
            try { menuEl.remove(); } catch (err) { /* ignore */ }
        }
        menuEl = null;
        itemEls = [];
        activeIndex = -1;
    }

    function close() {
        removeMenu();
    }

    /* ══════════════════════════════════════════════════════════════════
     * 7) Sự kiện toàn cục + useContextMenu (install/cleanup)
     * ══════════════════════════════════════════════════════════════════ */
    var installed = false;

    function onCtxCapture() { if (menuEl) close(); }   // pha capture: đóng menu CŨ trước tiên

    function onContextMenu(e) {
        // Thành phần khác đã xử lý (vd menu drawing của StockDetail) → nhường
        if (e.defaultPrevented) return;
        // Chuột phải trên chính menu/modal của TuniX → không mở menu mới
        var t = e.target;
        if (t && t.closest && (t.closest('.' + MENU_CLASS) || t.closest('.tunix-ctx-modal'))) {
            e.preventDefault();
            return;
        }
        e.preventDefault();                     // chặn menu mặc định của trình duyệt
        var ctx = detectContext(e);
        ctx.event = e;
        var list = buildContextMenu(ctx);
        if (!list.length) return;
        // Ô nhập liệu: focus để các lệnh soạn thảo (cut/copy/paste/undo) tác động đúng chỗ
        if ((ctx.type === 'INPUT' || ctx.type === 'TEXTAREA') && ctx.editableEl) {
            try { ctx.editableEl.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
        }
        openExport(list, e.clientX, e.clientY, ctx);
    }

    function onPointerDown(e) {
        if (!menuEl) return;
        if (e.button === 2) { close(); return; }               // chuột phải lần nữa → đóng
        if (e.target && menuEl.contains(e.target)) return;     // click trong menu → giữ
        close();                                               // click trái bên ngoài → đóng
    }

    function onKeyDown(e) {
        // ESC/Bàn phím chỉ áp dụng khi menu ĐANG MỞ (kể cả menu chỉ có item bị vô hiệu)
        if (menuEl) {
            if (e.key === 'Escape') { e.preventDefault(); close(); restoreFocus(); return; }
            if (!itemEls.length) return;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                var dir = e.key === 'ArrowDown' ? 1 : -1;
                var next = activeIndex + dir;
                if (next >= itemEls.length) next = 0;
                if (next < 0) next = itemEls.length - 1;
                setActive(next);
                return;
            }
            if (e.key === 'Home' || e.key === 'End') { e.preventDefault(); setActive(e.key === 'Home' ? 0 : itemEls.length - 1); return; }
            if (e.key === 'Tab') { e.preventDefault(); setActive(activeIndex < 0 ? 0 : (activeIndex + (e.shiftKey ? -1 : 1) + itemEls.length) % itemEls.length); return; }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                if (activeIndex >= 0 && activeIndex < itemEls.length) itemEls[activeIndex].click();
            }
            return;
        }
        // Phím tắt toàn cục của TuniX (KHÔNG đụng tới shortcut mặc định khác của trình duyệt)
        if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
            if (e.key === 'ArrowLeft') { e.preventDefault(); goBackPage(); return; }
            if (e.key === 'ArrowRight') { e.preventDefault(); goForwardPage(); return; }
        }
    }

    function restoreFocus() {
        try { if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
    }

    function onBlur() { close(); }
    function onVisibility() { if (document.hidden) close(); }

    function useContextMenu() {
        if (installed) return;
        installed = true;
        ensureStyle();
        document.addEventListener('contextmenu', onCtxCapture, true);
        document.addEventListener('contextmenu', onContextMenu, false);
        window.addEventListener('pointerdown', onPointerDown, true);
        window.addEventListener('keydown', onKeyDown, true);
        window.addEventListener('blur', onBlur);
        window.addEventListener('resize', onBlur);
        window.addEventListener('scroll', onBlur, true);
        document.addEventListener('visibilitychange', onVisibility);
    }

    function uninstall() {
        if (!installed) return;
        installed = false;
        document.removeEventListener('contextmenu', onCtxCapture, true);
        document.removeEventListener('contextmenu', onContextMenu, false);
        window.removeEventListener('pointerdown', onPointerDown, true);
        window.removeEventListener('keydown', onKeyDown, true);
        window.removeEventListener('blur', onBlur);
        window.removeEventListener('resize', onBlur);
        window.removeEventListener('scroll', onBlur, true);
        document.removeEventListener('visibilitychange', onVisibility);
        closeQrModal();
        removeMenu();
    }

    function register(rule, factory) {
        if (typeof rule !== 'function') return;
        customRules.push({ rule: rule, factory: typeof factory === 'function' ? factory : function () { return []; } });
    }

    /* ══════════════════════════════════════════════════════════════════
     * 8) API công khai + khởi tạo
     * ══════════════════════════════════════════════════════════════════ */
    window.TuniXContextMenu = {
        version: VERSION,
        install: useContextMenu,
        uninstall: uninstall,
        open: function (items, x, y) { openExport(items, x, y, null); },
        close: close,
        register: register,
        detectContext: detectContext,
        buildContextMenu: buildContextMenu,
        openQr: showQrModal,
        // nhóm module nội bộ (đặt tên theo kiến trúc yêu cầu)
        ContextMenu: { open: openExport, close: close, activate: activate },
        ContextMenuItem: ContextMenuItem,
        ContextMenuSeparator: ContextMenuSeparator,
        useContextMenu: useContextMenu,
        contextMenuActions: contextMenuActions,
        contextMenuUtils: contextMenuUtils,
        config: { ITEMS: ITEMS, CONTEXT_MENU_MAP: CONTEXT_MENU_MAP, ICON: ICON }
    };

    // ⚠️ KHÔNG tự bật: mặc định dùng menu ngầm định của TRÌNH DUYỆT (user chốt 2026-09-28).
    // Bật lại menu TuniX bằng `window.TUNIX_CONTEXT_MENU_AUTO = true` (đặt trước khi nạp file)
    // hoặc `window.TuniXContextMenu.install()` sau khi trang đã tải.
    if (window.TUNIX_CONTEXT_MENU_AUTO === true) useContextMenu();
})();
