from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr

from app.models import NewsStatusEnum, PlanTierEnum, RoleEnum, TicketStatusEnum


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember: bool = False


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    phone: str | None = None
    terms_accepted: bool = False


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UserBase(BaseModel):
    email: EmailStr
    full_name: str


class UserCreate(UserBase):
    password: str
    phone: str | None = None
    role: str | None = None
    plan: PlanTierEnum = PlanTierEnum.free
    plan_expires_at: datetime | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    avatar: str | None = None
    is_active: bool | None = None
    plan: PlanTierEnum | None = None
    plan_expires_at: datetime | None = None


class UserOut(UserBase):
    id: int
    role: RoleEnum
    plan: PlanTierEnum
    is_active: bool
    email_verified: bool = False
    phone: str | None = None
    avatar: str | None = None
    plan_expires_at: datetime | None = None
    last_login: datetime | None = None
    created_at: datetime
    # Quyền dùng tính năng "Tín hiệu" (admin cấp) — frontend dùng để hiện/ẩn menu Tín hiệu.
    signal_access: bool = False

    class Config:
        from_attributes = True


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class GoogleLoginRequest(BaseModel):
    id_token: str
    remember: bool = False


class UserDeleteRequestCreate(BaseModel):
    user_id: int
    reason: str = ""


class UserDeleteRequestOut(BaseModel):
    id: int
    target_user_id: int
    target_name: str = ""
    target_email: str = ""
    requested_by_id: int
    requester_name: str = ""
    requester_email: str = ""
    reason: str = ""
    status: str
    created_at: datetime | None = None
    reviewed_at: datetime | None = None

    class Config:
        from_attributes = True


class AuthResponse(TokenPair):
    user: UserOut


class PortfolioHoldingCreate(BaseModel):
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0


class PortfolioHoldingUpdate(BaseModel):
    quantity: float | None = None
    avg_price: float | None = None
    # Phí giao dịch (%) khi sửa vị thế — tính lại y như khi MUA: giá vốn = giá × (1 + phí/100).
    fee: float | None = None


class PortfolioSell(BaseModel):
    quantity: float = 0.0
    fee: float = 0.0


class PortfolioHoldingOut(BaseModel):
    id: int
    symbol: str
    name: str = ""
    exchange: str = ""
    # quantity/avg_price = GIÁ VỐN ĐIỀU CHỈNH sau cổ tức (dùng để hiển thị + tính lãi/lỗ).
    quantity: float
    avg_price: float
    # Số liệu GỐC người dùng đã nhập trong DB (dùng khi sửa / bán vị thế).
    raw_quantity: float = 0.0
    raw_avg_price: float = 0.0
    # Thông tin điều chỉnh theo sự kiện cổ tức/thưởng (rỗng nếu không có sự kiện nào).
    adjustment: dict[str, Any] = {}
    price: float = 0.0
    change: float = 0.0
    percent_change: float = 0.0
    market_value: float = 0.0
    cost: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


# ---------- Tài sản tự khai báo (Crypto / Tiền gửi NH / Vàng-bạc) ----------
class UserAssetCreate(BaseModel):
    asset_class: str  # crypto | deposit | gold
    label: str = ""
    quantity: float | None = None
    unit_price: float | None = None
    amount: float = 0.0
    fee: float = 0.0          # phí giao dịch (%) — Crypto dùng (mặc định FE 0.10)
    currency: str = "USDT"    # stablecoin USDT | USDC — chỉ Crypto
    note: str | None = None


class UserAssetSell(BaseModel):
    """Bán một phần vị thế Crypto / Vàng-bạc (gộp theo tên).

    - Crypto: `amount` = số USDT/USDC nhận về (sau phí), `unit_price` = giá bán 1 coin
      → số coin bán được suy ra từ đó.
    - Vàng/Bạc: `quantity` = số lượng (lượng/chỉ) muốn bán.
    """
    asset_class: str  # crypto | gold
    label: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    fee: float = 0.0
    currency: str = "USDT"
    note: str | None = None


class UserAssetUpdate(BaseModel):
    label: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    note: str | None = None


class CashAmount(BaseModel):
    """Số tiền mặt hiện có (đ) — tương thích cũ (POST /cash = Nạp VND)."""
    amount: float = 0.0


class CashMove(BaseModel):
    """Nạp / Rút tiền mặt theo quỹ: VND (đ) hoặc USDT (USDT/USDC gộp chung, 1:1)."""
    currency: str = "VND"   # VND | USDT
    amount: float = 0.0
    note: str | None = None


