"""SSR/prerender nội dung chính cho URL sạch.

Mục tiêu Phase 3: HTML trả về cho crawler (và lần tải đầu) đã có nội dung thật,
không phụ thuộc hoàn toàn vào iframe/JS. Với các trang tĩnh (trợ giúp, pháp lý,
FAQ) nội dung được hiển thị luôn; với bài viết, danh sách tin, cổ phiếu… nội dung
được nhúng sẵn và JS chỉ ẩn đi khi iframe tương tác đã sẵn sàng.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import or_

from app.services.seo_service import _absolute_image, _plain, _seo_slug

SITE = "https://tunix.vn"
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 300
_CACHE_LIMIT = 240

CATEGORY_LABELS = {
    "chung-khoan": "Chứng khoán",
    "ngan-hang": "Ngân hàng",
    "bat-dong-san": "Bất động sản",
    "doanh-nghiep": "Doanh nghiệp",
    "dong-tien-thong-minh": "Dòng tiền thông minh",
    "tai-chinh-quoc-te": "Tài chính quốc tế",
    "cong-nghe": "Công nghệ",
    "tien-ao": "Crypto",
}

STATIC_PAGES: dict[str, tuple[str, str]] = {
    "/": ("Home.html", "/Home"),
    "/CauHoiThuongGap": ("CauHoiThuongGap.html", "/frontend/CauHoiThuongGap.html"),
    "/HuongDanSuDung": ("HuongDanSuDung.html", "/frontend/HuongDanSuDung.html"),
    "/TrungTamTroGiup": ("TrungTamTroGiup.html", "/frontend/TrungTamTroGiup.html"),
    "/LienHe": ("LienHe.html", "/frontend/LienHe.html"),
    "/GopY": ("GopY.html", "/frontend/GopY.html"),
    "/DieuKhoanSuDung": ("DieuKhoanSuDung.html", "/frontend/DieuKhoanSuDung.html"),
    "/ChinhSachBaoMat": ("ChinhSachBaoMat.html", "/frontend/ChinhSachBaoMat.html"),
    "/MienTruTrachNhiem": ("MienTruTrachNhiem.html", "/frontend/MienTruTrachNhiem.html"),
}

STATIC_BLOCK_SELECTOR = (
    ".help-page, .guide-page, .faq-page, .contact-page, "
    ".terms-page, .privacy-page, .disclaimer-page, .dashboard"
)

SECTOR_LINKS = [
    ("chung-khoan", "Chứng khoán"),
    ("ngan-hang", "Ngân hàng"),
    ("bat-dong-san", "Bất động sản"),
    ("doanh-nghiep", "Doanh nghiệp"),
    ("cong-nghe", "Công nghệ"),
    ("tai-chinh-quoc-te", "Tài chính quốc tế"),
    ("dong-tien-thong-minh", "Dòng tiền thông minh"),
    ("tien-ao", "Crypto"),
]

SECTOR_LABELS = {
    "tai-chinh": "Tài chính",
    "cong-nghe": "Công nghệ",
    "bat-dong-san": "Bất động sản",
    "nguyen-vat-lieu": "Nguyên vật liệu",
    "hang-hoa-thiet-yeu": "Hàng hóa thiết yếu",
    "hang-hoa-khong-thiet-yeu": "Hàng hóa không thiết yếu",
    "nang-luong": "Năng lượng",
    "cong-nghiep": "Công nghiệp",
    "cham-soc-suc-khoe": "Chăm sóc sức khỏe",
    "tien-ich": "Tiện ích",
    "tai-chinh-ngan-hang": "Tài chính ngân hàng",
}

TOOL_LINKS = [
    ("gia-dieu-chinh-co-phieu", "Tính giá điều chỉnh cổ phiếu"),
    ("chia-deu-von-dau-tu", "Chia đều vốn đầu tư"),
    ("loi-nhuan-tien-gui", "Tính lợi nhuận tiền gửi"),
    ("ty-gia-ngoai-te", "Tỷ giá ngoại tệ"),
]

ARTICLE_RE = re.compile(r"^/(?P<slug>.+)-(?P<id>\d+)\.html$")
STOCK_RE = re.compile(r"^/co-phieu/([A-Za-z0-9.]+)/?$")
SECTOR_STOCK_RE = re.compile(r"^/co-phieu/nganh/([^/]+)/([^/]+)/([A-Za-z0-9.]+)/?$")
LEGACY_STOCK_RE = re.compile(r"^/cophieu-([A-Za-z0-9.]+)/?$")
CRYPTO_RE = re.compile(r"^/crypto/([A-Za-z0-9._-]+)/?$")


def _frontend_dir() -> Path:
    try:
        from app.core.config import settings

        path = Path(settings.frontend_dir)
        if path.exists():
            return path
    except Exception:  # noqa: BLE001
        pass
    return Path(__file__).resolve().parents[2] / "frontend"


def _wrap(content: str, path: str, data_file: str = "") -> str:
    if not content.strip():
        return ""
    return (
        f'<div id="seoPrerender" class="seo-prerender" '
        f'data-path="{html_lib.escape(path, quote=True)}" '
        f'data-file="{html_lib.escape(data_file, quote=True)}">'
        f"{content}</div>"
    )


def _static_block(path: str, file_name: str, data_file: str) -> str:
    try:
        from bs4 import BeautifulSoup

        page = _frontend_dir() / file_name
        raw = page.read_text(encoding="utf-8")
        soup = BeautifulSoup(raw, "html.parser")
        block = soup.select_one(STATIC_BLOCK_SELECTOR)
        if not block:
            return ""
        for link in block.select(".back-link"):
            link.decompose()
        return _wrap(str(block), path, data_file)
    except Exception:  # noqa: BLE001 — thiếu file/lỗi parse không được làm hỏng trang
        return ""


def _faq_block(path: str) -> str:
    """Prerender FAQ từ DB (khớp đúng nội dung động) nhưng giữ header/tab tĩnh."""
    try:
        from bs4 import BeautifulSoup

        from app.db import SessionLocal
        from app.models import FAQ

        db = SessionLocal()
        try:
            rows = db.query(FAQ).filter(FAQ.is_visible.is_(True)).order_by(FAQ.id).all()
        finally:
            db.close()
        if not rows:
            return ""

        page = _frontend_dir() / "CauHoiThuongGap.html"
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        block = soup.select_one(".faq-page")
        faq_list = block.select_one(".faq-list") if block else None
        if not block or not faq_list:
            return ""
        for link in block.select(".back-link"):
            link.decompose()
        faq_list.clear()
        for idx, row in enumerate(rows):
            item = soup.new_tag("div", attrs={"class": "faq-item", "data-category": row.category or ""})
            question = soup.new_tag("div", attrs={"class": "question"})
            question.append(row.question or "")
            question.append(soup.new_tag("i", attrs={"class": "fas fa-chevron-down"}))
            answer = soup.new_tag("div", attrs={"class": "answer" + (" open" if idx == 0 else "")})
            answer.append(row.answer or "")
            item.append(question)
            item.append(answer)
            faq_list.append(item)
        return _wrap(str(block), path, "/frontend/CauHoiThuongGap.html")
    except Exception:  # noqa: BLE001
        return ""


def _sanitize_fragment(fragment: str) -> str:
    """Loại script/style/iframe + thuộc tính nguy hiểm khỏi nội dung bài viết."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(fragment or "", "html.parser")
        for tag in soup(["script", "style", "iframe", "object", "embed", "form", "input", "button"]):
            tag.decompose()
        # Bài viết không nên có H1 riêng (đã có H1 của trang) → hạ xuống H2.
        for h1 in soup.find_all("h1"):
            h1.name = "h2"
        for tag in soup.find_all(True):
            for attr in list(tag.attrs):
                low = attr.lower()
                if low.startswith("on") or low in ("srcdoc", "formaction"):
                    del tag.attrs[attr]
                elif low == "style" and re.search(r"(expression\s*\(|javascript:)", str(tag.attrs[attr]), re.I):
                    del tag.attrs[attr]
            for attr in ("href", "src"):
                value = tag.get(attr)
                if value and str(value).strip().lower().startswith("javascript:"):
                    del tag.attrs[attr]
        return str(soup)
    except Exception:  # noqa: BLE001
        return html_lib.escape(fragment or "")


