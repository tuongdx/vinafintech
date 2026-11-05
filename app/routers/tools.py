"""Tool hỗ trợ viết bài: import tin tức từ file JSON (VD: MarketTimes) vào bảng news.

Mapping các trường JSON:
  title          -> News.title
  sapo           -> News.summary
  main_image     -> News.image_url
  image_caption  -> News.image_caption
  tags           -> News.tags        (list -> chuỗi phẩy, bỏ phần tử rỗng)
  url            -> News.source_url
  content_html   -> News.content     (giữ text + ảnh, bỏ link trong text, bỏ banner/quảng cáo)
  published_at   -> News.published_at (ngày giờ ĐĂNG của bài — giữ đúng ngày giờ trong file:
                    published_at / published_at_raw / published_date + published_time).
                    File không có ngày giờ -> dùng thời điểm import (giờ VN).
  source         -> News.source (tự suy theo chuyên mục: Chứng khoán/Ngân hàng/Doanh nghiệp
                    = "An ninh tiền tệ"; còn lại = "MarketTimes". Có thể ghi đè qua param `source`).

Trạng thái theo lựa chọn của người dùng:
  approve=False, publish=False (publish bị disable) -> pending_approval (chờ duyệt thủ công)
  approve=True , publish=False                      -> approved (đã duyệt, chờ xuất bản)
  approve=True , publish=True                       -> published (xuất bản tự động)
"""
import json
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup, Comment
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import News, NewsStatusEnum, RoleEnum, ToolFile, User
from app.services import news_source, news_tags, news_time

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
    "tien-ao",
}

# Block quảng cáo / banner cần XÓA bỏ hoàn toàn
_AD_BLOCK_SELECTOR = (
    ".c-banner, .h-show-pc, .h-show-mobile, .td-a-ad, "
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


def clean_content(html: str, featured_image_url: str | None = None) -> str:
    """Làm sạch HTML và bỏ ảnh đầu bài nếu đã hiển thị ở phần ảnh đại diện."""
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

    # Khôi phục URL ảnh trước khi xóa thuộc tính lazy-load của nguồn.
    for el in soup.select("img, source"):
        for attr, lazy_attrs in (
            ("src", ("data-src", "data-lazy-src")),
            ("srcset", ("data-srcset", "data-lazy-srcset")),
        ):
            value = next((el.get(lazy_attr) for lazy_attr in lazy_attrs if el.get(lazy_attr)), None)
            if value:
                el[attr] = value

    # Ảnh đầu bài đã được hiển thị ở phần ảnh đại diện, không lặp lại trong nội dung.
    first_image = soup.find("img")
    if first_image and featured_image_url:
        image_url = (first_image.get("src") or "").split("?", 1)[0].rstrip("/")
        featured_url = featured_image_url.split("?", 1)[0].rstrip("/")
        if image_url == featured_url:
            image_block = (
                first_image.find_parent("figure")
                or first_image.find_parent("p")
                or first_image.find_parent("picture")
            )
            if image_block and not image_block.get_text(strip=True):
                image_block.decompose()
            else:
                first_image.decompose()

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


def _parse_news_json(raw: str) -> list[dict]:
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="File JSON đang trống.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(f"[{raw.strip()}]")
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail="File JSON không hợp lệ. Hãy dùng một object hoặc danh sách bài viết dạng JSON.",
            ) from exc

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise HTTPException(
            status_code=400,
            detail="File JSON phải là một object hoặc danh sách các bài viết.",
        )
    if any(not isinstance(item, dict) for item in data):
        raise HTTPException(
            status_code=400,
            detail="Mỗi bài viết trong file JSON phải là một object.",
        )
    return data


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
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được file JSON (sai đường dẫn hoặc mã hóa không hợp lệ).",
        ) from exc
    return _parse_news_json(raw)


