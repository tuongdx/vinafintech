"""Test SEO technical/on-page — chạy: python scripts/test_seo.py

Kiểm tra nhanh các thay đổi SEO Phase 1 + meta động theo route:
robots.txt, sitemap.xml, canonical/title theo URL, noindex trang riêng tư,
X-Robots-Tag cho file nhúng /frontend/*.html và JSON-LD.
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from app.main import app
from app.services.seo_service import build_shell_meta

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def test_route_meta():
    print("route meta")
    m = build_shell_meta("/thi-truong")
    check("thi-truong title", "Thị trường" in m["title"], m["title"])
    check("thi-truong canonical", m["canonical"].endswith("/thi-truong"), m["canonical"])
    check("thi-truong indexable", "index" in m["robots"], m["robots"])

    m = build_shell_meta("/co-phieu/nganh/cong-nghe/phan-mem-va-dich-vu-cntt/FPT")
    check("stock title has symbol", "FPT" in m["title"], m["title"])
    check("stock canonical is sector url", "/co-phieu/nganh/" in m["canonical"], m["canonical"])

    m = build_shell_meta("/taikhoan")
    check("private noindex", "noindex" in m["robots"], m["robots"])


def test_technical_routes():
    print("technical routes")
    client = TestClient(app)

    r = client.get("/robots.txt")
    check("robots 200", r.status_code == 200, r.status_code)
    check("robots has sitemap", "Sitemap:" in r.text, r.text[:80])

    r = client.get("/sitemap.xml")
    check("sitemap 200", r.status_code == 200, r.status_code)
    try:
        root = ET.fromstring(r.text)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [e.text for e in root.findall("s:url/s:loc", ns)]
        check("sitemap has home", any(u.rstrip("/").endswith("tunix.vn") or "localhost" in u for u in urls), urls[:2])
        check("sitemap excludes private", not any(u.rstrip("/").endswith("/taikhoan") for u in urls))
        check("sitemap unique", len(urls) == len(set(urls)), len(urls))
    except ET.ParseError as exc:
        check("sitemap valid xml", False, str(exc))

    r = client.get("/Index", follow_redirects=False)
    check("/Index 301", r.status_code == 301 and r.headers.get("location") == "/", r.status_code)

    r = client.get("/frontend/DieuKhoanSuDung.html")
    check("/frontend noindex header", r.headers.get("x-robots-tag") == "noindex, nofollow", r.headers.get("x-robots-tag"))

    r = client.get("/CauHoiThuongGap")
    check("FAQ page has FAQPage ld", "FAQPage" in r.text, "FAQPage missing")
    check("FAQ page prerendered", 'id="seoPrerender"' in r.text and "Câu hỏi thường gặp" in r.text)
    check("FAQ page one h1", r.text.count("<h1") == 1, r.text.count("<h1"))

    r = client.get("/co-phieu/FPT", follow_redirects=True)
    check("stock page prerendered", 'id="seoPrerender"' in r.text and "Cổ phiếu FPT" in r.text)

    r = client.get("/tin-tuc")
    check("news page prerendered", 'id="seoPrerender"' in r.text and ".html" in r.text)
    check("news page one h1", r.text.count("<h1") == 1, r.text.count("<h1"))

    r = client.get("/DieuKhoanSuDung")
    check("legal page prerendered", 'id="seoPrerender"' in r.text and "Điều khoản sử dụng" in r.text)
    check("legal page one h1", r.text.count("<h1") == 1, r.text.count("<h1"))

    try:
        from app.db import SessionLocal
        from app.models import News, NewsStatusEnum
        from app.services.seo_service import _seo_slug

        db = SessionLocal()
        try:
            news = (
                db.query(News)
                .filter(News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]))
                .order_by(News.id.desc())
                .first()
            )
        finally:
            db.close()
        if news:
            r = client.get(f"/{_seo_slug(news.title)}-{news.id}.html")
            check("article prerendered", 'id="seoPrerender"' in r.text and news.title[:20] in r.text)
            check("article one h1", r.text.count("<h1") == 1, r.text.count("<h1"))
    except Exception as exc:  # noqa: BLE001 — DB chưa có dữ liệu thì bỏ qua
        print(f"  SKIP  article prerender ({exc})")

    r = client.get("/taikhoan")
    check("private page no prerender", 'id="seoPrerender"' not in r.text)


if __name__ == "__main__":
    test_route_meta()
    test_technical_routes()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