def _news_url(news) -> str:
    slug = _seo_slug(news.title)
    if slug:
        return f"/{slug}-{news.id}.html"
    return f"/tintuc?id={news.id}"


def _news_date(news) -> str:
    stamp = news.published_at or news.created_at
    return stamp.strftime("%d/%m/%Y") if stamp else ""


def _news_summary(news, limit: int = 180) -> str:
    return _plain(news.summary or news.content, limit)


def _news_list(rows, heading: str = "") -> str:
    items = []
    for n in rows:
        items.append(
            '<article>'
            f'<h2><a href="{html_lib.escape(_news_url(n), quote=True)}">{html_lib.escape(n.title or "")}</a></h2>'
            f'<p>{html_lib.escape(_news_summary(n))}</p>'
            f'<div class="seo-meta">{html_lib.escape(_news_date(n))} · '
            f'{html_lib.escape(CATEGORY_LABELS.get(n.category or "", n.category or "Tin tức"))}</div>'
            '</article>'
        )
    if not items:
        return ""
    head = f'<h2 style="font-size:20px;margin:24px 0 12px;">{html_lib.escape(heading)}</h2>' if heading else ""
    return f'{head}<div class="seo-news-list">{"".join(items)}</div>'


def _query_news(limit: int = 30, category: str = "", symbol: str = ""):
    try:
        from app.db import SessionLocal
        from app.models import News, NewsStatusEnum

        db = SessionLocal()
        try:
            query = db.query(News).filter(
                News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published])
            )
            if category:
                query = query.filter(News.category == category)
            if symbol:
                like = f"%{symbol}%"
                query = query.filter(or_(News.title.ilike(like), News.tags.ilike(like)))
            return (
                query.order_by(News.published_at.desc(), News.id.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — lỗi DB không được làm hỏng trang
        return []


def _news_listing_block(path: str, category: str, title: str, intro: str) -> str:
    rows = _query_news(limit=30, category=category)
    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": title,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": idx,
                "url": f"{SITE}{_news_url(n)}",
                "name": n.title or "",
            }
            for idx, n in enumerate(rows, start=1)
        ],
    }
    ld_json = json.dumps(item_list, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ld_html = f'<script type="application/ld+json">{ld_json}</script>' if rows else ""
    content = (
        '<section>'
        f'<h1 style="font-size:26px;line-height:1.3;margin:0 0 10px;">{html_lib.escape(title)}</h1>'
        f'<p style="font-size:15px;line-height:1.6;color:#5b6b7f;margin:0 0 18px;">{html_lib.escape(intro)}</p>'
        f'{_news_list(rows)}'
        '<p style="margin:18px 0 0;"><a href="/tin-tuc">Xem tất cả tin tức</a></p>'
        f'{ld_html}'
        '</section>'
    )
    return _wrap(content, path, "/frontend/tin-tuc.html")


def _article_block(path: str, article_id: int) -> str:
    try:
        from app.db import SessionLocal
        from app.models import News, NewsStatusEnum

        db = SessionLocal()
        try:
            news = db.get(News, article_id)
            if not news or news.status not in (NewsStatusEnum.approved, NewsStatusEnum.published):
                return ""
            related = (
                db.query(News)
                .filter(
                    News.status.in_([NewsStatusEnum.approved, NewsStatusEnum.published]),
                    News.id != news.id,
                    News.category == news.category,
                )
                .order_by(News.published_at.desc(), News.id.desc())
                .limit(5)
                .all()
            )
        finally:
            db.close()

        title = html_lib.escape(news.title or "")
        summary = html_lib.escape(_news_summary(news, 220))
        image = _absolute_image(news.image_url)
        date = _news_date(news)
        author = html_lib.escape(news.author_name or "VinaFintech")
        category = html_lib.escape(CATEGORY_LABELS.get(news.category or "", news.category or "Tin tức"))
        body = _sanitize_fragment(news.content or "")

        image_html = (
            f'<img src="{html_lib.escape(image, quote=True)}" alt="{title}" '
            'style="width:100%;height:auto;border-radius:12px;margin:18px 0;" loading="eager" '
            'referrerpolicy="no-referrer" onerror="this.style.display=\'none\'" />'
            if image else ""
        )
        share_url = quote(f"{SITE}{path}", safe="")
        share_title = quote(news.title or "", safe="")
        category_slug = html_lib.escape(news.category or "", quote=True)
        category_html = (
            f'<a href="/{category_slug}" style="color:#008080;text-decoration:none;">{category}</a>'
            if category_slug else category
        )
        share_html = (
            '<div style="display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 4px;font-size:14px;">'
            f'<a href="https://www.facebook.com/sharer/sharer.php?u={share_url}" target="_blank" rel="noopener" '
            'style="color:#008080;">Chia sẻ Facebook</a>'
            f'<a href="https://twitter.com/intent/tweet?url={share_url}&amp;text={share_title}" target="_blank" rel="noopener" '
            'style="color:#008080;">Chia sẻ X</a>'
            '<a href="/tin-tuc" style="color:#008080;">Xem thêm tin tức</a>'
            '</div>'
        )
        content = (
            '<article>'
            f'<p style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:0 0 8px;">{category_html}</p>'
            f'<h1 style="font-size:clamp(22px,4vw,30px);line-height:1.3;margin:0 0 12px;">{title}</h1>'
            f'<div class="seo-meta" style="font-size:13px;color:#8a99b1;margin:0 0 14px;">{date} · {author}</div>'
            f'{image_html}'
            f'<p style="font-size:17px;line-height:1.65;font-weight:600;margin:0 0 18px;">{summary}</p>'
            f'<div class="seo-article-body">{body}</div>'
            f'{share_html}'
            f'{_news_list(related, "Tin liên quan")}'
            '</article>'
        )
        return _wrap(content, path, "/tintuc")
    except Exception:  # noqa: BLE001
        return ""


def _stock_block(path: str, symbol: str, sector: str = "", industry: str = "") -> str:
    sym = re.sub(r"[^A-Z0-9.]", "", (symbol or "").upper())
    if not sym:
        return ""
    sector = (sector or "").strip().lower()
    industry = (industry or "").strip().lower()
    sector_name = ""
    industry_name = ""
    try:
        from app.services.vnstock_service import _symbol_sector_index

        info = (_symbol_sector_index() or {}).get(sym) or {}
        sector = sector or str(info.get("sector") or "").strip().lower()
        industry = industry or str(info.get("industry") or "").strip().lower()
        sector_name = str(info.get("sectorName") or "").strip()
        industry_name = str(info.get("industryName") or "").strip()
    except Exception:  # noqa: BLE001
        pass

    exchange = ""
    try:
        from app.db import SessionLocal
        from app.models import StockSymbol

        db = SessionLocal()
        try:
            row = db.query(StockSymbol).filter(StockSymbol.symbol == sym).first()
            exchange = (row.exchange or "") if row else ""
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass

    rows = _query_news(limit=30, symbol=sym)
    sym_re = re.compile(rf"(?<![A-Z0-9]){re.escape(sym)}(?![A-Z0-9])", re.IGNORECASE)
    rows = [n for n in rows if sym_re.search(n.title or "") or sym_re.search(n.tags or "")][:6]
    parts = [
        '<section>',
        f'<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Cổ phiếu {html_lib.escape(sym)}</h1>',
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        f'Phân tích cổ phiếu {html_lib.escape(sym)}: giá, biểu đồ kỹ thuật, chỉ số tài chính, '
        'cổ đông và tin tức doanh nghiệp trên VinaFintech.'
        '</p>',
    ]
    if sector or industry:
        sector_label = sector_name or (sector.replace("-", " ").capitalize() if sector else "")
        industry_label = industry_name or (industry.replace("-", " ").capitalize() if industry else "")
        parts.append(
            '<p style="font-size:14px;line-height:1.6;margin:0 0 14px;">'
            f'Ngành: <strong>{html_lib.escape(sector_label)}</strong>'
            + (f' · Nhóm ngành: <strong>{html_lib.escape(industry_label)}</strong>' if industry_label else "")
            + (f' · Sàn: <strong>{html_lib.escape(exchange)}</strong>' if exchange else "")
            + '</p>'
        )
    links = [
        '<a href="/co-phieu">Khám phá cổ phiếu</a>',
        '<a href="/co-phieu/nganh">Cổ phiếu theo ngành</a>',
    ]
    if sector and industry:
        links.insert(0, f'<a href="/co-phieu/nganh/{quote(sector)}/{quote(industry)}">Cùng nhóm ngành</a>')
    parts.append(
        '<p style="font-size:14px;line-height:1.8;margin:0 0 6px;">' + ' · '.join(links) + '</p>'
    )
    parts.append(_news_list(rows, f"Tin liên quan {sym}"))
    parts.append('</section>')
    return _wrap("".join(parts), path, "/frontend/CoPhieu.html")


def _market_block(path: str) -> str:
    rows = _query_news(limit=10, category="chung-khoan")
    sectors = " · ".join(f'<a href="/{slug}">{label}</a>' for slug, label in SECTOR_LINKS)
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Thị trường chứng khoán Việt Nam</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Theo dõi chỉ số VN-Index, HNX, UPCOM, thanh khoản, khối ngoại và diễn biến ngành. '
        'Dữ liệu thị trường được cập nhật liên tục trên VinaFintech.'
        '</p>'
        f'<p style="font-size:14px;line-height:1.8;margin:0 0 6px;">{sectors}</p>'
        f'{_news_list(rows, "Tin thị trường mới nhất")}'
        '</section>'
    )
    return _wrap(content, path, "/frontend/ThiTruong.html")


