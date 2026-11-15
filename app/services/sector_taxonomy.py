"""Phân ngành theo chuẩn ICB (2 cấp) — taxonomy dùng cho toàn site.

Nguồn gán mã → ngành: VCI `price/symbols/getAll` (field `icbCode2`), do
`app/services/vnstock_service.py` tải và cache. Đây chính là chuẩn ICB mà thư viện
vnstock sử dụng (`Listing.symbols_by_industries`), nhưng KHÔNG gọi thư viện vì:
- vnstock 3.x đã ngừng phát hành (PyPI đã gỡ gói) và thế hệ 4/5 ngừng hoạt động từ 10/11/2026;
- endpoint GraphQL mà thư viện dùng (`trading.vietcap.com.vn/data-mt/graphql`) nay trả rỗng,
  trong khi VCI vẫn trả `icbCode2` trong danh sách mã.

Cấp 1: 10 nhóm ngành. Cấp 2: 19 ngành (đúng các mã ICB level 2 đang có trên thị trường VN).
Slug cố định ở đây (không suy ra từ tên) để URL ngành không đổi khi đổi nhãn hiển thị.
"""

ICB_LEVEL1: dict[str, str] = {
    "dau-khi": "Dầu khí",
    "nguyen-vat-lieu": "Nguyên vật liệu",
    "cong-nghiep": "Công nghiệp",
    "hang-tieu-dung": "Hàng tiêu dùng",
    "y-te": "Y tế",
    "dich-vu-tieu-dung": "Dịch vụ tiêu dùng",
    "vien-thong": "Viễn thông",
    "tien-ich": "Tiện ích",
    "tai-chinh": "Tài chính",
    "cong-nghe": "Công nghệ",
}

# code ICB level 2 → (slug, tên hiển thị, slug nhóm cấp 1)
ICB_LEVEL2: dict[str, tuple[str, str, str]] = {
    "0500": ("dau-khi", "Dầu khí", "dau-khi"),
    "1300": ("hoa-chat", "Hóa chất", "nguyen-vat-lieu"),
    "1700": ("tai-nguyen-co-ban", "Tài nguyên cơ bản", "nguyen-vat-lieu"),
    "2300": ("xay-dung-va-vat-lieu", "Xây dựng và Vật liệu", "cong-nghiep"),
    "2700": ("hang-va-dich-vu-cong-nghiep", "Hàng và Dịch vụ công nghiệp", "cong-nghiep"),
    "3300": ("o-to-va-phu-tung", "Ô tô và Phụ tùng", "hang-tieu-dung"),
    "3500": ("thuc-pham-va-do-uong", "Thực phẩm và Đồ uống", "hang-tieu-dung"),
    "3700": ("hang-ca-nhan-va-gia-dung", "Hàng cá nhân và Gia dụng", "hang-tieu-dung"),
    "4500": ("y-te", "Y tế", "y-te"),
    "5300": ("ban-le", "Bán lẻ", "dich-vu-tieu-dung"),
    "5500": ("truyen-thong", "Truyền thông", "dich-vu-tieu-dung"),
    "5700": ("du-lich-va-giai-tri", "Du lịch và Giải trí", "dich-vu-tieu-dung"),
    "6500": ("vien-thong", "Viễn thông", "vien-thong"),
    "7500": ("tien-ich-cong-dong", "Tiện ích cộng đồng", "tien-ich"),
    "8300": ("ngan-hang", "Ngân hàng", "tai-chinh"),
    "8500": ("bao-hiem", "Bảo hiểm", "tai-chinh"),
    "8600": ("bat-dong-san", "Bất động sản", "tai-chinh"),
    "8700": ("dich-vu-tai-chinh", "Dịch vụ tài chính", "tai-chinh"),
    "9500": ("cong-nghe-thong-tin", "Công nghệ Thông tin", "cong-nghe"),
}

ICB_LEVEL1_SLUGS = frozenset(ICB_LEVEL1)
ICB_LEVEL2_SLUGS = frozenset(slug for slug, _name, _parent in ICB_LEVEL2.values())
# slug ngành cấp 2 → slug nhóm cấp 1 chứa nó (để canonical hoá URL)
ICB_LEVEL2_PARENT: dict[str, str] = {slug: parent for slug, _name, parent in ICB_LEVEL2.values()}

# Nhãn dùng cho SEO (title/description trang /co-phieu/nganh/...): gộp cả 2 cấp.
SECTOR_LABELS: dict[str, str] = {
    **ICB_LEVEL1,
    **{slug: name for slug, name, _parent in ICB_LEVEL2.values()},
}

