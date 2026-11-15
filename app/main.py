import gc
import logging
import re
import time
import unicodedata
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import decode_token
from app.db import SessionLocal
from app.db_schema import ensure_schema
from app.models import News, NewsStatusEnum, RoleEnum, User
from app.services import admin_log, prerender_service, sector_taxonomy, seo_service
from app.routers import ai, alerts, asset_events, assets, auth, bank_rates, chart, contact, crypto, crypto_signals, crypto_watchlist, dashboard, exchange, faqs, features, feedback, gold, logs, membership, news, notifications, plans, portfolio, signal_public, signals_admin, stock_signals, stocks, stocks_admin, tickets, tools, users, watchlist

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Nén HTTP (giảm băng thông → API/HTML tải nhanh hơn trên mọi client)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ============================================================
# Bảo vệ khu vực Quản trị: chỉ user có quyền quản trị mới xem được.
# Người không có quyền truy cập /QuanTri (hoặc file tĩnh QuanTri.html)
# sẽ nhận 404 "Not Found" — giống hệt đường dẫn không tồn tại.
# ============================================================
ADMIN_ROLES = {
    RoleEnum.super_admin,
    RoleEnum.admin,
    RoleEnum.content_manager,
    RoleEnum.editor,
    RoleEnum.support,
}

# Những đường dẫn thuộc khu vực quản trị cần chặn
ADMIN_PAGE_PATHS = {"/QuanTri", "/frontend/QuanTri.html"}


def _resolve_admin(request: Request) -> User | None:
    """Trả về user admin nếu token hợp lệ (Authorization header hoặc cookie), ngược lại None."""
    token: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.cookies.get("vinafintech_admin_token")
    if not token:
        return None

    payload = decode_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        return None

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    finally:
        db.close()

    if user and user.role in ADMIN_ROLES:
        return user
    return None


@app.middleware("http")
async def protect_admin_pages(request: Request, call_next):
    if request.url.path in ADMIN_PAGE_PATHS and _resolve_admin(request) is None:
        # Giả vờ trang không tồn tại để không lộ khu vực quản trị
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


# ============================================================
# Hiệu năng: Cache-Control cho asset tĩnh + API công khai ổn định,
# và log thời gian phản hồi mỗi request (phục vụ performance monitoring).
# ============================================================
_perf_logger = logging.getLogger("vinafintech.perf")


def _stable_api_max_age(path: str) -> int | None:
    """max-age (giây) cho API công khai ổn định (dữ liệu đã cache server-side).
    Trả None nếu không nên cache ở trình duyệt (tránh stale cho dữ liệu realtime)."""
    if any(
        seg in path
        for seg in (
            "/company-summary",
            "/dividends",
            "/reports",
            "/profit-analysis",
            "/financial-statement",
            "/shareholders",
            "/business",
        )
    ):
        return 600
    # Bộ dữ liệu screener/discover vốn đã cache server 30 phút → cache trình duyệt 60s
    # giúp các trang (cổ phiếu/khám phá/danh mục/VinaF AI/trang chủ) load lại gần như tức thì.
    if path.startswith("/api/stocks/stock-screener") or path.startswith("/api/stocks/stock-discover"):
        return 60
    # Bảng xếp hạng realtime (poll định kỳ ở trang Khám phá) — KHÔNG cache trình duyệt
    # để mỗi vòng poll luôn nhận giá mới nhất từ server.
    if path.startswith("/api/stocks/stock-rankings"):
        return 0
    # Lịch sử giá (daily bars) là dữ liệu ổn định → cache trình duyệt 5 phút.
    if path.startswith("/api/stocks/") and "/history" in path:
        return 300
    if "/stock-lists" in path or path.startswith("/api/chart/config"):
        return 300
    # Trạng thái MỞ/KHOÁ block hội viên phải cập nhật tức thì cho toàn bộ website
    # → KHÔNG cache /api/features ở trình duyệt (tránh user thấy trạng thái cũ).
    if path.startswith("/api/features"):
        return 0
    if path.startswith("/api/faqs") or path.startswith("/api/plans"):
        return 600
    if path.startswith("/api/stocks/search"):
        return 120
    if path.startswith("/api/stocks/filter"):
        return 60
    if path.startswith("/api/news/popular"):
        return 120
    if path.startswith("/api/news"):
        return 60
    return None