class UserAssetOut(BaseModel):
    id: int
    asset_class: str
    label: str = ""
    quantity: float | None = None
    unit_price: float | None = None
    amount: float = 0.0
    currency: str | None = None        # Crypto: USDT | USDC
    vnd_amount: float | None = None    # Crypto: giá trị quy đổi VND (nếu lấy được tỷ giá)
    fx_rate: float | None = None       # tỷ giá USD/VND đã dùng
    fx_as_of: int | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class NewsCreate(BaseModel):
    title: str
    summary: str = ""
    content: str = ""
    category: str = "chung-khoan"
    image_url: str | None = None
    author_name: str | None = None
    image_caption: str | None = None
    tags: str | None = None
    source: str | None = None
    source_url: str | None = None


class NewsUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    category: str | None = None
    image_url: str | None = None
    author_name: str | None = None
    image_caption: str | None = None
    tags: str | None = None
    source: str | None = None
    source_url: str | None = None


class NewsOut(BaseModel):
    id: int
    title: str
    summary: str
    content: str
    category: str
    image_url: str | None = None
    image_caption: str | None = None
    tags: str | None = None
    source: str | None = None
    source_url: str | None = None
    views: int = 0
    slug: str = ""
    status: NewsStatusEnum
    author_name: str | None = None
    created_at: datetime
    # Ngày giờ đăng bài — đúng định dạng file input (VD: 2026-09-12T15:50:01+07:00).
    # Khi bài chưa có published_at, tự suy ra từ created_at (giờ VN) nên không bao giờ None
    # với bài đã tạo; vẫn để Optional cho an toàn.
    published_at: str | None = None
    published_date: str | None = None
    published_time: str | None = None
    published_at_raw: str | None = None

    class Config:
        from_attributes = True


class UserSettingsOut(BaseModel):
    notify_reports: bool = True
    notify_news: bool = True
    theme: str = "light"


class UserSettingsUpdate(BaseModel):
    notify_reports: bool | None = None
    notify_news: bool | None = None
    theme: str | None = None


class TicketCreate(BaseModel):
    subject: str
    message: str


class TicketReplyCreate(BaseModel):
    message: str


class TicketOut(BaseModel):
    id: int
    subject: str
    message: str
    user_id: int
    assignee_id: int | None
    status: TicketStatusEnum
    created_at: datetime

    class Config:
        from_attributes = True


class WatchlistCreate(BaseModel):
    symbol: str


class WatchlistOut(BaseModel):
    symbol: str
    created_at: datetime

    class Config:
        from_attributes = True


class CryptoWatchlistCreate(BaseModel):
    symbol: str


class CryptoWatchlistOut(BaseModel):
    symbol: str
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationItem(BaseModel):
    symbol: str
    favorited_at: datetime
    news: NewsOut


class NotificationReadCreate(BaseModel):
    news_id: int


class NotificationsOut(BaseModel):
    favorites: list[WatchlistOut]
    items: list[NotificationItem]


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    body: str = ""
    symbol: str | None = None
    link: str | None = None
    is_read: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread: int


class NotificationReadAllCreate(BaseModel):
    type: str | None = None  # "stock" | "news" | "system" | null = đọc tất cả


class SystemAnnouncementCreate(BaseModel):
    title: str
    body: str = ""
    link: str | None = None


class SystemAnnouncementOut(BaseModel):
    id: int
    title: str
    body: str = ""
    link: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class FAQCreate(BaseModel):
    question: str
    answer: str
    category: str = ""
    is_visible: bool = True


class FAQOut(FAQCreate):
    id: int

    class Config:
        from_attributes = True


class ContactMessageCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: str | None = None
    subject: str = ""
    message: str


class ContactMessageOut(BaseModel):
    id: int
    full_name: str
    email: str
    phone: str | None = None
    subject: str
    message: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class FeedbackCreate(BaseModel):
    type: str = "news"  # news | market
    content: str
    user_name: str | None = None
    user_email: str | None = None


class FeedbackOut(BaseModel):
    id: int
    type: str
    content: str
    user_name: str | None = None
    user_email: str | None = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PlanCreate(BaseModel):
    name: str
    tier: PlanTierEnum
    price: int = 0


class PlanOut(BaseModel):
    id: int
    name: str
    tier: PlanTierEnum
    price: int
    is_active: bool

    class Config:
        from_attributes = True


class FeatureCreate(BaseModel):
    key: str
    name: str
    description: str = ""
    access_level: PlanTierEnum = PlanTierEnum.free
    enabled: bool = True


class FeatureOut(FeatureCreate):
    id: int

    class Config:
        from_attributes = True


class AdminStockSymbolOut(BaseModel):
    """Mã cổ phiếu hiển thị trên trang quản trị — toàn bộ (gốc + tự cập nhật)."""

    symbol: str
    name: str
    exchange: str
    sector: str = ""
    sectorName: str = ""
    industry: str = ""
    industryName: str = ""
    is_custom: bool = False
    created_at: datetime | None = None
