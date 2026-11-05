# VinaFintech × TradingView — Integration Layer (Lightweight Charts + Advanced Charts)

## TRẠNG THÁI HIỆN TẠI (2026-08-15)

Biểu đồ đang CHẠY trên VinaFintech là **Lightweight Charts** — thư viện mã nguồn mở
chính thức của TradingView (`github.com/tradingview/lightweight-charts`, MIT),
đã tự host tại:

```
frontend/assets/vendor/lightweight-charts.standalone.production.js   (v5.2.1)
```

Nó được `frontend/StockDetail.html` dùng làm biểu đồ nến (candlestick + volume +
OHLC + crosshair + timeframe 1W/1M/3M/6M/1Y/3Y/5Y/ALL + dark/light), toàn bộ dữ
liệu OHLCV lấy từ **VinaFintech** qua backend `/api/chart/history`.

> Lightweight Charts **không phải** TradingView Advanced Charts (bản cấp phép).
> Nếu sau này bạn có bản `charting_library` do TradingView cấp, lớp tích hợp bên
> dưới (`tv-datafeed.js`, `tv-chart.js`, `charting_library/`) vẫn giữ nguyên để
> chuyển sang Advanced Charts mà không cần xây lại.

## Cấu trúc

```
frontend/assets/
├── vendor/lightweight-charts.standalone.production.js   # thư viện đang dùng (v5.2.1)
└── tradingview/
    ├── tv-datafeed.js                 # TuniXDatafeed — Datafeed Advanced Charts (để dành)
    ├── tv-chart.js                    # TuniXTradingViewChart — component Advanced Charts (để dành)
    └── charting_library/              # (placeholder) đặt thư viện cấp phép tại đây
        └── README.md                  # hướng dẫn cài thư viện
```

## Luồng dữ liệu

```
VinaFintech (Simplize adapter -> OHLCV daily)
        ↓
/api/chart/*  (FastAPI: config, symbols, history, search, quote, time)
        ↓
StockDetail.html (Lightweight Charts — đang chạy)
   hoặc  TuniXDatafeed -> TradingView Advanced Charts (khi có charting_library)
        ↓
frontend StockDetail.html
```

## Timeframe hỗ trợ

Chỉ hiển thị timeframe mà dữ liệu VinaFintech thực sự có:

| Resolution | Nguồn |
|------------|-------|
| `1D`       | Nến daily của VinaFintech/Simplize (250 bar ≈ 1 năm) |
| `1W`       | Gộp tuần từ nến daily (backend aggregate) |
| `1M`       | Gộp tháng từ nến daily (backend aggregate) |

`1m/5m/15m/30m/1H/4H` **không** được bật vì VinaFintech chưa có lịch sử intraday OHLCV
nhiều ngày (không tạo dữ liệu giả).

## Lưu layout chart

- `saveChartLayout()` / `loadChartLayout()` — abstraction hiện lưu `localStorage`
  (theo mã cổ phiếu). Muốn lưu vào database VinaFintech sau này: thay
  `layoutStorage.save/load` trong `tv-chart.js` bằng API
  `GET/PUT /api/chart/layout?symbol=...` (có auth) là xong.

## Realtime

VinaFintech hiện chưa có nguồn OHLCV realtime → chart chạy chế độ **historical**.
`subscribeBars` trong `tv-datafeed.js` đã được thiết kế sẵn (polling
`/api/chart/quote`), bật bằng `REALTIME_ENABLED = true` khi có nguồn thật.