@app.middleware("http")
async def cache_and_timing(request: Request, call_next):
    start = time.perf_counter()

    # Nhật ký trang quản trị: chuẩn bị ngữ cảnh TRƯỚC khi route chạy (đọc body JSON =
    # dữ liệu sau khi sửa + chụp bản ghi hiện tại = dữ liệu trước khi sửa/xoá).
    admin_ctx = None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        admin_ctx = await admin_log.prepare(request)

    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    # Log request chậm để xác định bottleneck.
    if duration_ms > 500:
        _perf_logger.warning("SLOW %s %s -> %d %.0fms", request.method, request.url.path, response.status_code, duration_ms)

    if request.method == "GET" and response.status_code == 200:
        path = request.url.path
        # File HTML nội dung nhúng (/frontend/*.html) chỉ phục vụ render iframe;
        # URL chuẩn của người dùng là route sạch (/thi-truong, /co-phieu, ...).
        # Đánh dấu noindex để Google không index bản sao /frontend/..., nhưng vẫn
        # cho crawler tải file (cần thiết để render nội dung iframe).
        if path.startswith("/frontend/") and path.endswith(".html"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        # Asset tĩnh (JS/CSS/ảnh) — không phải HTML.
        # Nếu URL có tham số version (?v=...) thì nội dung là BẤT BIẾN → cache 1 năm
        # (không cần revalidate) ⇒ chuyển trang sau này không tốn request nào cho asset.
        # Không có ?v= → cache 1 giờ (StaticFiles vẫn gửi ETag nên revalidate rất rẻ).
        if (path.startswith("/static/") or path.startswith("/frontend/assets/")) and not path.endswith(".html"):
            if "v=" in (request.url.query or ""):
                response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
            else:
                response.headers.setdefault("Cache-Control", "public, max-age=3600")
        elif path.startswith("/api/"):
            if request.headers.get("authorization"):
                # API yêu cầu đăng nhập trả dữ liệu cá nhân (danh mục, watchlist,
                # khoản tài sản...) → KHÔNG cache ở trình duyệt, tránh stale sau khi
                # người dùng thêm/sửa/xoá (nếu không Chrome heuristic cache tới 60s).
                response.headers["Cache-Control"] = "no-store"
            else:
                # API công khai ổn định: cache trình duyệt ngắn + cho phép phục vụ bản cũ
                # trong lúc làm mới nền (stale-while-revalidate) ⇒ lần xem lại tức thì.
                max_age = _stable_api_max_age(path)
                if max_age is not None:
                    if max_age == 0:
                        response.headers.setdefault("Cache-Control", "no-cache")
                    else:
                        swr = max(max_age, 300)
                        response.headers.setdefault(
                            "Cache-Control", f"public, max-age={max_age}, stale-while-revalidate={swr}"
                        )
        else:
            # Trang HTML — CHỈ đặt Cache-Control MẶC ĐỊNH (setdefault): route phía trên đã cố ý đặt
            # "no-store" cho SHELL (index.html) thì TUYỆT ĐỐI không được ghi đè.
            # ⚠️ Bài học: trước đây nhánh này ghi đè no-store bằng "max-age=60, stale-while-revalidate=600"
            # ⇒ trình duyệt phục vụ SHELL CŨ tới 10 phút sau khi deploy ⇒ tab đang mở chạy JS điều hướng
            # cũ (bấm menu Thị trường/Cổ phiếu lại ra trang chủ). Không bao giờ cache shell.
            seg = path.strip("/").split("/")[0]
            is_shell = (
                path == "/"
                or path == "/frontend/index.html"
                or seg in INDEX_SHELL_PAGES
                or path in ADMIN_PAGE_PATHS
            )
            if is_shell or path.endswith(".html"):
                # Mọi file .html (kể cả nội dung nhúng /frontend/*.html) → LUÔN revalidate.
                # ⚠️ Bài học: nếu cache dài cho HTML có ?v=... thì sửa HTML mà quên bump
                # CACHE_VERSION ⇒ trình duyệt vẫn phục vụ bản cũ tới 7 ngày.
                response.headers.setdefault("Cache-Control", "no-cache")
            elif "v=" in (request.url.query or ""):
                # Asset có ?v=CACHE_VERSION → URL đã gắn phiên bản, đổi version là đổi URL
                # ⇒ cache dài để mở lại trang tức thì, không cần revalidate.
                response.headers.setdefault("Cache-Control", "public, max-age=604800, stale-while-revalidate=86400")
            else:
                response.headers.setdefault("Cache-Control", "public, max-age=60, stale-while-revalidate=600")

    # Ghi nhật ký thao tác quản trị (ai làm, lúc nào, thao tác gì, tính năng nào, trước/sau khi sửa).
    if admin_ctx:
        await admin_log.record(admin_ctx, response.status_code)

    return response


api = settings.api_prefix
app.include_router(ai.router, prefix=api)
app.include_router(auth.router, prefix=api)
app.include_router(membership.router, prefix=api)
app.include_router(chart.router, prefix=api)
app.include_router(users.router, prefix=api)
app.include_router(stocks.router, prefix=api)
app.include_router(stocks_admin.router, prefix=api)
app.include_router(tools.router, prefix=api)
app.include_router(news.router, prefix=api)
app.include_router(tickets.router, prefix=api)
app.include_router(contact.router, prefix=api)
app.include_router(feedback.router, prefix=api)
app.include_router(faqs.router, prefix=api)
app.include_router(plans.router, prefix=api)
app.include_router(features.router, prefix=api)
app.include_router(dashboard.router, prefix=api)
app.include_router(logs.router, prefix=api)
app.include_router(watchlist.router, prefix=api)
app.include_router(crypto_watchlist.router, prefix=api)
app.include_router(crypto_signals.router, prefix=api)
app.include_router(stock_signals.router, prefix=api)
app.include_router(notifications.router, prefix=api)
app.include_router(portfolio.router, prefix=api)
app.include_router(alerts.router, prefix=api)
app.include_router(asset_events.router, prefix=api)
app.include_router(bank_rates.router, prefix=api)
app.include_router(crypto.router, prefix=api)
app.include_router(gold.router, prefix=api)
app.include_router(exchange.router, prefix=api)
app.include_router(assets.router, prefix=api)
# VinaFintech Signal Engine — API Tín hiệu (yêu cầu đăng nhập + quyền được cấp: role admin hoặc
# cờ users.signal_access do admin bật) + API public phân tích (không có BUY/SELL)
app.include_router(signals_admin.router, prefix=api)
app.include_router(signal_public.router, prefix=api)

frontend_path = Path(settings.frontend_dir)
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path / "assets")), name="static")
    app.mount("/frontend", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


# Shell index.html dùng chung cho nhiều URL sạch → cache bản gốc, mỗi request chỉ
# thay title/description/canonical/OG/JSON-LD theo route (xem app/services/seo_service.py).
_INDEX_SHELL_CACHE: dict[str, object] = {"mtime": 0.0, "html": ""}


def _read_index_shell() -> str:
    index_file = frontend_path / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Index file not found")
    mtime = index_file.stat().st_mtime
    if not _INDEX_SHELL_CACHE["html"] or _INDEX_SHELL_CACHE["mtime"] != mtime:
        _INDEX_SHELL_CACHE["html"] = index_file.read_text(encoding="utf-8")
        _INDEX_SHELL_CACHE["mtime"] = mtime
    return str(_INDEX_SHELL_CACHE["html"])


def _index_response(path: str) -> Response:
    meta = seo_service.build_shell_meta(path)
    headers = {"Cache-Control": "no-store"}
    if "noindex" in str(meta.get("robots", "")):
        headers["X-Robots-Tag"] = "noindex, nofollow"
        prerender_html = ""
    else:
        prerender_html = prerender_service.build_prerender_html(path)
    return Response(
        content=seo_service.render_shell(_read_index_shell(), meta, prerender_html),
        media_type="text/html",
        headers=headers,
    )


@app.get("/api/seo/meta")
def api_seo_meta(path: str = "/") -> dict:
    """Meta của shell cho 1 path — dùng khi điều hướng phía client không nạp lại trang
    (vd bấm chip đổi công cụ Tiện ích) để cập nhật title/description/canonical."""
    meta = seo_service.build_shell_meta(path)
    return {
        "path": path,
        "title": meta.get("title", ""),
        "description": meta.get("description", ""),
        "canonical": meta.get("canonical", ""),
        "robots": meta.get("robots", ""),
    }


@app.on_event("startup")
def on_startup() -> None:
    ensure_schema()
    # Tạo sẵn các dòng block website (MỞ cho tất cả) nếu chưa tồn tại.
    try:
        from app.db import SessionLocal
        from app.services import feature_flags

        session = SessionLocal()
        try:
            feature_flags.ensure_block_features(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — không để lỗi seed làm hỏng startup
        pass
    # Seed dữ liệu lãi suất ngân hàng (dữ liệu MẪU tham khảo fallback) nếu bảng trống.
    try:
        from app.services import bank_rate_service

        session = SessionLocal()
        try:
            bank_rate_service.seed_rates(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — không để lỗi seed làm hỏng startup
        pass
    # Đăng ký 3 chiến thuật của VinaFintech Signal Engine (idempotent) vào bảng signal_strategies.
    try:
        from app.services.signal_engine import repository as signal_repository

        session = SessionLocal()
        try:
            signal_repository.seed_strategies(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — không để lỗi seed làm hỏng startup
        pass
    # Sửa nhãn NGUỒN bài viết An ninh Tiền tệ (Chứng khoán/Ngân hàng/Doanh nghiệp)
    # từng bị gán nhầm 'MarketTimes' do import cũ hard-code nguồn (idempotent).
    try:
        from app.services import news_source

        session = SessionLocal()
        try:
            news_source.apply_legacy_fix(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — không để lỗi migrate làm hỏng startup
        pass
    # Đồng bộ dữ liệu THẬT từ 24HMoney ở nền (chạy sau boot vài giây, TTL mặc định 24h,
    # 1 request/kỳ — không chặn startup, không spam nguồn).
    try:
        from app.services import bank_rate_sync

        bank_rate_sync.run_auto_sync()
    except Exception:  # noqa: BLE001
        pass
    # Lịch sự kiện thị trường (toàn bộ cổ phiếu 3 sàn): dựng lại snapshot vào 12h trưa
    # giờ VN mỗi ngày, chạy trong thread nền (idempotent, không chặn startup).
    try:
        from app.routers import asset_events as asset_events_router

        asset_events_router.start_daily_refresh()
    except Exception:  # noqa: BLE001
        pass
    # Block "Cơ hội mới" (trang chủ): snapshot cổ phiếu theo dữ liệu thị trường, cũng
    # dựng lại vào 12h trưa giờ VN mỗi ngày (thread nền riêng, idempotent).
    try:
        from app.services import home_opportunity_service

        home_opportunity_service.start_daily_refresh()
    except Exception:  # noqa: BLE001
        pass
    # Block "Báo cáo" (trang chi tiết cổ phiếu, nguồn cafef.vn): xoá + dựng lại cache
    # vào 12h trưa giờ VN mỗi ngày (thread nền riêng, idempotent).
    try:
        from app.services import vnstock_service

        vnstock_service.start_daily_reports_refresh()
    except Exception:  # noqa: BLE001
        pass
    # Tín hiệu VinaFintech: QUÉT TỰ ĐỘNG toàn thị trường theo lịch (mặc định 11:00 · 13:30 · 15:00
    # giờ VN mỗi ngày giao dịch) — thread nền riêng, không chặn người dùng.
    try:
        from app.services.signal_engine import scheduler as signal_scheduler

        signal_scheduler.start_scheduler()
    except Exception:  # noqa: BLE001
        pass
    _warm_home_caches()


# ============================================================
# Warm cache trang chủ — điều tiết để KHÔNG vượt 512MB của Render free tier.
# `_WARM_MAX_WORKERS` = số job nặng chạy song song (8 cũ ⇒ đỉnh bộ nhớ quá cao ⇒ OOM-kill
#   ⇒ instance chết ⇒ mọi API trả 502/503).
# `_WARM_START_DELAY` = giây chờ sau boot để instance vượt health-check trước khi warm.
# ============================================================
_WARM_MAX_WORKERS = 2
_WARM_START_DELAY = 25


def _warm_home_caches() -> None:
    """Warm cache các block trang chủ ở nền (không chặn startup) để người dùng
    không phải chờ lần đầu — kết hợp stale-while-revalidate ở cache service."""
    import threading
    from app.services import vnstock_service as vs
    def _warm() -> None:
        from concurrent.futures import ThreadPoolExecutor

        from app.routers.asset_events import get_upcoming_events as upcoming_events_fn
        from app.services import dividend_adjust
        from app.services import home_opportunity_service
        from app.services.opportunity_service import get_top_opportunities

        jobs = [
            # Các endpoint nặng của trang chủ / khám phá — làm nóng cache ở nền để
            # người dùng đầu tiên sau khi deploy không phải chờ tính toán đồng bộ.
            ("market-dashboard", vs.get_market_dashboard),
            ("stock-screener", vs.get_stock_screener_page),
            ("stock-discover", vs.get_stock_discover_page),
            # Snapshot "Cơ hội mới" (trang chủ) theo mốc 12h trưa hằng ngày.
            ("home-opportunities", home_opportunity_service.refresh_home_opportunities),
            ("opportunities", lambda: get_top_opportunities(force=True)),
            ("index-detail", lambda: vs.get_index_detail("vnindex", "1y")),
            ("market-overview", vs.get_market_overview),
            ("watchlist", vs.get_watchlist),
            ("market-map", vs.get_market_map),
            ("exchange-chart", lambda: vs.get_exchange_chart("HOSE")),
            ("top-stocks", lambda: vs.get_top_stocks("breakout", "1w")),
            # Snapshot lịch sự kiện toàn thị trường (3 sàn) — 1 nguồn cho mọi mã.
            ("asset-events-upcoming", lambda: upcoming_events_fn(50)),
            # Lịch cổ tức SẮP chia toàn thị trường (nhãn "Sắp chia cổ tức" ở mọi bảng).
            ("dividend-upcoming", dividend_adjust.market_upcoming),
        ]

        def run(item):
            name, job = item
            try:
                job()
            except Exception:  # noqa: BLE001
                _perf_logger.debug("warm %s failed", name)
            finally:
                # Trả bộ nhớ tạm (JSON 1MB+, DataFrame nguồn) về hệ thống NGAY sau mỗi job
                # ⇒ hạ đỉnh bộ nhớ khi chạy nhiều job liên tiếp.
                gc.collect()

        # Chờ instance ổn định + vượt health-check của Render TRƯỚC khi chạy việc nặng:
        # warm ngay lúc boot làm đỉnh bộ nhớ trùng lúc khởi động ⇒ dễ bị OOM-kill
        # (Render free 512MB) ⇒ build/deploy fail hoặc 502/503 cho người dùng.
        time.sleep(_WARM_START_DELAY)

        # Chạy SONG SONG ÍT luồng (mặc định 2) thay vì 8: vẫn làm nóng cache nhưng hạ
        # mạnh ĐỈNH bộ nhớ, vì mỗi job lại tự gọi API nguồn song song bên trong
        # (screener ~1MB, opportunity quét 50 mã...). 8 luồng ⇒ 8 job nặng cùng lúc
        # ⇒ dễ vượt 512MB của Render free.
        with ThreadPoolExecutor(max_workers=max(1, min(_WARM_MAX_WORKERS, len(jobs)))) as ex:
            list(ex.map(run, jobs))

    threading.Thread(target=_warm, daemon=True).start()


@app.get("/")
def root():
    if not (frontend_path / "index.html").exists():
        return {"message": "VinaFintech API running"}
    return _index_response("/")


@app.get("/api/health")
def health():
    """Health check SIÊU NHẸ — không chạm DB, không gọi mạng ngoài.

    Dùng để biết instance còn sống hay không (Render trả 502/503 khi process đã chết)
    và có thể đặt làm Health Check Path trong Render → Settings.
    """
    return {"ok": True, "app": settings.app_name, "ts": time.time()}


PAGE_MAP: dict[str, str] = {
    "Index": "index.html",
    "Home": "Home.html",
    "StockDetail": "StockDetail.html",
    "Login": "Login.html",
    "register": "register.html",
    "verify-email": "VerifyEmail.html",
    "forgot-password": "forgot-password.html",
    "reset-password": "reset-password.html",
    "tintuc": "tintuc.html",
    "tin-tuc": "tin-tuc.html",
    "thi-truong": "ThiTruong.html",
    "co-phieu": "CoPhieu.html",
    # Trang Lãi ngân hàng: /lai-ngan-hang giữ nguyên shell, nội dung nạp vào iframe left-col.
    "lai-ngan-hang": "lai-ngan-hang.html",
    # Trang Giá Vàng/Bạc: /gia-vang-bac giữ nguyên shell, nội dung nạp vào iframe left-col.
    "gia-vang-bac": "gold.html",
    # Trang Crypto: /crypto giữ nguyên shell, nội dung nạp vào iframe left-col.
    "crypto": "crypto.html",
    "kham-pha": "KhamPha.html",
    "vinafai": "VinafAI.html",
    "caidat": "caidat.html",
    "taikhoan": "taikhoan.html",
    "doimatkhau": "doimatkhau.html",
    "quan-ly-tai-san": "quanlytaisan.html",
    # Trang Tín hiệu (VinaFintech Signal Engine): /tin-hieu giữ nguyên shell, nội dung nạp vào iframe left-col.
    # Menu CHỈ hiện với tài khoản đã đăng nhập VÀ được admin cấp quyền (users.signal_access);
    # API /api/signals kiểm tra quyền ở server (require_signal_access).
    "tin-hieu": "tin-hieu.html",
    "tien-ich": "tienich.html",
    # Trang Tỷ giá ngoại tệ: /ty-gia-ngoai-te giữ nguyên shell, nội dung nạp vào iframe left-col.
    "ty-gia-ngoai-te": "exchange.html",
    "QuanTri": "QuanTri.html",
    "AdminLogin": "AdminLogin.html",
    "TrungTamTroGiup": "TrungTamTroGiup.html",
    "HuongDanSuDung": "HuongDanSuDung.html",
    "CauHoiThuongGap": "CauHoiThuongGap.html",
    "LienHe": "LienHe.html",
    "GopY": "GopY.html",
    "DieuKhoanSuDung": "DieuKhoanSuDung.html",
    "ChinhSachBaoMat": "ChinhSachBaoMat.html",
    "MienTruTrachNhiem": "MienTruTrachNhiem.html",
    # Danh mục tin tức (URL đẹp: /ngan-hang, /chung-khoan, ...) → mở shell index + trang tin tức
    "chung-khoan": "tin-tuc.html",
    "ngan-hang": "tin-tuc.html",
    "bat-dong-san": "tin-tuc.html",
    "doanh-nghiep": "tin-tuc.html",
    "dong-tien-thong-minh": "tin-tuc.html",
    "tai-chinh-quoc-te": "tin-tuc.html",
    "cong-nghe": "tin-tuc.html",
    # Chuyên mục Crypto (tiền ảo): slug "tien-ao" để KHÔNG trùng URL /crypto của trang giá Crypto.
    "tien-ao": "tin-tuc.html",
}

INDEX_SHELL_PAGES = {
    "TrungTamTroGiup",
    "HuongDanSuDung",
    "CauHoiThuongGap",
    "LienHe",
    "GopY",
    "DieuKhoanSuDung",
    "ChinhSachBaoMat",
    "MienTruTrachNhiem",
    # Trang tài khoản / cài đặt / đổi mật khẩu: URL đẹp /taikhoan, /caidat, /doimatkhau
    # giữ nguyên shell, nội dung nạp vào iframe left-col (deep-link + F5 vẫn đúng trang).
    "taikhoan",
    "caidat",
    "doimatkhau",
    # Trang tin tức: /tin-tuc giữ nguyên shell (header + right-col), nội dung nạp vào left-col.
    "tin-tuc",
    # Trang thị trường: /thi-truong giữ nguyên shell, nội dung nạp vào left-col.
    "thi-truong",
    # Trang cổ phiếu: /co-phieu giữ nguyên shell, nội dung nạp vào left-col.
    "co-phieu",
    # Trang Lãi ngân hàng: /lai-ngan-hang giữ nguyên shell, nội dung nạp vào iframe left-col.
    "lai-ngan-hang",
    # Trang Giá Vàng/Bạc: /gia-vang-bac giữ nguyên shell, nội dung nạp vào iframe left-col.
    "gia-vang-bac",
    # Trang Crypto: /crypto giữ nguyên shell, nội dung nạp vào iframe left-col.
    "crypto",
    # Trang khám phá: /kham-pha giữ nguyên shell, nội dung nạp vào left-col.
    "kham-pha",
    # Trang VinaF AI: /vinafai giữ nguyên shell, nội dung nạp vào left-col.
    "vinafai",
    # Trang Quản lý tài sản: /quan-ly-tai-san giữ nguyên shell, nội dung nạp vào iframe left-col.
    "quan-ly-tai-san",
    # Trang Tín hiệu: /tin-hieu giữ nguyên shell, nội dung nạp vào iframe left-col.
    "tin-hieu",
    # Trang Tiện ích: /tien-ich giữ nguyên shell, nội dung nạp vào iframe left-col.
    "tien-ich",
    # Trang Tỷ giá ngoại tệ: /ty-gia-ngoai-te giữ nguyên shell, nội dung nạp vào iframe left-col.
    "ty-gia-ngoai-te",
    # URL đẹp danh mục tin tức
    "chung-khoan",
    "ngan-hang",
    "bat-dong-san",
    "doanh-nghiep",
    "dong-tien-thong-minh",
    "tai-chinh-quoc-te",
    "cong-nghe",
    "tien-ao",
}


# ============================================================
# SEO technical: robots.txt + sitemap.xml
# ============================================================
_STATIC_SITEMAP_PATHS: tuple[tuple[str, str, float], ...] = (
    ("/", "daily", 1.0),
    ("/thi-truong", "daily", 0.9),
    ("/co-phieu", "daily", 0.9),
    ("/co-phieu/nganh", "weekly", 0.7),
    ("/tin-tuc", "daily", 0.9),
    ("/crypto", "daily", 0.8),
    ("/gia-vang-bac", "daily", 0.8),
    ("/ty-gia-ngoai-te", "daily", 0.7),
    ("/lai-ngan-hang", "daily", 0.7),
    ("/kham-pha", "weekly", 0.7),
    ("/vinafai", "weekly", 0.6),
    ("/tien-ich", "weekly", 0.6),
    ("/tien-ich/gia-dieu-chinh-co-phieu", "monthly", 0.5),
    ("/tien-ich/chia-deu-von-dau-tu", "monthly", 0.5),
    ("/tien-ich/loi-nhuan-tien-gui", "monthly", 0.5),
    ("/tien-ich/ty-gia-ngoai-te", "monthly", 0.5),
    ("/CauHoiThuongGap", "monthly", 0.5),
    ("/HuongDanSuDung", "monthly", 0.5),
    ("/TrungTamTroGiup", "monthly", 0.5),
    ("/LienHe", "monthly", 0.4),
    ("/GopY", "monthly", 0.4),
    ("/DieuKhoanSuDung", "yearly", 0.3),
    ("/ChinhSachBaoMat", "yearly", 0.3),
    ("/MienTruTrachNhiem", "yearly", 0.3),
    # URL danh mục tin tức
    ("/chung-khoan", "daily", 0.7),
    ("/ngan-hang", "daily", 0.7),
    ("/bat-dong-san", "daily", 0.7),
    ("/doanh-nghiep", "daily", 0.7),
    ("/dong-tien-thong-minh", "daily", 0.7),
    ("/tai-chinh-quoc-te", "daily", 0.7),
    ("/cong-nghe", "daily", 0.7),
    ("/tien-ao", "daily", 0.7),
)

_SITEMAP_CACHE_TTL = 3600
_sitemap_cache: dict[str, object] = {"ts": 0.0, "xml": ""}


def _site_base() -> str:
    """Domain công khai dùng cho robots/sitemap."""
    base = (settings.app_url or settings.frontend_url or "https://tunix.vn").strip().rstrip("/")
    if settings.app_env == "production" and ("localhost" in base or "127.0.0.1" in base):
        return "https://tunix.vn"
    return base or "https://tunix.vn"


def _seo_slug(text: str) -> str:
    """Bỏ dấu tiếng Việt + ký tự đặc biệt → slug ASCII (khớp cách frontend dựng URL bài viết)."""
    value = (text or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return value.strip("-")


def _build_sitemap_xml() -> str:
    base = _site_base()
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, changefreq, priority in _STATIC_SITEMAP_PATHS:
        parts.append(
            f"<url><loc>{xml_escape(base + path)}</loc>"
            f"<changefreq>{changefreq}</changefreq><priority>{priority:.1f}</priority></url>"
        )

    # Bài viết đã duyệt/đăng — URL đẹp /{slug}-{id}.html (frontend dựng đúng định dạng này).
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(News.id, News.title, News.published_at, News.created_at)
                .filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
                .order_by(News.id.desc())
                .limit(5000)
                .all()
            )
        finally:
            db.close()
        for news_id, title, published_at, created_at in rows:
            slug = _seo_slug(title)
            if not slug:
                continue
            last = published_at or created_at
            lastmod = f"<lastmod>{last.date().isoformat()}</lastmod>" if last else ""
            parts.append(
                f"<url><loc>{xml_escape(f'{base}/{slug}-{news_id}.html')}</loc>{lastmod}"
                f"<changefreq>weekly</changefreq><priority>0.6</priority></url>"
            )
    except Exception:  # noqa: BLE001 — lỗi DB không được làm sitemap 500
        logging.getLogger(__name__).warning("sitemap: cannot load published news", exc_info=True)

    parts.append("</urlset>")
    return "\n".join(parts)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /QuanTri",
        "Disallow: /AdminLogin",
        "Disallow: /admin/",
        "Disallow: /Login",
        "Disallow: /register",
        "Disallow: /forgot-password",
        "Disallow: /reset-password",
        "Disallow: /verify-email",
        "Disallow: /taikhoan",
        "Disallow: /caidat",
        "Disallow: /doimatkhau",
        "Disallow: /quan-ly-tai-san",
        "Disallow: /tin-hieu",
        "Disallow: /tintuc",
        "Disallow: /StockDetail",
        "",
        f"Sitemap: {_site_base()}/sitemap.xml",
        "",
    ]
    return PlainTextResponse("\n".join(lines), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    now = time.time()
    if not _sitemap_cache["xml"] or now - float(_sitemap_cache["ts"]) > _SITEMAP_CACHE_TTL:
        _sitemap_cache["xml"] = _build_sitemap_xml()
        _sitemap_cache["ts"] = now
    return Response(
        content=str(_sitemap_cache["xml"]),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/admin/login")
def admin_login_page():
    """Trang đăng nhập quản trị — cùng phong cách VinaFintech."""
    page_file = frontend_path / "AdminLogin.html"
    if not page_file.exists():
        raise HTTPException(status_code=404, detail="Page file not found")
    return FileResponse(str(page_file), headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon_ico():
    """Favicon gốc — trả logo VinaFintech (ICO) để trình duyệt tự tìm tại /favicon.ico."""
    icon_file = frontend_path / "Logo" / "favicon.ico"
    if icon_file.exists():
        return FileResponse(str(icon_file), media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")


_CO_PHIEU_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.]+$")


def _co_phieu_sector_url(symbol: str) -> str:
    """URL đẹp CẤP NGÀNH của 1 mã: /co-phieu/nganh/{sector}/{industry}/{MÃ}.

    Trả '' nếu mã không hợp lệ hoặc chưa biết ngành (khi đó giữ URL rút gọn như cũ).
    """
    sym = str(symbol or "").strip().upper()
    if not sym or not _CO_PHIEU_SYMBOL_RE.match(sym):
        return ""
    try:
        from app.services.vnstock_service import _symbol_sector_index

        info = _symbol_sector_index().get(sym) or {}
    except Exception:  # noqa: BLE001 — lỗi tra ngành KHÔNG được làm hỏng trang
        return ""
    sector = str(info.get("sector") or "").strip().lower()
    industry = str(info.get("industry") or "").strip().lower()
    if not (sector and industry):
        return ""
    return f"/co-phieu/nganh/{sector}/{industry}/{sym}"


@app.get("/co-phieu/{symbol}")
def co_phieu_stock(symbol: str):
    """URL đẹp chi tiết cổ phiếu tại trang cổ phiếu: /co-phieu/ACB (giữ shell).

    Canonical hoá: nếu biết ngành của mã → 301 sang
    /co-phieu/nganh/{sector}/{industry}/{MÃ} để chi tiết cổ phiếu chỉ có MỘT kiểu URL
    (khớp đúng URL mà trang Cổ phiếu dùng khi duyệt theo ngành).
    """
    # '/co-phieu/nganh' là URL danh sách nhóm ngành (route này bắt trước do khai báo trước)
    if symbol.strip().lower() != "nganh":
        target = _co_phieu_sector_url(symbol)
        if target:
            return RedirectResponse(url=target, status_code=301)
    return _index_response(f"/co-phieu/{symbol}")


@app.get("/co-phieu/nganh/{sub1}/{sub2}/{symbol}")
def co_phieu_nganh_stock(sub1: str, sub2: str, symbol: str):
    """URL đẹp chi tiết cổ phiếu theo ngành: /co-phieu/nganh/tai-chinh/ngan-hang/ACB (giữ shell)."""
    legacy = sector_taxonomy.legacy_redirect_path(sub1, sub2, symbol)
    if legacy:
        return RedirectResponse(url=legacy, status_code=301)
    return _index_response(f"/co-phieu/nganh/{sub1}/{sub2}/{symbol}")


@app.get("/co-phieu/nganh/{sub1}/{sub2}")
def co_phieu_nganh_sub2(sub1: str, sub2: str):
    """URL đẹp phân cấp ngành cấp 2: /co-phieu/nganh/tai-chinh/ngan-hang (giữ shell)."""
    legacy = sector_taxonomy.legacy_redirect_path(sub1, sub2)
    if legacy:
        return RedirectResponse(url=legacy, status_code=301)
    return _index_response(f"/co-phieu/nganh/{sub1}/{sub2}")


@app.get("/co-phieu/nganh/{sub1}")
def co_phieu_nganh_sub1(sub1: str):
    """URL đẹp phân cấp ngành cấp 1: /co-phieu/nganh/tai-chinh (giữ shell)."""
    legacy = sector_taxonomy.legacy_redirect_path(sub1)
    if legacy:
        return RedirectResponse(url=legacy, status_code=301)
    # Slug ngành cấp 2 bị dùng như cấp 1 → 301 về URL chuẩn /{nhóm}/{ngành}.
    canonical = sector_taxonomy.canonical_level1_path(sub1)
    if canonical:
        return RedirectResponse(url=canonical, status_code=301)
    return _index_response(f"/co-phieu/nganh/{sub1}")


@app.get("/co-phieu/nganh")
def co_phieu_nganh():
    """URL đẹp danh sách nhóm ngành: /co-phieu/nganh (giữ shell)."""
    return _index_response("/co-phieu/nganh")


@app.get("/cophieu-{symbol}")
def stock_page(symbol: str):
    """URL cũ /cophieu-HDB → 301 về URL ngành chuẩn nếu đã biết ngành (tránh trùng lặp SEO)."""
    target = _co_phieu_sector_url(symbol)
    if target:
        return RedirectResponse(url=target, status_code=301)
    return _index_response(f"/cophieu-{symbol}")


@app.get("/quan-ly-tai-san/{section}")
def qlts_section_page(section: str):
    """URL đẹp section Quản lý tài sản: /quan-ly-tai-san/phan-tich-tai-san (giữ shell)."""
    return _index_response(f"/quan-ly-tai-san/{section}")


@app.get("/tien-ich/{tool}")
def tien_ich_tool_page(tool: str):
    """URL đẹp công cụ Tiện ích: /tien-ich/gia-dieu-chinh-co-phieu (giữ shell)."""
    return _index_response(f"/tien-ich/{tool}")


@app.get("/crypto/{symbol}")
def crypto_coin_page(symbol: str):
    """URL đẹp chi tiết coin: /crypto/BTC → giữ shell, nội dung nạp vào iframe (crypto.html?symbol=BTC)."""
    return _index_response(f"/crypto/{symbol}")


@app.get("/{page_name}")
def frontend_page(page_name: str):
    # /Index là bản sao của trang chủ → 301 về "/" để chỉ còn một URL chuẩn.
    if page_name.lower() == "index":
        return RedirectResponse(url="/", status_code=301)

    # URL bài viết dạng đẹp: /{slug}-{id}.html → mở trang chủ với bài trong left-col
    pretty_match = re.fullmatch(r".+-\d+\.html", page_name)
    if pretty_match:
        return _index_response(f"/{page_name}")

    file_name = PAGE_MAP.get(page_name)
    if not file_name:
        raise HTTPException(status_code=404, detail="Page not found")

    if page_name in INDEX_SHELL_PAGES:
        # Phục vụ index.html trực tiếp để giữ URL đẹp /Trang (không redirect sang ?page=)
        return _index_response(f"/{page_name}")

    page_file = frontend_path / file_name
    if not page_file.exists():
        raise HTTPException(status_code=404, detail="Page file not found")

    return FileResponse(str(page_file), headers={"Cache-Control": "no-store"})


# ============================================================
# Nén GZip — đặt CUỐI để là middleware ngoài cùng → nén mọi response
# JSON/JS/CSS/HTML ≥500B. SSE (text/event-stream của AI chat) được
# GZipMiddleware bỏ qua tự động (DEFAULT_EXCLUDED_CONTENT_TYPES).
# ============================================================
app.add_middleware(GZipMiddleware, minimum_size=500)
