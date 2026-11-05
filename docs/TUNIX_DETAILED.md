# VinaFintech — Tài liệu Đặc tả Chi Tiết (Detailed Specification)

> Phiên bản: 1.0 · Cập nhật: 2026-08-16
> Tài liệu này đi sâu vào từng module: cấu hình, cơ sở dữ liệu, API, nguồn dữ liệu, cache, frontend, VinaF AI, chart engine và deploy.
> Bản tổng quan ngắn gọn xem tại `docs/TUNIX_SPEC.md`.

---

## Mục lục

1. [Kiến trúc & luồng dữ liệu](#1)
2. [Cấu hình hệ thống (Settings)](#2)
3. [Cơ sở dữ liệu chi tiết](#3)
4. [API chi tiết theo từng router](#4)
5. [Nguồn dữ liệu & cache map](#5)
6. [Frontend — luồng điều hướng & giao thức postMessage](#6)
7. [VinaF AI — chat & tools](#7)
8. [Chart Engine chi tiết](#8)
9. [VinaF AI market ticker — đã gỡ bỏ](#9)
10. [Hiệu năng & tối ưu](#10)
11. [Deploy & biến môi trường](#11)
12. [Các tính năng lớn đã triển khai](#12)

---

<a name="1"></a>
## 1. Kiến trúc & luồng dữ liệu

```
                        ┌──────────────────────────────────────────────┐
  Trình duyệt  ───────▶ │  Frontend tĩnh (frontend/)                   │
                        │  index.html = SHELL (header, chat)           │
                        │  ├─ iframe: Home / StockDetail / tin-tuc / caidat / taikhoan / doimatkhau │
                        │  └─ fetch + DOMParser cho trang nhỏ           │
                        └───────────────────┬──────────────────────────┘
                                            │ /api/*
                                            ▼
                        ┌──────────────────────────────────────────────┐
                        │  Backend FastAPI  (app/main.py)              │
                        │  Middleware: CORS, bảo vệ /QuanTri (404)     │
                        │  Routers (17) → Services                     │
                        └──────────┬───────────┬───────────┬───────────┘
                                   ▼           ▼           ▼
                        ┌──────────────┐ ┌───────────┐ ┌──────────────┐
                        │ vnstock_svc  │ │chart_svc  │ │  ai_service  │
                        │ Simplize/VCI │ │ KBS/agg   │ │ DeepSeek     │
                        │ /KBS/foreign │ │ index     │ │ tools/insight│
                        └──────┬───────┘ └─────┬─────┘ └──────┬───────┘
                               ▼               ▼              ▼
                     Simplize api2.simplize.vn · VCI iQ · KBS · DeepSeek
                               └─────────── PostgreSQL/SQLite ───────────┘
```

**Luồng trang chi tiết cổ phiếu** (mục tiêu hiệu năng — tải song song):

```
mở /cophieu-{MÃ}
  │
  ├─ (ngay) loadTunixChart(symbol)        → vẽ chart, không chờ quote
  ├─ (song song) loadProfitAnalysis / Shareholders / Reports / Accumulation
  │              loadOrderBook / RecentTrades / PeerComparison
  └─ (chờ) quote  → renderMetrics + header (giá, %, cập nhật favorite)
       → nếu mã chỉ số (VNINDEX, VN30, HNX, HNXINDEX, HNX30, UPCOM, UPINDEX, UPCOMM) → bỏ qua các block không có dữ liệu
```

---

<a name="2"></a>
## 2. Cấu hình hệ thống (Settings — `app/core/config.py`, đọc từ `.env`)

| Biến | Mặc định | Mô tả |
|---|---|---|
| `APP_NAME` / `APP_ENV` / `API_PREFIX` | VinaFintech API / development / `/api` | định danh |
| `DATABASE_URL` | `sqlite:///./tunix.db` | prod dùng Postgres Render |
| `SECRET_KEY`, `ALGORITHM` | change_me / HS256 | JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 60 | access token |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | 52560000 (~100 năm) | refresh token — phiên "nhớ" tới khi đăng xuất |
| `RESET_TOKEN_EXPIRE_MINUTES` | 30 | token reset mật khẩu |
| `FRONTEND_URL` | `http://localhost:8000` | redirect OAuth + link reset |
| `VNSOCKET_API_KEY` / `REDIS_URL` | "" | (dự phòng, chưa dùng) |
| `FRONTEND_DIR` | `frontend` | thư mục file tĩnh |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_TLS` | – / 587 / – / – / no-reply@tunix.vn / true | gửi email reset |
| `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | "" | OAuth Google |
| `LOGIN_RATE_LIMIT_MINUTES/MAX` | 15 / 10 | chống brute-force |
| `DEEPSEEK_API_KEY` | "" | VinaF AI (bắt buộc để chat + nhận định AI) |
| `DEEPSEEK_BASE_URL` / `MODEL` | https://api.deepseek.com / deepseek-chat | model AI |
| `AI_CHAT_MAX_TURNS` | 8 | số vòng tool-calling tối đa |
| `AI_CHAT_MAX_TOKENS` | 4096 | giới hạn trả lời |
| `AI_RATE_LIMIT_MINUTES/MAX` | 5 / 30 | rate limit VinaF AI |

---

<a name="3"></a>
## 3. Cơ sở dữ liệu chi tiết (`app/models.py`)

**Enums**: `RoleEnum` (super-admin, admin, content-manager, editor, support, member), `NewsStatusEnum` (pending-approval, approved, published, rejected), `TicketStatusEnum` (open, assigned, resolved, closed), `PlanTierEnum` (free, basic, premium, pro), `UserDeleteRequestStatus` (pending, approved, rejected), `NotificationType` (stock, news, system).

| Bảng | Cột chính |
|---|---|
| **users** | id, email (unique), full_name, hashed_password, role, plan, is_active, phone, avatar (Text/base64), plan_expires_at, last_login, terms_accepted, notify_reports, notify_news, pref_theme, created_at |
| **social_accounts** | user_id (FK), provider, provider_user_id |
| **user_delete_requests** | target_user_id, requested_by_id, reason, status (pending/approved/rejected), reviewed_by_id, created_at, reviewed_at |
| **revoked_tokens** | jti (unique), expires_at, created_at |
| **password_reset_tokens** | user_id, token (unique), expires_at, used_at, created_at |
| **news** | id, title, summary, content, category, image_url, author_name, image_caption, tags, source, source_url, views, author_id, status, created_at, published_at (ngày giờ ĐĂNG, giờ VN — lấy từ file JSON khi import bằng tool; NULL thì suy ra `created_at` + 7h) |
| **support_tickets** | subject, message, user_id, assignee_id, status (open/assigned/resolved/closed), created_at |
| **support_replies** | ticket_id, user_id, message, created_at |
| **faqs** | question, answer, category, is_visible |
| **contact_messages** | full_name, email, phone, subject, message, is_read, created_at |
| **feedback** | type (news/market), content, user_name, user_email, user_id, is_read, created_at |
| **plans** | name, tier, price, is_active |
| **features** | key, name, description, access_level, enabled |
| **settings** | key, value |
| **logs** | level, message, created_at |
| **watchlist** | user_id, symbol (unique per user), created_at |
| **notification_reads** | user_id, news_id, read_at |
| **notifications** | user_id, type (stock/news/system), title, body, symbol, link, ref_key, is_read, created_at |
| **system_announcements** | title, body, link, created_at |
| **stock_symbols** | symbol (unique), name, exchange, added_by_id, created_at |

---

<a name="4"></a>
## 4. API chi tiết theo từng router (prefix `/api`)

### 4.1 `stocks` — dữ liệu cổ phiếu & thị trường
| Method & Path | Params | Trả về chính |
|---|---|---|
| `GET /stocks/search?q=` | q | danh sách mã khớp |
| `GET /stocks/{symbol}/quote` | – | `{valid, symbol, name, exchange, price, change, percent_change, reference_price, open/high/low/volume/value, bid/ask, market_cap, pe, pb, eps, roe, roa, avg_volume_10d, pct_1w/1m/ytd, source}` |
| `GET /stocks/{symbol}/history?interval=&period=` | interval: 1d, 1w, 1m, 1h · period: 1m, 3m, 6m, 1y, 5y | mảng OHLCV bar |
| `GET /stocks/{symbol}/financials` | – | (fallback) |
| `GET /stocks/{symbol}/insider-trades` | – | giao dịch nội bộ |
| `GET /stocks/{symbol}/shareholders` | – | cơ cấu cổ đông |
| `GET /stocks/{symbol}/reports?category=` | financial/analysis/annual/agm/prospectus | tài liệu |
| `GET /stocks/{symbol}/dividends` | – | lịch sử cổ tức |
| `GET /stocks/{symbol}/company-summary` | – | tóm tắt doanh nghiệp |
| `GET /stocks/{symbol}/order-book` | – | sổ lệnh (bids/asks) |
| `GET /stocks/{symbol}/trades?limit=&before=` | 50 | giao dịch khớp lệnh gần nhất; `before` (epoch giây) để phân trang lấy toàn bộ phiên (VCI tối đa 100 lệnh/request) |
| `GET /stocks/{symbol}/money-flow?sessions=` | 10 | dòng tiền ròng N phiên gần nhất **tính từ OHLCV** (không có lịch sử mua/bán chủ động): `{symbol, sessions, source:"ohlcv-derived", items:[{date, close, change_pct, volume, value, clv, inflow, outflow, net}], summary:{inflow, outflow, net, ratio, inflow_sessions, outflow_sessions, avg_value}}` |
| `GET /stocks/{symbol}/peers?limit=` | 10 | so sánh tương quan `{peers:[{symbol,name,is_current,market_cap,revenue,net_profit}]}` |
| `GET /stocks/{symbol}/profit-analysis?quarters=` | 0 (toàn bộ lịch sử) | `{symbol, source:"vci-finance", quarters:[{year,quarter,label,period,revenue,gross_profit,profit_before_tax,net_profit}]}` |
| `GET /stocks/indices` | – | VNINDEX/VN30/DJI |
| `GET /stocks/market-overview` | – | VNINDEX, VN30, DJI, BTC, vàng TG/VN |
| `GET /stocks/index-detail?key=&period=` | vnindex, vn30, hnx, hnx30, upcom, dowjones, nikkei, kospi, hkex, sgx, bitcoin, gold_world, gold_vn | `{value, change, percent_change, history[]}` |
| `GET /stocks/market-map` | – | bản đồ thị trường |
| `GET /stocks/realtime-board?exchanges=` | HOSE,HNX,UPCOM | `{total_symbols, data:[{symbol, exchange, price, matched_price, reference_price, open, high, low, change, percent_change, volume, value, bid/ask, as_of, source}]}` |
| `GET /stocks/foreign-trading?type=&period=` | foreign · 1D | `{totals:{buy,sell,net}, buy_list[], sell_list[]}` |
| `GET /stocks/top-stocks?tab=&period=` | breakout/foreign-buy/foreign-sell/break-high/break-low · 1w | top mã |
| `GET /stocks/watchlist` | – | sparkline VN30 |

### 4.2 `chart` — chart engine backend
| Path | Params | Trả về |
|---|---|---|
| `GET /chart/config` | – | `{supports_search, supports_group_request, supports_marks, supports_timescale_marks, supports_time, exchanges:[HOSE,HNX,UPCOM], symbols_types, supported_resolutions:["1D","1W","1M","1H"], resolutions, timezone:"Asia/Ho_Chi_Minh", session:"0900-1500", has_intraday, locale, realtime}` |
| `GET /chart/symbols` | symbol | symbol info |
| `GET /chart/history?symbol=&interval=&from=&to=&countback=` | – | `{bars:[{time,open,high,low,close,volume}], noData}` |
| `GET /chart/search?q=` | – | tìm mã |
| `GET /chart/quote` | symbol | quote chart |
| `GET /chart/server-time` | – | `{time}` |
| `GET /chart/library-status` | – | trạng thái thư viện |

### 4.3 `ai` — VinaF AI
| Path | Auth | Mô tả |
|---|---|---|
| `GET /ai/status` | public | `{configured, model, enabled, access_level}` |
| `POST /ai/chat` | JWT + rate limit | body `{messages:[{role,content}], stream}` → `{reply, tool_calls}` hoặc SSE |

### 4.4 `auth` — xác thực
`POST /auth/register` · `POST /auth/login` · `POST /auth/logout` · `POST /auth/refresh-token` · `GET /auth/me` · `POST /auth/forgot-password` · `POST /auth/reset-password` · `POST /auth/change-password` · `GET /auth/oauth/{provider}/authorize` · (callback).

### 4.5 `users` — quản lý tài khoản (admin + cá nhân)
`GET/POST /users` · `PUT /users/me` · `PUT /users/{id}` · `DELETE /users/{id}` · `PATCH /users/{id}/role` · `PATCH /users/{id}/plan` · `GET /users/me` · `GET/PUT /users/me/settings` · `POST /users/delete-requests` · `GET /users/delete-requests`.

### 4.6 Nội dung & hỗ trợ
- `news`: `GET /news?page=&category=&q=` (phân trang), `/all`, `/pending`, `/popular`, `/related?symbol=`, `/{id}`, `POST/PUT/DELETE`, `/{id}/approve`.
  Mọi bài trả về kèm 4 trường ngày giờ đăng đúng định dạng file input:
  `published_at` / `published_at_raw` = `2026-09-12T15:50:01+07:00`, `published_date` = `2026-09-12`, `published_time` = `15:50`.
  Danh sách sắp xếp theo `published_at` giảm dần (bài cũ chưa có thì theo `created_at`).
- `tickets`: `GET/POST`, `/{id}/replies`, `/{id}/assign`, `/{id}/resolve`.
- `contact`, `feedback`, `faqs`, `plans`, `features`: CRUD cơ bản (admin tạo/sửa).
- `dashboard/stats`, `logs`, `tools/import-news` (import JSON → news, tự đọc `published_at`; bài trùng URL được bổ sung lại ngày giờ), `stocks_admin` (CRUD mã, `/lookup`).

---

<a name="5"></a>
## 5. Nguồn dữ liệu & Cache Map

### 5.1 Nguồn
| Nguồn | Base URL | Dùng cho |
|---|---|---|
| Simplize API | `https://api2.simplize.vn` | `/api/historical/prices/last|chart`, `/api/company/screener/filter`, pageProps web |
| Simplize Web | `https://simplize.vn` | scrape `__NEXT_DATA__` (summary, dividends) |
| VCI iQ | `https://iq.vietcap.com.vn/api/iq-insight-service` | BCTC / lợi nhuận |
| KBS | `https://kbbuddywts.kbsec.com.vn/iis-server/investment` | OHLCV sâu + 1H |
| DeepSeek | `https://api.deepseek.com/chat/completions` | VinaF AI |

### 5.2 Cache map (`SimpleTTLCache` — in-memory, dict)
| Key | TTL | Ghi chú |
|---|---|---|
| `quote:{SYM}` | 15s | quote cổ phiếu |
| `history:{SYM}:{int}:{period}` | 180s | lịch sử OHLCV |
| `index-detail:{key}:{period}` | 30s | chỉ số |
| `foreign-trading-{period}` | 90s | khối ngoại |
| `realtime-board:{exchanges}` | 10s | độ rộng |
| `market-overview` | ngắn | tổng quan |
| `symbol-directory-base` | 900s | directory gốc |
| `custom-symbols` | 20s | mã admin thêm |
| `symbol-summary:{SYM}` | 86400s (24h) | chỉ số cơ bản + **file cache** `.cache/symbol_summaries.json` |
| `kbs-chart:{sym}:{int}:{sdate}:{edate}` | 60s | chart KBS |
| `kbs-earliest` | 3600s | mốc sớm nhất |

---

<a name="6"></a>
## 6. Frontend — điều hướng & giao thức postMessage

### 6.1 `loadContent(page)` — 2 nhánh
- **Embedded** (Home, StockDetail, tin-tuc, caidat, taikhoan, doimatkhau, tintuc):
  - Tạo `<iframe id="contentEmbedFrame" src="{page}?v={Date.now()}" allow="fullscreen">`.
  - Theo dõi `tunix-home-height` / `tunix-embed-height` để set chiều cao iframe.
- **Fetch** (trang hỗ trợ/legal): `fetch(page?v={cacheBust})` → `DOMParser` → lấy `.dashboard` hoặc body.

### 6.2 Giao thức postMessage (parent ⇄ iframe)
| Message | Hướng | Ý nghĩa |
|---|---|---|
| `tunix-embed-height` / `tunix-home-height` | iframe → parent | cập nhật chiều cao iframe |
| `tunix-switch-stock` | parent → iframe | đổi mã ngay trong trang (không reload) |
| `tunix-open-stock` | iframe → parent | yêu cầu mở mã (in-place) |
| `tunix-symbol-changed` | iframe → parent | báo mã đã đổi (vd đổi mã khi fullscreen) |
| `tunix-set-theme` | iframe → parent | đồng bộ dark/light |
| `tunix-open-tintuc` / `tunix-open-url` | iframe → parent | mở bài tin / link ngoài |
| `tunix-cat-changed` / `tunix-search-clear` | iframe → parent | đồng bộ nav tin tức |
| `tunix-user-updated` | iframe → parent | render lại menu user |
| `tunix-stock-modal` | iframe → parent | mở modal trong shell |

### 6.3 Fullscreen chart (icon nến)
1. Click icon → `openStockDetailInIndex(sym, true)` → `loadContent('/StockDetail?symbol=X&fs=1')`.
2. **Parent** gọi `frame.requestFullscreen()` **trong cử chỉ click** (có user activation) → 1 click vào fullscreen mọi trình duyệt.
3. Iframe nhận `?fs=1`: thêm sớm class `chart-fullscreen` + `syncParentFs` (nghe parent fullscreenchange) + `maybeAutoFullscreen` (chỉ 1 lần).
4. Thoát: `syncParentFs` gỡ class + `tunixChart.resetChartHeight()` (trả placeholder về 480px).
5. Icon nến fullscreen **đúng mã đang xem** ở trang chi tiết; thoát fullscreen mở từ trang khác thì **quay lại trang trước** (`fsReturnState` + `restorePreviousPage`).

---

<a name="7"></a>
## 7. VinaF AI — chat, tools, market insight

### 7.1 Chat (`ai_service.py`)
- System prompt: phải dùng tool, tiếng Việt, không bịa số liệu, không khuyến nghị mua/bán.
- Vòng lặp tool-calling tối đa `ai_chat_max_turns` (8); `temperature=0.3`; `max_tokens=4096`.
- `_run_tool` chạy handler trong thread (`asyncio.to_thread`).
- **19 tool**:
  - **Cổ phiếu**: `search_stocks(q)`, `get_quote(symbol)`, `get_history(symbol, period)`, `get_financials(symbol)`, `get_insider_trades(symbol)`, `get_shareholders(symbol)`, `get_reports(symbol, category)`, `get_dividends(symbol)`, `get_peers(symbol, limit)`, `get_profit_analysis(symbol, quarters)`, `get_recent_trades(symbol, limit)`.
  - **Thị trường**: `get_market_overview()`, `get_top_stocks(tab, period)`, `get_foreign_trading(type, period)`, `get_realtime_board()`, `filter_stocks(industry, basic, valuation, risk)`, **`get_market_flow()`** (mới).
  - **Tin tức**: `search_news(symbol?, limit)`, `get_news_detail(news_id)`.

### 7.2 `get_market_flow` — dữ liệu dòng tiền (dùng cho chat)
```
vnindex: { value, change, percent_change,
           trend: tăng|giảm|đi ngang (từ %1M & %3M),
           changes: {1w, 1m, 3m, 6m}, high_1y, low_1y }
foreign: { totals: {buy, sell, net}, buy_top[5], sell_top[5] }
liquidity: { last_volume, avg_volume_1m, volume_ratio }
breadth:  { up, down, flat }   (best-effort, tốn kém)
```

---

<a name="8"></a>
## 8. Chart Engine chi tiết

- **Thư viện**: Lightweight Charts v5.2.1 (vendor) — không dùng TradingView Advanced Charts (folder `tradingview/` để dành).
- **Timeframe**: `1D`, `1W`, `1M`, `1H` (KBS 1H = 60P). Timezone `Asia/Ho_Chi_Minh`, session `0900-1500`.
- **Nguồn**: daily Simplize → aggregator cho 1W/1M; KBS cho dữ liệu sâu + 1H; index qua `_INDEX_CHART_MAP` (`vnindex/vn30/hnx/hnx30/upcom`).
- **Module** (`frontend/assets/chart/`):
  - `stockDetailChart.js` — controller, API `{load, setTimeframe, setTheme, destroy, resetChartHeight}`.
  - `core/`: chartEngine, chartState, chartEvents, chartExport, fullscreen, paneResizer, crosshairSync, hotkeys, persistence, lastPriceLine, historyLoader.
  - `data/`: dataProvider (fetch + cache), dataNormalizer, dataCache.
  - `indicators/`, `drawings/`, `analysis/`, `compare/`, `ui/` (legend, toolbar, panels).
- **Fullscreen**: `FullscreenManager` xử lý cả 2 chế độ (iframe tự fullscreen + parent-driven). `resetChartHeightAfterFullscreen` trả placeholder về chiều cao CSS (480px) sau khi thoát.
- **Điều chỉnh quan trọng**: `maybeAutoFullscreen` chỉ chạy 1 lần (cờ `autoFullscreenDone`) để không tự nhảy fullscreen lại khi đổi mã sau khi đã thoát.

---

<a name="9"></a>
## 9. VinaF AI market ticker — đã gỡ bỏ

- **2026-08-21**: dòng chữ chạy nhận định thị trường (ticker) dưới header **đã bị gỡ bỏ**.
  Endpoint `/api/ai/market-insight` + các hàm ticker trong `ai_service.py` và giao diện ticker trong `index.html` đã xoá.
- **Giữ nguyên**: tool chat `get_market_flow` (dòng tiền + VNINDEX) vẫn hoạt động trong VinaF AI chat.

---

<a name="10"></a>
## 10. Hiệu năng & tối ưu

- **`get_quote` song song**: 3 request (giá / lịch sử 1M / chỉ số cơ bản) qua `ThreadPoolExecutor(max_workers=3)` → giảm ~3× thời gian lần đầu.
- **Frontend tải song song**: chart render sớm (không chờ quote); sổ lệnh / giao dịch / so sánh chạy song song với quote.
- **Cache đa tầng** (bảng §5.2) + file cache `.cache/*.json`.
- **Breadth best-effort**: realtime board có timeout 14s; quá chậm thì bỏ độ rộng.
- **Ticker**: cache 5 phút, DeepSeek chỉ gọi lại khi hết hạn.

---

<a name="11"></a>
## 11. Deploy & biến môi trường (Render)

`render.yaml`:
- **Database**: `Tunix-test1-db` / `tunixtest1` (Postgres free, region singapore).
- **Service**: `VinaFintech` (web, python, free, singapore, `autoDeploy: true`).
  - `buildCommand: pip install -r requirements.txt`
  - `startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT`
  - Env: `APP_ENV=production`, `SECRET_KEY` (generateValue), `DATABASE_URL` (from DB), `FRONTEND_URL=https://tunix.onrender.com`, `DEEPSEEK_API_KEY` (sync:false — set thủ công).

**Luồng deploy**: `git push origin main` → Render autoDeploy. Scripts: `scripts/migrate_to_render.py`, `push_news_to_render.py`, `create_superadmin.py`, `import_markettimes_news.py`, `backfill_news_published_at.py` (bổ sung ngày giờ đăng cho bài cũ — chạy 1 lần sau khi có cột `news.published_at`).

---

<a name="12"></a>
## 12. Các tính năng lớn đã triển khai (gần đây)

| Ngày | Tính năng |
|---|---|
| 2026-08-15 | Chart engine hoàn thiện: KBS dữ liệu sâu + 1H, fullscreen VNINDEX, đổi mã tại chỗ, thước đo 2 trục, favicon, tin tức |
| 2026-08-16 | Fix fullscreen: trả chart về chiều cao bình thường; icon nến fullscreen đúng mã; thoát fullscreen quay lại trang trước |
| 2026-08-16 | Click mã trong "So sánh tương quan" → chuyển mã; ẩn spinner ô nhập tích sản; clamp tooltip mép block |
| 2026-08-16 | Tối ưu tốc độ (quote song song, frontend tải song song) + dọn dẹp source |
| 2026-08-16 | **VinaF AI market ticker**: endpoint market-insight, tool `get_market_flow`, nhận định dòng tiền + VNINDEX + lời khuyên NĐT, chỉ hiện trang chủ, cập nhật 5 phút |
| 2026-08-21 | **Gỡ bỏ VinaF AI market ticker** (dòng chữ chạy dưới header): xoá endpoint `/api/ai/market-insight`, frontend ticker; giữ tool `get_market_flow` cho chat |
| 2026-08-16 | Tài liệu: `docs/TUNIX_SPEC.md` (tổng quan) + `docs/TUNIX_DETAILED.md` (chi tiết — tài liệu này) |
