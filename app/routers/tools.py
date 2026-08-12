"""Tool hỗ trợ viết bài: import tin tức từ file JSON (VD: MarketTimes) vào bảng news.

Mapping các trường JSON:
  title          -> News.title
  sapo           -> News.summary
  main_image     -> News.image_url
  image_caption  -> News.image_caption
  tags           -> News.tags        (list -> chuỗi phẩy, bỏ phần tử rỗng)
  url            -> News.source_url
  content_html   -> News.content     (giữ text + ảnh, bỏ link trong text, bỏ banner/quảng cáo)

Trạng thái theo lựa chọn của người dùng:
  approve=False, publish=False (publish bị disable) -> pending_approval (chờ duyệt thủ công)
  approve=True , publish=False                      -> approved (đã duyệt, chờ xuất bản)
  approve=True , publish=True                       -> published (xuất bản tự động)
"""
import json
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import News, NewsStatusEnum, RoleEnum, User

router = APIRouter(prefix="/admin/tools", tags=["admin-tools"])

# Những vai trò được phép viết bài & dùng tool (giống quyền tạo tin tức)
_TOOL_ROLES = (RoleEnum.super_admin, RoleEnum.content_manager, RoleEnum.editor)

_ALLOWED_CATEGORIES = {
    "chung-khoan",
    "ngan-hang",
    "bat-dong-san",
    "doanh-nghiep",
    "dong-tien-thong-minh",
    "tai-chinh-quoc-te",
    "cong-nghe",
}

# Block quảng cáo / banner cần XÓA bỏ hoàn toàn
_AD_BLOCK_SELECTOR = (
    ".c-banner, .h-show-pc, .h-show-mobile, "
    ".c-banner-item, .c-banner-item__inner, .c-banner-item__box"
)

# Thuộc tính data-* của wrapper "rác" (crawl từ trình soạn thảo AI) -> unwrap giữ nội dung
_JUNK_ATTRS = (
    "data-is-intersecting",
    "data-conversation-screenshot-content",
    "data-message-author-role",
    "data-testid",
    "data-turn-id",
    "data-turn-id-container",
    "data-message-model-slug",
    "data-turn-start-message",
)


def clean_content(html: str) -> str:
    """Làm sạch content_html: giữ text + ảnh, bỏ link & banner, gỡ wrapper rác."""
    soup = BeautifulSoup(html or "", "html.parser")

    # Bỏ comment
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # Xóa block quảng cáo / banner
    for el in soup.select(_AD_BLOCK_SELECTOR):
        el.decompose()

    # Unwrap wrapper AI rác (giữ text/ảnh bên trong)
    for el in soup.find_all(True):
        if any(el.has_attr(a) for a in _JUNK_ATTRS):
            el.unwrap()

    # Bỏ link (giữ text) — an toàn kể cả khi nguồn có <a>
    for a in soup.find_all("a"):
        a.unwrap()

    # Gỡ div bọc ngoài bài viết
    for w in soup.select("div.content-main-normal"):
        w.unwrap()

    # Gỡ mọi thuộc tính data-*
    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.startswith("data-")]:
            del el.attrs[attr]

    # Xóa các tag rỗng (không text, không ảnh)
    changed = True
    while changed:
        changed = False
        for el in soup.find_all(["p", "div", "span", "section"]):
            text = el.get_text(strip=True)
            has_media = el.find("img") or el.find("figure")
            if not text and not has_media and not el.find(
                ["p", "ul", "ol", "h1", "h2", "h3", "h4", "table"]
            ):
                el.decompose()
                changed = True

    return str(soup).strip()


def _join_tags(tags) -> str | None:
    """tags (list) -> chuỗi phân tách bằng phẩy, bỏ phần tử rỗng."""
    if not tags:
        return None
    items = [str(t).strip() for t in tags if str(t).strip()]
    return ", ".join(items) if items else None


def _resolve_category(category: str) -> str:
    cat = (category or "").strip().lower()
    return cat if cat in _ALLOWED_CATEGORIES else "chung-khoan"