def _co_phieu_block(
    path: str,
    heading: str = "Khám phá cổ phiếu và ngành",
    intro: str = (
        "Tra cứu cổ phiếu theo ngành, bộ lọc chỉ số và dữ liệu giao dịch. "
        "Theo dõi cơ hội đầu tư cùng VinaFintech."
    ),
) -> str:
    rows = _query_news(limit=10, category="chung-khoan")
    sectors = "".join(f'<li><a href="/{slug}">{label}</a></li>' for slug, label in SECTOR_LINKS)
    content = (
        '<section>'
        f'<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">{html_lib.escape(heading)}</h1>'
        f'<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">{html_lib.escape(intro)}</p>'
        f'<ul style="font-size:14px;line-height:1.9;margin:0 0 10px;">{sectors}</ul>'
        f'{_news_list(rows, "Tin cổ phiếu mới nhất")}'
        '</section>'
    )
    return _wrap(content, path, "/frontend/CoPhieu.html")


def _crypto_block(path: str) -> str:
    rows = _query_news(limit=12, category="tien-ao")
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Giá crypto hôm nay</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Giá Bitcoin, Ethereum và các đồng tiền mã hóa: biểu đồ, vốn hóa và biến động thị trường, '
        'cập nhật real-time trên VinaFintech.'
        '</p>'
        f'{_news_list(rows, "Tin crypto mới nhất")}'
        '</section>'
    )
    return _wrap(content, path, "/frontend/crypto.html")


