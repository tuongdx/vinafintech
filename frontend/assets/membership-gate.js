/* VinaFintech Membership Gate — trạng thái gói + hộp thoại mở gói + helper khóa UI.
 * Dùng chung cho: Quản lý tài sản, Thị trường, VinaF AI...
 * Load như script thường (không module) → window.VinaFintechMembership.
 */
(function () {
  'use strict';

  var CSS = '' +
    '.tg-modal-backdrop{position:fixed;inset:0;background:rgba(9,14,24,.6);backdrop-filter:blur(3px);z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px}' +
    '.tg-modal{background:#fff;color:#12203a;border-radius:18px;max-width:460px;width:100%;padding:24px;box-shadow:0 24px 60px rgba(0,0,0,.3);max-height:90vh;overflow:auto}' +
    'body.dark-mode .tg-modal{background:#161b22;color:#e6edf3}' +
    '.tg-modal .tg-title{font-size:18px;font-weight:800;margin-bottom:4px}' +
    '.tg-modal .tg-sub{font-size:13px;color:#8a99b1;margin-bottom:16px}' +
    'body.dark-mode .tg-modal .tg-sub{color:#6b7a8f}' +
    '.tg-plan{border:1px solid #e5eaf0;border-radius:14px;padding:14px 16px;margin-bottom:10px;display:flex;align-items:center;gap:12px;cursor:default}' +
    'body.dark-mode .tg-plan{border-color:#30363d}' +
    '.tg-plan .tg-plan-ico{width:42px;height:42px;flex:0 0 42px;border-radius:12px;background:rgba(0,128,128,.12);color:#008080;display:flex;align-items:center;justify-content:center;font-size:18px}' +
    '.tg-plan .tg-plan-info{flex:1;min-width:0}' +
    '.tg-plan .tg-plan-name{font-weight:700;font-size:14px}' +
    '.tg-plan .tg-plan-desc{font-size:12px;color:#8a99b1}' +
    'body.dark-mode .tg-plan .tg-plan-desc{color:#6b7a8f}' +
    '.tg-plan .tg-plan-price{font-weight:800;font-size:15px;color:#008080;white-space:nowrap}' +
    '.tg-buy{width:100%;border:none;background:#008080;color:#fff;font-weight:800;font-size:14px;padding:12px;border-radius:12px;cursor:pointer;margin-top:8px;font-family:inherit}' +
    '.tg-buy:hover{background:#006666}' +
    '.tg-close{background:transparent;border:1px solid #e5eaf0;color:#8a99b1;width:100%;padding:10px;border-radius:12px;cursor:pointer;margin-top:8px;font-family:inherit}' +
    '.tg-lock-card{display:flex;align-items:center;gap:14px;background:rgba(0,128,128,.06);border:1px dashed rgba(0,128,128,.35);border-radius:14px;padding:18px 20px;margin:8px 0;color:#12203a}' +
    'body.dark-mode .tg-lock-card{color:#e6edf3;background:rgba(0,128,128,.08)}' +
    '.tg-lock-card .tg-lock-ico{width:46px;height:46px;flex:0 0 46px;border-radius:50%;background:#008080;color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px}' +
    '.tg-lock-card .tg-lock-title{font-weight:700;font-size:14px}' +
    '.tg-lock-card .tg-lock-desc{font-size:12.5px;color:#8a99b1;margin-top:2px}' +
    '.tg-upg-btn{border:1px solid #008080;background:transparent;color:#008080;font-weight:700;font-size:12.5px;padding:7px 12px;border-radius:9px;cursor:pointer;font-family:inherit;white-space:nowrap}' +
    '.tg-upg-btn:hover{background:#008080;color:#fff}' +
    '.tg-blur-zone{position:relative}' +
    '.tg-blur-zone .tg-blur-body{filter:blur(7px);opacity:.6;pointer-events:none;user-select:none}' +
    '.tg-blur-zone .tg-blur-veil{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;z-index:6;padding:10px}' +
    '.tg-blur-text{display:inline-block;filter:blur(5px);opacity:.55;user-select:none;pointer-events:none}' +
    '.tg-cell-lock{margin-left:6px;color:#008080;cursor:pointer}' +
    '.tg-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#12203a;color:#fff;padding:10px 18px;border-radius:30px;font-size:13px;z-index:100000;box-shadow:0 8px 24px rgba(0,0,0,.25)}' +
    'body.dark-mode .tg-toast{background:#e6edf3;color:#12203a}' +
    '.qd-backdrop{position:fixed;inset:0;background:rgba(9,14,24,.55);backdrop-filter:blur(3px);z-index:99980;display:flex;align-items:center;justify-content:center;padding:18px}' +
    '.qd-card{background:#fff;color:#12203a;border-radius:20px;max-width:400px;width:100%;padding:26px 24px 22px;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.3);position:relative}' +
    'body.dark-mode .qd-card{background:#161b22;color:#e6edf3}' +
    '.qd-card .qd-ico{width:54px;height:54px;margin:0 auto 12px;border-radius:50%;background:linear-gradient(135deg,#008080,#00a08a);color:#fff;display:flex;align-items:center;justify-content:center;font-size:24px;box-shadow:0 10px 22px rgba(0,128,128,.35)}' +
    '.qd-card .qd-title{font-weight:800;font-size:16px;line-height:1.5}' +
    '.qd-card .qd-desc{font-size:13px;color:#5b6b85;margin-top:8px;line-height:1.6}' +
    'body.dark-mode .qd-card .qd-desc{color:#9fb0c7}' +
    '.qd-card .qd-go{display:inline-flex;align-items:center;gap:8px;margin-top:18px;border:none;background:linear-gradient(135deg,#008080,#00a08a);color:#fff;font-weight:700;font-size:14px;padding:12px 24px;border-radius:30px;cursor:pointer;font-family:inherit;box-shadow:0 8px 22px rgba(0,128,128,.35)}' +
    '.qd-card .qd-go:hover{transform:translateY(-1px)}' +
    '.qd-card .qd-x{position:absolute;top:12px;right:12px;border:none;background:transparent;color:#8a99b1;font-size:15px;cursor:pointer;width:32px;height:32px;border-radius:50%;font-family:inherit;line-height:1}' +
    '.qd-card .qd-x:hover{background:#eef1f5;color:#12203a}' +
    'body.dark-mode .qd-card .qd-x:hover{background:#21262d;color:#e6edf3}' +
    '.tg-mem-title{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:800}' +
    '.tg-mem-title i{color:#f5a623}' +
    '.tg-mem-badge{display:inline-block;margin:2px 0 14px;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700;background:#e3f5ec;color:#0f7b3a}' +
    '.tm-row{display:flex;justify-content:space-between;gap:12px;padding:10px 2px;border-bottom:1px dashed #e5eaf0;font-size:13.5px}' +
    'body.dark-mode .tm-row{border-bottom-color:#30363d}' +
    '.tm-row .tm-label{color:#8a99b1}' +
    '.tm-row .tm-val{font-weight:700;text-align:right}' +
    '.tm-perks{margin-top:12px}' +
    '.tm-perks-title{font-size:13px;font-weight:800;margin:12px 0 8px}' +
    '.tm-perk{display:flex;align-items:flex-start;gap:10px;background:#f4f9f8;border:1px solid #e0efe9;border-radius:12px;padding:10px 12px;margin-bottom:8px}' +
    'body.dark-mode .tm-perk{background:#0f2420;border-color:#1f4d43}' +
    '.tm-perk .tm-perk-ico{flex:0 0 34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#008080,#00a08a);color:#fff;display:flex;align-items:center;justify-content:center}' +
    '.tm-perk b{font-size:13.5px;display:block}' +
    '.tm-perk span{font-size:12px;color:#8a99b1}' +
    '.tm-empty{color:#8a99b1;font-size:13px;padding:4px 0}' +
    '.tg-mem-badge.off{background:#fde8e5;color:#d0314e}' +
    'body.dark-mode .tg-mem-badge.off{background:#2d1b1e;color:#f85149}' +
    '.tm-hint{font-size:12.5px;color:#8a99b1;margin:2px 0 8px}' +
    '.tm-plans .tg-plan{margin-bottom:8px}' +
    '.tm-plans .tg-plan .tg-plan-desc{margin-top:2px}' +
    '.tm-actions{display:flex;gap:10px;margin-top:16px}' +
    '.tm-actions button{flex:1}';

  function injectCss() {
    if (document.getElementById('tg-gate-css')) return;
    var st = document.createElement('style');
    st.id = 'tg-gate-css';
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  function authHeaders(extra) {
    var token = localStorage.getItem('vinafintech_access_token') || '';
    var h = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = 'Bearer ' + token;
    if (extra) Object.assign(h, extra);
    return h;
  }

  var M = {
    data: null,      // trạng thái hội viên từ /api/membership/status (null nếu chưa đăng nhập)
    features: null,  // bản đồ key->enabled của các block website (từ /api/features, công khai)
    promise: null,

    /* Tải bản đồ trạng thái block website (MỞ/KHOÁ) — endpoint công khai, không cần đăng nhập.
     * `cache:'no-store'` để luôn lấy trạng thái mới nhất sau khi admin bật/tắt. */
    _loadFeatures: function () {
      var self = this;
      return fetch('/api/features', { cache: 'no-store', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (list) {
          var m = {};
          if (Array.isArray(list)) {
            list.forEach(function (f) { if (f && f.key) m[f.key] = !!f.enabled; });
          }
          self.features = m;
          return m;
        })
        .catch(function () { if (!self.features) self.features = {}; return self.features; });
    },

    ensure: function () {
      if (this.promise) return this.promise;
      var token = localStorage.getItem('vinafintech_access_token');
      var self = this;
      this.promise = Promise.all([
        token
          ? fetch('/api/membership/status', { headers: authHeaders() })
              .then(function (r) { return r.ok ? r.json() : null; })
              .catch(function () { return null; })
          : Promise.resolve(null),
        this._loadFeatures()
      ]).then(function (res) {
        self.data = res[0];
        return res[0];
      });
      return this.promise;
    },

    refresh: function () { this.promise = null; this.data = null; return this.ensure(); },

    isMember: function () { return !!(this.data && this.data.active); },

    isLoggedIn: function () { return !!localStorage.getItem('vinafintech_access_token'); },

    /* Chưa đăng nhập → mở trang Đăng nhập (nếu đang chạy trong iframe thì báo shell mở). */
    goLogin: function () {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'vinafintech-go-login' }, '*');
      } else {
        window.location.href = '/Login';
      }
    },

    /* Block `key` có đang MỞ cho user hiện tại không?
     * - Không phải block hội viên (không có trong bảng /api/features) → luôn mở.
     * - Block hội viên + chưa đăng nhập → FALSE: làm mờ + yêu cầu đăng nhập.
     * - Hội viên đã mở gói → luôn dùng được.
     * - Đã đăng nhập, chưa mua gói → chỉ dùng được khi block đang MỞ cho mọi tài khoản (enabled=true). */
    featureOpen: function (key) {
      if (!key) return true;
      var f = this.features;
      if (!f) return true;              // chưa tải xong → mặc định mở (không khóa nhầm)
      // key không thuộc danh sách block hội viên quản lý → luôn mở.
      var managed = Object.prototype.hasOwnProperty.call(f, key);
      if (!managed) return true;
      // Block đang MỞ (enabled=true) → mọi người đều xem được, kể cả khách chưa đăng nhập.
      if (f[key] === true) return true;
      // Block KHOÁ hội viên (enabled=false) → chỉ hội viên đã mở gói dùng được.
      return this.isMember();
    },

    /* Block `key` có đang được bật MỞ cho tất cả (không phụ thuộc hội viên)? */
    featureEnabled: function (key) {
      var f = this.features;
      return !!f && f[key] === true;
    },

    fmt: function (v) { return Number(v || 0).toLocaleString('vi-VN'); },

    toast: function (msg, isErr) {
      var old = document.querySelector('.tg-toast');
      if (old) old.remove();
      var t = document.createElement('div');
      t.className = 'tg-toast';
      t.style.background = isErr ? '#c0392b' : '#12203a';
      t.style.color = '#fff';
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(function () { t.remove(); }, 3200);
    },

    /* HTML khóa tính năng "hiện mờ + nút nâng cấp". */
    lockCardHTML: function (opts) {
      opts = opts || {};
      return '<div class="tg-lock-card">' +
        '<div class="tg-lock-ico"><i class="fas ' + (opts.icon || 'fa-lock') + '"></i></div>' +
        '<div style="flex:1;min-width:0;">' +
        '<div class="tg-lock-title">' + (opts.title || 'Tính năng dành cho Hội viên') + '</div>' +
        '<div class="tg-lock-desc">' + (opts.desc || 'Mở gói hội viên để dùng tính năng này.') + '</div>' +
        '</div>' +
        '<button type="button" class="tg-upg-btn" onclick="window.VinaFintechMembership.openUpgrade()"><i class="fas fa-crown"></i> Nâng cấp</button>' +
        '</div>';
    },

    openUpgrade: function () {
      injectCss();
      var self = this;
      if (!this.data) {
        this.ensure().then(function () { if (self.isMember()) { self.toast('Bạn đang là Hội viên.'); return; } self._renderModal(); });
        return;
      }
      if (this.isMember()) { this.toast('Bạn đang là Hội viên. Các tính năng đã được mở.'); return; }
      this._renderModal();
    },

    /* Dialog "hết lượt hỏi" hiển thị nổi trên khung chat (đóng được).
     * Nút "Mở khóa Hội viên" → mở modal chọn gói. */
    showQuotaDialog: function (opts) {
      injectCss();
      var self = this;
      var o = opts || {};
      var old = document.querySelector('.qd-backdrop');
      if (old) old.remove();
      var close = function () { var b = document.querySelector('.qd-backdrop'); if (b) b.remove(); };
      var bd = document.createElement('div');
      bd.className = 'qd-backdrop';
      bd.innerHTML = '<div class="qd-card">' +
        '<button type="button" class="qd-x" aria-label="Đóng"><i class="fas fa-times"></i></button>' +
        '<div class="qd-ico"><i class="fas fa-crown"></i></div>' +
        '<div class="qd-title">' + (o.title || 'Bạn đã dùng hết lượt hỏi VinaF AI miễn phí trong tháng này') + '</div>' +
        '<div class="qd-desc">' + (o.desc || 'Mở gói Hội viên để hỏi không giới hạn và dùng toàn bộ tính năng VinaFintech.') + '</div>' +
        '<div class="qd-go"><i class="fas fa-crown"></i> Mở khóa Hội viên</div>' +
      '</div>';
      document.body.appendChild(bd);
      bd.addEventListener('click', function (e) { if (e.target === bd) close(); });
      var x = bd.querySelector('.qd-x');
      if (x) x.addEventListener('click', close);
      var go = bd.querySelector('.qd-go');
      if (go) go.addEventListener('click', function () { close(); self.openUpgrade(); });
    },

    /* Mục "Hội viên" trong tài khoản:
     * - Chưa đăng ký hội viên → hiển thị form đăng ký (chọn gói + mở gói).
     * - Đã đăng ký → hiển thị thông tin gói, số tiền, ngày đăng ký/hết hạn, tính năng đã mở khoá.
     */
    openMembershipCenter: function () {
      injectCss();
      var self = this;
      this.ensure().then(function (d) {
        if (!self.isLoggedIn()) { self.goLogin(); return; }
        // Đã từng/đang đăng ký hội viên → xem thông tin gói (+ gia hạn nếu hết hạn).
        var everSubscribed = !!(d && (d.subscribed_at || (d.plan && d.plan !== 'free')));
        if (everSubscribed) {
          self._renderMemberInfo(d);
        } else {
          self._renderModal(); // chưa từng đăng ký → form đăng ký hội viên
        }
      });
    },

    _renderMemberInfo: function (d) {
      injectCss();
      var self = this;
      var close = function () { var b = document.querySelector('.tg-modal-backdrop'); if (b) b.remove(); };
      var fmt = function (v) { return v == null ? '—' : Number(v).toLocaleString('vi-VN') + 'đ'; };
      var dt = function (iso) {
        if (!iso) return '—';
        try {
          var dd = new Date(iso);
          return dd.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch (e) { return iso; }
      };
      var perks = (d && d.perks) || [];
      var planLabel = (d && d.plan_label) || 'Hội viên';
      var active = !!(d && d.active);
      var plans = (d && d.products) || [
        { code: 'monthly', name: 'Theo tháng', price: 99000 },
        { code: 'yearly', name: 'Theo năm', price: 999000 }
      ];

      var perksHtml;
      if (perks.length) {
        perksHtml = perks.map(function (p) {
          return '<div class="tm-perk">' +
            '<div class="tm-perk-ico"><i class="fas fa-unlock"></i></div>' +
            '<div><b>' + (p.name || p.key) + '</b><span>' + (p.description || '') + '</span></div>' +
            '</div>';
        }).join('');
      } else {
        perksHtml = '<div class="tm-empty">Hiện không có tính năng bị khoá nào — các tính năng hội viên đang được mở cho tất cả mọi người.</div>';
      }

      // Khi HẾT HẠN: hiển thị thêm 2 gói (Theo tháng / Theo năm) để gia hạn.
      // Khi CÒN HẠN: không hiện gói, nút Gia hạn bị disable.
      var renewArea;
      var renewBtn;
      if (active) {
        renewArea = '<div class="tm-hint">Gói của bạn còn hiệu lực đến ' + dt(d && d.expires_at) + ' — không cần gia hạn lúc này.</div>';
        renewBtn = '<button type="button" class="tg-buy" style="margin-top:0;" disabled id="tmRenew"><i class="fas fa-sync-alt"></i> Gia hạn</button>';
      } else {
        renewArea = '<div class="tm-hint">Gói đã hết hạn. Chọn gói để gia hạn:</div>' +
          '<div class="tm-plans">' + plans.map(function (p) {
            var icon = p.code === 'yearly' ? 'fa-calendar-alt' : 'fa-calendar';
            return '<div class="tg-plan" data-code="' + p.code + '">' +
              '<div class="tg-plan-ico"><i class="fas ' + icon + '"></i></div>' +
              '<div class="tg-plan-info"><div class="tg-plan-name">Gói ' + p.name + '</div>' +
              '<div class="tg-plan-desc">' + (p.code === 'yearly' ? 'Thanh toán 1 lần / 12 tháng' : 'Gia hạn mỗi tháng') + '</div></div>' +
              '<div class="tg-plan-price">' + self.fmt(p.price) + 'đ/' + (p.code === 'yearly' ? 'năm' : 'tháng') + '</div>' +
              '</div>';
          }).join('') + '</div>';
        renewBtn = '<button type="button" class="tg-buy" style="margin-top:0;" id="tmRenew"><i class="fas fa-sync-alt"></i> Gia hạn</button>';
      }

      var html = '<div class="tg-modal">' +
        '<div class="tg-mem-title"><i class="fas fa-crown"></i> Gói hội viên của bạn</div>' +
        '<span class="tg-mem-badge' + (active ? '' : ' off') + '">' + (active ? '<i class="fas fa-check-circle"></i> Đang hoạt động' : '<i class="fas fa-exclamation-circle"></i> Đã hết hạn') + '</span>' +
        '<div class="tm-row"><span class="tm-label">Gói hội viên</span><span class="tm-val">' + planLabel + '</span></div>' +
        '<div class="tm-row"><span class="tm-label">Số tiền đã thanh toán</span><span class="tm-val">' + fmt(d && d.amount) + '</span></div>' +
        '<div class="tm-row"><span class="tm-label">Ngày đăng ký</span><span class="tm-val">' + dt(d && d.subscribed_at) + '</span></div>' +
        '<div class="tm-row"><span class="tm-label">Hết hạn</span><span class="tm-val">' + dt(d && d.expires_at) + '</span></div>' +
        '<div class="tm-perks"><div class="tm-perks-title">Tính năng đã mở khoá</div>' + perksHtml + '</div>' +
        '<div class="tm-perks">' + renewArea + '</div>' +
        '<div class="tm-actions">' +
          '<button type="button" class="tg-close" style="margin-top:0;">Đóng</button>' +
          renewBtn +
        '</div>' +
        '</div>';

      var bd = document.createElement('div');
      bd.className = 'tg-modal-backdrop';
      bd.innerHTML = html;
      document.body.appendChild(bd);
      bd.addEventListener('click', function (e) { if (e.target === bd) close(); });
      var x = bd.querySelector('.tg-close');
      if (x) x.addEventListener('click', close);

      var renew = bd.querySelector('#tmRenew');
      if (!renew || active) return; // còn hạn → không cho gia hạn

      // Chọn gói gia hạn (Theo tháng / Theo năm) rồi bấm Gia hạn.
      var chosen = plans[0] ? plans[0].code : 'monthly';
      var planEls = bd.querySelectorAll('.tg-plan');
      function pick(code) {
        chosen = code;
        planEls.forEach(function (el) {
          el.style.borderColor = el.getAttribute('data-code') === code ? '#008080' : '';
          el.style.boxShadow = el.getAttribute('data-code') === code ? '0 0 0 2px rgba(0,128,128,.25)' : '';
          el.style.cursor = 'pointer';
        });
        var p = plans.filter(function (pr) { return pr.code === code; })[0] || plans[0];
        renew.innerHTML = '<i class="fas fa-sync-alt"></i> Gia hạn ' + p.name + ' — ' + self.fmt(p.price) + 'đ';
      }
      planEls.forEach(function (el) {
        el.addEventListener('click', function () { pick(el.getAttribute('data-code')); });
      });
      pick(chosen);

      renew.addEventListener('click', function () {
        renew.disabled = true;
        renew.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang gia hạn…';
        fetch('/api/membership/subscribe', {
          method: 'POST', headers: authHeaders(), body: JSON.stringify({ product: chosen })
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
          .then(function (res) {
            if (!res.ok) throw new Error((res.body && res.body.detail) || 'Không gia hạn được.');
            self.toast('✅ ' + (res.body.message || 'Đã gia hạn gói hội viên.'));
            self.promise = null; self.data = null;
            setTimeout(function () { close(); location.reload(); }, 900);
          })
          .catch(function (e) {
            renew.disabled = false;
            renew.innerHTML = '<i class="fas fa-sync-alt"></i> Gia hạn';
            self.toast('❌ ' + (e.message || 'Lỗi'), true);
          });
      });
    },

    _renderModal: function () {
      injectCss();
      var self = this;
      var close = function () { var b = document.querySelector('.tg-modal-backdrop'); if (b) b.remove(); };
      var plans = (this.data && this.data.products) || [
        { code: 'monthly', name: 'Theo tháng', price: 99000 },
        { code: 'yearly', name: 'Theo năm', price: 999000 }
      ];
      var html = '<div class="tg-modal">' +
        '<div class="tg-title"><i class="fas fa-crown" style="color:#f5a623;"></i> Mở gói Hội viên</div>' +
        '<div class="tg-sub">Mở khóa toàn bộ: VinaF AI không giới hạn, Trung tâm cảnh báo, bảng danh mục (Hỗ trợ/Kháng cự/Trạng thái), VinaF AI Market Analysis.</div>';
      plans.forEach(function (p) {
        var icon = p.code === 'yearly' ? 'fa-calendar-alt' : 'fa-calendar';
        html += '<div class="tg-plan">' +
          '<div class="tg-plan-ico"><i class="fas ' + icon + '"></i></div>' +
          '<div class="tg-plan-info"><div class="tg-plan-name">Gói ' + p.name + '</div>' +
          '<div class="tg-plan-desc">' + (p.code === 'yearly' ? 'Thanh toán 1 lần / 12 tháng' : 'Gia hạn mỗi tháng') + '</div></div>' +
          '<div class="tg-plan-price">' + self.fmt(p.price) + 'đ/' + (p.code === 'yearly' ? 'năm' : 'tháng') + '</div>' +
          '</div>';
      });
      html += '<button type="button" class="tg-buy" id="tgBuyBtn"><i class="fas fa-lock-open"></i> Mở gói ngay (chọn ở trên)</button>' +
        '<button type="button" class="tg-close">Đóng</button>' +
        '</div>';

      var bd = document.createElement('div');
      bd.className = 'tg-modal-backdrop';
      bd.innerHTML = html;
      document.body.appendChild(bd);
      bd.addEventListener('click', function (e) { if (e.target === bd) close(); });
      bd.querySelector('.tg-close').addEventListener('click', close);
      var chosen = 'monthly';
      var planEls = bd.querySelectorAll('.tg-plan');
      var buyBtn = bd.querySelector('#tgBuyBtn');
      function pick(code) {
        chosen = code;
        planEls.forEach(function (el, i) {
          var c = plans[i].code;
          el.style.borderColor = c === code ? '#008080' : '';
          el.style.boxShadow = c === code ? '0 0 0 2px rgba(0,128,128,.25)' : '';
          el.style.cursor = 'pointer';
        });
        var p = plans.filter(function (x) { return x.code === code; })[0] || plans[0];
        buyBtn.innerHTML = '<i class="fas fa-lock-open"></i> Mở gói ' + p.name + ' — ' + self.fmt(p.price) + 'đ';
      }
      planEls.forEach(function (el, i) {
        el.addEventListener('click', function () { pick(plans[i].code); });
      });
      pick('monthly');
      buyBtn.addEventListener('click', function () {
        buyBtn.disabled = true;
        buyBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang mở gói…';
        fetch('/api/membership/subscribe', {
          method: 'POST', headers: authHeaders(), body: JSON.stringify({ product: chosen })
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
          .then(function (res) {
            if (!res.ok) throw new Error((res.body && res.body.detail) || 'Không mở được gói.');
            self.toast('✅ ' + (res.body.message || 'Đã mở gói hội viên.'));
            self.promise = null; self.data = null;
            setTimeout(function () { close(); location.reload(); }, 900);
          })
          .catch(function (e) { buyBtn.disabled = false; buyBtn.innerHTML = '<i class="fas fa-lock-open"></i> Mở gói ngay'; self.toast('❌ ' + (e.message || 'Lỗi'), true); });
      });
    },

    /* Giới hạn VinaF AI free: trả true nếu còn lượt, false nếu hết/muốn mở gói. */
    canAskAI: function () {
      if (this.isMember()) return true;
      var q = (this.data && this.data.quota) || {};
      var max = q.ai_questions_max;
      if (max == null) return true; // không có giới hạn
      return (q.ai_questions_used || 0) < max;
    },
    canNewChat: function () {
      if (this.isMember()) return true;
      var q = (this.data && this.data.quota) || {};
      var max = q.ai_new_chats_max;
      if (max == null) return true;
      return (q.ai_new_chats_used || 0) < max;
    },

    /* Khóa KIỂU MỜ: giữ nguyên nội dung phía sau lớp blur + nút mở khóa ở giữa.
     * Phải gọi SAU khi nội dung đã được dựng nhưng TRƯỚC khi gắn sự kiện vào phần tử trong.
     * `small=true` → nút gọn cho vùng nhỏ (thẻ/card). */
    lockBlur: function (container, small) {
      if (!container || container.getAttribute('data-tg-locked') === '1') return;
      container.classList.add('tg-blur-zone');
      container.setAttribute('data-tg-locked', '1');
      var inner = container.innerHTML;
      var btnStyle = small
        ? 'background:#008080;color:#fff;border:1px solid #008080;padding:8px 14px;border-radius:10px;font-size:12.5px;box-shadow:0 6px 18px rgba(0,128,128,.3);'
        : 'background:#008080;color:#fff;border:1px solid #008080;padding:12px 20px;border-radius:12px;font-size:14px;box-shadow:0 10px 30px rgba(0,128,128,.35);';
      // Chưa đăng nhập → nút "Đăng nhập"; đã đăng nhập (chưa mua gói) → nút "Mở khóa Hội viên".
      var logged = this.isLoggedIn();
      var label = logged ? (small ? 'Mở khóa' : 'Mở khóa Hội viên') : (small ? 'Đăng nhập' : 'Đăng nhập');
      var icon = logged ? 'fa-crown' : 'fa-right-to-bracket';
      var action = logged ? 'window.VinaFintechMembership.openUpgrade()' : 'window.VinaFintechMembership.goLogin()';
      container.innerHTML = '<div class="tg-blur-body">' + inner + '</div>' +
        '<div class="tg-blur-veil"><button type="button" class="tg-upg-btn" style="' + btnStyle + '" onclick="' + action + '"><i class="fas ' + icon + '"></i> ' + label + '</button></div>';
    },

    /* Bọc HTML nội dung bằng lớp mờ (dùng cho ô/cột nhỏ). */
    blurText: function (html) {
      return '<span class="tg-blur-text">' + (html == null ? '' : html) + '</span>';
    }
  };

  window.VinaFintechMembership = M;
  injectCss();
})();
