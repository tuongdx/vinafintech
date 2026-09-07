from datetime import datetime
import enum

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RoleEnum(str, enum.Enum):
    super_admin = "super-admin"
    admin = "admin"
    content_manager = "content-manager"
    editor = "editor"
    support = "support"
    member = "member"


class NewsStatusEnum(str, enum.Enum):
    pending_approval = "pending-approval"
    approved = "approved"
    published = "published"
    rejected = "rejected"


class TicketStatusEnum(str, enum.Enum):
    open = "open"
    assigned = "assigned"
    resolved = "resolved"
    closed = "closed"


class PlanTierEnum(str, enum.Enum):
    free = "free"
    basic = "basic"  # legacy — coi là trả phí
    premium = "premium"  # legacy — coi là trả phí
    pro = "pro"  # legacy — coi là trả phí
    monthly = "monthly"
    yearly = "yearly"


class UserDeleteRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum), default=RoleEnum.member, nullable=False)
    plan: Mapped[PlanTierEnum] = mapped_column(Enum(PlanTierEnum), default=PlanTierEnum.free, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # avatar lưu data-URI base64 có thể rất lớn (ảnh nén vào DB) → dùng Text thay vì String(500)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Thông tin đăng ký hội viên (hiển thị ở mục "Hội viên" trong tài khoản)
    membership_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    membership_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    terms_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_news: Mapped[bool] = mapped_column(Boolean, default=True)
    pref_theme: Mapped[str] = mapped_column(String(10), default="light")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    social_accounts: Mapped[list["SocialAccount"]] = relationship(
        "SocialAccount", back_populates="user", cascade="all, delete-orphan"
    )


class SocialAccount(Base):
    """Liên kết tài khoản mạng xã hội (Google) với user."""

    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_social_provider_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # google
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="social_accounts")


class UserDeleteRequest(Base):
    """Yêu cầu xóa tài khoản do admin gửi, chờ super-admin phê duyệt."""

    __tablename__ = "user_delete_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[UserDeleteRequestStatus] = mapped_column(
        Enum(UserDeleteRequestStatus), default=UserDeleteRequestStatus.pending, nullable=False
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RevokedToken(Base):
    """JWT đã bị thu hồi (logout) — check ở middleware."""

    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PasswordResetToken(Base):
    """Token đặt lại mật khẩu — ngẫu nhiên, có thời hạn, chỉ dùng 1 lần."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EmailVerificationToken(Base):
    """Token xác thực email — ngẫu nhiên, có thời hạn, chỉ dùng 1 lần.

    Lưu dạng HASH (sha256) thay vì plaintext để không lộ token nếu DB bị đọc.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="tin-tuc")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_caption: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[NewsStatusEnum] = mapped_column(Enum(NewsStatusEnum), default=NewsStatusEnum.pending_approval)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    author: Mapped[User | None] = relationship("User")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[TicketStatusEnum] = mapped_column(Enum(TicketStatusEnum), default=TicketStatusEnum.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SupportReply(Base):
    __tablename__ = "support_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FAQ(Base):
    __tablename__ = "faqs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    question: Mapped[str] = mapped_column(String(255), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="")
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)


