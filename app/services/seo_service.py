"""SEO metadata cho shell ``index.html`` (route-aware).

Toàn bộ URL sạch (/, /thi-truong, /co-phieu/..., /tin-tuc, /{slug}-{id}.html)
đều trả cùng một file shell. Nếu để nguyên meta tĩnh thì mọi URL sẽ có chung
title/description/canonical của trang chủ. Module này dựng meta riêng theo path
và thay vào bản HTML shell trước khi trả về.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import unicodedata
from pathlib import Path

from app.services import sector_taxonomy

SITE = "https://tunix.vn"
OG_IMAGE = f"{SITE}/frontend/Logo/VinaFintechCause.png"
OG_IMAGE_W, OG_IMAGE_H = 1536, 1024
DEFAULT_ROBOTS = "index,follow,max-image-preview:large"

META_START = "    <!-- SEO-META-START -->"
META_END = "    <!-- SEO-META-END -->"


def _seo_slug(text: str) -> str:
    value = (text or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return value.strip("-")


def _plain(text: str, limit: int = 160) -> str:
    value = re.sub(r"<[^>]+>", " ", text or "")
    value = html_lib.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _absolute_image(url: str | None) -> str:
    value = (url or "").strip()
    if not value:
        return OG_IMAGE
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"{SITE}{value}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return OG_IMAGE


def _webpage(name: str, desc: str, url: str) -> dict:
    return {
        "@type": "WebPage",
        "@id": f"{url}#webpage",
        "url": url,
        "name": name,
        "description": desc,
        "inLanguage": "vi-VN",
        "isPartOf": {"@id": f"{SITE}/#website"},
    }


def _breadcrumb(page_name: str, url: str) -> dict:
    return {
        "@type": "BreadcrumbList",
        "@id": f"{url}#breadcrumb",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Trang chủ", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": page_name, "item": url},
        ],
    }


_FAQ_CACHE: dict[str, list[dict] | None] = {"entities": None}


def _faq_entities() -> list[dict]:
    """Đọc Q&A tĩnh từ CauHoiThuongGap.html để gắn FAQPage cho URL sạch."""
    if _FAQ_CACHE["entities"] is not None:
        return _FAQ_CACHE["entities"]
    entities: list[dict] = []
    # Ưu tiên DB — khớp đúng nội dung FAQ động mà shell hiển thị sau khi gọi /api/faqs.
    try:
        from app.db import SessionLocal
        from app.models import FAQ

        db = SessionLocal()
        try:
            rows = (
                db.query(FAQ)
                .filter(FAQ.is_visible.is_(True))
                .order_by(FAQ.id)
                .all()
            )
        finally:
            db.close()
        for row in rows:
            question = (row.question or "").strip()
            answer = (row.answer or "").strip()
            if question and answer:
                entities.append({
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer},
                })
    except Exception:  # noqa: BLE001 — DB lỗi thì dùng fallback tĩnh bên dưới
        entities = []

    if not entities:
        try:
            from bs4 import BeautifulSoup

            from app.core.config import settings

            page = Path(settings.frontend_dir) / "CauHoiThuongGap.html"
            if not page.exists():
                page = Path(__file__).resolve().parents[2] / "frontend" / "CauHoiThuongGap.html"
            soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
            seen: set[str] = set()
            for item in soup.select(".faq-item"):
                q_el = item.select_one(".question")
                a_el = item.select_one(".answer")
                if not q_el or not a_el:
                    continue
                question = q_el.get_text(" ", strip=True)
                answer = a_el.get_text(" ", strip=True)
                if not question or not answer or question in seen:
                    continue
                seen.add(question)
                entities.append({
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer},
                })
        except Exception:  # noqa: BLE001 — thiếu file/lỗi parse không được làm hỏng trang
            entities = []
    _FAQ_CACHE["entities"] = entities
    return entities


# path → (title, description, h1, breadcrumb label)
ROUTES: dict[str, tuple[str, str, str, str]] = {
    "/": (
        "VinaFintech - Phân tích cổ phiếu & dữ liệu thị trường",
        "VinaFintech cung cấp dữ liệu thị trường chứng khoán, phân tích cổ phiếu, tin tức tài chính và công cụ đầu tư thông minh cho nhà đầu tư Việt Nam.",
        "VinaFintech - Nền tảng phân tích và đầu tư chứng khoán",
        "",
    ),
    "/thi-truong": (
        "Thị trường chứng khoán Việt Nam - VinaFintech",
        "Thị trường chứng khoán Việt Nam: chỉ số VN-Index, HNX, UPCOM, thanh khoản, khối ngoại và diễn biến ngành, cập nhật real-time.",
        "Thị trường chứng khoán Việt Nam",
        "Thị trường",
    ),
    "/co-phieu": (
        "Khám phá cổ phiếu, ngành & chỉ số - VinaFintech",
        "Khám phá cổ phiếu theo ngành, bộ lọc chỉ số và dữ liệu giao dịch. Theo dõi cơ hội đầu tư cùng VinaFintech.",
        "Khám phá cổ phiếu và ngành",
        "Cổ phiếu",
    ),
    "/tin-tuc": (
        "Tin tức chứng khoán, tài chính mới nhất - VinaFintech",
        "Tin tức chứng khoán, tài chính, bất động sản và doanh nghiệp mới nhất. Cập nhật nhanh, phân tích chuyên sâu cùng VinaFintech.",
        "Tin tức chứng khoán và tài chính",
        "Tin tức",
    ),
    "/tintuc": (
        "Tin tức chứng khoán & tài chính - VinaFintech",
        "Đọc tin tức chứng khoán, tài chính, doanh nghiệp và kinh tế mới nhất. Phân tích nhanh, cập nhật liên tục trên VinaFintech.",
        "Tin tức chứng khoán và tài chính",
        "Tin tức",
    ),
    "/crypto": (
        "Giá crypto, Bitcoin hôm nay - VinaFintech",
        "Giá crypto, Bitcoin, Ethereum hôm nay: biểu đồ, vốn hóa và biến động thị trường tiền mã hóa, cập nhật real-time.",
        "Giá crypto hôm nay",
        "Crypto",
    ),
    "/gia-vang-bac": (
        "Giá vàng, giá bạc hôm nay - VinaFintech",
        "Giá vàng, giá bạc hôm nay: giá trong nước, thế giới, biểu đồ biến động và nhận định thị trường kim loại quý.",
        "Giá vàng, giá bạc hôm nay",
        "Giá vàng bạc",
    ),
    "/ty-gia-ngoai-te": (
        "Tỷ giá ngoại tệ hôm nay - VinaFintech",
        "Tỷ giá ngoại tệ hôm nay: USD, EUR, JPY, CNY và nhiều loại tiền tệ, cập nhật liên tục trên VinaFintech.",
        "Tỷ giá ngoại tệ hôm nay",
        "Tỷ giá ngoại tệ",
    ),
    "/lai-ngan-hang": (
        "Lãi suất ngân hàng mới nhất - VinaFintech",
        "Lãi suất ngân hàng mới nhất: tiền gửi, tiết kiệm và vay vốn của các ngân hàng Việt Nam, cập nhật liên tục.",
        "Lãi suất ngân hàng mới nhất",
        "Lãi suất ngân hàng",
    ),
    "/kham-pha": (
        "Khám phá cơ hội đầu tư - VinaFintech",
        "Khám phá cơ hội đầu tư: cổ phiếu nổi bật, ngành tiềm năng, tín hiệu thị trường và dữ liệu phân tích trên VinaFintech.",
        "Khám phá cơ hội đầu tư",
        "Khám phá",
    ),
    "/vinafai": (
        "VinaF AI - Trợ lý đầu tư thông minh - VinaFintech",
        "VinaF AI - trợ lý tài chính thông minh: phân tích cổ phiếu, tổng hợp tin tức và gợi ý đầu tư dựa trên dữ liệu thị trường.",
        "VinaF AI - Trợ lý đầu tư thông minh",
        "VinaF AI",
    ),
    "/tien-ich": (
        "Bộ công cụ đầu tư chứng khoán - VinaFintech",
        "Bộ công cụ đầu tư: tính giá điều chỉnh, chia đều vốn, lợi nhuận tiền gửi và tỷ giá ngoại tệ trên VinaFintech.",
        "Bộ công cụ đầu tư chứng khoán",
        "Tiện ích",
    ),
    "/CauHoiThuongGap": (
        "Câu hỏi thường gặp về đầu tư - VinaFintech",
        "Giải đáp câu hỏi thường gặp về tài khoản, dữ liệu thị trường, tính năng phân tích và dịch vụ của VinaFintech.",
        "Câu hỏi thường gặp",
        "Câu hỏi thường gặp",
    ),
    "/ChinhSachBaoMat": (
        "Chính sách bảo mật - VinaFintech",
        "Chính sách bảo mật VinaFintech: cách thu thập, sử dụng và bảo vệ thông tin cá nhân của người dùng.",
        "Chính sách bảo mật",
        "Chính sách bảo mật",
    ),
    "/DieuKhoanSuDung": (
        "Điều khoản sử dụng - VinaFintech",
        "Điều khoản sử dụng VinaFintech: quyền và nghĩa vụ của người dùng khi sử dụng nền tảng phân tích đầu tư.",
        "Điều khoản sử dụng",
        "Điều khoản sử dụng",
    ),
    "/MienTruTrachNhiem": (
        "Miễn trừ trách nhiệm - VinaFintech",
        "Miễn trừ trách nhiệm VinaFintech: lưu ý về thông tin thị trường, khuyến nghị đầu tư và rủi ro khi giao dịch.",
        "Miễn trừ trách nhiệm",
        "Miễn trừ trách nhiệm",
    ),
    "/HuongDanSuDung": (
        "Hướng dẫn sử dụng VinaFintech chi tiết",
        "Hướng dẫn sử dụng VinaFintech: cách tra cứu cổ phiếu, đọc biểu đồ, sử dụng bộ lọc và công cụ phân tích.",
        "Hướng dẫn sử dụng VinaFintech",
        "Hướng dẫn sử dụng",
    ),
    "/TrungTamTroGiup": (
        "Trung tâm trợ giúp - VinaFintech",
        "Trung tâm trợ giúp VinaFintech: hướng dẫn, câu hỏi thường gặp và kênh liên hệ hỗ trợ người dùng.",
        "Trung tâm trợ giúp",
        "Trung tâm trợ giúp",
    ),
    "/LienHe": (
        "Liên hệ VinaFintech - Hỗ trợ người dùng",
        "Liên hệ VinaFintech: gửi câu hỏi, góp ý hoặc yêu cầu hỗ trợ về nền tảng phân tích và đầu tư chứng khoán.",
        "Liên hệ VinaFintech",
        "Liên hệ",
    ),
    "/GopY": (
        "Góp ý và phản hồi dịch vụ - VinaFintech",
        "Góp ý và phản hồi VinaFintech: chia sẻ ý kiến để chúng tôi cải thiện chất lượng dữ liệu và dịch vụ.",
        "Góp ý và phản hồi",
        "Góp ý",
    ),
}

CATEGORY_ROUTES: dict[str, tuple[str, str]] = {
    "chung-khoan": (
        "Tin tức chứng khoán - VinaFintech",
        "Tin tức chứng khoán mới nhất: thị trường, cổ phiếu, giao dịch và phân tích chuyên sâu trên VinaFintech.",
    ),
    "ngan-hang": (
        "Tin tức ngân hàng - VinaFintech",
        "Tin tức ngân hàng mới nhất: lãi suất, tín dụng, chính sách và hoạt động của các ngân hàng Việt Nam.",
    ),
    "bat-dong-san": (
        "Tin tức bất động sản - VinaFintech",
        "Tin tức bất động sản mới nhất: thị trường, dự án, chính sách và diễn biến giá trên VinaFintech.",
    ),
    "doanh-nghiep": (
        "Tin tức doanh nghiệp - VinaFintech",
        "Tin tức doanh nghiệp mới nhất: kết quả kinh doanh, cổ đông, đầu tư và hoạt động trên VinaFintech.",
    ),
    "dong-tien-thong-minh": (
        "Dòng tiền thông minh - VinaFintech",
        "Phân tích dòng tiền thông minh: khối ngoại, tự doanh, thanh khoản và biến động dòng tiền thị trường.",
    ),
    "tai-chinh-quoc-te": (
        "Tin tức tài chính quốc tế - VinaFintech",
        "Tin tức tài chính quốc tế mới nhất: chứng khoán Mỹ, kinh tế vĩ mô, hàng hóa và tiền tệ toàn cầu.",
    ),
    "cong-nghe": (
        "Tin tức công nghệ - VinaFintech",
        "Tin tức công nghệ mới nhất: AI, bán dẫn, chuyển đổi số và tác động tới thị trường tài chính.",
    ),
    "tien-ao": (
        "Tin tức tiền ảo - VinaFintech",
        "Tin tức tiền ảo mới nhất: Bitcoin, Ethereum, blockchain và xu hướng thị trường crypto toàn cầu.",
    ),
}

TOOL_ROUTES: dict[str, tuple[str, str]] = {
    "gia-dieu-chinh-co-phieu": (
        "Tính giá điều chỉnh cổ phiếu - VinaFintech",
        "Công cụ tính giá điều chỉnh cổ phiếu sau chia cổ tức, chia tách và phát hành thêm trên VinaFintech.",
    ),
    "chia-deu-von-dau-tu": (
        "Chia đều vốn đầu tư - VinaFintech",
        "Công cụ chia đều vốn đầu tư theo danh mục cổ phiếu, giúp phân bổ vốn hợp lý trên VinaFintech.",
    ),
    "loi-nhuan-tien-gui": (
        "Tính lợi nhuận tiền gửi - VinaFintech",
        "Công cụ tính lãi tiền gửi tiết kiệm theo số tiền, kỳ hạn và lãi suất ngân hàng trên VinaFintech.",
    ),
    "ty-gia-ngoai-te": (
        "Tỷ giá ngoại tệ - VinaFintech",
        "Bảng tỷ giá ngoại tệ hôm nay: USD, EUR, JPY, CNY và nhiều loại tiền tệ, cập nhật trên VinaFintech.",
    ),
}

SECTOR_LABELS = sector_taxonomy.SECTOR_LABELS

PRIVATE_ROUTES: dict[str, tuple[str, str]] = {
    "/taikhoan": ("Thông tin tài khoản - VinaFintech", "Quản lý thông tin tài khoản VinaFintech."),
    "/caidat": ("Cài đặt tài khoản - VinaFintech", "Cài đặt thông tin và tùy chọn tài khoản VinaFintech."),
    "/doimatkhau": ("Đổi mật khẩu - VinaFintech", "Đổi mật khẩu tài khoản VinaFintech."),
    "/tin-hieu": ("Tín hiệu giao dịch - VinaFintech", "Tín hiệu giao dịch cổ phiếu từ VinaFintech Signal Engine."),
}

STOCK_RE = re.compile(r"^/co-phieu/([A-Za-z0-9.]+)/?$")
SECTOR_STOCK_RE = re.compile(r"^/co-phieu/nganh/([^/]+)/([^/]+)/([A-Za-z0-9.]+)/?$")
LEGACY_STOCK_RE = re.compile(r"^/cophieu-([A-Za-z0-9.]+)/?$")
CRYPTO_RE = re.compile(r"^/crypto/([A-Za-z0-9._-]+)/?$")
ARTICLE_RE = re.compile(r"^/(?P<slug>.+)-(?P<id>\d+)\.html$")


def _meta(
    title: str,
    desc: str,
    url: str,
    h1: str,
    *,
    page_name: str = "",
    robots: str = DEFAULT_ROBOTS,
    og_type: str = "website",
    image: str = OG_IMAGE,
    ld: dict | None = None,
) -> dict:
    return {
        "title": title,
        "description": desc,
        "canonical": url,
        "h1": h1,
        "robots": robots,
        "og_type": og_type,
        "image": image,
        "page_name": page_name,
        "ld": ld,
    }


def _simple(title: str, desc: str, path: str, h1: str, page_name: str) -> dict:
    url = f"{SITE}{path}"
    ld = {"@context": "https://schema.org", "@graph": [_webpage(title, desc, url)]}
    if page_name:
        ld["@graph"].append(_breadcrumb(page_name, url))
    return _meta(title, desc, url, h1, page_name=page_name, ld=ld)


def _stock_meta(symbol: str, path: str) -> dict:
    sym = symbol.upper()
    title = f"{sym} - Phân tích cổ phiếu & giá hôm nay - VinaFintech"
    desc = (
        f"Phân tích cổ phiếu {sym}: giá, biểu đồ kỹ thuật, chỉ số tài chính, "
        "cổ đông và tin tức doanh nghiệp trên VinaFintech."
    )
    url = f"{SITE}{path}"
    ld = {
        "@context": "https://schema.org",
        "@graph": [
            _webpage(title, desc, url),
            _breadcrumb(f"Cổ phiếu {sym}", url),
        ],
    }
    return _meta(title, desc, url, f"Cổ phiếu {sym} - Phân tích và giá hôm nay", page_name=f"Cổ phiếu {sym}", ld=ld)


def _article_meta(path: str, article_id: int) -> dict:
    url = f"{SITE}{path}"
    try:
        from app.db import SessionLocal
        from app.models import News, NewsStatusEnum

        db = SessionLocal()
        try:
            news = db.get(News, article_id)
        finally:
            db.close()
        if not news or news.status not in (NewsStatusEnum.approved, NewsStatusEnum.published):
            raise ValueError("not published")
        title = f"{news.title} - VinaFintech"
        desc = _plain(news.summary or news.content, 160)
        canonical = f"{SITE}/{_seo_slug(news.title)}-{news.id}.html"
        image = _absolute_image(news.image_url)
        published = news.published_at or news.created_at
        ld = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Article",
                    "@id": f"{canonical}#article",
                    "mainEntityOfPage": {"@id": f"{canonical}#webpage"},
                    "headline": news.title,
                    "description": desc,
                    "image": [image],
                    "datePublished": published.isoformat() if published else None,
                    "dateModified": (news.created_at or published).isoformat() if (news.created_at or published) else None,
                    "author": {"@type": "Person", "name": news.author_name or "VinaFintech"},
                    "publisher": {
                        "@type": "Organization",
                        "@id": f"{SITE}/#organization",
                        "name": "VinaFintech",
                        "logo": {"@type": "ImageObject", "url": f"{SITE}/frontend/Logo/VinaFintech-Logo.png"},
                    },
                    "inLanguage": "vi-VN",
                },
                _webpage(title, desc, canonical),
                _breadcrumb("Tin tức", canonical),
            ],
        }
        return _meta(
            title, desc, canonical, news.title,
            page_name="Tin tức", og_type="article", image=image, ld=ld,
        )
    except Exception:  # noqa: BLE001 — bài không tồn tại/DB lỗi → meta an toàn
        return _meta(
            "Tin tức - VinaFintech",
            "Tin tức chứng khoán, tài chính mới nhất trên VinaFintech.",
            url,
            "Tin tức",
            page_name="Tin tức",
            robots="noindex,nofollow",
            ld={"@context": "https://schema.org", "@graph": [_webpage("Tin tức - VinaFintech", "Tin tức chứng khoán, tài chính mới nhất trên VinaFintech.", url)]},
        )


def build_shell_meta(path: str) -> dict:
    path = (path or "/").split("?", 1)[0]
    if path != "/":
        path = path.rstrip("/") or "/"

    if path in PRIVATE_ROUTES:
        title, desc = PRIVATE_ROUTES[path]
        return _meta(title, desc, f"{SITE}{path}", title, robots="noindex,nofollow")

    if path.startswith("/quan-ly-tai-san"):
        title = "Quản lý tài sản - VinaFintech"
        return _meta(
            title,
            "Quản lý tài sản đầu tư: danh mục, lợi nhuận, phân bổ tài sản và cảnh báo biến động trên VinaFintech.",
            f"{SITE}{path}",
            "Quản lý tài sản",
            robots="noindex,nofollow",
        )

    if path == "/CauHoiThuongGap":
        title, desc, h1, page_name = ROUTES[path]
        url = f"{SITE}{path}"
        graph: list[dict] = [_webpage(title, desc, url), _breadcrumb(page_name, url)]
        entities = _faq_entities()
        if entities:
            graph.append({"@type": "FAQPage", "@id": f"{url}#faq", "url": url, "mainEntity": entities})
        return _meta(
            title, desc, url, h1, page_name=page_name,
            ld={"@context": "https://schema.org", "@graph": graph},
        )

    if path in ROUTES:
        title, desc, h1, page_name = ROUTES[path]
        if path == "/":
            ld = {
                "@context": "https://schema.org",
                "@graph": [
                    {
                        "@type": "Organization",
                        "@id": f"{SITE}/#organization",
                        "name": "VinaFintech",
                        "url": f"{SITE}/",
                        "logo": f"{SITE}/frontend/Logo/VinaFintech-Logo.png",
                    },
                    {
                        "@type": "WebSite",
                        "@id": f"{SITE}/#website",
                        "url": f"{SITE}/",
                        "name": "VinaFintech",
                        "alternateName": "VinaFintech",
                        "publisher": {"@id": f"{SITE}/#organization"},
                        "inLanguage": "vi-VN",
                        "potentialAction": {
                            "@type": "SearchAction",
                            "target": {
                                "@type": "EntryPoint",
                                "urlTemplate": f"{SITE}/tin-tuc?q={{search_term_string}}",
                            },
                            "query-input": "required name=search_term_string",
                        },
                    },
                    _webpage(title, desc, f"{SITE}/"),
                ],
            }
            return _meta(title, desc, f"{SITE}/", h1, ld=ld)
        return _simple(title, desc, path, h1, page_name)

    slug = path.lstrip("/")
    if slug in CATEGORY_ROUTES:
        title, desc = CATEGORY_ROUTES[slug]
        return _simple(title, desc, path, title.replace(" - VinaFintech", ""), "Tin tức")

    if path == "/co-phieu/nganh":
        title = "Danh sách cổ phiếu theo ngành - VinaFintech"
        desc = "Danh sách cổ phiếu theo ngành: ngân hàng, bất động sản, công nghệ, sản xuất và nhiều nhóm ngành khác."
        return _simple(title, desc, path, "Danh sách cổ phiếu theo ngành", "Cổ phiếu theo ngành")

    match = SECTOR_STOCK_RE.match(path)
    if match:
        return _stock_meta(match.group(3), path)

    if path.startswith("/co-phieu/nganh/"):
        segs = [s for s in path.strip("/").split("/") if s]
        sub1 = segs[2] if len(segs) > 2 else ""
        sub2 = segs[3] if len(segs) > 3 else ""
        label = SECTOR_LABELS.get(sub2) or SECTOR_LABELS.get(sub1)
        if not label:
            parent = SECTOR_LABELS.get(sub1) or (sub1.replace("-", " ").capitalize() if sub1 else "theo ngành")
            label = f"{parent} - {sub2.replace('-', ' ').capitalize()}" if sub2 else parent
        title = f"Cổ phiếu ngành {label} - VinaFintech"
        desc = f"Danh sách cổ phiếu ngành {label}, kèm dữ liệu giá, thanh khoản và chỉ số tài chính."
        return _simple(title, desc, path, f"Cổ phiếu ngành {label}", "Cổ phiếu theo ngành")

    match = STOCK_RE.match(path)
    if match and match.group(1).lower() != "nganh":
        return _stock_meta(match.group(1), path)

    match = LEGACY_STOCK_RE.match(path)
    if match:
        return _stock_meta(match.group(1), f"/co-phieu/{match.group(1).upper()}")

    match = CRYPTO_RE.match(path)
    if match:
        sym = match.group(1).upper()
        title = f"Giá {sym} hôm nay - Crypto - VinaFintech"
        desc = (
            f"Giá {sym} hôm nay: biểu đồ, vốn hóa, khối lượng và biến động thị trường crypto, "
            "cập nhật real-time trên VinaFintech."
        )
        return _simple(title, desc, path, f"Giá {sym} hôm nay", "Crypto")

    if path.startswith("/tien-ich/"):
        tool = path.rsplit("/", 1)[-1]
        title, desc = TOOL_ROUTES.get(
            tool,
            ("Công cụ đầu tư - VinaFintech", "Bộ công cụ tính toán và phân tích đầu tư trên VinaFintech."),
        )
        return _simple(title, desc, path, title.replace(" - VinaFintech", ""), "Tiện ích")

    match = ARTICLE_RE.match(path)
    if match:
        return _article_meta(path, int(match.group("id")))

    # Fallback an toàn: không để canonical trỏ sai về trang chủ.
    title = "VinaFintech - Phân tích và đầu tư chứng khoán"
    desc = "Dữ liệu thị trường, phân tích cổ phiếu và công cụ đầu tư trên VinaFintech."
    return _simple(title, desc, path, title, "")


def _meta_block(meta: dict) -> str:
    title = html_lib.escape(meta["title"], quote=True)
    desc = html_lib.escape(meta["description"], quote=True)
    url = meta["canonical"]
    image = meta["image"]
    block = [
        META_START,
        f'    <meta name="description" content="{desc}" />',
        f'    <meta name="robots" content="{meta["robots"]}" />',
        f'    <link rel="canonical" href="{url}" />',
        '    <meta property="og:locale" content="vi_VN" />',
        f'    <meta property="og:type" content="{meta["og_type"]}" />',
        '    <meta property="og:site_name" content="VinaFintech" />',
        f'    <meta property="og:title" content="{title}" />',
        f'    <meta property="og:description" content="{desc}" />',
        f'    <meta property="og:url" content="{url}" />',
        f'    <meta property="og:image" content="{image}" />',
        f'    <meta property="og:image:width" content="{OG_IMAGE_W}" />',
        f'    <meta property="og:image:height" content="{OG_IMAGE_H}" />',
        '    <meta property="og:image:alt" content="VinaFintech - Nền tảng phân tích đầu tư" />',
        '    <meta name="twitter:card" content="summary_large_image" />',
        f'    <meta name="twitter:title" content="{title}" />',
        f'    <meta name="twitter:description" content="{desc}" />',
        f'    <meta name="twitter:image" content="{image}" />',
    ]
    if meta.get("ld"):
        payload = json.dumps(meta["ld"], ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        block.append(f'    <script type="application/ld+json">{payload}</script>')
    block.append(META_END)
    return "\n".join(block)


def render_shell(base_html: str, meta: dict, prerender_html: str = "") -> str:
    result = re.sub(
        r"<title>.*?</title>",
        lambda _m: f"<title>{html_lib.escape(meta['title'], quote=False)}</title>",
        base_html,
        count=1,
        flags=re.DOTALL,
    )
    block = _meta_block(meta)
    if META_START in result and META_END in result:
        result = re.sub(
            re.escape(META_START) + r".*?" + re.escape(META_END),
            lambda _m: block,
            result,
            count=1,
            flags=re.DOTALL,
        )
    else:
        result = result.replace("</title>", "</title>\n" + block, 1)

    # Nội dung SSR/prerender: chèn vào left-col ngay trong HTML trả về.
    if prerender_html:
        marker = "<!-- SEO_PRERENDER -->"
        if marker in result:
            result = result.replace(marker, prerender_html, 1)
        else:
            result = result.replace(
                '<div class="left-col" id="dynamicContent">',
                '<div class="left-col" id="dynamicContent">' + prerender_html,
                1,
            )
        # Nếu khối prerender đã có H1 thật → bỏ H1 ẩn của shell để tránh 2 H1.
        if re.search(r"<h1\b", prerender_html, re.IGNORECASE):
            result = re.sub(
                r"\s*<!-- SEO-H1 -->\s*<h1 class=\"seo-h1\"[^>]*>.*?</h1>",
                "",
                result,
                count=1,
                flags=re.DOTALL,
            )

    if meta.get("h1"):
        result = re.sub(
            r'(<h1 class="seo-h1"[^>]*>).*?(</h1>)',
            lambda m: m.group(1) + html_lib.escape(meta["h1"], quote=False) + m.group(2),
            result,
            count=1,
            flags=re.DOTALL,
        )
    # Trang riêng tư không có prerender → bỏ marker để HTML gọn.
    result = result.replace("<!-- SEO_PRERENDER -->", "", 1)
    return result
