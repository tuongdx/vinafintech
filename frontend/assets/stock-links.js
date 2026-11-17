/* Helper dùng chung: dựng URL CHUẨN cấp ngành cho mã cổ phiếu và tự nâng cấp href của
   mọi liên kết đang có trong DOM.
   - URL: /co-phieu/nganh/{sector}/{industry}/{MÃ} (bản đồ lấy từ /api/stocks/symbol-sectors).
   - Chưa biết ngành → tạm /co-phieu/{MÃ} (server 301 sang URL chuẩn), tự nâng cấp khi bản đồ về.
   API: window.vftStockHref(sym), window.vftUpgradeStockLinks(root), window.vftSetStockSectors(map),
        window.vftStockLinksReady (Promise). */
(function () {
    var urls = {};
    var ready = null;

    function normalize(sym) {
        return String(sym || '').trim().toUpperCase();
    }

    function stockHref(sym) {
        var key = normalize(sym);
        return urls[key] || ('/co-phieu/' + encodeURIComponent(key));
    }

    function upgrade(root) {
        var scope = root || document;
        if (!scope.querySelectorAll) return;
        scope.querySelectorAll('a[data-symbol]').forEach(function (a) {
            var key = normalize(a.getAttribute('data-symbol'));
            if (urls[key]) a.setAttribute('href', urls[key]);
        });
    }

    function setSectors(map) {
        urls = {};
        Object.keys(map || {}).forEach(function (sym) {
            var v = map[sym] || {};
            var sec = String(v.sector || '').trim();
            var ind = String(v.industry || '').trim();
            if (!sec || !ind) return;
            var key = normalize(sym);
            urls[key] = '/co-phieu/nganh/' + encodeURIComponent(sec) + '/' + encodeURIComponent(ind) + '/' + encodeURIComponent(key);
        });
        upgrade();
    }

    function load() {
        if (ready) return ready;
        ready = fetch('/api/stocks/symbol-sectors')
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (map) { setSectors(map); })
            .catch(function () { /* lỗi mạng → giữ URL tạm /co-phieu/{MÃ} */ });
        return ready;
    }

    window.vftStockHref = stockHref;
    window.vftUpgradeStockLinks = upgrade;
    window.vftSetStockSectors = setSectors;
    window.vftStockLinksReady = load();
})();
