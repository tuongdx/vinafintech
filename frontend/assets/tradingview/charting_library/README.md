# charting_library — Thư mục dành riêng cho TradingView Advanced Charts

> ⚠️ **KHÔNG có thư viện TradingView ở đây.** Đây là vị trí dành riêng (placeholder)
> để bạn đặt bản **charting_library** do TradingView cấp phép. TuniX **không tự tải
> hay nhúng thư viện từ nguồn không chính thức** — bạn phải đưa thư viện được
> cấp phép vào đây theo hướng dẫn chính thức của TradingView.

## Cách cài đặt

1. Đăng ký / tải **TradingView Charting Library** (Advanced Charts) theo quy trình
   chính thức: https://www.tradingview.com/advanced-charts/
2. Giải nén bản `charting_library.zip` được cấp.
3. Copy **nội dung bên trong** thư mục `charting_library/` của bản zip vào đúng
   thư mục này:

```
frontend/assets/tradingview/charting_library/
├── charting_library.min.js      <- file chính (BẮT BUỘC)
├── charting_library.d.ts
├── static/                      <- assets (icon, css...) BẮT BUỘC
│   ├── chart.css
│   ├── images/...
│   └── ...
└── (các file khác của bản gốc)
```

> Lưu ý: chỉ copy **nội dung bên trong** `charting_library/` (không lồng thêm một
> cấp `charting_library/charting_library/`). Đường dẫn tải trên website là:
> `/static/tradingview/charting_library/charting_library.min.js`

## Sau khi đặt thư viện

Không cần sửa code. `frontend/assets/tradingview/tv-chart.js` sẽ tự phát hiện thư
viện và khởi tạo TradingView Advanced Charts với **datafeed dữ liệu TuniX**
(`/api/chart/*`). Biểu đồ chạy ngay trên trang chi tiết cổ phiếu (nút "TradingView").

## Cấu trúc dữ liệu

```
TradingView Advanced Charts
   |  Datafeed API (tv-datafeed.js)
   v
/api/chart/*  (FastAPI backend TuniX)
   |  chart_service -> vnstock_service -> Simplize (dữ liệu OHLCV TuniX)
   v
Dữ liệu OHLCV thực của TuniX (1D/1W/1M)
```
