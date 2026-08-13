import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import decode_token
from app.db import Base, SessionLocal, engine
from app.db_schema import ensure_schema
from app.models import RoleEnum, User
from app.routers import ai, auth, contact, dashboard, faqs, features, feedback, logs, news, notifications, plans, stocks, stocks_admin, tickets, tools, users, watchlist

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

api = settings.api_prefix
app.include_router(ai.router, prefix=api)
app.include_router(auth.router, prefix=api)
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

frontend_path = Path(settings.frontend_dir)
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path / "assets")), name="static")
    app.mount("/frontend", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.on_event("startup")
def on_startup() -> None:
    ensure_schema()


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
    "forgot-password": "forgot-password.html",
    "reset-password": "reset-password.html",
    "NewsDetail": "NewsDetail.html",
    "tintuc": "tintuc.html",
    "tin-tuc": "tin-tuc.html",
    "caidat": "caidat.html",
    "taikhoan": "taikhoan.html",
    "doimatkhau": "doimatkhau.html",
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
