(function () {
    function getHeaderSearchInput() {
        return document.querySelector('.header-search input');
    }

    // Đang ở trang tin tức → ô tìm kiếm là tìm tin; ngược lại tìm cổ phiếu.
    function isNewsMode() {
        return typeof window.headerSearchIsNews === 'function' && window.headerSearchIsNews();
    }

    function goToIndexStock(symbol) {
        const normalized = String(symbol || '').trim().toUpperCase();
        if (!normalized) {
            return;
        }
        if (typeof window.openStockDetailInIndex === 'function') {
            window.openStockDetailInIndex(normalized);
            return;
        }
        window.location.href = `/cophieu-${encodeURIComponent(normalized)}`;
    }

    function openNewsSearch(query) {
        const q = String(query || '').trim();
        if (!q) {
            return;
        }
        if (typeof window.headerOpenNewsSearch === 'function') {
            window.headerOpenNewsSearch(q);
            return;
        }
        window.location.href = '/tin-tuc?q=' + encodeURIComponent(q);
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function ensureDropdown() {
        let dd = document.getElementById('headerSearchDropdown');
        if (!dd) {
            dd = document.createElement('div');
            dd.id = 'headerSearchDropdown';
            dd.className = 'header-search-dropdown';
            const wrap = document.querySelector('.header-search');
            if (wrap) wrap.appendChild(dd);
        }
        return dd;
    }

    function hideDropdown() {
        const dd = document.getElementById('headerSearchDropdown');
        if (dd) dd.style.display = 'none';
    }

    function updateMode() {
        const input = getHeaderSearchInput();
        if (!input) {
            return;
        }
        input.placeholder = isNewsMode() ? 'Tìm tin tức...' : 'Tìm kiếm cổ phiếu...';
        hideDropdown();
    }

    let debounceTimer = null;
    function onInput() {
        clearTimeout(debounceTimer);
        const input = getHeaderSearchInput();
        const q = String(input.value || '').trim();
        const dd = ensureDropdown();
        // Tin tức: KHÔNG gợi ý dropdown (kết quả hiện trên trang). Cổ phiếu: gợi ý khi có ít nhất 1 ký tự.
        if (isNewsMode() || !q) {
            hideDropdown();
            return;
        }
        debounceTimer = setTimeout(function () {
            fetch('/api/stocks/search?q=' + encodeURIComponent(q), { cache: 'no-store' })
                .then(function (r) {
                    return r.ok ? r.json() : [];
                })
                .then(function (items) {
                    if (!Array.isArray(items) || !items.length) {
                        dd.innerHTML = '<div class="hsd-empty">Không tìm thấy mã cổ phiếu</div>';
                        dd.style.display = 'block';
                        return;
                    }
                    dd.innerHTML = items.map(function (it) {
                        const sym = String(it.symbol || '').trim().toUpperCase();
                        return '<div class="hsd-item" data-symbol="' + escapeHtml(sym) + '">' +
                            '<span class="hsd-title">' + escapeHtml(sym) + '</span>' +
                            (it.name ? '<span class="hsd-meta">' + escapeHtml(it.name) + '</span>' : '') +
                            '</div>';
                    }).join('');
                    dd.style.display = 'block';
                })
                .catch(function () {
                    hideDropdown();
                });
        }, 250);
    }

    document.addEventListener('DOMContentLoaded', function () {
        const input = getHeaderSearchInput();
        if (!input) {
            return;
        }

        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                hideDropdown();
                return;
            }
            if (event.key !== 'Enter') {
                return;
            }
            const query = input.value.trim();
            if (!query) {
                return;
            }
            event.preventDefault();
            hideDropdown();
            if (isNewsMode()) {
                openNewsSearch(query);
            } else {
                goToIndexStock(query);
            }
        });

        input.addEventListener('input', onInput);
        input.addEventListener('focus', function () {
            const q = String(input.value || '').trim();
            if (!isNewsMode() && q) {
                onInput();
            }
        });

        const dd = ensureDropdown();
        dd.addEventListener('click', function (e) {
            const item = e.target.closest('.hsd-item');
            if (!item) {
                return;
            }
            const symbol = item.getAttribute('data-symbol');
            hideDropdown();
            if (symbol) {
                goToIndexStock(symbol);
            }
        });

        document.addEventListener('click', function (e) {
            const dropdown = document.getElementById('headerSearchDropdown');
            if (dropdown && !dropdown.contains(e.target) && !e.target.closest('.header-search')) {
                hideDropdown();
            }
        });

        updateMode();
        window.onHeaderModeChange = updateMode;
    });
})();