(function () {
    function getHeaderSearchInput() {
        return document.querySelector('.header-search input');
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

    document.addEventListener('DOMContentLoaded', function () {
        const input = getHeaderSearchInput();
        if (!input) {
            return;
        }

        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') {
                return;
            }

            const query = input.value.trim().toUpperCase();
            if (!query) {
                return;
            }

            event.preventDefault();
            goToIndexStock(query);
        });
    });
})();