def _crypto_symbol_block(path: str, symbol: str) -> str:
    sym = re.sub(r"[^A-Za-z0-9._-]", "", (symbol or "").upper())
    if not sym:
        return ""
    rows = _query_news(limit=10, category="tien-ao")
    content = (
        '<section>'
        f'<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Giá {html_lib.escape(sym)} hôm nay</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        f'Giá {html_lib.escape(sym)} hôm nay: biểu đồ, vốn hóa, khối lượng và biến động thị trường crypto, '
        'cập nhật real-time trên VinaFintech.'
        '</p>'
        '<p style="font-size:14px;line-height:1.8;margin:0 0 6px;">'
        '<a href="/crypto">Tất cả crypto</a> · <a href="/tin-tuc">Tin tức crypto</a></p>'
        f'{_news_list(rows, "Tin crypto mới nhất")}'
        '</section>'
    )
    return _wrap(content, path, "/frontend/crypto.html")


def _gold_block(path: str) -> str:
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Giá vàng, giá bạc hôm nay</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Giá vàng trong nước, giá vàng thế giới, giá bạc và biểu đồ biến động kim loại quý. '
        'Theo dõi nhận định thị trường trên VinaFintech.'
        '</p>'
        '<p style="font-size:14px;line-height:1.8;margin:0;">'
        '<a href="/tin-tuc">Tin tức tài chính</a> · <a href="/ty-gia-ngoai-te">Tỷ giá ngoại tệ</a>'
        '</p>'
        '</section>'
    )
    return _wrap(content, path, "/frontend/gold.html")


