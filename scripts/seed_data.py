# -*- coding: utf-8 -*-
"""Seed dữ liệu nền tảng cho TuniX (chạy trên máy local, idempotent).

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
    # Feature backend TuniAI (ai.py) — Phase 1 mở cho mọi user
    ("ai_chat", "TuniAI Chat", "Trò chuyện, phân tích thị trường bằng AI", PlanTierEnum.free),
]

# (question, answer, category)
FAQS = [
    ("TuniX là gì?", "TuniX là nền tảng phân tích cổ phiếu toàn diện, cung cấp dữ liệu thị trường chứng khoán Việt Nam theo thời gian thực, biểu đồ phân tích kỹ thuật và công cụ quản lý danh mục đầu tư.", "getting-started"),
    ("Tôi có cần đăng ký tài khoản để sử dụng TuniX không?", "Bạn có thể xem dữ liệu cơ bản mà không cần đăng ký. Tuy nhiên, để sử dụng danh mục đầu tư, theo dõi cổ phiếu yêu thích và TuniAI, bạn cần tạo tài khoản miễn phí.", "getting-started"),
    ("Làm thế nào để tạo tài khoản?", "Vào trang Đăng ký, điền email, mật khẩu và xác nhận. Bạn sẽ nhận email xác thực tài khoản. Sau khi xác thực, bạn có thể đăng nhập và sử dụng dịch vụ.", "account"),
    ("Tôi quên mật khẩu, làm sao để lấy lại?", "Trên trang Đăng nhập, chọn 'Quên mật khẩu', nhập email đã đăng ký. Hệ thống sẽ gửi link đặt lại mật khẩu về email của bạn.", "account"),
    ("Làm sao để đổi mật khẩu?", "Vào mục 'Tài khoản' > 'Đổi mật khẩu', nhập mật khẩu hiện tại và mật khẩu mới, sau đó lưu thay đổi.", "account"),
    ("TuniAI là gì?", "TuniAI là trợ lý phân tích thị trường bằng AI (DeepSeek), có thể tìm kiếm cổ phiếu, tra cứu báo giá, lịch sử giá, báo cáo tài chính và trả lời câu hỏi về thị trường.", "features"),
    ("Dữ liệu thị trường trên TuniX lấy từ đâu?", "TuniX sử dụng dữ liệu từ API công khai của Simplize và vnstock — các nguồn dữ liệu chứng khoán Việt Nam. Dữ liệu được cập nhật và cache để tăng tốc độ truy cập.", "features"),
    ("Các gói dịch vụ của TuniX gồm những gì?", "TuniX có 4 gói: Miễn phí, Cơ bản, Cao cấp và Pro. Gói trả phí mở khóa phân tích kỹ thuật nâng cao, dữ liệu real-time, phân tích chuyên sâu và tính năng AI.", "billing"),
    ("Tôi có thể nâng cấp/hạ cấp gói dịch vụ không?", "Có. Bạn vào mục 'Tài khoản' > 'Gói hội viên' để xem và thay đổi gói. Thay đổi sẽ có hiệu lực theo chu kỳ thanh toán hiện tại.", "billing"),
    ("TuniX có bảo mật thông tin tài khoản của tôi không?", "Có. Mật khẩu được mã hóa bằng thuật toán an toàn (bcrypt), phiên đăng nhập dùng JWT có thời hạn, và toàn bộ kết nối đều qua HTTPS khi triển khai production.", "security"),
    ("Tôi có thể xóa tài khoản không?", "Có. Bạn có thể gửi yêu cầu xóa tài khoản trong mục 'Tài khoản'. Yêu cầu sẽ được quản trị viên phê duyệt trước khi xóa dữ liệu.", "security"),
]

SETTINGS = [
    ("site_name", "TuniX"),
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
                source="Ban biên tập TuniX",
                author_name="Ban biên tập TuniX",
                status=NewsStatusEnum.published,
            )
        )
        added += 1
    db.commit()
    return added


def main() -> None:
    db: Session = SessionLocal()
    try:
        print("=== Seed dữ liệu TuniX ===\n")

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
