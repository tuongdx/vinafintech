# VinaFintech Stock Analysis Platform

Du an khoi tao backend FastAPI + frontend HTML/CSS/JS cho he thong phan tich chung khoan VinaFintech.

## 1) Cau truc

- app/: backend API FastAPI
- frontend/: cac trang HTML giao dien va tai nguyen JS/CSS
- main.py: entrypoint cho uvicorn
- .env.example: mau bien moi truong

## 2) Cai dat nhanh

1. Tao virtual environment va kich hoat.
2. Cai dependencies:

   pip install -r requirements.txt

3. Tao file .env tu .env.example va cap nhat DATABASE_URL, SECRET_KEY.
4. Chay backend:

   uvicorn main:app --reload

5. Mo trinh duyet:

- Trang chu: http://localhost:8000/
- Quan tri: http://localhost:8000/frontend/QuanTri.html
- Login: http://localhost:8000/frontend/Login.html

## 3) Endpoint chinh da tao

- Auth: /api/auth/login, /api/auth/register, /api/auth/refresh-token, /api/auth/forgot-password
- Users: /api/users, /api/users/{id}/role, /api/users/{id}/plan
- Stocks: /api/stocks/search, /api/stocks/{symbol}/quote, /api/stocks/{symbol}/history, /api/stocks/{symbol}/financials, /api/stocks/{symbol}/insider-trades, /api/stocks/{symbol}/shareholders, /api/stocks/{symbol}/reports?category=analysis|financial|annual|agm|prospectus, /api/stocks/indices, /api/stocks/filter
- News: /api/news + /pending, /approve, /publish, /reject
- Tickets: /api/tickets + /replies, /assign, /resolve, /close
- FAQs: /api/faqs
- Plans: /api/plans + /{id}/toggle
- Features: /api/features + /free-mode
- Dashboard: /api/dashboard/stats
- Logs: /api/logs
- SEO: /robots.txt, /sitemap.xml; nội dung URL sạch được SSR/prerender (app/services/prerender_service.py)

## 4) Luu y ve du lieu thi truong

Service du lieu nam tai app/services/vnstock_service.py (adapter Simplize).

- Du lieu gia/charts lay tu API cong khai cua Simplize (api2.simplize.vn).
- Neu loi API, he thong tra fallback data de frontend khong vo trang.
- Co cache TTL don gian tai backend de giam tan suat goi.

## 5) Frontend da ket noi API

- frontend/index.html: tim kiem co phieu + chi so
- frontend/StockDetail.html: quote va history
- frontend/Login.html: dang nhap JWT
- frontend/QuanTri.html: dashboard thong ke + danh sach user

## 6) Doi ten thuong hieu trong DB (mot lan)

- VinaFintech -> VinaFintech, TuniAI -> VinaF AI (chi sua du lieu hien thi: FAQ, ten tac gia bai viet, features, site_name).

```
python scripts/rename_brand.py --dry-run
python scripts/rename_brand.py

# (tuy chon) cap nhat ca lich su chat AI
python scripts/rename_brand.py --include-history
```

Voi production (PostgreSQL Render), dat `DATABASE_URL` truoc khi chay.

## 7) Ke hoach tiep theo de hoan thien production

- Chuyen cache sang Redis that su (hien tai la in-memory TTL)
- Dung Alembic de quan ly migration schema
- Them workflow duyet tin chi tiet hon va audit logs day du
- Tich hop Chart.js/TradingView cho bieu do nen
- Viet unit tests cho services va routers chinh
