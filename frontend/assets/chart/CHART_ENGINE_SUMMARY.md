# VinaFintech — Advanced Financial Chart Engine · Tổng kết toàn dự án

**Phiên bản:** 25 phase (PHASE 1 → PHASE 25) · **Nền tảng:** Lightweight Charts v5.2.1 (TradingView open-source, MIT)
**Dữ liệu:** 100% từ backend VinaFintech (không dữ liệu giả) · **Trạng thái:** ✅ hoàn thiện, đã kiểm thử trình duyệt thực.

---

## 1. Tổng quan

Biểu đồ nến chuyên nghiệp cho trang **VinaFintech** (phân tích cổ phiếu Việt Nam), xây dựng theo kiến trúc
**modular, nhẹ, không fake data**, hoàn thiện qua **25 phase** — mỗi phase một tính năng lớn, kiểm thử
trong trình duyệt thực (Playwright) trước khi bàn giao.

- **Vị trí mã nguồn:** `frontend/assets/chart/`
- **Trang host:** `frontend/StockDetail.html` (nhúng qua iframe từ `index.html`, route `/cophieu-{symbol}`)
- **Điểm vào:** `stockDetailChart.js` → `createStockDetailChart(options)`
- **Thư viện:** `frontend/assets/vendor/lightweight-charts.standalone.production.js` (self-hosted, v5.2.1)

---

## 2. Kiến trúc (mô-đun)

```
frontend/assets/chart/
├── stockDetailChart.js        # CONTROLLER (entry) — gắn kết mọi engine, khôi phục state, debug hook
├── core/
│   ├── chartConfig.js         # Hằng số: kỳ hạn (PERIOD_MAP), màu theo theme
│   ├── chartEngine.js         # Rendering lõi: tạo chart, nến + volume, crosshair→legend, setData/theme/range
│   ├── chartState.js          # State (symbol, period, theme, bars, loading, error)
│   ├── chartEvents.js         # Sự kiện đồng bộ
│   ├── persistence.js         # localStorage: storageGet/Set/Remove + STORAGE_KEYS (6 key)
│   ├── chartExport.js         # Xuất PNG (takeScreenshot + SVG overlays)
│   ├── paneResizer.js         # Kéo resize pane (drag handle, stretch factor)
│   ├── crosshairSync.js       # PH20: đồng bộ crosshair giữa các sub-pane (SVG)
│   ├── hotkeys.js             # PH21: quản lý phím tắt nâng cao
│   ├── fullscreen.js          # PH22: Fullscreen API trên iframe
│   ├── lastPriceLine.js       # PH25: đường giá mới nhất (price line native)
├── data/
│   ├── dataProvider.js        # Gọi API: /api/chart/history, /api/stocks/index-detail, /api/stocks/.../history
│   ├── dataNormalizer.js      # Chuẩn hoá dữ liệu → {time, open, high, low, close, volume}
│   └── dataCache.js           # Cache dữ liệu
├── drawings/
│   ├── drawingManager.js      # Danh sách bản vẽ (theo symbol+timeframe), undo/redo, lưu/khôi phục
│   ├── geometry.js            # Tính toán hình học (hit test…)
│   ├── shapes.js              # Định nghĩa + render từng loại hình
│   ├── drawingEngine.js       # SVG overlay z-4, pointer events, magnet, text input, context menu, phím 1-9/0/m
├── indicators/
│   ├── calculations.js        # ma, ema, rsi, macd, atr, bollinger, stochastic, adx, cci, obv, ichimoku
│   ├── indicatorConfig.js     # Định nghĩa chỉ báo (INDICATOR_DEFS, pane overlay/sub)
│   └── indicatorEngine.js     # Vòng đời chỉ báo: add/remove/update, moveToPane động, valuesAtTime
├── analysis/
│   ├── analysis.js            # detectSR, computeVolumeProfile
│   ├── analysisEngine.js      # SVG overlay z-3: S/R · Volume Profile · Confluence
│   └── confluence.js          # computeConfluence (MA/BOLL/Fib/S-R/VP…)
├── compare/
│   └── comparisonEngine.js    # So sánh đa mã (overlay scale 'compare'/'compareAbs'), mode % / Giá
└── ui/
    ├── legend.js              # Chú thích OHLC + Vol + chỉ báo (theo crosshair)
    ├── toolbar.js             # Thanh công cụ vẽ (trái)
    ├── indicatorPanel.js      # Dropdown "Chỉ báo"
    ├── analysisPanel.js       # Dropdown "Phân tích" (S/R·VP·Hội tụ·Thị trường)
    ├── comparisonPanel.js     # Dropdown "So sánh" (thêm mã, chip, mode %/Giá)
    ├── marketBreadth.js       # Độ rộng thị trường (VNINDEX breadth)
    ├── fullscreenWorkspace.js # Toàn màn hình: sidebar (thông tin/ngành/chỉ báo/S-R) + bottom dashboard
    ├── accumulationPanel.js   # Panel "Giả định tích sản" (dùng lại dữ liệu block ở trang chi tiết)
    └── settingsPanel.js       # PH23: popover Cài đặt (6 tuỳ chọn)
```