# ---------------------------------------------------------------------------
# 301 redirect cho URL ngành cũ (taxonomy Simplize trước đây) → trang ICB tương ứng.
# Giữ link/SEO cũ không bị 404. Slug nào đã trùng slug ICB thì không cần redirect.
# ---------------------------------------------------------------------------
LEGACY_LEVEL1_REDIRECTS: dict[str, str] = {
    "bat-dong-san": "/co-phieu/nganh/tai-chinh/bat-dong-san",
    "cham-soc-suc-khoe": "/co-phieu/nganh/y-te",
    "hang-hoa-thiet-yeu": "/co-phieu/nganh/hang-tieu-dung",
    "hang-hoa-khong-thiet-yeu": "/co-phieu/nganh/hang-tieu-dung",
    "nang-luong": "/co-phieu/nganh/dau-khi",
}

LEGACY_LEVEL2_REDIRECTS: dict[str, str] = {
    # Bất động sản (nay là ngành cấp 2 trong nhóm Tài chính)
    "quan-ly-va-phat-trien-bat-dong-san": "/co-phieu/nganh/tai-chinh/bat-dong-san",
    # Y tế
    "duoc-pham": "/co-phieu/nganh/y-te/y-te",
    "thiet-bi-vat-tu-y-te": "/co-phieu/nganh/y-te/y-te",
    "dich-vu-cham-soc-suc-khoe": "/co-phieu/nganh/y-te/y-te",
    # Công nghệ
    "phan-mem-va-dich-vu-cntt": "/co-phieu/nganh/cong-nghe/cong-nghe-thong-tin",
    "truyen-thong-mang": "/co-phieu/nganh/cong-nghe/cong-nghe-thong-tin",
    "may-tinh-dien-thoai-dien-tu-gia-dung": "/co-phieu/nganh/cong-nghe/cong-nghe-thong-tin",
    "thiet-bi-van-phong": "/co-phieu/nganh/cong-nghe/cong-nghe-thong-tin",
    "chat-ban-dan-thiet-bi-ban-dan": "/co-phieu/nganh/cong-nghe/cong-nghe-thong-tin",
    "dich-vu-vien-thong": "/co-phieu/nganh/vien-thong/vien-thong",
    # Công nghiệp
    "xay-dung": "/co-phieu/nganh/cong-nghiep/xay-dung-va-vat-lieu",
    "xay-dung-va-vat-lieu-xay-dung-dan-dung": "/co-phieu/nganh/cong-nghiep/xay-dung-va-vat-lieu",
    "vat-lieu-xay-dung": "/co-phieu/nganh/cong-nghiep/xay-dung-va-vat-lieu",
    "dich-vu-cong-nghiep-va-thuong-mai": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "may-moc-thiet-bi-nang-va-dong-tau": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "co-so-ha-tang-giao-thong-van-tai": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "van-chuyen-hang-hoa-va-giao-nhan": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "ban-buon-hang-cong-nghiep-tong-hop": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "thiet-bi-phu-tung-dien-tu": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "hang-khong-vu-tru-va-quoc-phong": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    "hop-dung-va-bao-bi": "/co-phieu/nganh/cong-nghiep/hang-va-dich-vu-cong-nghiep",
    # Hàng tiêu dùng
    "thuc-pham-va-thuoc-la": "/co-phieu/nganh/hang-tieu-dung/thuc-pham-va-do-uong",
    "do-uong": "/co-phieu/nganh/hang-tieu-dung/thuc-pham-va-do-uong",
    "det-may": "/co-phieu/nganh/hang-tieu-dung/hang-ca-nhan-va-gia-dung",
    "hang-gia-dung": "/co-phieu/nganh/hang-tieu-dung/hang-ca-nhan-va-gia-dung",
    "san-pham-giai-tri": "/co-phieu/nganh/hang-tieu-dung/hang-ca-nhan-va-gia-dung",
    "tap-doan-da-nganh-hang-tieu-dung": "/co-phieu/nganh/hang-tieu-dung/hang-ca-nhan-va-gia-dung",
    "san-pham-dich-vu-ca-nhan-gia-dung": "/co-phieu/nganh/hang-tieu-dung/hang-ca-nhan-va-gia-dung",
    "o-to-va-phu-tung-o-to": "/co-phieu/nganh/hang-tieu-dung/o-to-va-phu-tung",
    # Dịch vụ tiêu dùng
    "ban-le-chuyen-dung": "/co-phieu/nganh/dich-vu-tieu-dung/ban-le",
    "ban-le-tong-hop": "/co-phieu/nganh/dich-vu-tieu-dung/ban-le",
    "ban-le-thuc-pham-va-thuoc": "/co-phieu/nganh/dich-vu-tieu-dung/ban-le",
    "truyen-thong-va-xuat-ban": "/co-phieu/nganh/dich-vu-tieu-dung/truyen-thong",
    "khach-san-va-giai-tri": "/co-phieu/nganh/dich-vu-tieu-dung/du-lich-va-giai-tri",
    "van-chuyen-hanh-khach": "/co-phieu/nganh/dich-vu-tieu-dung/du-lich-va-giai-tri",
    # Dầu khí
    "dau-va-khi-dot": "/co-phieu/nganh/dau-khi/dau-khi",
    "dich-vu-va-thiet-bi-dau-khi": "/co-phieu/nganh/dau-khi/dau-khi",
    # Nguyên vật liệu
    "kim-loai-va-khai-khoang": "/co-phieu/nganh/nguyen-vat-lieu/tai-nguyen-co-ban",
    "than": "/co-phieu/nganh/nguyen-vat-lieu/tai-nguyen-co-ban",
    "giay-va-lam-san": "/co-phieu/nganh/nguyen-vat-lieu/tai-nguyen-co-ban",
    # Tài chính
    "chung-khoan-va-ngan-hang-dau-tu": "/co-phieu/nganh/tai-chinh/dich-vu-tai-chinh",
    "cong-ty-dau-tu": "/co-phieu/nganh/tai-chinh/dich-vu-tai-chinh",
    "tai-chinh-ngan-hang": "/co-phieu/nganh/tai-chinh/ngan-hang",
    # Tiện ích
    "nuoc-tien-ich-lien-quan": "/co-phieu/nganh/tien-ich/tien-ich-cong-dong",
    "tien-ich-dien-va-san-xuat-dien": "/co-phieu/nganh/tien-ich/tien-ich-cong-dong",
    "tien-ich-khi-tu-nhien": "/co-phieu/nganh/tien-ich/tien-ich-cong-dong",
    "da-tien-ich": "/co-phieu/nganh/tien-ich/tien-ich-cong-dong",
    "nang-luong-tai-tao": "/co-phieu/nganh/tien-ich/tien-ich-cong-dong",
}


