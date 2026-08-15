/**
 * ChartExport (PHASE 7) — xuất biểu đồ ra ảnh PNG.
 *
 * Lấy canvas chart (candles + indicators + sub-pane) từ `chart.takeScreenshot()`,
 * rồi composite các SVG overlay (drawing + S/R/VP/Confluence) lên trên.
 * Vẽ thêm tiêu đề nhỏ (symbol · period) ở góc trên trái.
 */

function svgToDataURL(svgEl, w, h) {
    const clone = svgEl.cloneNode(true);
    clone.removeAttribute('style');
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', w);
    clone.setAttribute('height', h);
    clone.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    const xml = new XMLSerializer().serializeToString(clone);
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml);
}

function loadImage(src) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('Không tải được overlay'));
        img.src = src;
    });
}

/**
 * @param {object} opts
 *   - chartEngine : ChartEngine
 *   - overlays    : SVGElement[] (drawing + analysis)
 *   - title       : string (vd 'FPT · 1Y')
 * @returns {Promise<string|null>} data URL PNG
 */
export async function exportChartPNG(opts) {
    const chartEngine = opts.chartEngine;
    const overlays = Array.isArray(opts.overlays) ? opts.overlays : [];
    const chart = chartEngine.getChart();
    const container = chartEngine.getContainer();
    if (!chart || !container) return null;

    const src = chart.takeScreenshot();
    if (!src) return null;

    const out = document.createElement('canvas');
    out.width = src.width;
    out.height = src.height;
    const ctx = out.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(src, 0, 0);

    const cssW = container.clientWidth || src.width;
    const cssH = container.clientHeight || src.height;

    // Composite các overlay (vẽ theo đúng toạ độ CSS px của container)
    for (const svgEl of overlays) {
        if (!svgEl || !svgEl.outerHTML) continue;
        try {
            const img = await loadImage(svgToDataURL(svgEl, cssW, cssH));
            ctx.drawImage(img, 0, 0, out.width, out.height);
        } catch (e) {
            /* bỏ qua overlay lỗi */
        }
    }

    // Tiêu đề nhỏ
    if (opts.title) {
        const label = String(opts.title);
        ctx.font = '600 13px system-ui, -apple-system, "Segoe UI", sans-serif';
        ctx.textBaseline = 'top';
        const pad = 12;
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = 'rgba(15, 23, 42, 0.62)';
        ctx.fillRect(pad, pad, tw + 18, 24);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(label, pad + 9, pad + 6);
    }

    return out.toDataURL('image/png');
}