def _exchange_block(path: str) -> str:
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Tỷ giá ngoại tệ hôm nay</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Tỷ giá USD, EUR, JPY, CNY và nhiều loại tiền tệ, cập nhật liên tục trên VinaFintech.'
        '</p>'
        '<p style="font-size:14px;line-height:1.8;margin:0;">'
        '<a href="/tien-ich/ty-gia-ngoai-te">Công cụ tỷ giá</a> · <a href="/gia-vang-bac">Giá vàng bạc</a>'
        '</p>'
        '</section>'
    )
    return _wrap(content, path, "/frontend/exchange.html")


def _bank_rates_block(path: str) -> str:
    rows = []
    try:
        from app.db import SessionLocal
        from app.models import BankInterestRate

        db = SessionLocal()
        try:
            rows = (
                db.query(BankInterestRate)
                .filter(BankInterestRate.term_months == 12)
                .order_by(BankInterestRate.interest_rate.desc())
                .limit(12)
                .all()
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        rows = []
    items = "".join(
        f'<tr><td style="padding:6px 8px;border-bottom:1px solid rgba(127,127,127,.15);">{html_lib.escape(r.bank_name)}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid rgba(127,127,127,.15);text-align:right;font-weight:600;">{r.interest_rate:g}%</td></tr>'
        for r in rows
    )
    table = (
        f'<table style="width:100%;border-collapse:collapse;font-size:14px;margin:0 0 10px;">{items}</table>'
        if items else ""
    )
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Lãi suất ngân hàng mới nhất</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Lãi suất tiền gửi, tiết kiệm và vay vốn của các ngân hàng Việt Nam, cập nhật liên tục.'
        '</p>'
        f'{table}'
        '<p style="font-size:14px;line-height:1.8;margin:0;">'
        '<a href="/tien-ich/loi-nhuan-tien-gui">Tính lợi nhuận tiền gửi</a> · '
        '<a href="/tin-tuc">Tin tức ngân hàng</a></p>'
        '</section>'
    )
    return _wrap(content, path, "/frontend/lai-ngan-hang.html")


