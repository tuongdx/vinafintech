# -*- coding: utf-8 -*-
"""Seed dữ liệu nền tảng cho VinaFintech (chạy trên máy local, idempotent).

Seed các bảng: plans, features, faqs, settings, users (super admin + test),
news (bài viết demo tiếng Việt).

Chạy:
    python scripts/seed_data.py

Lưu ý:
  - An toàn chạy lại nhiều lần (bỏ qua bản ghi đã tồn tại).
  - Mật khẩu admin/test mặc định chỉ dùng cho môi trường DEV, nên đổi sau khi login.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.db import SessionLocal
from app.models import (
    FAQ,
    Feature,
    News,
    NewsStatusEnum,
    Plan,
    PlanTierEnum,
    RoleEnum,
    Setting,
    User,
)

# ============================================================
# Dữ liệu
# ============================================================
PLANS = [
    {"name": "Miễn phí", "tier": PlanTierEnum.free, "price": 0},
    {"name": "Cơ bản", "tier": PlanTierEnum.basic, "price": 99000},
    {"name": "Cao cấp", "tier": PlanTierEnum.premium, "price": 199000},
    {"name": "Pro", "tier": PlanTierEnum.pro, "price": 399000},
]

# (key, name, description, access_level) — access_level = gói tối thiểu để dùng
FEATURES = [
    ("view_data", "Xem dữ liệu cơ bản", "Giá, khối lượng, chỉ số cơ bản", PlanTierEnum.free),
    ("watchlist", "Danh sách theo dõi", "Theo dõi cổ phiếu yêu thích", PlanTierEnum.free),
    ("price_alert", "Cảnh báo giá", "Nhận thông báo khi giá đạt ngưỡng", PlanTierEnum.free),
    ("tech_analysis", "Phân tích kỹ thuật", "Biểu đồ, chỉ báo kỹ thuật", PlanTierEnum.basic),
    ("weekly_report", "Báo cáo hàng tuần", "Tổng hợp thị trường hàng tuần", PlanTierEnum.basic),
    ("signal", "Tín hiệu giao dịch", "Gợi ý mua/bán dựa trên thuật toán", PlanTierEnum.basic),
    ("realtime_data", "Dữ liệu real-time", "Cập nhật giá theo thời gian thực", PlanTierEnum.premium),
    ("deep_analysis", "Phân tích chuyên sâu", "Phân tích tài chính, P/E, P/B, ROE", PlanTierEnum.premium),
    ("comparison", "Công cụ so sánh", "So sánh cổ phiếu với nhau", PlanTierEnum.premium),
    ("priority_support", "Hỗ trợ ưu tiên", "Phản hồi nhanh chóng từ đội ngũ", PlanTierEnum.premium),
    ("api_access", "API truy cập", "Truy cập dữ liệu qua API", PlanTierEnum.pro),
    ("historical_data", "Dữ liệu lịch sử", "Truy cập dữ liệu 10 năm", PlanTierEnum.pro),
    ("ai_analysis", "Phân tích AI", "Dự đoán xu hướng bằng AI", PlanTierEnum.pro),
    ("custom_report", "Báo cáo tùy chỉnh", "Tạo báo cáo theo yêu cầu", PlanTierEnum.pro),
    ("support_24h", "Hỗ trợ 24/7", "Hỗ trợ qua chat và email 24/7", PlanTierEnum.pro),
    # Feature backend VinaF AI (ai.py) — Phase 1 mở cho mọi user
    ("ai_chat", "VinaF AI Chat", "Trò chuyện, phân tích thị trường bằng AI", PlanTierEnum.free),
]

# (question, answer, category)
FAQS = [
    ("VinaFintech là gì?", "VinaFintech là nền tảng phân tích cổ phiếu và quản trị tài sản cho thị trường Việt Nam: dữ liệu thị trường và cổ phiếu, bộ lọc và khám phá cơ hội, lãi suất ngân hàng, giá vàng – bạc, crypto, các công cụ tính toán, tin tức và trợ lý AI VinaF AI. VinaFintech không khuyến nghị mua, bán hay nắm giữ bất kỳ mã chứng khoán nào.", "getting-started"),
    ("Tôi có cần đăng ký tài khoản để sử dụng VinaFintech không?", "Không bắt buộc. Bạn có thể xem Thị trường, Cổ phiếu, Khám phá, Lãi ngân hàng, Giá Vàng/Bạc, Crypto, Tiện ích và Tin tức khi chưa đăng nhập. Cần tài khoản (miễn phí) khi muốn dùng Quản lý tài sản, VinaF AI và nhận thông báo – cảnh báo.", "getting-started"),
    ("VinaFintech có những khu vực chính nào?", "Menu chính gồm: Thị trường (chỉ số, dòng tiền, tin nổi bật), Cổ phiếu (theo nhóm ngành, bộ lọc, chi tiết mã và biểu đồ kỹ thuật), Khám phá (cơ hội nổi bật, bản đồ cơ hội, chiến lược, xếp hạng), Lãi ngân hàng, Giá Vàng/Bạc, Crypto, Quản lý tài sản (danh mục, cảnh báo AI, phân tích tài sản), Tiện ích (giá điều chỉnh, lãi tiền gửi, tỷ giá) và VinaF AI. Trang Tin tức và ô tìm kiếm cổ phiếu luôn có ở thanh trên cùng.", "getting-started"),
    ("Làm thế nào để tạo tài khoản?", "Bấm icon tài khoản ở góc phải thanh trên cùng → chọn Đăng ký ngay, điền Họ tên, Email, Số điện thoại (không bắt buộc) và mật khẩu tối thiểu 6 ký tự. Sau khi đồng ý điều khoản, VinaFintech gửi email xác thực — mở email và bấm liên kết để kích hoạt tài khoản; nếu chưa nhận được, bấm Gửi lại email xác thực.", "account"),
    ("Tôi quên mật khẩu, làm sao để lấy lại?", "Tại trang Đăng nhập, chọn Quên mật khẩu?, nhập email đã đăng ký. VinaFintech sẽ gửi liên kết đặt lại mật khẩu về email của bạn.", "account"),
    ("Làm sao để đổi mật khẩu?", "Mở menu tài khoản (bấm tên hoặc ảnh đại diện ở góc phải) → Thông tin tài khoản → bấm Đổi mật khẩu, nhập mật khẩu hiện tại và mật khẩu mới rồi lưu lại.", "account"),
    ("VinaFintech có bảo mật thông tin tài khoản của tôi không?", "Có. Mật khẩu được mã hóa một chiều (bcrypt) và không hiển thị lại; phiên đăng nhập dùng JWT có thời hạn và tự động làm mới; email phải được xác thực trước khi sử dụng tài khoản. Khi triển khai chính thức, toàn bộ kết nối đi qua HTTPS.", "account"),
    ("Tôi có thể xóa tài khoản không?", "Có. Trong Thông tin tài khoản, bạn gửi yêu cầu xóa tài khoản. Yêu cầu sẽ được quản trị viên phê duyệt trước khi dữ liệu bị xóa.", "account"),
    ("VinaF AI là gì và dùng được những gì?", "VinaF AI là trợ lý AI của VinaFintech: hỏi đáp về cổ phiếu, báo cáo tài chính, phân tích biểu đồ, tìm cổ phiếu theo tiêu chí, tình hình thị trường và cả danh mục bạn đang nắm giữ/theo dõi. Câu trả lời có thể kèm bảng số liệu, biểu đồ và xuất ra PDF. Tài khoản Miễn phí được 1 câu hỏi và 2 cuộc trò chuyện mới mỗi tháng; gói Hội viên hỏi không giới hạn.", "features"),
    ("Trung tâm cảnh báo hoạt động như thế nào?", "Trung tâm cảnh báo (trong Quản lý tài sản) dùng AI phân tích các mã cổ phiếu và coin bạn đang nắm giữ hoặc theo dõi, trả về 3 nhóm Cơ hội – Cảnh báo – Xu hướng kèm mức độ mạnh, độ tin cậy và lý do cụ thể. Bấm Quét để phân tích lại khi danh mục hoặc thị trường thay đổi.", "features"),
    ("Tôi có thể theo dõi danh mục đầu tư của mình trên VinaFintech không?", "Có. Trong Quản lý tài sản, bạn ghi nhận cổ phiếu, crypto, vàng/bạc và tiền gửi ngân hàng; quản lý tiền mặt VND/USDT; theo dõi thêm những mã chưa sở hữu; xem bảng danh mục với lãi/lỗ, vùng hỗ trợ – kháng cự và trạng thái kỹ thuật; xem sự kiện liên quan (cổ tức, chốt quyền, kết quả kinh doanh) và phân tích tài sản tổng thể.", "features"),
    ("Tiện ích của VinaFintech gồm những công cụ gì?", "Ba công cụ trong menu Tiện ích: (1) Giá điều chỉnh cổ phiếu sau cổ tức, chia tách hoặc phát hành quyền mua; (2) Lợi nhuận tiền gửi — tính lãi đơn hoặc lãi kép, có thể gửi thêm định kỳ mỗi tháng; (3) Tỷ giá ngoại tệ — xem tỷ giá và hoán đổi giữa VND với các ngoại tệ, có tùy chọn trừ phí.", "features"),
    ("Dữ liệu thị trường trên VinaFintech lấy từ đâu?", "VinaFintech tổng hợp dữ liệu từ các nguồn công khai: giá và chỉ số chứng khoán Việt Nam, bảng giá vàng trong nước và thế giới, tỷ giá ngoại tệ, lãi suất huy động của các ngân hàng và dữ liệu crypto từ các sàn lớn. Dữ liệu được lưu đệm để tải nhanh và tự làm mới theo từng khu vực; một số nguồn có thể chậm hơn thực tế vài giây đến vài phút.", "features"),
    ("Các gói dịch vụ của VinaFintech gồm những gì?", "VinaFintech có 2 hình thức sử dụng:\n\n• Miễn phí (mặc định khi đăng ký): dùng đầy đủ dữ liệu và công cụ của website — Thị trường, Cổ phiếu (biểu đồ kỹ thuật, chỉ báo), Khám phá, Lãi ngân hàng, Giá Vàng/Bạc, Crypto, Tiện ích, Tin tức và Quản lý tài sản (danh mục, tiền mặt, theo dõi, phân tích tài sản). VinaF AI có hạn mức 1 câu hỏi + 2 cuộc trò chuyện mới mỗi tháng.\n\n• Hội viên - Gói tháng: 99.000đ/tháng, gia hạn mỗi tháng.\n• Hội viên - Gói năm: 999.000đ/năm, thanh toán 1 lần cho 12 tháng (tiết kiệm hơn gói tháng).\n\nMở gói Hội viên, bạn được: VinaF AI không giới hạn câu hỏi, Trung tâm cảnh báo đầy đủ (cảnh báo cổ phiếu và tín hiệu crypto do AI phân tích, có nút Quét), cùng toàn bộ nhóm chiến lược và bảng xếp hạng nâng cao ở trang Khám phá.\n\nĐăng ký Hội viên tại icon tài khoản (góc phải trang chủ) → Hội viên, hoặc bấm nút \"Nâng cấp Hội viên\" ở những khu vực đang bị khoá.", "billing"),
    ("Tôi có thể nâng cấp/hạ cấp gói dịch vụ không?", "Có. Mở menu tài khoản → Hội viên để xem gói hiện tại, ngày hết hạn và chọn gói mới: Gói tháng 99.000đ/tháng hoặc Gói năm 999.000đ/12 tháng (gói năm rẻ hơn nếu tính theo tháng). Nếu không gia hạn, tài khoản tự trở về hình thức Miễn phí khi gói hết hạn.", "billing"),
    ("Khi gói Hội viên hết hạn thì điều gì xảy ra?", "Tài khoản trở về hình thức Miễn phí: VinaF AI áp dụng lại hạn mức theo tháng và các khu vực dành riêng cho Hội viên sẽ bị khoá (bạn vẫn thấy một phần nội dung dưới dạng mờ để tham khảo). Danh mục, danh sách theo dõi và lịch sử trò chuyện của bạn vẫn được giữ nguyên.", "billing"),
    ("Tôi đăng ký hoặc gia hạn gói Hội viên ở đâu?", "Mở menu tài khoản → Hội viên, chọn Gói tháng hoặc Gói năm rồi xác nhận. Gói được kích hoạt ngay và thông tin gói (ngày bắt đầu, ngày hết hạn) hiển thị tại đây. Bạn cũng có thể bấm nút “Nâng cấp Hội viên” ở các khu vực đang bị khoá.", "billing"),
]

SETTINGS = [
    ("site_name", "VinaFintech"),
    ("contact_email", "support@tunix.vn"),
    ("free_mode", "true"),
    ("default_theme", "light"),
    ("hotline", "1900 0000"),
]

# Super admin mặc định (DEV) — nên đổi mật khẩu sau khi đăng nhập
ADMIN_EMAIL = "admin@tunix.vn"
ADMIN_PASSWORD = "Taniku@1710"
ADMIN_NAME = "Quản trị viên"

# (email, full_name, password)
TEST_USERS = [
    ("user.demo@tunix.vn", "Nguyễn Văn An", "Test@123456"),
    ("user.vip@tunix.vn", "Trần Thị Bích", "Test@123456"),
]

# Tin tức demo — (title, summary, content, category, tags)
DEMO_NEWS = [
    (
        "VN-Index tăng hơn 18 điểm, vượt mốc 1.780 nhờ nhóm ngân hàng",
        "Thị trường chứng khoán Việt Nam khởi sắc khi dòng tiền đổ mạnh vào nhóm cổ phiếu ngân hàng và bất động sản, đưa VN-Index chạm mức cao nhất trong 3 tháng.",
        "<p>Phiên giao dịch hôm nay chứng kiến sắc xanh lan tỏa trên toàn thị trường khi VN-Index tăng 18,33 điểm (+1,04%) lên 1.786,45 điểm. Đây là phiên tăng mạnh nhất trong vòng 3 tháng qua.</p><p>Theo các chuyên gia, dòng tiền tập trung vào nhóm ngân hàng (VCB, BID, CTG) và bất động sản. Khối ngoại quay lại mua ròng sau chuỗi ngày bán ròng.</p>",
        "chung-khoan",
        "VN-Index, chứng khoán, ngân hàng",
    ),
    (
        "Lãi suất huy động tiếp tục đi ngang, tín dụng tăng trưởng tích cực",
        "Mặt bằng lãi suất huy động giữ ổn định trong tháng qua trong khi tăng trưởng tín dụng cải thiện nhờ nhu cầu vốn của doanh nghiệp phục hồi.",
        "<p>Mặt bằng lãi suất huy động của các ngân hàng thương mại tiếp tục đi ngang ở mức 4,5-5%/năm cho kỳ hạn 12 tháng, theo số liệu cập nhật của Ngân hàng Nhà nước.</p><p>Tăng trưởng tín dụng toàn hệ thống cải thiện rõ rệt trong quý gần nhất, cho thấy nhu cầu vốn của nền kinh tế đang phục hồi.</p>",
        "ngan-hang",
        "lãi suất, tín dụng, ngân hàng",
    ),
    (
        "Cổ phiếu bất động sản hút dòng tiền sau chuỗi tin tích cực về pháp lý",
        "Nhóm cổ phiếu bất động sản giao dịch sôi động khi thị trường kỳ vọng các dự án lớn được tháo gỡ vướng mắc pháp lý trong nửa cuối năm.",
        "<p>Nhóm cổ phiếu bất động sản ghi nhận thanh khoản tăng vọt trong phiên hôm nay, tập trung ở các mã như VHM, VRE và NVL.</p><p>Giới phân tích cho rằng việc đẩy nhanh tiến độ pháp lý cho các dự án bất động sản lớn sẽ là chất xúc tác chính cho ngành trong các quý tới.</p>",
        "bat-dong-san",
        "bất động sản, cổ phiếu, pháp lý",
    ),
    (
        "FPT công bố kế hoạch mở rộng mảng chuyển đổi số tại thị trường Nhật Bản",
        "FPT đặt mục tiêu tăng trưởng hai chữ số ở mảng xuất khẩu phần mềm, trọng tâm là thị trường Nhật Bản và châu Âu.",
        "<p>Tập đoàn FPT vừa công bố chiến lược mở rộng mảng chuyển đổi số giai đoạn mới, đặt trọng tâm vào thị trường Nhật Bản — một trong những thị trường xuất khẩu phần mềm lớn nhất của doanh nghiệp.</p><p>Đại diện FPT cho biết nhu cầu chuyển đổi số của các doanh nghiệp Nhật Bản đang tăng mạnh, tạo dư địa tăng trưởng lớn cho mảng dịch vụ này.</p>",
        "doanh-nghiep",
        "FPT, chuyển đổi số, Nhật Bản",
    ),
    (
        "Dòng tiền thông minh: Khối ngoại mua ròng trở lại ở nhóm cổ phiếu vốn hóa lớn",
        "Khối ngoại quay lại mua ròng hơn 300 tỷ đồng, tập trung vào nhóm cổ phiếu vốn hóa lớn có câu chuyện tăng trưởng rõ ràng.",
        "<p>Sau một tuần bán ròng, khối ngoại đã quay lại mua ròng hơn 300 tỷ đồng trên sàn HOSE, tập trung vào nhóm cổ phiếu vốn hóa lớn.</p><p>Các chuyên gia nhận định dòng tiền ngoại sẽ tiếp tục hướng đến những doanh nghiệp có nền tảng tài chính tốt và câu chuyện tăng trưởng rõ ràng.</p>",
        "dong-tien-thong-minh",
        "khối ngoại, dòng tiền, mua ròng",
    ),
    (
        "Chứng khoán Mỹ lập đỉnh mới, nhà đầu tư chờ dữ liệu lạm phát",
        "Các chỉ số chính của Phố Wall tăng điểm nhờ kỳ vọng Cục Dự trữ Liên bang Mỹ (Fed) sớm nới lỏng chính sách tiền tệ.",
        "<p>Chứng khoán Mỹ tiếp tục leo lên vùng đỉnh lịch sử khi nhà đầu tư đặt cược Fed sẽ sớm hạ lãi suất trong các cuộc họp tới.</p><p>Trong tuần này, thị trường sẽ chú ý đến loạt dữ liệu lạm phát và báo cáo việc làm mới nhất để định hướng cho xu hướng tiếp theo.</p>",
        "tai-chinh-quoc-te",
        "chứng khoán Mỹ, Fed, lãi suất",
    ),
    (
        "Công nghệ AI thúc đẩy mảng dịch vụ của các công ty chứng khoán Việt Nam",
        "Các công ty chứng khoán đầu tư mạnh vào ứng dụng AI để cá nhân hóa trải nghiệm khách hàng và hỗ trợ quyết định đầu tư.",
        "<p>Xu hướng ứng dụng trí tuệ nhân tạo (AI) đang lan rộng trong ngành chứng khoán Việt Nam, từ chatbot hỗ trợ khách hàng đến hệ thống gợi ý cổ phiếu thông minh.</p><p>Đây được xem là bước đi chiến lược giúp các công ty chứng khoán tăng trải nghiệm người dùng và tối ưu chi phí vận hành.</p>",
        "cong-nghe",
        "AI, chứng khoán, công nghệ",
    ),
    (
        "Hướng dẫn đọc bảng giá chứng khoán cho nhà đầu tư mới",
        "Bài viết giải thích các thuật ngữ quan trọng trên bảng giá: giá trần, giá sàn, khối lượng khớp, dư mua, dư bán và cách đọc nhanh biến động thị trường.",
        "<p>Bảng giá chứng khoán có thể gây bối rối cho nhà đầu tư mới. Bài viết này sẽ giúp bạn hiểu các thuật ngữ cơ bản: giá trần, giá sàn, giá tham chiếu, khối lượng khớp lệnh, dư mua/dư bán.</p><p>Nắm rõ cách đọc bảng giá giúp nhà đầu tư đưa ra quyết định nhanh chóng và chính xác hơn trong phiên giao dịch.</p>",
        "tin-tuc",
        "hướng dẫn, bảng giá, nhà đầu tư mới",
    ),
]


# ============================================================
# Hàm seed
# ============================================================
def seed_plans(db: Session) -> int:
    added = 0
    for p in PLANS:
        exists = db.query(Plan).filter(Plan.tier == p["tier"]).first()
        if exists:
            continue
        db.add(Plan(name=p["name"], tier=p["tier"], price=p["price"]))
        added += 1
    db.commit()
    return added


def seed_features(db: Session) -> int:
    added = 0
    for key, name, desc, level in FEATURES:
        exists = db.query(Feature).filter(Feature.key == key).first()
        if exists:
            continue
        db.add(Feature(key=key, name=name, description=desc, access_level=level, enabled=True))
        added += 1
    db.commit()
    return added


def seed_faqs(db: Session) -> int:
    added = 0
    for question, answer, category in FAQS:
        exists = db.query(FAQ).filter(FAQ.question == question).first()
        if exists:
            continue
        db.add(FAQ(question=question, answer=answer, category=category, is_visible=True))
        added += 1
    db.commit()
    return added


def seed_settings(db: Session) -> int:
    added = 0
    for key, value in SETTINGS:
        exists = db.query(Setting).filter(Setting.key == key).first()
        if exists:
            continue
        db.add(Setting(key=key, value=value))
        added += 1
    db.commit()
    return added


def seed_superadmin(db: Session) -> int:
    existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()
    if existing:
        if existing.role != RoleEnum.super_admin:
            existing.role = RoleEnum.super_admin
            existing.is_active = True
            existing.email_verified = True
            db.commit()
            print(f"  ℹ️ Đã nâng quyền Super Admin cho {ADMIN_EMAIL}")
            return 1
        print(f"  ℹ️ {ADMIN_EMAIL} đã là Super Admin.")
        return 0

    db.add(
        User(
            email=ADMIN_EMAIL,
            full_name=ADMIN_NAME,
            hashed_password=get_password_hash(ADMIN_PASSWORD),
            role=RoleEnum.super_admin,
            plan=PlanTierEnum.pro,
            is_active=True,
            terms_accepted=True,
            email_verified=True,
        )
    )
    db.commit()
    print(f"  ✅ Đã tạo Super Admin: {ADMIN_EMAIL}")
    return 1


def seed_test_users(db: Session) -> int:
    added = 0
    for email, name, password in TEST_USERS:
        exists = db.query(User).filter(User.email == email).first()
        if exists:
            continue
        db.add(
            User(
                email=email,
                full_name=name,
                hashed_password=get_password_hash(password),
                role=RoleEnum.member,
                plan=PlanTierEnum.basic,
                is_active=True,
                terms_accepted=True,
                email_verified=True,
            )
        )
        added += 1
    db.commit()
    return added


def seed_news(db: Session) -> int:
    added = 0
    for title, summary, content, category, tags in DEMO_NEWS:
        exists = db.query(News).filter(News.title == title).first()
        if exists:
            continue
        db.add(
            News(
                title=title,
                summary=summary,
                content=content,
                category=category,
                tags=tags,
                source="Ban biên tập VinaFintech",
                author_name="Ban biên tập VinaFintech",
                status=NewsStatusEnum.published,
            )
        )
        added += 1
    db.commit()
    return added


def main() -> None:
    db: Session = SessionLocal()
    try:
        print("=== Seed dữ liệu VinaFintech ===\n")

        n = seed_plans(db)
        print(f"[plans] Thêm mới: {n} (tổng: {db.query(Plan).count()})")

        n = seed_features(db)
        print(f"[features] Thêm mới: {n} (tổng: {db.query(Feature).count()})")

        n = seed_faqs(db)
        print(f"[faqs] Thêm mới: {n} (tổng: {db.query(FAQ).count()})")

        n = seed_settings(db)
        print(f"[settings] Thêm mới: {n} (tổng: {db.query(Setting).count()})")

        n = seed_superadmin(db)
        print(f"[users/superadmin] → {ADMIN_EMAIL} (tổng: {db.query(User).count()})")

        n = seed_test_users(db)
        print(f"[users/test] Thêm mới: {n} (tổng: {db.query(User).count()})")

        n = seed_news(db)
        print(f"[news] Thêm mới: {n} (tổng: {db.query(News).count()})")

        print("\n=== Hoàn tất ===")
        print(f"Super Admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}  (DEV — nên đổi sau khi đăng nhập)")
        for email, name, pwd in TEST_USERS:
            print(f"User test: {email} / {pwd}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
