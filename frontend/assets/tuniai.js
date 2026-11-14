        (function() {
            const sugg = [
                { text: 'Phân tích VCI trong 6 tháng tới', q: 'Phân tích VCI trong 6 tháng tới' },
                { text: 'So sánh ACB và MBB', q: 'So sánh ACB và MBB' },
                { text: 'Tìm cổ phiếu có ROE cao và định giá thấp', q: 'Tìm cổ phiếu có ROE cao và định giá thấp' },
                { text: 'Thị trường hôm nay đang mạnh hay yếu?', q: 'Thị trường hôm nay đang mạnh hay yếu?' },
                { text: 'Phân tích danh mục của tôi', q: 'Phân tích danh mục của tôi' }
            ];
            const tools = [
                { p: 'Phân tích chuyên sâu cổ phiếu', icon: 'fa-chart-line', title: 'AI Stock', tag: 'Phân tích' },
                { p: 'So sánh doanh nghiệp', icon: 'fa-balance-scale', title: 'AI Compare', tag: 'So sánh' },
                { p: 'Tìm cổ phiếu bằng ngôn ngữ tự nhiên', icon: 'fa-sliders-h', title: 'AI Screener', tag: 'Bộ lọc' },
                { p: 'Phân tích danh mục của tôi', icon: 'fa-briefcase', title: 'AI Portfolio', tag: 'Danh mục' }
            ];

            // Text hiển thị trong khung chat khi VinaF AI đang xử lý/tìm kiếm thông tin
            // (chỉ hiện khi chưa trả được nội dung; khi có câu trả lời sẽ bị thay thế tự động).
            const WAITING_HTML = '<span class="ta-wait"><i class="fas fa-spinner fa-spin"></i> Xin chờ chút để VinaF AI xử lý thông tin...</span>';

            // ---------- Quản lý hội thoại (đồng bộ server theo tài khoản) ----------
            const CONV_KEY = 'vinafintech_ai_conversations';
            const API_CONV = '/api/ai/conversations';
            const msgsEl = document.getElementById('taMessages');
            const historyEl = document.getElementById('taHistory');
            const titleEl = document.getElementById('taChatTitle');
            let conversations = [];
            let currentId = null;
            let busy = false;

            function loggedIn() { return !!localStorage.getItem('vinafintech_access_token'); }

            // ---------- Trang bị khóa (yêu cầu đăng nhập) ----------
            function showLocked() {
                const page = document.querySelector('.ta-page');
                if (!page) return;
                page.innerHTML =
                    '<div class="ta-locked">' +
                        '<div class="ta-lock-icon"><i class="fas fa-lock"></i></div>' +
                        '<h2>Trang bị khóa</h2>' +
                        '<p>Trang VinaF AI yêu cầu đăng nhập để sử dụng trợ lý tài chính thông minh.</p>' +
                        '<div class="ta-lock-actions">' +
                            '<a class="ta-lock-btn primary" href="/Login?next=' + encodeURIComponent('/vinafai') + '" target="_top"><i class="fas fa-sign-in-alt"></i> Đăng nhập</a>' +
                            '<a class="ta-lock-btn" href="/register" target="_top"><i class="fas fa-user-plus"></i> Tạo tài khoản</a>' +
                        '</div>' +
                    '</div>';
                notifyParentHeight();
            }

            function authHeaders() {
                return {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + (localStorage.getItem('vinafintech_access_token') || '')
                };
            }

            // Fetch có tự refresh token khi hết hạn (thử 1 lần)
            async function serverFetch(url, opts) {
                let res = await fetch(url, opts);
                if (res.status === 401) {
                    const refresh = localStorage.getItem('vinafintech_refresh_token');
                    if (refresh) {
                        try {
                            const rr = await fetch('/api/auth/refresh-token', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ refresh_token: refresh })
                            });
                            const d = await rr.json();
                            if (d.access_token) {
                                localStorage.setItem('vinafintech_access_token', d.access_token);
                                if (d.refresh_token) localStorage.setItem('vinafintech_refresh_token', d.refresh_token);
                                if (d.user) localStorage.setItem('vinafintech_user', JSON.stringify(d.user));
                                opts = Object.assign({}, opts, { headers: authHeaders() });
                                res = await fetch(url, opts);
                            }
                        } catch (e) {}
                    }
                }
                return res;
            }

            function loadConvs() {
                try {
                    const raw = JSON.parse(localStorage.getItem(CONV_KEY) || '[]');
                    if (Array.isArray(raw)) conversations = raw;
                } catch (e) { conversations = []; }
            }
            function saveConvs() {
                try { localStorage.setItem(CONV_KEY, JSON.stringify(conversations.slice(-50))); } catch (e) {}
                scheduleServerSync();
            }

            let _syncTimer = null;
            function scheduleServerSync() {
                if (!loggedIn()) return;
                if (_syncTimer) clearTimeout(_syncTimer);
                _syncTimer = setTimeout(syncAllToServer, 800);
            }
            async function syncAllToServer() {
                if (!loggedIn()) return;
                const list = conversations.slice(0, 50);
                for (let i = 0; i < list.length; i++) {
                    const c = list[i];
                    if (!c.messages || !c.messages.length) continue; // chỉ lưu hội thoại có nội dung
                    try {
                        await serverFetch(API_CONV + '/' + encodeURIComponent(c.id), {
                            method: 'PUT',
                            headers: authHeaders(),
                            body: JSON.stringify({ title: convTitleOf(c), messages: c.messages })
                        });
                    } catch (e) {}
                }
            }
            // Tải lịch sử từ server về (theo tài khoản đã đăng nhập)
            async function loadServerConvs() {
                try {
                    const res = await serverFetch(API_CONV, { headers: authHeaders() });
                    if (!res.ok) return false;
                    const list = await res.json();
                    if (Array.isArray(list)) {
                        conversations = list.map(function(x) {
                            return {
                                id: x.id,
                                updated_at: x.updated_at ? new Date(x.updated_at).getTime() : Date.now(),
                                title: (x.title && String(x.title).trim()) ? String(x.title).trim() : undefined,
                                messages: Array.isArray(x.messages) ? x.messages : []
                            };
                        });
                        try { localStorage.setItem(CONV_KEY, JSON.stringify(conversations.slice(-50))); } catch (e) {}
                        return true;
                    }
                } catch (e) {}
                return false;
            }
            function currentConv() {
                return conversations.find(function(c) { return c.id === currentId; }) || null;
            }
            // Tiêu đề hiển thị: ưu tiên tên NGƯỜI DÙNG tự đổi (c.title), nếu chưa có mới lấy tự động
            // từ câu hỏi đầu tiên trong hội thoại.
            function convTitleOf(c) {
                if (c && c.title && String(c.title).trim()) return String(c.title).trim();
                const first = (c.messages || []).find(function(m) { return m.role === 'user'; });
                if (first && first.content) {
                    const t = String(first.content).trim();
                    return t.length > 42 ? t.slice(0, 42) + '…' : t;
                }
                return 'Cuộc trò chuyện mới';
            }

            function newChat() {
                const c = { id: 'c' + Date.now(), updated_at: Date.now(), messages: [] };
                conversations.unshift(c);
                currentId = c.id;
                saveConvs();
                renderHistory();
                renderMessages();
            }
            function openConv(id) {
                const c = conversations.find(function(x) { return x.id === id; });
                if (!c) return;
                currentId = id;
                renderHistory();
                renderMessages();
            }
            function deleteConv(id) {
                conversations = conversations.filter(function(c) { return c.id !== id; });
                if (currentId === id) {
                    currentId = null;
                    if (conversations.length) currentId = conversations[0].id;
                }
                saveConvs();
                if (loggedIn()) {
                    serverFetch(API_CONV + '/' + encodeURIComponent(id), { method: 'DELETE', headers: authHeaders() }).catch(function() {});
                }
                renderHistory();
                renderMessages();
            }

            // Đổi tên 1 cuộc trò chuyện trong danh sách Lịch sử (sửa inline bằng ô nhập).
            function startRenameConv(id) {
                const c = conversations.find(function(x) { return x.id === id; });
                if (!c) return;
                const item = historyEl && historyEl.querySelector('.ta-h-item[data-id="' + id + '"]');
                if (!item || item.classList.contains('editing')) return;
                const span = item.querySelector('.ta-h-title');
                if (!span) return;
                const oldTitle = convTitleOf(c);
                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'ta-h-input';
                input.maxLength = 80;
                input.value = oldTitle;
                input.setAttribute('aria-label', 'Tiêu đề cuộc trò chuyện');
                span.replaceWith(input);
                item.classList.add('editing');
                input.focus();
                input.select();
                let finished = false;
                function finish(save) {
                    if (finished) return;
                    finished = true;
                    const val = input.value.replace(/\s+/g, ' ').trim();
                    if (save) {
                        if (val && val !== oldTitle) {
                            c.title = val;          // tên người dùng tự đặt
                        } else if (!val) {
                            delete c.title;          // xoá trắng → về tiêu đề tự động
                        }
                    }
                    const newSpan = document.createElement('span');
                    newSpan.className = 'ta-h-title';
                    newSpan.textContent = convTitleOf(c);
                    item.classList.remove('editing');
                    input.replaceWith(newSpan);
                    if (currentId === c.id && titleEl) titleEl.textContent = convTitleOf(c);
                    saveConvs(); // lưu local + đồng bộ title mới lên server
                }
                input.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
                    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
                });
                input.addEventListener('blur', function() { finish(true); });
            }

            function escChat(s) {
                return String(s == null ? '' : s)
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
            }

            function scrollChat() { if (msgsEl) msgsEl.scrollTop = msgsEl.scrollHeight; }

            // Giải mã thực thể HTML mà model lỡ xuất ra (VD: &quot; → " , &amp; → & , &lt; → < ...).
            // Giải mã &amp; trước để cả chuỗi bị escape 2 lần (&amp;quot;) cũng về đúng dấu ".
            function decodeAiEntities(s) {
                return String(s == null ? '' : s)
                    .replace(/&amp;/g, '&')
                    .replace(/&quot;/g, '"').replace(/&#34;/g, '"')
                    .replace(/&#39;/g, "'").replace(/&apos;/g, "'")
                    .replace(/&lt;/g, '<').replace(/&#60;/g, '<')
                    .replace(/&gt;/g, '>').replace(/&#62;/g, '>')
                    .replace(/&nbsp;/g, ' ')
                    .replace(/&ndash;/g, '\u2013')
                    .replace(/&mdash;/g, '\u2014')
                    .replace(/&hellip;/g, '\u2026')
                    .replace(/&#x([0-9a-fA-F]+);/g, function(m, h) {
                        const c = parseInt(h, 16);
                        return (c > 0 && c <= 0x10FFFF) ? String.fromCodePoint(c) : m;
                    })
                    .replace(/&#(\d+);/g, function(m, d) {
                        const c = parseInt(d, 10);
                        return (c > 0 && c <= 0x10FFFF) ? String.fromCodePoint(c) : m;
                    });
            }

            // Chuyển thẻ HTML lỡ xuất ra (<strong>, <br>, <li>, <h3>...) thành Markdown / xuống dòng,
            // để không hiển thị lộn xộn như "<strong>…</strong>" hay "&quot;".
            function aiTagsToMarkdown(s) {
                return String(s == null ? '' : s)
                    .replace(/<\/(p|div|li|ul|ol|h[1-6]|blockquote|table|tr|section|article|pre)>/gi, '\n')
                    .replace(/<(p|div|ul|ol|table|section|article|blockquote|pre)\b[^>]*>/gi, '\n')
                    .replace(/<(br|hr)\s*\/?>/gi, '\n')
                    .replace(/<li\b[^>]*>/gi, '\n- ')
                    .replace(/<tr\b[^>]*>/gi, '\n')
                    .replace(/<(h[1-6])\b[^>]*>/gi, function(m, tag) {
                        const n = Math.min(parseInt(tag.charAt(1), 10), 3);
                        return '\n' + new Array(n + 1).join('#') + ' ';
                    })
                    .replace(/<\/(strong|b)>/gi, '**')
                    .replace(/<(strong|b)\b[^>]*>/gi, '**')
                    .replace(/<\/(td|th)>/gi, ' | ')
                    .replace(/<\/?[a-zA-Z][^>]*>/g, '');
            }

            // ---------- Bảng Markdown (| cột | ...) ----------
            // Dòng phân tách kiểu: | --- | :---: | ---: |
            // (DeepSeek hay xuất THIẾU "|" ở đầu/cuối dòng → vẫn nhận diện được "--- | --- | ---").
            function isTableSep(line) {
                const tr = String(line || '').trim();
                return /^[\s|:\-]+$/.test(tr) && tr.indexOf('-') >= 0 && tr.indexOf('|') >= 0;
            }
            // Cắt 1 hàng "| a | b |" (hoặc "a | b") → ['a','b']
            function splitTableRow(line) {
                return String(line || '').trim()
                    .replace(/^\|/, '').replace(/\|$/, '')
                    .split('|')
                    .map(function(c) { return String(c == null ? '' : c).trim(); });
            }
            // Dựng <table> an toàn (nội dung ô đã được escape + render markdown inline từ trước).
            // TỰ CÂN CHỈNH CỘT: AI hay quên cột "chỉ tiêu" ở header (VD header chỉ có "PC1 | TV2"
            // nhưng mỗi dòng 3 ô "Giá trị cuối kỳ | ~593 triệu | ~430-450 triệu") → chèn header trống
            // phía trước để các cột thẳng hàng, hết bị lệch cột.
            function buildTableHtml(headCells, bodyRows) {
                const rawHead = (headCells || []).map(function(c) { return String(c == null ? '' : c).trim(); });
                // headCount tính theo ô KHÔNG rỗng (dùng cho việc bẻ 4→2 ô như bảng "Chỉ tiêu|Giá trị").
                const headCount = rawHead.filter(function(c) { return c !== ''; }).length;
                // Nếu hàng dữ liệu có GẤP BỘI số cột header (VD header 2 cột mà hàng 4 ô:
                // "Giá | 72.800 đ | P/E | 12,34") → tách thành nhiều hàng 2 cột cho dễ đọc.
                const rows = [];
                (bodyRows || []).forEach(function(r) {
                    if (!r || !r.length) return;
                    const cells = r.map(function(c) { return String(c == null ? '' : c).trim(); });
                    if (headCount >= 2 && cells.length > headCount && cells.length % headCount === 0) {
                        for (let k = 0; k < cells.length; k += headCount) rows.push(cells.slice(k, k + headCount));
                    } else {
                        rows.push(cells);
                    }
                });
                // Số cột thật = số ô PHỔ BIẾN NHẤT ở các hàng dữ liệu (tránh lệch khi hàng thiếu ô).
                let cols = 0;
                const freq = {};
                rows.forEach(function(r) {
                    if (!r || !r.length) return;
                    freq[r.length] = (freq[r.length] || 0) + 1;
                    if ((freq[r.length] || 0) > (freq[cols] || 0)) cols = r.length;
                });
                // Header ghép đủ số cột: nếu hàng dữ liệu NHIỀU HƠN header (hay gặp: thừa cột chỉ tiêu đầu)
                // → chèn header trống vào ĐẦU để các ô chỉ tiêu nằm đúng cột riêng, không lệch.
                const head = rawHead.slice();
                if (head.length && cols > head.length) {
                    for (let k = head.length; k < cols; k++) head.unshift('');
                }
                let html = '<div class="ta-table-wrap"><table class="ta-table">';
                if (head.length) {
                    html += '<thead><tr>' + head.map(function(c) { return '<th>' + (c || '') + '</th>'; }).join('') + '</tr></thead>';
                }
                if (rows.length) {
                    html += '<tbody>';
                    rows.forEach(function(r) {
                        html += '<tr>' + r.map(function(c) { return '<td>' + (c || '') + '</td>'; }).join('') + '</tr>';
                    });
                    html += '</tbody>';
                }
                html += '</table></div>';
                return html;
            }

            // ---------- Biểu đồ trong câu trả lời (tròn / cột) ----------
            const CHART_COLORS = ['#008080', '#f39c12', '#4caf50', '#e74c3c', '#9b59b6', '#3498db', '#f1c40f', '#1abc9c', '#e67e22', '#7f8c8d'];
            function escSvg(s) { return escChat(s); }
            // Chỉ chấp nhận màu an toàn (HEX / tên màu) tránh injection vào thuộc tính SVG.
            function safeColor(c) {
                const v = String(c == null ? '' : c).trim();
                if (/^#[0-9a-f]{3}([0-9a-f]{3})?([0-9a-f]{2})?$/i.test(v)) return v;
                if (/^[a-zA-Z]{3,24}$/.test(v)) return v;
                return '';
            }
            function numArr(v) {
                if (!Array.isArray(v)) return [];
                return v.map(function(x) { const n = Number(x); return isFinite(n) ? n : 0; });
            }
            // Phân tích 1 nhãn thành mốc thời gian để sắp xếp. Trả về {kind, key} hoặc null.
            // Hỗ trợ: Q1/2026 · Q1-2026 · 2026-Q1 · Quý 1/2026 · Quý I/2026 · H1/2026
            //         T1/2026 · Tháng 1/2026 · 01/2026 · 2026-01 · 2025 (năm).
            function parseTimeLabel(label) {
                const s = String(label == null ? '' : label).trim();
                if (!s) return null;
                const ROMAN = { i: 1, ii: 2, iii: 3, iv: 4 };
                let m;
                if ((m = s.match(/^Q\s*([1-4])\s*[\/\-.]\s*(20\d{2})$/i))) return { kind: 'q', key: (+m[2]) * 10 + (+m[1]) };
                if ((m = s.match(/^(20\d{2})\s*[\/\-.]\s*Q\s*([1-4])$/i))) return { kind: 'q', key: (+m[1]) * 10 + (+m[2]) };
                if ((m = s.match(/^Q\s*([1-4])\s+(20\d{2})$/i))) return { kind: 'q', key: (+m[2]) * 10 + (+m[1]) };
                if ((m = s.match(/^Qu[ýy]\s*([1-4IVX]+)\s*[\/\-.]?\s*(20\d{2})$/i))) {
                    const q = ROMAN[m[1].toLowerCase()] || parseInt(m[1], 10);
                    if (q >= 1 && q <= 4) return { kind: 'q', key: (+m[2]) * 10 + q };
                    return null;
                }
                if ((m = s.match(/^H\s*([12])\s*[\/\-.]?\s*(20\d{2})$/i))) return { kind: 'h', key: (+m[2]) * 10 + (+m[1]) };
                if ((m = s.match(/^[Tt](?:h[áa]ng)?\s*(1[0-2]|[1-9])\s*[\/\-.]\s*(20\d{2})$/))) return { kind: 'm', key: (+m[2]) * 100 + (+m[1]) };
                if ((m = s.match(/^(1[0-2]|0?[1-9])\s*[\/\-. ]\s*(20\d{2})$/))) return { kind: 'm', key: (+m[2]) * 100 + (+m[1]) };
                if ((m = s.match(/^(20\d{2})\s*[\/\-.]\s*(1[0-2]|0[1-9])$/))) return { kind: 'm', key: (+m[1]) * 100 + (+m[2]) };
                if ((m = s.match(/^(20\d{2})$/))) return { kind: 'y', key: (+m[1]) * 100 };
                return null;
            }
            // Nếu TẤT CẢ nhãn là chuỗi thời gian cùng loại → trả về hoán vị chỉ số theo CŨ → MỚI.
            // Trả về null khi nhãn không phải thời gian hoặc đã đúng thứ tự.
            function timeOrderIdx(labels) {
                if (!Array.isArray(labels) || labels.length < 2) return null;
                const parsed = [];
                let kind = '';
                for (let i = 0; i < labels.length; i++) {
                    const p = parseTimeLabel(labels[i]);
                    if (!p) return null;
                    if (!kind) kind = p.kind;
                    else if (kind !== p.kind) return null;
                    parsed.push({ idx: i, key: p.key });
                }
                let ordered = true;
                for (let i = 1; i < parsed.length; i++) {
                    if (parsed[i - 1].key > parsed[i].key) { ordered = false; break; }
                }
                if (ordered) return null;
                parsed.sort(function(a, b) { return a.key - b.key; });
                return parsed.map(function(x) { return x.idx; });
            }
            function fmtCompact(v) {
                const abs = Math.abs(v);
                if (abs >= 1e12) return (v / 1e12).toFixed(2).replace(/\.?0+$/, '') + 'T';
                if (abs >= 1e9) return (v / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B';
                if (abs >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
                if (abs >= 1e3) return (v / 1e3).toFixed(0) + 'k';
                return String(Math.round(v * 100) / 100);
            }
            function pieSvgHtml(cfg) {
                const raw = Array.isArray(cfg.data) ? cfg.data : (Array.isArray(cfg.items) ? cfg.items : []);
                const items = [];
                raw.forEach(function(x, xi) {
                    const v = Number(x && x.value);
                    if (x && isFinite(v) && v > 0) {
                        items.push({
                            label: String(x.label == null ? '' : x.label),
                            value: v,
                            color: safeColor(x.color) || CHART_COLORS[xi % CHART_COLORS.length]
                        });
                    }
                });
                if (!items.length) return '';
                const total = items.reduce(function(s, x) { return s + x.value; }, 0);
                const size = 190, cx = 95, cy = 95, r = 80;
                let paths = '';
                let a = -Math.PI / 2;
                items.forEach(function(it) {
                    const ang = it.value / total * Math.PI * 2;
                    const large = ang > Math.PI ? 1 : 0;
                    const x1 = cx + r * Math.cos(a), y1 = cy + r * Math.sin(a);
                    const x2 = cx + r * Math.cos(a + ang), y2 = cy + r * Math.sin(a + ang);
                    paths += '<path d="M' + cx + ',' + cy + ' L' + x1.toFixed(2) + ',' + y1.toFixed(2) +
                        ' A' + r + ',' + r + ' 0 ' + large + ' 1 ' + x2.toFixed(2) + ',' + y2.toFixed(2) + ' Z" fill="' + it.color + '"></path>';
                    a += ang;
                });
                const legend = items.map(function(it) {
                    const pct = (it.value / total * 100).toFixed(1).replace('.', ',');
                    return '<div class="tcl-row"><span class="tcl-swatch" style="background:' + it.color + '"></span><span class="tcl-name">' + escSvg(it.label) + '</span><span class="tcl-val">' + pct + '%</span></div>';
                }).join('');
                return '<div class="ta-chart tc-pie"><div class="tc-ctitle">' + escSvg(cfg.title || '') + '</div>' +
                    '<div class="tc-cbody"><svg viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="' + escSvg(cfg.title || 'Biểu đồ tròn') + '">' + paths + '</svg>' +
                    '<div class="tcl-legend">' + legend + '</div></div></div>';
            }
            function barSvgHtml(cfg) {
                const labels = (Array.isArray(cfg.labels) ? cfg.labels : []).map(function(x) { return String(x == null ? '' : x); });
                let series = [];
                if (Array.isArray(cfg.series)) {
                    series = cfg.series.map(function(s, si) {
                        return {
                            name: String(s && s.name == null ? '' : s.name),
                            values: numArr(s && s.values),
                            color: safeColor(s && s.color) || CHART_COLORS[si % CHART_COLORS.length]
                        };
                    });
                } else if (Array.isArray(cfg.values)) {
                    series = [{ name: String(cfg.seriesName || 'Giá trị'), values: numArr(cfg.values), color: safeColor(cfg.color) || CHART_COLORS[0] }];
                }
                series = series.filter(function(s) { return s.values.length; });
                // Biểu đồ theo THỜI GIAN (quý/năm/tháng) → luôn xếp CŨ NHẤT bên trái, MỚI NHẤT bên phải.
                const chrono = timeOrderIdx(labels);
                if (chrono) {
                    const nl = chrono.map(function(i) { return labels[i]; });
                    labels.length = 0;
                    for (let i = 0; i < nl.length; i++) labels.push(nl[i]);
                    series.forEach(function(s) {
                        if (s.values.length === chrono.length) {
                            s.values = chrono.map(function(i) { return s.values[i]; });
                        }
                    });
                }
                if (!labels.length || !series.length) return '';
                const n = Math.min(labels.length, Math.max.apply(null, series.map(function(s) { return s.values.length; })));
                const H = 230, padL = 52, padR = 16, padT = 18, padB = 46;
                const slot = Math.max(72, 44 + series.length * 18);
                const W = padL + padR + n * slot;
                let max = 0;
                for (let i = 0; i < n; i++) {
                    for (let si = 0; si < series.length; si++) {
                        const v = Math.abs(series[si].values[i] || 0);
                        if (v > max) max = v;
                    }
                }
                if (max <= 0) return '';
                max = max * 1.18;
                const baseY = H - padB;
                function yFor(v) { return padT + (1 - v / max) * (H - padT - padB); }
                let grid = '';
                for (let g = 1; g <= 4; g++) {
                    const val = max / 4 * g;
                    const gy = yFor(val);
                    grid += '<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '" stroke="#e5eaf0"></line>' +
                        '<text x="' + (padL - 6) + '" y="' + (gy + 3).toFixed(1) + '" text-anchor="end" font-size="9" fill="#8a99b1">' + fmtCompact(val) + '</text>';
                }
                let bars = '';
                const bw = Math.min(30, Math.max(6, (slot - 8) / series.length));
                for (let i = 0; i < n; i++) {
                    const gx = padL + i * slot;
                    const used = series.length * bw + (series.length - 1) * 4;
                    let x0 = gx + (slot - used) / 2;
                    for (let si = 0; si < series.length; si++) {
                        const s = series[si];
                        const v = Number(s.values[i]) || 0;
                        if (v > 0) {
                            const yTop = yFor(v);
                            bars += '<rect x="' + x0.toFixed(1) + '" y="' + yTop.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1, baseY - yTop).toFixed(1) + '" rx="2" fill="' + s.color + '"></rect>' +
                                '<text x="' + (x0 + bw / 2).toFixed(1) + '" y="' + (yTop - 5).toFixed(1) + '" text-anchor="middle" font-size="9" fill="#5a6b85">' + fmtCompact(v) + '</text>';
                        }
                        x0 += bw + 4;
                    }
                    const lbl = labels[i];
                    const disp = lbl.length > 16 ? lbl.slice(0, 15) + '…' : lbl;
                    bars += '<text x="' + (gx + slot / 2).toFixed(1) + '" y="' + (H - 18).toFixed(1) + '" text-anchor="middle" font-size="10" fill="#5a6b85">' + escSvg(disp) + '</text>';
                }
                const legend = series.map(function(s) {
                    return '<span class="tcl-item"><span class="tcl-swatch" style="background:' + s.color + '"></span>' + escSvg(s.name) + '</span>';
                }).join('');
                return '<div class="ta-chart tc-bar"><div class="tc-ctitle">' + escSvg(cfg.title || '') + '</div>' +
                    '<div class="tcl-legend">' + legend + '</div>' +
                    '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="' + escSvg(cfg.title || 'Biểu đồ cột') + '">' +
                    '<line x1="' + padL + '" y1="' + baseY.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + baseY.toFixed(1) + '" stroke="#c4ccd6"></line>' +
                    grid + bars + '</svg></div>';
            }
            function chartBlockToHtml(raw) {
                let cfg = null;
                try { cfg = JSON.parse(raw); } catch (e) { cfg = null; }
                if (!cfg || typeof cfg !== 'object') return '';
                const type = String(cfg.type || '').toLowerCase();
                if (type === 'pie') return pieSvgHtml(cfg);
                if (type === 'bar') return barSvgHtml(cfg);
                return '';
            }

            function renderAiText(text) {
                // Làm sạch trước khi escape: giải mã entity (&quot; → ") + chuyển thẻ HTML (<strong>) → **bold**.
                let t = aiTagsToMarkdown(decodeAiEntities(text || ''));
                // Tách khối biểu đồ ```chart {json}``` ra trước khi escape để parse JSON nguyên bản.
                const charts = [];
                t = t.replace(/```(?:chart|bieudo|pie|bar|tron|cot|bieu-do)\s*\n?([\s\S]*?)```/gi, function(m, body) {
                    charts.push(String(body || '').trim());
                    return '\n@@CHART' + (charts.length - 1) + '@@\n';
                });
                t = escChat(t);
                t = t.replace(/```([\s\S]*?)```/g, function(m, c) { return '<pre>' + c + '</pre>'; });
                t = t.replace(/`([^`]+)`/g, function(m, c) { return '<code>' + c + '</code>'; });
                t = t.replace(/\*\*([^*]+)\*\*/g, function(m, c) { return '<strong>' + c + '</strong>'; });
                // Ảnh chart cổ phiếu do VinaFintech cung cấp (khối Markdown image trỏ về /api/stocks/.../chart-svg).
                t = t.replace(/!\[([^\]]*)\]\((\/api\/stocks\/[^)\s]*chart-svg[^)\s]*)\)/g, function(m, alt, src) {
                    return '<img class="ta-img" src="' + src + '" alt="' + escChat(alt || 'chart') + '" loading="lazy"/>';
                });
                t = t.replace(/(https?:\/\/[^\s]+)/g, function(m) { return '<a href="' + m + '" target="_blank" rel="noopener">' + m + '</a>'; });
                const lines = t.split('\n');
                let out = '';
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];
                    // Khối biểu đồ đã tách ở trên (@@CHARTn@@) → chèn SVG.
                    const cm = line.match(/^@@CHART(\d+)@@\s*$/);
                    if (cm) {
                        const chartHtml = chartBlockToHtml(charts[+cm[1]]);
                        if (chartHtml) out += chartHtml;
                        continue;
                    }
                    const h = line.match(/^(#{1,3})\s+(.*)$/);
                    if (h) { out += '<h' + h[1].length + '>' + h[2] + '</h' + h[1].length + '>'; continue; }
                    if (/^\s*[-*]\s+/.test(line)) { out += '<div class="ta-li">• ' + line.replace(/^\s*[-*]\s+/, '') + '</div>'; continue; }
                    // Dòng đã được escape + render markdown ở trên → KHÔNG escape lại (tránh hiện &lt;strong&gt;).
                    if (/^\s*\d+\.\s+/.test(line)) { out += '<div class="ta-li">' + line.trim() + '</div>'; continue; }
                    if (line.trim() === '') { continue; }
                    // Bảng Markdown: gom các dòng liên tiếp có dấu "|".
                    // Hỗ trợ CẢ bảng DeepSeek hay xuất KHÔNG có "|" ở đầu/cuối dòng
                    // (VD "Chỉ tiêu | PC1 | TV2" và "--- | --- | ---").
                    if (/^\s*\|/.test(line) || line.indexOf('|') >= 0) {
                        const rawRows = [];
                        while (i < lines.length && (/^\s*\|/.test(lines[i]) || lines[i].indexOf('|') >= 0)) {
                            rawRows.push(lines[i]);
                            i++;
                        }
                        i--; // vòng for sẽ tăng tiếp
                        const sepCount = rawRows.filter(isTableSep).length;
                        const rowCount = rawRows.length - sepCount;
                        // Chỉ coi là bảng khi: MỌI dòng bắt đầu bằng "|" (chủ đích viết bảng),
                        // hoặc có ≥ 2 dòng dữ liệu, hoặc ≥ 1 dòng dữ liệu kèm dòng phân tách.
                        const allPipeStart = rawRows.length > 0 && rawRows.every(function(r) { return /^\s*\|/.test(r); });
                        const isTable = allPipeStart
                            ? rawRows.length >= 1
                            : (rowCount >= 2 || (rowCount >= 1 && sepCount >= 1));
                        if (isTable) {
                            // Hàng 1 là header nếu hàng 2 là dòng phân tách (| --- | ... |).
                            let headCells = null;
                            let startBody = 0;
                            if (rawRows.length >= 2 && isTableSep(rawRows[1])) {
                                headCells = splitTableRow(rawRows[0]);
                                startBody = 2;
                            } else if (rawRows.length >= 2 && !isTableSep(rawRows[0])) {
                                // Không có dòng phân tách (DeepSeek hay bỏ): dòng 1 là header khi
                                // SỐ Ô khác dòng kế tiếp, HOẶC dòng 1 toàn ô in đậm (**...) còn dòng kế thì không
                                // (kiểu "**PC1** | **TV2**" rồi các dòng dữ liệu thường). Tránh đoán nhầm
                                // khi 2 dòng cùng số ô và cùng kiểu in đậm.
                                const firstCells = splitTableRow(rawRows[0]);
                                const secondCells = splitTableRow(rawRows[1]);
                                const allBold = function(cells) {
                                    return cells.length > 0 && cells.every(function(c2) { return c2.indexOf('<strong>') >= 0; });
                                };
                                if (firstCells.length !== secondCells.length ||
                                    (allBold(firstCells) && !allBold(secondCells))) {
                                    headCells = firstCells;
                                    startBody = 1;
                                }
                            }
                            const bodyRows = [];
                            for (let j = startBody; j < rawRows.length; j++) {
                                if (isTableSep(rawRows[j])) continue;
                                const cells = splitTableRow(rawRows[j]);
                                if (cells.length) bodyRows.push(cells);
                            }
                            if (headCells || bodyRows.length) {
                                out += buildTableHtml(headCells, bodyRows);
                                continue;
                            }
                        }
                    }
                    out += '<div class="ta-p">' + line + '</div>';
                }
                return out || '<div class="ta-p"></div>';
            }

            // ---------- Xuất PDF riêng từng câu trả lời ----------
            function lastUserQuestion() {
                const c = currentConv();
                const msgs = (c && c.messages) || [];
                for (let k = msgs.length - 1; k >= 0; k--) {
                    if (msgs[k].role === 'user') return msgs[k].content || '';
                }
                return '';
            }
            // Xuất ĐÚNG nội dung câu trả lời này ra PDF.
            function exportContentToPdf(content, question) {
                const sheet = document.getElementById('pdfSheet');
                if (!sheet) return;
                let html =
                    '<div class="ps-head">' +
                        '<div class="ps-brand">VinaFintech · VinaF AI</div>' +
                        '<div class="ps-title">Nội dung phân tích</div>' +
                        '<div class="ps-meta">' + (question ? 'Câu hỏi: ' + escChat(question) + ' · ' : '') + 'Xuất lúc: ' + new Date().toLocaleString('vi-VN') + '</div>' +
                    '</div>' +
                    '<div class="ps-msg"><div class="ps-ai"><span class="ps-ai-ico"><i class="fas fa-robot"></i></span>VinaF AI trả lời</div><div class="ps-body">' + renderAiText(content) + '</div></div>' +
                    '<div class="ps-foot">Tài liệu được tạo tự động bởi VinaFintech. Nội dung chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.</div>';
                sheet.innerHTML = html;
                setTimeout(function() {
                    try { window.focus(); window.print(); } catch (e) {}
                }, 80);
            }
            // Hộp thoại xác nhận trước khi xuất.
            function askExportPdf(content, question) {
                if (!content || !String(content).trim()) return;
                const old = document.getElementById('taConfirm');
                if (old && old.parentNode) old.parentNode.removeChild(old);
                const back = document.createElement('div');
                back.className = 'ta-confirm-backdrop';
                back.id = 'taConfirm';
                back.innerHTML =
                    '<div class="ta-confirm-card">' +
                        '<div class="tc-ico"><i class="fas fa-file-pdf"></i></div>' +
                        '<div class="tc-title">Xuất nội dung này ra PDF?</div>' +
                        '<div class="tc-text">Bạn có muốn xuất nội dung này ra file PDF không?</div>' +
                        '<div class="tc-actions">' +
                            '<button type="button" class="tc-btn tc-cancel" data-act="cancel">Hủy</button>' +
                            '<button type="button" class="tc-btn tc-go" data-act="go"><i class="fas fa-file-pdf"></i> Xuất PDF</button>' +
                        '</div>' +
                    '</div>';
                document.body.appendChild(back);
                function onKey(e) { if (e.key === 'Escape') close(); }
                function close() {
                    document.removeEventListener('keydown', onKey);
                    if (back.parentNode) back.parentNode.removeChild(back);
                }
                document.addEventListener('keydown', onKey);
                back.addEventListener('mousedown', function(e) { if (e.target === back) close(); });
                back.querySelector('[data-act="cancel"]').addEventListener('click', close);
                back.querySelector('[data-act="go"]').addEventListener('click', function() {
                    close();
                    exportContentToPdf(content, question);
                });
            }
            // Thêm thanh nút xuất PDF VÀO TRONG khung trả lời (.ta-ai-reply) của VinaF AI
            // (giữ nguyên nút "Xuất PDF" toàn bộ hội thoại ở header chat).
            function attachExportBar(replyHost, content, question) {
                const host = (replyHost && replyHost.classList && replyHost.classList.contains('ta-ai-reply'))
                    ? replyHost
                    : (replyHost ? replyHost.querySelector('.ta-ai-reply') : null);
                if (!host) return;
                let bar = host.querySelector(':scope > .ta-ai-bar');
                if (!bar) {
                    bar = document.createElement('div');
                    bar.className = 'ta-ai-bar';
                    bar.innerHTML = '<button type="button" class="ta-ai-export" title="Xuất nội dung này ra PDF"><i class="fas fa-file-pdf"></i> Xuất nội dung này ra PDF</button>';
                    host.appendChild(bar);
                }
                const btn = bar.querySelector('.ta-ai-export');
                if (btn && !btn.__expBound) {
                    btn.__expBound = true;
                    btn.addEventListener('click', function() { askExportPdf(content, question); });
                }
            }
            // Gắn nút sau khi streaming xong 1 câu trả lời (bubble = khung .ta-ai-reply).
            function attachLiveExport(bubble, content) {
                if (!bubble || !content) return;
                attachExportBar(bubble, content, lastUserQuestion());
            }

            function renderMessages() {
                if (!msgsEl) return;
                const c = currentConv();
                if (!c || !c.messages || !c.messages.length) {
                    msgsEl.innerHTML = '<div class="ta-welcome">' +
                        '<div class="tw-ico"><i class="fas fa-robot"></i></div>' +
                        '<div class="tw-title">VinaF AI — Trợ lý chứng khoán Việt Nam</div>' +
                        '<div class="tw-text">Hỏi về cổ phiếu, doanh nghiệp, thị trường hoặc danh mục của bạn.</div>' +
                        '<div class="ta-suggests"></div>' +
                        '<div class="ta-tools"></div>' +
                    '</div>';
                    renderStarter();
                    if (titleEl) titleEl.textContent = 'Cuộc trò chuyện mới';
                    return;
                }
                if (titleEl) titleEl.textContent = convTitleOf(c);
                let html = '';
                c.messages.forEach(function(m) {
                    if (m.role === 'user') {
                        html += '<div class="ta-msg-user">' + escChat(m.content) + '</div>';
                    } else if (m.role === 'assistant') {
                        html += '<div class="ta-msg-ai"><div class="ta-ai-avatar"><i class="fas fa-robot"></i></div>' +
                            '<div class="ta-ai-body"><div class="ta-ai-name">VinaF AI</div>' +
                            '<div class="ta-ai-reply">' + renderAiText(m.content) + '</div></div></div>';
                    }
                });
                msgsEl.innerHTML = html;
                // Gắn nút "Xuất nội dung này ra PDF" cho từng câu trả lời AI (đặt TRONG khung trả lời).
                const aiEls = msgsEl.querySelectorAll('.ta-msg-ai');
                let aiIdx = 0;
                c.messages.forEach(function(m, mi) {
                    if (m.role !== 'assistant') return;
                    const row = aiEls[aiIdx];
                    aiIdx++;
                    if (!row) return;
                    let q = '';
                    for (let k = mi - 1; k >= 0; k--) {
                        if (c.messages[k] && c.messages[k].role === 'user') { q = c.messages[k].content || ''; break; }
                    }
                    attachExportBar(row.querySelector('.ta-ai-reply'), m.content, q);
                });
                scrollChat();
            }

            function renderStarter() {
                const suggEl = msgsEl.querySelector('.ta-suggests');
                if (suggEl) {
                    suggEl.innerHTML = sugg.map(function(s) {
                        return '<div class="ta-sugg" data-q="' + s.q.replace(/"/g, '&quot;') + '">' + s.text + '</div>';
                    }).join('');
                    suggEl.querySelectorAll('.ta-sugg').forEach(function(el) {
                        el.addEventListener('click', function() { sendAi(el.getAttribute('data-q')); });
                    });
                }
                const toolsEl = msgsEl.querySelector('.ta-tools');
                if (toolsEl) {
                    toolsEl.innerHTML = tools.map(function(t) {
                        return '<div class="ta-tool" data-p="' + t.p.replace(/"/g, '&quot;') + '">' +
                            '<div class="tt-icon"><i class="fas ' + t.icon + '"></i></div>' +
                            '<div class="tt-title">' + t.title + '</div>' +
                            '<span class="tt-tag">' + t.tag + '</span>' +
                        '</div>';
                    }).join('');
                    toolsEl.querySelectorAll('.ta-tool').forEach(function(el) {
                        el.addEventListener('click', function() { sendAi(el.getAttribute('data-p')); });
                    });
                }
            }

            function renderHistory() {
                if (!historyEl) return;
                if (!conversations.length) {
                    historyEl.innerHTML = '<div class="ta-h-empty">Chưa có cuộc trò chuyện nào.</div>';
                    return;
                }
                historyEl.innerHTML = conversations.map(function(c) {
                    const active = c.id === currentId ? ' is-active' : '';
                    const title = escChat(convTitleOf(c));
                    const time = new Date(c.updated_at).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
                    return '<div class="ta-h-item' + active + '" data-id="' + c.id + '">' +
                        '<span class="ta-h-title">' + title + '</span>' +
                        '<span class="ta-h-time">' + time + '</span>' +
                        '<button type="button" class="ta-h-ren" title="Đổi tên" data-ren="' + c.id + '"><i class="fas fa-pen"></i></button>' +
                        '<button type="button" class="ta-h-del" title="Xóa" data-del="' + c.id + '"><i class="fas fa-trash"></i></button>' +
                    '</div>';
                }).join('');
                historyEl.querySelectorAll('.ta-h-item').forEach(function(el) {
                    el.addEventListener('click', function(e) {
                        if (e.target.closest('.ta-h-del') || e.target.closest('.ta-h-ren') ||
                            e.target.closest('.ta-h-input') || el.classList.contains('editing')) return;
                        openConv(el.getAttribute('data-id'));
                    });
                });
                historyEl.querySelectorAll('.ta-h-ren').forEach(function(el) {
                    el.addEventListener('click', function(e) {
                        e.stopPropagation();
                        startRenameConv(el.getAttribute('data-ren'));
                    });
                });
                historyEl.querySelectorAll('.ta-h-del').forEach(function(el) {
                    el.addEventListener('click', function(e) {
                        e.stopPropagation();
                        deleteConv(el.getAttribute('data-del'));
                    });
                });
            }

            function addUserMsg(text) {
                if (!msgsEl) return;
                const row = document.createElement('div');
                row.className = 'ta-msg-user';
                row.textContent = text;
                msgsEl.appendChild(row);
                scrollChat();
            }
            function addAiMsg(html) {
                if (!msgsEl) return null;
                const row = document.createElement('div');
                row.className = 'ta-msg-ai';
                row.innerHTML = '<div class="ta-ai-avatar"><i class="fas fa-robot"></i></div>' +
                    '<div class="ta-ai-body"><div class="ta-ai-name">VinaF AI</div><div class="ta-ai-reply">' +
                    (html || '<div class="ta-typing"><span></span><span></span><span></span></div>') +
                    '</div></div>';
                msgsEl.appendChild(row);
                scrollChat();
                return row.querySelector('.ta-ai-reply');
            }

            async function fetchChat(retried) {
                const c = currentConv();
                const msgs = (c && c.messages) ? c.messages.slice(-20) : [];
                const res = await fetch('/api/ai/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + (localStorage.getItem('vinafintech_access_token') || '')
                    },
                    body: JSON.stringify({ messages: msgs, stream: true })
                });
                if (res.status === 401 && !retried) {
                    const refresh = localStorage.getItem('vinafintech_refresh_token');
                    if (refresh) {
                        try {
                            const rr = await fetch('/api/auth/refresh-token', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ refresh_token: refresh })
                            });
                            const d = await rr.json();
                            if (d.access_token) {
                                localStorage.setItem('vinafintech_access_token', d.access_token);
                                if (d.refresh_token) localStorage.setItem('vinafintech_refresh_token', d.refresh_token);
                                if (d.user) localStorage.setItem('vinafintech_user', JSON.stringify(d.user));
                                return fetchChat(true);
                            }
                        } catch (e) {}
                    }
                    localStorage.removeItem('vinafintech_access_token');
                    localStorage.removeItem('vinafintech_refresh_token');
                    localStorage.removeItem('vinafintech_user');
                }
                return res;
            }

            async function streamAnswer(bubble) {
                const res = await fetchChat(false);
                if (!res.ok) {
                    let detail = 'Lỗi khi gọi VinaF AI. Vui lòng thử lại.';
                    try { const d = await res.json(); if (d && d.detail) detail = d.detail; } catch (e) {}
                    if (res.status === 401) detail = 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.';
                    // Hết lượt hỏi VinaF AI miễn phí → tin nhắn ngắn + mở DIALOG nổi trên khung chat.
                    if (res.status === 403 && /hết lượt|VinaF AI miễn phí/i.test(detail)) {
                        bubble.innerHTML = escChat('Bạn đã dùng hết lượt hỏi VinaF AI miễn phí trong tháng này.');
                        if (window.VinaFintechMembership && window.VinaFintechMembership.showQuotaDialog) window.VinaFintechMembership.showQuotaDialog();
                        return '';
                    }
                    bubble.innerHTML = escChat(detail);
                    return '';
                }
                if (!res.body) { bubble.innerHTML = 'Trình duyệt không hỗ trợ streaming.'; return ''; }
                const reader = res.body.getReader();
                const dec = new TextDecoder();
                let buf = '';
                let full = '';
                let received = false; // đã nhận nội dung thật (delta) để thay text chờ
                let sawError = false;  // đã có lỗi trả về → giữ nguyên thông báo lỗi
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buf += dec.decode(value, { stream: true });
                    let idx;
                    while ((idx = buf.indexOf('\n\n')) >= 0) {
                        const raw = buf.slice(0, idx);
                        buf = buf.slice(idx + 2);
                        raw.split('\n').forEach(function(line) {
                            if (!line.startsWith('data:')) return;
                            const data = line.slice(5).trim();
                            if (data === '[DONE]') return;
                            let ev;
                            try { ev = JSON.parse(data); } catch (e) { return; }
                            if (ev.type === 'delta') {
                                received = true;
                                full += (ev.content || '');
                                // Nội dung bắt đầu hiển thị → text chờ tự động biến mất.
                                bubble.innerHTML = renderAiText(full);
                                scrollChat();
                            } else if (ev.type === 'error') {
                                sawError = true;
                                bubble.innerHTML = escChat(ev.content || 'Có lỗi xảy ra.');
                                scrollChat();
                            }
                        });
                    }
                }
                // Xử lý xong mà vẫn chưa có nội dung/lỗi nào → bỏ text chờ, thay bằng thông báo nhẹ.
                if (!received && !sawError) {
                    bubble.innerHTML = 'VinaF AI chưa tìm thấy đủ thông tin để trả lời. Bạn vui lòng thử lại với câu hỏi khác nhé.';
                    scrollChat();
                }
                // Có câu trả lời hoàn chỉnh → gắn nút xuất PDF riêng cho câu trả lời này.
                if (received && full) attachLiveExport(bubble, full);
                return full;
            }

            function sendAi(prompt) {
                prompt = String(prompt || '').trim();
                if (!prompt || busy) return;
                let c = currentConv();
                if (!c) { newChat(); c = currentConv(); }
                if (!loggedIn()) {
                    const b = addAiMsg('');
                    if (b) b.innerHTML = 'Hãy <strong>đăng nhập</strong> để được hỏi đáp về cổ phiếu. <a href="/Login?next=/vinafai" target="_top" style="color:#008080;font-weight:700;">Đăng nhập</a>';
                    return;
                }
                addUserMsg(prompt);
                c.messages.push({ role: 'user', content: prompt });
                c.updated_at = Date.now();
                if (titleEl) titleEl.textContent = convTitleOf(c);
                // Clear textbox ngay sau khi text đã được gửi đi
                const sentInput = document.getElementById('taInput');
                if (sentInput) sentInput.value = '';
                busy = true;
                const btn = document.getElementById('taBtn');
                if (btn) btn.disabled = true;
                // Hiển thị text chờ khi VinaF AI đang xử lý/tìm kiếm (xóa đi khi có nội dung trả về).
                const bubble = addAiMsg(WAITING_HTML);
                if (!bubble) { busy = false; saveConvs(); return; }
                streamAnswer(bubble).then(function(reply) {
                    if (reply) c.messages.push({ role: 'assistant', content: reply });
                }).finally(function() {
                    busy = false;
                    if (btn) btn.disabled = false;
                    const input = document.getElementById('taInput');
                    if (input) { input.value = ''; input.focus(); }
                    c.updated_at = Date.now();
                    saveConvs();
                    renderHistory();
                    notifyParentHeight();
                });
            }

            // Nhận câu hỏi từ nơi khác (chart, Home, KhamPha...) qua sessionStorage → tự gửi
            function consumePendingPrompt() {
                try {
                    const p = sessionStorage.getItem('vinafintech_ai_prompt');
                    if (p && loggedIn()) {
                        sessionStorage.removeItem('vinafintech_ai_prompt');
                        setTimeout(function() { sendAi(p); }, 300);
                    }
                } catch (e) {}
            }
            const newBtn = document.getElementById('taNewChat');
            if (newBtn) newBtn.addEventListener('click', newChat);

            // ===== Toàn màn hình (fullscreen) =====
            const fsBtn = document.getElementById('taFsToggle');
            function nativeFsActive() {
                return !!(document.fullscreenElement || document.webkitFullscreenElement);
            }
            function applyFsState(on) {
                document.documentElement.classList.toggle('ta-fs', on);
                document.body.classList.toggle('ta-fs', on);
                if (fsBtn) {
                    fsBtn.innerHTML = on ? '<i class="fas fa-compress"></i>' : '<i class="fas fa-expand"></i>';
                    fsBtn.title = on ? 'Thoát toàn màn hình' : 'Xem toàn màn hình';
                }
                setTimeout(notifyParentHeight, 80);
            }
            // Sự kiện trình duyệt (bấm Esc / thoát native) → đồng bộ theo trạng thái thật.
            function updateFsUi() {
                applyFsState(nativeFsActive());
            }
            function toggleFullscreen() {
                // Đang fullscreen trình duyệt → thoát.
                if (nativeFsActive()) {
                    if (document.exitFullscreen) document.exitFullscreen();
                    else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
                    return;
                }
                // Đang ở chế độ fallback (trình duyệt/iframe không cho fullscreen gốc) → thoát.
                if (document.body.classList.contains('ta-fs')) {
                    applyFsState(false);
                    return;
                }
                const el = document.documentElement;
                const rfs = el.requestFullscreen || el.webkitRequestFullscreen;
                if (!rfs) { applyFsState(true); return; } // không có API fullscreen → fallback layout
                let p;
                try { p = rfs.call(el); } catch (e) { p = null; }
                if (p && p.catch) {
                    // Bị từ chối (VD iframe thiếu allowfullscreen) → dùng class fallback.
                    p.catch(function() {
                        if (!nativeFsActive()) applyFsState(true);
                    });
                } else if (!nativeFsActive()) {
                    // API không trả Promise (Safari cũ…) — chờ rồi kiểm tra lại.
                    setTimeout(function() { if (!nativeFsActive()) applyFsState(true); }, 250);
                }
            }
            if (fsBtn) fsBtn.addEventListener('click', toggleFullscreen);
            document.addEventListener('fullscreenchange', updateFsUi);
            document.addEventListener('webkitfullscreenchange', updateFsUi);

            // ===== Xuất PDF cuộc trò chuyện đang xem =====
            const pdfBtn = document.getElementById('taPdfBtn');
            function buildPdfSheet() {
                const sheet = document.getElementById('pdfSheet');
                const c = currentConv();
                if (!sheet || !c || !c.messages || !c.messages.length) return;
                let html =
                    '<div class="ps-head">' +
                        '<div class="ps-brand">VinaFintech · VinaF AI</div>' +
                        '<div class="ps-title">Báo cáo phân tích chứng khoán</div>' +
                        '<div class="ps-meta">Hội thoại: ' + escChat(convTitleOf(c)) + ' · Xuất lúc: ' + new Date().toLocaleString('vi-VN') + '</div>' +
                    '</div>';
                c.messages.forEach(function(m) {
                    if (m.role === 'user') {
                        html += '<div class="ps-msg"><div class="ps-user">❓ Bạn hỏi</div><div class="ps-body">' + escChat(m.content) + '</div></div>';
                    } else if (m.role === 'assistant') {
                        html += '<div class="ps-msg"><div class="ps-ai"><span class="ps-ai-ico"><i class="fas fa-robot"></i></span>VinaF AI trả lời</div><div class="ps-body">' + renderAiText(m.content) + '</div></div>';
                    }
                });
                html += '<div class="ps-foot">Tài liệu được tạo tự động bởi VinaFintech. Nội dung chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.</div>';
                sheet.innerHTML = html;
            }
            function exportPdf() {
                if (!currentConv() || !currentConv().messages || !currentConv().messages.length) return;
                buildPdfSheet();
                // Chờ CSS print áp dụng rồi mở hộp thoại in (chọn "Lưu dưới dạng PDF").
                setTimeout(function() {
                    try { window.focus(); window.print(); } catch (e) {}
                }, 80);
            }
            if (pdfBtn) pdfBtn.addEventListener('click', exportPdf);

            document.getElementById('taBtn').addEventListener('click', function() {
                const txt = document.getElementById('taInput').value.trim();
                if (txt) sendAi(txt);
            });
            document.getElementById('taInput').addEventListener('keydown', function(e) {
                if (e.key === 'Enter') document.getElementById('taBtn').click();
            });
            function initChat() {
                if (!conversations.length) {
                    newChat();
                } else {
                    currentId = conversations[0].id;
                    renderHistory();
                    renderMessages();
                }
            }
            loadConvs();
            if (!loggedIn()) {
                showLocked();
            } else {
                loadServerConvs().then(function() { initChat(); });
            }

            // ===== Embed =====
            if (window.self !== window.top) document.body.classList.add('embedded');
            function applyEmbedTheme(mode) { document.body.classList.toggle('dark-mode', mode === 'dark'); }
            function notifyParentHeight() {
                try {
                    const el = document.querySelector('.ta-page') || document.body;
                    const height = Math.ceil(el.getBoundingClientRect().height);
                    if (!height || height <= 0) return;
                    if (Math.abs(height - (window.__lastSentH || 0)) < 2) return;
                    window.__lastSentH = height;
                    if (window.parent && window.parent !== window) {
                        window.parent.postMessage({ type: 'vinafintech-embed-height', height: height }, '*');
                    }
                } catch (e) {}
            }
            window.addEventListener('message', function(event) {
                const data = event.data;
                if (!data || data.type !== 'vinafintech-theme') return;
                const mode = data.mode === 'dark' ? 'dark' : 'light';
                localStorage.setItem('theme', mode);
                applyEmbedTheme(mode);
                setTimeout(notifyParentHeight, 50);
            });
            window.addEventListener('load', function() {
                applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
                setTimeout(notifyParentHeight, 60);
                setTimeout(notifyParentHeight, 400);
            });
            window.addEventListener('resize', notifyParentHeight);
            if (window.ResizeObserver) {
                const obs = new ResizeObserver(notifyParentHeight);
                obs.observe(document.querySelector('.ta-page') || document.body);
            }
            applyEmbedTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light');
            consumePendingPrompt();
        })();
    