def _kham_pha_block(path: str) -> str:
    rows = _query_news(limit=10, category="dong-tien-thong-minh")
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Khám phá cơ hội đầu tư</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Cổ phiếu nổi bật, ngành tiềm năng, tín hiệu thị trường và dữ liệu phân tích trên VinaFintech.'
        '</p>'
        f'{_news_list(rows, "Tin dòng tiền thông minh")}'
        '</section>'
    )
    return _wrap(content, path, "/frontend/KhamPha.html")


def _vinafai_block(path: str) -> str:
    content = (
        '<section>'
        '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">VinaF AI - Trợ lý đầu tư thông minh</h1>'
        '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
        'Phân tích cổ phiếu, tổng hợp tin tức và gợi ý đầu tư dựa trên dữ liệu thị trường.'
        '</p>'
        '<p style="font-size:14px;line-height:1.8;margin:0;">'
        '<a href="/co-phieu">Khám phá cổ phiếu</a> · <a href="/tin-tuc">Tin tức thị trường</a></p>'
        '</section>'
    )
    return _wrap(content, path, "/frontend/VinafAI.html")


def _tien_ich_block(path: str) -> str:
    tool = path.rsplit("/", 1)[-1] if path.startswith("/tien-ich/") else ""
    tool_title = dict(TOOL_LINKS).get(tool, "Bộ công cụ đầu tư")
    if tool:
        content = (
            '<section>'
            f'<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">{html_lib.escape(tool_title)}</h1>'
            '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
            'Công cụ tính toán và phân tích đầu tư trên VinaFintech.'
            '</p>'
            '<p style="font-size:14px;line-height:1.8;margin:0;">'
            '<a href="/tien-ich">Tất cả công cụ</a> · <a href="/co-phieu">Cổ phiếu</a></p>'
            '</section>'
        )
    else:
        items = "".join(
            f'<li><a href="/tien-ich/{slug}">{label}</a></li>' for slug, label in TOOL_LINKS
        )
        content = (
            '<section>'
            '<h1 style="font-size:28px;line-height:1.3;margin:0 0 10px;">Bộ công cụ đầu tư chứng khoán</h1>'
            '<p style="font-size:15px;line-height:1.65;color:#5b6b7f;margin:0 0 14px;">'
            'Tính giá điều chỉnh, chia đều vốn, lợi nhuận tiền gửi và tỷ giá ngoại tệ.'
            '</p>'
            f'<ul style="font-size:14px;line-height:1.9;margin:0;">{items}</ul>'
            '</section>'
        )
    return _wrap(content, path, "/frontend/tienich.html")


