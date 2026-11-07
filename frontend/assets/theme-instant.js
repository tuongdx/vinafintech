/* ============================================================
   theme-instant.js — đổi dark/light: màu đổi ngay & đồng bộ toàn trang.

   Bật class `theme-switching` trên <html> ngay khi class `dark-mode` của <body>
   thay đổi (theme-instant.css tắt toàn bộ transition trong lúc đó) rồi gỡ sau
   2 frame. Nhờ vậy nền/viền/chữ của mọi block đổi cùng một frame, không fade
   lệch nhau; hiệu ứng hover/animation ngoài lúc chuyển theme không bị ảnh hưởng.

   Dùng MutationObserver nên mọi nơi toggle theme (nút header, message
   `tunix-theme`/`toggleTheme` từ shell gửi vào iframe, hay localStorage lúc tải
   trang) đều được áp dụng mà không phải sửa code từng trang.
   ============================================================ */
(function () {
    var root = document.documentElement;
    var body = document.body;
    if (!body) return;

    var isDark = body.classList.contains('dark-mode');
    var timer = null;

    function endSwitch() {
        root.classList.remove('theme-switching');
    }

    function beginSwitch() {
        root.classList.add('theme-switching');
        if (timer) clearTimeout(timer);
        // Gỡ sau 2 frame: đủ để style mới được áp dụng mà không kịp khởi động transition.
        timer = setTimeout(function () {
            if (window.requestAnimationFrame) {
                window.requestAnimationFrame(function () {
                    window.requestAnimationFrame(endSwitch);
                });
            } else {
                endSwitch();
            }
        }, 0);
    }

    if (window.MutationObserver) {
        new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i += 1) {
                if (mutations[i].attributeName !== 'class') continue;
                var now = body.classList.contains('dark-mode');
                if (now !== isDark) {
                    isDark = now;
                    beginSwitch();
                    return;
                }
            }
        }).observe(body, { attributes: true, attributeFilter: ['class'] });
    }
})();