**Luồng dữ liệu:** `DataProvider` (API) → `ChartState` → `ChartEngine.setData` → các engine vẽ overlay.
**Theme:** `body.dark-mode` + postMessage `{type:'vinafintech-theme', mode}`.
**Chiều cao nhúng:** postMessage `{type:'vinafintech-embed-height', height}`.

---

## 3. Danh sách 25 phase

| Phase | Tính năng |
|------|-----------|
| 1 | **Chart core** — nến + volume + crosshair → legend, đổi theme không recreate |
| 2 | **Drawing engine** — 9 công cụ vẽ (trendline, horizontal/vertical, channel, fib, fib-extension, rect, text, marker, measure), SVG overlay |
| 3 | **Indicator engine** — MA/EMA/RSI/MACD/ATR/BOLL + sub-pane động (`moveToPane`) |
| 4 | **S/R + Volume Profile** — tự phát hiện kháng cự/hỗ trợ, phân bố khối lượng |
| 5 | **Confluence + Market Breadth** — hội tụ tín hiệu (MA/BOLL/Fib/S-R/VP) + độ rộng thị trường |
| 6 | **Persist** — lưu/khôi phục drawings, indicators, analysis qua localStorage |
| 7 | **Export PNG** — `chart.takeScreenshot()` + composite SVG overlays |
| 8 | **Multi-symbol comparison** — overlay so sánh nhiều mã |
| 9 | **Advanced indicators** — STOCH, ADX, CCI, OBV |
| 10 | **Ichimoku + Kumo** — cloud vẽ bằng SVG (Span A/B ghép theo thời gian) |
| 11 | **Pane auto-height** — tự tăng chiều cao chart theo số sub-pane |
| 12 | **Consolidate header** — gom nút điều khiển 1 hàng |
| 13 | **Multi-comparison rework** — thêm/xoá nhiều mã, chip |
| 14 | **Context menu + shortcuts** — menu chuột phải, phím 1-9/0/m, Ctrl+Z/Y, Delete |
| 15 | **Crosshair-sync legend** — legend cập nhật theo crosshair (OHLC + chỉ báo tại bar) |
| 16 | **Drag-resize pane** — kéo tay cầm đổi kích thước pane (stretch factor) |
| 17 | **Persist layout + Reset** — lưu stretch pane + nút đặt lại mọi thiết lập |
| 18 | **Responsive / mobile** — header gọn, kỳ hạn cuộn ngang, panel neo trái, ẩn nhãn nút ≤640px |
| 19 | **Comparison mode % / Giá** — hiển thị phần trăm hoặc giá tuyệt đối (thang riêng) |
| 20 | **Crosshair sync sub-pane** — đường ngang + chip giá trị ở mọi sub-pane khi hover |
| 21 | **Hotkeys + Bảng phím tắt** — điều hướng, kỳ hạn, phân tích, hành động; overlay `?` |
| 22 | **Toàn màn hình** — Fullscreen API (cần iframe `allow="fullscreen"`) |
| 23 | **Cài đặt biểu đồ** — grid, magnet, legend, sync, volume, toolbar (persist) |
| 24 | **Điều hướng từng nến** — Shift+←/→ di chuyển crosshair cố định + đồng bộ legend/sync |
| 25 | **Đường giá mới nhất** — dashed price line tại close nến cuối, màu theo tăng/giảm |

---

## 4. Phím tắt (PH21 + PH24)