@router.post("/import-news")
async def import_news(
    file: UploadFile | None = File(None),
    file_path: str = Form(""),
    author: str = Form("Ban biên tập VinaFintech"),
    category: str = Form("chung-khoan"),
    source: str = Form(""),
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
        data = _parse_news_json(raw)
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
    # Nguồn tự suy theo chuyên mục (An ninh Tiền tệ vs MarketTimes); cho phép ghi đè thủ công.
    source_override = (source or "").strip()
    default_source = source_override or news_source.source_for_category(category)
    author = (author or "").strip() or "Ban biên tập VinaFintech"

    existing_news = {
        row.source_url: row
        for row in db.query(News).filter(News.source_url.isnot(None)).all()
    }

    inserted = skipped = updated = repaired = errors = 0
    inserted_titles: list[str] = []
    skipped_titles: list[str] = []
    updated_titles: list[str] = []
    error_details: list[str] = []

    for i, item in enumerate(data, start=1):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title:
            errors += 1
            error_details.append(f"[{i}] Thiếu tiêu đề.")
            continue
        published_at = news_time.parse_published_at(item)
        if url and url in existing_news:
            # Sửa dữ liệu ảnh cũ bị lưu sai; các nội dung đã biên tập vẫn được giữ nguyên.
            row = existing_news[url]
            image_url = (item.get("main_image") or "").strip() or None
            stored_content = (row.content or "").lower()
            needs_image_repair = (
                "data:image/" in stored_content
                or "data-src=" in stored_content
                or "data-srcset=" in stored_content
                or (image_url is not None and row.image_url != image_url)
            )
            if needs_image_repair:
                if not dry_bool:
                    row.content = clean_content(item.get("content_html") or "", image_url)
                    if image_url:
                        row.image_url = image_url
                repaired += 1
            if published_at is not None and row.published_at != published_at:
                if not dry_bool:
                    row.published_at = published_at
                updated += 1
                updated_titles.append(title)
            skipped += 1
            skipped_titles.append(title)
            continue

        if not dry_bool:
            image_url = (item.get("main_image") or "").strip() or None
            content = clean_content(item.get("content_html") or "", image_url)
            news = News(
                title=title,
                summary=(item.get("sapo") or "").strip(),
                content=content,
                category=category,
                image_url=image_url,
                image_caption=(item.get("image_caption") or "").strip() or None,
                tags=news_tags.with_default_news_tags(
                    _join_tags(item.get("tags") or item.get("hashtags"))
                ),
                source=source_override or (item.get("source") or "").strip() or default_source,
                source_url=url or None,
                author_name=author,
                status=status,
                # Ngày giờ trong file input; file không có thì lấy thời điểm import (giờ VN)
                published_at=published_at or news_time.now_vn(),
            )
            db.add(news)
            if url:
                existing_news[url] = news

        inserted += 1
        inserted_titles.append(title)

    if not dry_bool:
        db.commit()

    return {
        "dry_run": dry_bool,
        "total": len(data),
        "inserted": inserted,
        "skipped": skipped,
        "updated": updated,
        "repaired": repaired,
        "errors": errors,
        "status": status.value,
        "category": category,
        "source": default_source,
        "author": author,
        "titles": (inserted_titles + skipped_titles)[:50],
        "updated_titles": updated_titles[:50],
        "error_details": error_details[:20],
    }


# ============================================================================
# TOOL CRAWL BÀI VIẾT — admin tải lên; biên tập viên / quản trị nội dung tải về
# ============================================================================
_CRAWL_TOOL_KEY = "crawl-news"
_CRAWL_TOOL_MAX_BYTES = 25 * 1024 * 1024  # 25 MB
# Vai trò được XEM & TẢI VỀ (giống quyền dùng tool viết bài) + admin
_CRAWL_DOWNLOAD_ROLES = (*_TOOL_ROLES, RoleEnum.admin)
# Vai trò được TẢI LÊN / thay file tool — chỉ nhóm quản trị
_CRAWL_UPLOAD_ROLES = (RoleEnum.super_admin, RoleEnum.admin)


def _safe_filename(name: str) -> str:
    """Chỉ giữ tên file (bỏ đường dẫn), loại ký tự có thể phá header khi tải về."""
    base = Path(name or "").name.strip().replace('"', "").replace("\\", "")
    return base[:200] or "crawl-tool"


@router.get("/crawl-tool")
def crawl_tool_meta(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_CRAWL_DOWNLOAD_ROLES)),
):
    """Thông tin file tool crawl hiện có (để UI hiển thị tên / kích thước / ngày cập nhật)."""
    row = db.query(ToolFile).filter(ToolFile.key == _CRAWL_TOOL_KEY).first()
    if not row:
        return {"exists": False, "filename": "", "size": 0, "updated_at": None, "uploaded_by": ""}
    return {
        "exists": True,
        "filename": row.filename,
        "size": row.size or 0,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "uploaded_by": row.uploaded_by or "",
    }


@router.get("/crawl-tool/download")
def download_crawl_tool(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_CRAWL_DOWNLOAD_ROLES)),
):
    """Tải file tool crawl về máy người dùng (Content-Disposition: attachment)."""
    row = db.query(ToolFile).filter(ToolFile.key == _CRAWL_TOOL_KEY).first()
    if not row:
        raise HTTPException(status_code=404, detail="Admin chưa tải tool crawl bài viết lên hệ thống.")
    filename = _safe_filename(row.filename)
    ascii_name = filename.encode("ascii", "ignore").decode() or "crawl-tool"
    return Response(
        content=row.data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
            ),
            "Content-Length": str(len(row.data)),
            "Cache-Control": "no-store",
        },
    )


@router.post("/crawl-tool")
async def upload_crawl_tool(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_CRAWL_UPLOAD_ROLES)),
):
    """Admin tải lên (hoặc thay thế) file tool crawl bài viết."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="File tải lên rỗng.")
    if len(raw) > _CRAWL_TOOL_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File quá lớn (tối đa {_CRAWL_TOOL_MAX_BYTES // (1024 * 1024)} MB).",
        )
    filename = _safe_filename(file.filename)
    content_type = file.content_type or "application/octet-stream"
    row = db.query(ToolFile).filter(ToolFile.key == _CRAWL_TOOL_KEY).first()
    if row is None:
        row = ToolFile(key=_CRAWL_TOOL_KEY, data=b"")
        db.add(row)
    row.filename = filename
    row.content_type = content_type
    row.size = len(raw)
    row.data = raw
    row.uploaded_by = user.email or ""
    db.commit()
    return {"ok": True, "filename": filename, "size": len(raw), "uploaded_by": row.uploaded_by}
