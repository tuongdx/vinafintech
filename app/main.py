import logging
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import decode_token
from app.db import Base, SessionLocal, engine
from app.db_schema import ensure_schema
from app.models import RoleEnum, User
from app.routers import ai, auth, chart, contact, dashboard, faqs, features, feedback, logs, news, notifications, plans, portfolio, stocks, stocks_admin, tickets, tools, users, watchlist

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        token = request.cookies.get("tunix_admin_token")
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
_perf_logger = logging.getLogger("tunix.perf")


def _stable_api_max_age(path: str) -> int | None:
    """max-age (giây) cho API công khai ổn định (dữ liệu đã cache server-side).
    Trả None nếu không nên cache ở trình duyệt (tránh stale cho dữ liệu realtime)."""
    if any(
        seg in path
        for seg in ("/company-summary", "/dividends", "/reports", "/profit-analysis", "/shareholders", "/business")
    ):
        return 600
    # Bộ dữ liệu screener/discover vốn đã cache server 30 phút → cache trình duyệt 60s
    # giúp các trang (cổ phiếu/khám phá/danh mục/TuniAI/trang chủ) load lại gần như tức thì.
    if path.startswith("/api/stocks/stock-screener") or path.startswith("/api/stocks/stock-discover"):
        return 60
    # Lịch sử giá (daily bars) là dữ liệu ổn định → cache trình duyệt 5 phút.
    if path.startswith("/api/stocks/") and "/history" in path:
        return 300
    if "/stock-lists" in path or path.startswith("/api/chart/config"):
        return 300
    if path.startswith("/api/faqs") or path.startswith("/api/plans") or path.startswith("/api/features"):
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
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    # Log request chậm để xác định bottleneck.
    if duration_ms > 500:
        _perf_logger.warning("SLOW %s %s -> %d %.0fms", request.method, request.url.path, response.status_code, duration_ms)

    if request.method == "GET" and response.status_code == 200:
        path = request.url.path
        # Asset tĩnh (JS/CSS/ảnh) — không phải HTML — cache trình duyệt 1 giờ
        # (StaticFiles vẫn gửi ETag nên sau 1h chỉ cần revalidate 304, rẻ).
        if (path.startswith("/static/") or path.startswith("/frontend/assets/")) and not path.endswith(".html"):
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        # API công khai ổn định: cache trình duyệt ngắn.
        elif path.startswith("/api/") and not request.headers.get("authorization"):
            max_age = _stable_api_max_age(path)
            if max_age is not None:
                response.headers.setdefault("Cache-Control", f"public, max-age={max_age}")
        else:
            # Trang HTML (shell + trang con): cache ngắn 60s → chuyển trang/load lại nhanh,
            # không phải tải lại toàn bộ HTML mỗi lần. Khi deploy bản mới, bump version
            # cache-bust phía frontend (index.html) để toàn bộ làm mới.
            response.headers["Cache-Control"] = "public, max-age=60"

    return response


api = settings.api_prefix
app.include_router(ai.router, prefix=api)
app.include_router(auth.router, prefix=api)
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
app.include_router(notifications.router, prefix=api)
app.include_router(portfolio.router, prefix=api)

