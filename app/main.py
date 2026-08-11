import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db import Base, engine
from app.db_schema import ensure_schema
from app.routers import auth, contact, dashboard, faqs, features, logs, news, plans, stocks, stocks_admin, tickets, tools, users, watchlist

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = settings.api_prefix
app.include_router(auth.router, prefix=api)
app.include_router(users.router, prefix=api)
app.include_router(stocks.router, prefix=api)
app.include_router(stocks_admin.router, prefix=api)
app.include_router(tools.router, prefix=api)
app.include_router(news.router, prefix=api)
app.include_router(tickets.router, prefix=api)
app.include_router(contact.router, prefix=api)
app.include_router(faqs.router, prefix=api)
app.include_router(plans.router, prefix=api)
app.include_router(features.router, prefix=api)
app.include_router(dashboard.router, prefix=api)
app.include_router(logs.router, prefix=api)
app.include_router(watchlist.router, prefix=api)

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
    "caidat": "caidat.html",
    "taikhoan": "taikhoan.html",
    "doimatkhau": "doimatkhau.html",
    "QuanTri": "QuanTri.html",
    "AdminLogin": "AdminLogin.html",
    "TrungTamTroGiup": "TrungTamTroGiup.html",
    "HuongDanSuDung": "HuongDanSuDung.html",
    "CauHoiThuongGap": "CauHoiThuongGap.html",
    "LienHe": "LienHe.html",
    "DieuKhoanSuDung": "DieuKhoanSuDung.html",
    "ChinhSachBaoMat": "ChinhSachBaoMat.html",
    "MienTruTrachNhiem": "MienTruTrachNhiem.html",
}

INDEX_SHELL_PAGES = {
    "TrungTamTroGiup",
    "HuongDanSuDung",
    "CauHoiThuongGap",
    "LienHe",
    "DieuKhoanSuDung",
    "ChinhSachBaoMat",
    "MienTruTrachNhiem",
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