def _read_json(path: Path) -> list[dict]:
    if path.suffix.lower() != ".json" or not path.is_file():
        raise HTTPException(
            status_code=400,
            detail="Đường dẫn file không hợp lệ — cần trỏ tới một file .json tồn tại trên máy chủ.",
        )
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được file JSON (sai đường dẫn hoặc file không hợp lệ).",
        ) from exc
    if not isinstance(data, list):
        raise HTTPException(
            status_code=400,
            detail="File JSON phải là một danh sách (array) các bài viết.",
        )
    return data


@router.post("/import-news")
async def import_news(
    file: UploadFile | None = File(None),
    file_path: str = Form(""),
    author: str = Form("Ban biên tập TuniX"),
    category: str = Form("chung-khoan"),
    approve: str = Form("false"),
    publish: str = Form("false"),
    dry_run: str = Form("false"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_TOOL_ROLES)),
):
    """Import bài viết từ file JSON (tải lên từ máy người dùng HOẶC đường dẫn trên máy chủ).

    - Chọn file JSON từ máy (khi website public) hoặc dán đường dẫn file trên máy chủ.
    - dry_run=true chỉ xem trước (đếm), không ghi DB.
    """
    approve_bool = str(approve).strip().lower() in ("1", "true", "yes", "on")
    publish_bool = str(publish).strip().lower() in ("1", "true", "yes", "on")
    dry_bool = str(dry_run).strip().lower() in ("1", "true", "yes", "on")

    # Ưu tiên file tải lên; nếu không có thì đọc theo đường dẫn máy chủ
    if file is not None and file.filename:
        try:
            raw = (await file.read()).decode("utf-8")
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Không đọc được file đã chọn (sai định dạng hoặc không phải UTF-8)."
            ) from exc
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="File đã chọn không phải JSON hợp lệ.") from exc
        if not isinstance(data, list):
            raise HTTPException(
                status_code=400, detail="File JSON phải là một danh sách (array) các bài viết."
            )
    elif file_path.strip():
        data = _read_json(Path(file_path.strip()))
    else:
        raise HTTPException(
            status_code=400, detail="Vui lòng chọn file JSON hoặc nhập đường dẫn file trên máy chủ."
        )

    # Trạng thái theo lựa chọn phê duyệt / xuất bản
    if approve_bool and publish_bool:
        status = NewsStatusEnum.published
    elif approve_bool:
        status = NewsStatusEnum.approved
    else:
        status = NewsStatusEnum.pending_approval

    category = _resolve_category(category)
    author = (author or "").strip() or "Ban biên tập TuniX"

    existing_urls = {
        row.source_url
        for row in db.query(News.source_url).filter(News.source_url.isnot(None)).all()
    }

    inserted = skipped = errors = 0
    inserted_titles: list[str] = []
    skipped_titles: list[str] = []
    error_details: list[str] = []

    for i, item in enumerate(data, start=1):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title:
            errors += 1
            error_details.append(f"[{i}] Thiếu tiêu đề.")
            continue
        if url and url in existing_urls:
            skipped += 1
            skipped_titles.append(title)
            continue

        if not dry_bool:
            content = clean_content(item.get("content_html") or "")
            news = News(
                title=title,
                summary=(item.get("sapo") or "").strip(),
                content=content,
                category=category,
                image_url=(item.get("main_image") or "").strip() or None,
                image_caption=(item.get("image_caption") or "").strip() or None,
                tags=_join_tags(item.get("tags")),
                source="MarketTimes",
                source_url=url or None,
                author_name=author,
                status=status,
            )
            db.add(news)
            if url:
                existing_urls.add(url)

        inserted += 1
        inserted_titles.append(title)

    if not dry_bool:
        db.commit()

    return {
        "dry_run": dry_bool,
        "total": len(data),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "status": status.value,
        "category": category,
        "author": author,
        "titles": (inserted_titles + skipped_titles)[:50],
        "error_details": error_details[:20],
    }