| Phím | Hành động |
|------|-----------|
| `←` `→` | Cuộn biểu đồ |
| `Shift`+`←` `→` | Di chuyển crosshair từng nến |
| `+` `−` | Phóng to / thu nhỏ |
| `F` | Khớp toàn bộ dữ liệu |
| `[` `]` | Kỳ hạn trước / sau |
| `S` `V` `C` `B` | S/R · Volume Profile · Hội tụ · Độ rộng thị trường |
| `E` | Xuất ảnh PNG |
| `R` | Đặt lại bố cục (có xác nhận) |
| `?` | Bảng phím tắt |
| `Esc` | Đóng popup / huỷ vẽ |
| `1`–`9`, `0`, `M` | Chọn công cụ vẽ |
| `Del` / `Ctrl+Z` / `Ctrl+Y` | Xoá / Hoàn tác / Làm lại |

---

## 5. Persistence (localStorage)

| Key | Nội dung |
|-----|----------|
| `vinafintech.drawings.v1` | Bản vẽ (theo symbol + timeframe) |
| `vinafintech.indicators.v1` | Chỉ báo đang bật + cài đặt |
| `vinafintech.analysis.v1` | S/R · VP · Confluence · Market Breadth |
| `vinafintech.compare.v1` | `{ mode, symbols }` so sánh |
| `vinafintech.layout.v1` | Stretch factor các pane |
| `vinafintech.settings.v1` | Cài đặt hiển thị (grid/magnet/legend/sync/volume/toolbar) |

---

## 6. Debug hook

`window.__vinafintechChartDebug` gồm: `engine, indicatorEngine, drawingEngine, drawingManager,
analysisEngine, marketBreadth, comparisonEngine, exportPNG, crosshairSync, hotkeys, fullscreen,
settingsPanel, lastPriceLine`.

---

## 7. Điểm lưu ý kỹ thuật (Lightweight Charts v5.2.1)

- `paneIndex` option khi `addSeries` **bị bỏ qua** → phải `series.moveToPane(n)`.
- `chart.addSeries(L.CandlestickSeries, opts)` (không phải `addCandlestickSeries`).
- `series.priceToCoordinate(price)` trả y **tương đối pane**; `priceScale().priceToCoordinate` **không tồn tại**.
- `timeScale().shiftVisibleRange` **không tồn tại** → pan bằng `setVisibleLogicalRange`.
- Crosshair vẽ **đường dọc xuyên mọi pane** nhưng **đường ngang chỉ pane hover** → PH20 dùng SVG bổ sung.
- `param.paneIndex` có trong `subscribeCrosshairMove` (v5).
- Fullscreen trong iframe cần thẻ iframe có `allow="fullscreen"`.
- **Version asset:** sửa file trong `/static/chart/**` thì PHẢI bump `CHART_ASSET_VER` (StockDetail.html)
  và `?v=` ở câu `import` tương ứng — asset có `?v=` được cache `immutable` 1 năm nên trình duyệt sẽ
  giữ bản cũ vô thời hạn (đã gặp: panel fullscreen còn nhãn cũ + không có dữ liệu sau khi đổi brand).

---

## 8. Giới hạn đã biết (tổng hợp)

1. Dữ liệu theo kỳ (daily/weekly) — không có realtime tick; giá mới nhất cập nhật khi tải/đổi kỳ hạn.
2. Export PNG qua `<a download>` — trình duyệt có thể chặn tải trong iframe.
3. `R` (reset) dùng `window.confirm` — chặn test tự động trong iframe.
4. So sánh mode **Giá** dùng thang riêng (các mã có giá rất khác nhau → index nằm gần đáy).
5. Fullscreen chỉ hiệu quả khi iframe cha có `allow="fullscreen"`.
6. Bảng phím tắt là HTML tĩnh (thêm phím mới phải sửa cả `hotkeys.js` lẫn HTML).

---

## 9. Hướng dẫn kiểm tra

1. Chạy backend: uvicorn trên cổng 8000 (venv `c:/4.Deverlop/1.Python/VinaFintech/.venv`).
2. Mở `http://localhost:8000/cophieu-FPT` (hoặc `cophieu-DXG`).
3. Mở DevTools console, gõ `window.__vinafintechChartDebug` để kiểm tra các module.
4. Kiểm tra nhanh: bật chỉ báo RSI/MACD (sub-pane), hover để xem crosshair sync, `?` để mở bảng phím tắt,
   nút Cài đặt để bật/tắt hiển thị, nút toàn màn hình, Shift+←/→ điều hướng nến.