frontend_path = Path(settings.frontend_dir)
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path / "assets")), name="static")
    app.mount("/frontend", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.on_event("startup")
def on_startup() -> None:
    ensure_schema()
    _warm_home_caches()


def _warm_home_caches() -> None:
    """Warm cache các block trang chủ ở nền (không chặn startup) để người dùng
    không phải chờ lần đầu — kết hợp stale-while-revalidate ở cache service."""
    import threading
    from app.services import vnstock_service as vs

    def _warm() -> None:
        jobs = [
            ("index-detail", lambda: vs.get_index_detail("vnindex", "1y")),
            ("market-overview", vs.get_market_overview),
            ("watchlist", vs.get_watchlist),
            ("market-map", vs.get_market_map),
            ("exchange-chart", lambda: vs.get_exchange_chart("HOSE")),
            ("top-stocks", lambda: vs.get_top_stocks("breakout", "1w")),
        ]
        for name, job in jobs:
            try:
                job()
            except Exception:  # noqa: BLE001
                _perf_logger.debug("warm %s failed", name)

    threading.Thread(target=_warm, daemon=True).start()


@app.get("/")
def root():
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    return {"message": "TuniX API running"}


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
    "kham-pha": "KhamPha.html",
    "tuniai": "TuniAI.html",
    "caidat": "caidat.html",
    "taikhoan": "taikhoan.html",
    "doimatkhau": "doimatkhau.html",
    "danh-muc": "danhmuc.html",
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
    # Trang tin tức: /tin-tuc giữ nguyên shell (header + right-col), nội dung nạp vào left-col.
    "tin-tuc",
    # Trang thị trường: /thi-truong giữ nguyên shell, nội dung nạp vào left-col.
    "thi-truong",
    # Trang cổ phiếu: /co-phieu giữ nguyên shell, nội dung nạp vào left-col.
    "co-phieu",
    # Trang khám phá: /kham-pha giữ nguyên shell, nội dung nạp vào left-col.
    "kham-pha",
    # Trang TuniAI: /tuniai giữ nguyên shell, nội dung nạp vào left-col.
    "tuniai",
    # URL đẹp danh mục tin tức
    "chung-khoan",
    "ngan-hang",
    "bat-dong-san",
    "doanh-nghiep",
    "dong-tien-thong-minh",
    "tai-chinh-quoc-te",
    "cong-nghe",
}


@app.get("/admin/login")
def admin_login_page():
    """Trang đăng nhập quản trị — cùng phong cách TuniX."""
    page_file = frontend_path / "AdminLogin.html"
    if not page_file.exists():
        raise HTTPException(status_code=404, detail="Page file not found")
    return FileResponse(str(page_file), headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon_ico():
    """Favicon gốc — trả logo TuniX (ICO) để trình duyệt tự tìm tại /favicon.ico."""
    icon_file = frontend_path / "Logo" / "favicon.ico"
    if icon_file.exists():
        return FileResponse(str(icon_file), media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/co-phieu/{symbol}")
def co_phieu_stock(symbol: str):
    """URL đẹp chi tiết cổ phiếu tại trang cổ phiếu: /co-phieu/ACB (giữ shell)."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Index file not found")


@app.get("/co-phieu/nganh/{sub1}/{sub2}/{symbol}")
def co_phieu_nganh_stock(sub1: str, sub2: str, symbol: str):
    """URL đẹp chi tiết cổ phiếu theo ngành: /co-phieu/nganh/tai-chinh/tai-chinh-ngan-hang/ACB (giữ shell)."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Index file not found")


@app.get("/co-phieu/nganh/{sub1}/{sub2}")
def co_phieu_nganh_sub2(sub1: str, sub2: str):
    """URL đẹp phân cấp ngành cấp 2: /co-phieu/nganh/tai-chinh/tai-chinh-ngan-hang (giữ shell)."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Index file not found")


@app.get("/co-phieu/nganh/{sub1}")
def co_phieu_nganh_sub1(sub1: str):
    """URL đẹp phân cấp ngành cấp 1: /co-phieu/nganh/tai-chinh (giữ shell)."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Index file not found")


@app.get("/co-phieu/nganh")
def co_phieu_nganh():
    """URL đẹp danh sách nhóm ngành: /co-phieu/nganh (giữ shell)."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Index file not found")


@app.get("/cophieu-{symbol}")
def stock_page(symbol: str):
    """URL đẹp cho trang chi tiết cổ phiếu: /cophieu-HDB (giữ nguyên URL, không redirect ?symbol=)."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Index file not found")


@app.get("/{page_name}")
def frontend_page(page_name: str):
    # URL bài viết dạng đẹp: /{slug}-{id}.html → mở trang chủ với bài trong left-col
    pretty_match = re.fullmatch(r".+-\d+\.html", page_name)
    if pretty_match:
        index_file = frontend_path / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="Index file not found")

    file_name = PAGE_MAP.get(page_name)
    if not file_name:
        raise HTTPException(status_code=404, detail="Page not found")

    if page_name in INDEX_SHELL_PAGES:
        # Phục vụ index.html trực tiếp để giữ URL đẹp /Trang (không redirect sang ?page=)
        index_file = frontend_path / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="Index file not found")

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