def legacy_redirect_path(sub1: str, sub2: str = "", symbol: str = "") -> str:
    """Đường dẫn 301 cho URL ngành cũ ('' nếu là URL ngành hiện hành)."""
    s1 = str(sub1 or "").strip().lower()
    s2 = str(sub2 or "").strip().lower()
    suffix = f"/{str(symbol).strip().upper()}" if str(symbol or "").strip() else ""
    if s2 and s2 in LEGACY_LEVEL2_REDIRECTS and s2 not in ICB_LEVEL2_SLUGS:
        return LEGACY_LEVEL2_REDIRECTS[s2] + suffix
    if s1 in LEGACY_LEVEL1_REDIRECTS and s1 not in ICB_LEVEL1_SLUGS:
        return LEGACY_LEVEL1_REDIRECTS[s1] + suffix
    # Slug ngành cấp 2 cũ bị dùng như cấp 1 (link cũ dạng /co-phieu/nganh/{ngành}).
    if s1 in LEGACY_LEVEL2_REDIRECTS and s1 not in ICB_LEVEL2_SLUGS:
        return LEGACY_LEVEL2_REDIRECTS[s1] + suffix
    return ""


def canonical_level1_path(sub1: str, symbol: str = "") -> str:
    """URL chuẩn khi slug NGÀNH (cấp 2) bị dùng ở vị trí NHÓM NGÀNH (cấp 1).

    VD /co-phieu/nganh/ngan-hang → /co-phieu/nganh/tai-chinh/ngan-hang.
    Trả '' nếu slug đã là nhóm cấp 1 (hoặc không thuộc taxonomy).
    """
    s1 = str(sub1 or "").strip().lower()
    if not s1 or s1 in ICB_LEVEL1_SLUGS:
        return ""
    parent = ICB_LEVEL2_PARENT.get(s1)
    if not parent:
        return ""
    suffix = f"/{str(symbol).strip().upper()}" if str(symbol or "").strip() else ""
    return f"/co-phieu/nganh/{parent}/{s1}{suffix}"