class ContactMessage(Base):
    """Tin nhắn gửi từ trang "Liên hệ" (không cần đăng nhập)."""

    __tablename__ = "contact_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    """Góp ý từ người dùng (Tin tức / Thị trường)."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    type: Mapped[str] = mapped_column(String(20), default="news")  # news | market
    content: Mapped[str] = mapped_column(Text, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    tier: Mapped[PlanTierEnum] = mapped_column(Enum(PlanTierEnum), nullable=False)
    price: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AiMemory(Base):
    """Bộ nhớ TuniAI học từ lịch sử trò chuyện (theo user).

    - memory: bản tóm tắt tiếng Việt về sở thích / thông tin người dùng đã nói,
      được model sinh ra để TuniAI "nhớ" qua các lần trò chuyện.
    - recent : JSON list các lượt gần đây ({"role", "content"}) để cập nhật memory.
    """

    __tablename__ = "ai_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True, nullable=False)
    memory: Mapped[str] = mapped_column(Text, default="")
    recent: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AiConversation(Base):
    """Lịch sử hội thoại TuniAI theo tài khoản.

    Đồng bộ lên server để người dùng logout → login lại vẫn giữ được lịch sử
    hỏi đáp của chính tài khoản đó (không phụ thuộc máy/trình duyệt).
    - cid     : id client sinh ra ('c<timestamp>'), dùng làm khóa đồng bộ.
    - messages: JSON list [{"role", "content"}] nội dung hội thoại.
    """

    __tablename__ = "ai_conversations"
    __table_args__ = (UniqueConstraint("user_id", "cid", name="uq_ai_conv_user_cid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    cid: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="")
    messages: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemberQuota(Base):
    """Hạn mức theo tháng cho user MIỄN PHÍ (TuniAI). Reset theo period 'YYYY-MM'."""

    __tablename__ = "member_quota"
    __table_args__ = (UniqueConstraint("user_id", "period", name="uq_member_quota_user_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    ai_questions: Mapped[int] = mapped_column(Integer, default=0)
    ai_new_chats: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BankInterestRate(Base):
    """Lãi suất tiền gửi (Giai đoạn 1 — dữ liệu TỔNG HỢP THAM KHẢO lưu trong DB, KHÔNG realtime).

    Kiến trúc hướng Giai đoạn 2: sau này thay nguồn bằng API ngân hàng / nguồn tự động
    → Backend cập nhật bảng này (không phải xây lại UI/API).
    Mỗi dòng = 1 (ngân hàng, kỳ hạn, hình thức gửi, đối tượng) với một mức lãi suất %/năm.
    """

    __tablename__ = "bank_interest_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bank_code: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)  # 1,3,6,12,18,24
    interest_rate: Mapped[float] = mapped_column(Float, nullable=False)  # %/năm
    channel: Mapped[str] = mapped_column(String(20), default="online")  # online | counter
    customer_type: Mapped[str] = mapped_column(String(60), default="Khách hàng cá nhân")
    min_amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # số tiền tối thiểu (đ)
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    access_level: Mapped[PlanTierEnum] = mapped_column(Enum(PlanTierEnum), default=PlanTierEnum.free)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    level: Mapped[str] = mapped_column(String(20), default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Watchlist(Base):
    """Cổ phiếu người dùng theo dõi (yêu thích) — dùng để gợi ý tin/báo cáo liên quan."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)


class PortfolioHolding(Base):
    """Cổ phiếu người dùng nắm giữ trong danh mục đầu tư (số lượng + giá vốn bình quân).

    Mỗi user tối đa 1 dòng/mã cổ phiếu (uq user+symbol); khi thêm lại → cập nhật hoặc 409.
    """

    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_portfolio_user_symbol"),)


class UserAssetClass(str, enum.Enum):
    """Loại tài sản ngoài cổ phiếu mà người dùng tự khai báo (không có nguồn giá tự động)."""

    crypto = "crypto"    # Danh sách Crypto
    deposit = "deposit"  # Tiền gửi ngân hàng
    gold = "gold"        # Vàng / Bạc


class UserAsset(Base):
    """Tài sản tự khai báo theo khoản (crypto / tiền gửi NH / vàng-bạc).

    Mỗi dòng = 1 khoản đầu tư mà user nhập tay (nhiều dòng/loại cho phép).
    - label: tên khoản (VD 'BTC', 'ACB – kỳ hạn 6 tháng', 'Vàng miếng SJC').
    - quantity / unit_price: (tuỳ chọn) số lượng + đơn giá — chỉ để tham khảo.
    - amount: SỐ TIỀN ĐẦU TƯ (VND) — giá trị chính.
    """

    __tablename__ = "user_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    asset_class: Mapped[UserAssetClass] = mapped_column(Enum(UserAssetClass), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CashTransaction(Base):
    """Sổ ghi TIỀN VÀO / RA của khoản Tiền mặt.

    - kind: sell_stock | sell_gold | mature_deposit | adjust
    - change: dương = TIỀN VÀO (+), âm = TIỀN RA (−)
    - balance_after: số dư Tiền mặt sau giao dịch (đ).
    """

    __tablename__ = "cash_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="adjust")
    change: Mapped[float] = mapped_column(Float, default=0.0)
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotificationRead(Base):
    """Tin/báo cáo người dùng đã xem trong thông báo (để không đếm lại)."""

    __tablename__ = "notification_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id"), nullable=False, index=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "news_id", name="uq_notif_read_user_news"),)


class NotificationType(str, enum.Enum):
    stock = "stock"
    news = "news"
    system = "system"


class Notification(Base):
    """Thông báo cá nhân cho user: cổ phiếu / tin tức / hệ thống (có lịch sử + trạng thái đã đọc)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # ref_key giúp tránh sinh trùng (vd: report:12, news:34, ceiling:TCB:2026-08-13, sys:1)
    ref_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "type", "ref_key", name="uq_notif_user_type_ref"),)


class SystemAnnouncement(Base):
    """Thông báo hệ thống phát đến toàn bộ thành viên (TuniX đổi mới, mở khoá tính năng...)."""

    __tablename__ = "system_announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockSymbol(Base):
    """Mã cổ phiếu do quản trị viên thêm thủ công (VD: cổ phiếu IPO mới)
    để hệ thống nhận dạng và lấy được dữ liệu từ API."""

    __tablename__ = "stock_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    exchange: Mapped[str] = mapped_column(String(20), default="")
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    added_by: Mapped[User | None] = relationship("User")


# ---------- Trung tâm cảnh báo (Alert Center) ----------

class AlertType(str, enum.Enum):
    """Loại cảnh báo — mở rộng được (thêm giá trị enum + handler trong AlertDetectionService)."""
    support_near = "SUPPORT_NEAR"
    resistance_near = "RESISTANCE_NEAR"
    breakout = "BREAKOUT"
    breakdown = "BREAKDOWN"
    price_target = "PRICE_TARGET"
    ma_cross_up = "MA_CROSS_UP"
    ma_cross_down = "MA_CROSS_DOWN"
    ma50_break_up = "MA50_BREAK_UP"
    ma50_break_down = "MA50_BREAK_DOWN"
    ma200_break_up = "MA200_BREAK_UP"
    ma200_break_down = "MA200_BREAK_DOWN"
    rsi_overbought = "RSI_OVERBOUGHT"
    rsi_oversold = "RSI_OVERSOLD"
    macd_bullish = "MACD_BULLISH_CROSS"
    macd_bearish = "MACD_BEARISH_CROSS"
    trend_strengthening = "TREND_STRENGTHENING"
    trend_weakening = "TREND_WEAKENING"


class AlertImportance(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AlertCategory(str, enum.Enum):
    """Nhóm để lọc trong Trung tâm cảnh báo."""
    opportunity = "opportunity"   # Cơ hội
    warning = "warning"           # Cảnh báo
    trend = "trend"               # Xu hướng
    event = "event"               # Sự kiện doanh nghiệp


class Alert(Base):
    """Cảnh báo kỹ thuật/tín hiệu cho từng user (có lịch sử + trạng thái đã đọc).

    ``metadata`` lưu JSON WHY_THIS_ALERT (giải thích lý do tạo cảnh báo).
    ``dedup_key`` = ``{symbol}:{alert_type}:{zone-or-giá trị}`` — dùng để chống spam
    (chỉ tạo lại khi giá rời vùng rồi quay lại HOẶC sau cooldown).
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    alert_type: Mapped[AlertType] = mapped_column(Enum(AlertType), nullable=False, index=True)
    category: Mapped[AlertCategory] = mapped_column(Enum(AlertCategory), nullable=False, index=True)
    importance: Mapped[AlertImportance] = mapped_column(Enum(AlertImportance), default=AlertImportance.medium, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # WHY_THIS_ALERT — JSON string: {current_price, zone, distance_percent, ...}
    alert_metadata: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssetEventType(str, enum.Enum):
    earnings = "EARNINGS"
    dividend = "DIVIDEND"
    ex_dividend_date = "EX_DIVIDEND_DATE"
    stock_split = "STOCK_SPLIT"
    rights_issue = "RIGHTS_ISSUE"
    important_news = "IMPORTANT_NEWS"
    unusual_price_movement = "UNUSUAL_PRICE_MOVEMENT"


class AssetEvent(Base):
    """Sự kiện liên quan đến tài sản (cổ phiếu đang nắm giữ / theo dõi của user):
    cổ tức, ngày chốt quyền, tin quan trọng, biến động giá bất thường...
    Dữ liệu lấy từ nguồn thật (vnstock/dividends + news DB) — không tạo dữ liệu giả.
    """

    __tablename__ = "asset_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    event_type: Mapped[AssetEventType] = mapped_column(Enum(AssetEventType), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    event_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # JSON: {source, dividend_amount, ex_date, ...}
    event_metadata: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