def _build(path: str) -> str:
    if path == "/CauHoiThuongGap":
        faq_block = _faq_block(path)
        if faq_block:
            return faq_block

    if path in STATIC_PAGES:
        file_name, data_file = STATIC_PAGES[path]
        block = _static_block(path, file_name, data_file)
        if block:
            return block

    match = ARTICLE_RE.match(path)
    if match:
        return _article_block(path, int(match.group("id")))

    if path == "/tin-tuc":
        return _news_listing_block(
            path, "", "Tin tức chứng khoán, tài chính mới nhất",
            "Tin tức chứng khoán, tài chính, bất động sản và doanh nghiệp mới nhất, cập nhật liên tục.",
        )
    if path.lstrip("/") in CATEGORY_LABELS:
        category = path.lstrip("/")
        label = CATEGORY_LABELS[category]
        return _news_listing_block(
            path, category, f"Tin tức {label}",
            f"Tin tức {label} mới nhất, cập nhật liên tục trên VinaFintech.",
        )

    match = SECTOR_STOCK_RE.match(path)
    if match:
        return _stock_block(path, match.group(3), match.group(1), match.group(2))
    if path == "/co-phieu/nganh":
        return _co_phieu_block(
            path, "Danh sách cổ phiếu theo ngành",
            "Danh sách cổ phiếu theo ngành: ngân hàng, bất động sản, công nghệ, sản xuất và nhiều nhóm ngành khác.",
        )
    if path.startswith("/co-phieu/nganh/"):
        segs = [s for s in path.strip("/").split("/") if s]
        sub1 = segs[2] if len(segs) > 2 else ""
        sub2 = segs[3] if len(segs) > 3 else ""
        label = SECTOR_LABELS.get(sub2) or SECTOR_LABELS.get(sub1)
        if not label:
            parent = SECTOR_LABELS.get(sub1) or (sub1.replace("-", " ").capitalize() if sub1 else "theo ngành")
            label = f"{parent} - {sub2.replace('-', ' ').capitalize()}" if sub2 else parent
        return _co_phieu_block(
            path, f"Cổ phiếu ngành {label}",
            f"Danh sách cổ phiếu ngành {label}, kèm dữ liệu giá, thanh khoản và chỉ số tài chính.",
        )
    match = STOCK_RE.match(path)
    if match and match.group(1).lower() != "nganh":
        return _stock_block(path, match.group(1))
    match = LEGACY_STOCK_RE.match(path)
    if match:
        return _stock_block(path, match.group(1))

    match = CRYPTO_RE.match(path)
    if match:
        return _crypto_symbol_block(path, match.group(1))

    if path == "/thi-truong":
        return _market_block(path)
    if path == "/co-phieu":
        return _co_phieu_block(path)
    if path == "/crypto":
        return _crypto_block(path)
    if path == "/gia-vang-bac":
        return _gold_block(path)
    if path == "/ty-gia-ngoai-te":
        return _exchange_block(path)
    if path == "/lai-ngan-hang":
        return _bank_rates_block(path)
    if path == "/kham-pha":
        return _kham_pha_block(path)
    if path == "/vinafai":
        return _vinafai_block(path)
    if path.startswith("/tien-ich"):
        return _tien_ich_block(path)
    return ""


def build_prerender_html(path: str) -> str:
    path = (path or "/").split("?", 1)[0]
    if path != "/":
        path = path.rstrip("/") or "/"

    now = time.time()
    cached = _CACHE.get(path)
    if cached and now - cached[0] <= _CACHE_TTL:
        return cached[1]

    content = _build(path)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[path] = (now, content)
    return content
