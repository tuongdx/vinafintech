/* ============================================================
   embed-frame.js — báo chiều cao + đồng bộ theme cho trang nhúng iframe.

   Shell (index.html) tạo iframe cao mặc định 560px và chỉ mở rộng khi
   trang con gửi message `vinafintech-embed-height`. Trang nào không gửi
   sẽ bị cắt nội dung (phải scroll trong iframe).

   Cách dùng: khai báo phần tử gốc cần đo qua `data-embed-root` trên <body>
   (bỏ trống = đo body) rồi nhúng file này ở cuối <body>, SAU theme-instant.js.
   ============================================================ */
(function () {
    if (!document.body) return;

    var root = document.body;
    var target = (root.getAttribute('data-embed-root') || '') && document.querySelector(root.getAttribute('data-embed-root'));
    if (!target) target = root;

    function inFrame() {
        return window.parent && window.parent !== window;
    }

    function applyTheme(mode) {
        root.classList.toggle('dark-mode', mode === 'dark');
    }

    function notifyParentHeight() {
        try {
            var height = Math.ceil(target.getBoundingClientRect().height);
            if (!height || height <= 0) return;
            // Làm tròn lên 1px + bỏ qua thay đổi < 2px để không bắn message liên tục.
            if (Math.abs(height - (window.__lastEmbedHeight || 0)) < 2) return;
            window.__lastEmbedHeight = height;
            if (inFrame()) window.parent.postMessage({ type: 'vinafintech-embed-height', height: height }, '*');
        } catch (e) {}
    }

    if (inFrame()) root.classList.add('embedded');

    window.addEventListener('message', function (event) {
        var data = event.data;
        if (!data || data.type !== 'vinafintech-theme') return;
        var mode = data.mode === 'dark' ? 'dark' : 'light';
        try { localStorage.setItem('theme', mode); } catch (e) {}
        applyTheme(mode);
        setTimeout(notifyParentHeight, 50);
    });

    try { applyTheme(localStorage.getItem('theme') === 'dark' ? 'dark' : 'light'); } catch (e) {}

    // Nội dung (bảng lãi suất, biểu đồ, tin tức) về sau khi load → đo lại.
    window.addEventListener('load', function () {
        setTimeout(notifyParentHeight, 60);
        setTimeout(notifyParentHeight, 400);
    });
    window.addEventListener('resize', notifyParentHeight);
    if (window.ResizeObserver) new ResizeObserver(notifyParentHeight).observe(target);

    notifyParentHeight();
})();
