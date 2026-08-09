"""Gửi email qua SMTP. Ở môi trường dev chưa cấu hình SMTP, ghi nội dung ra console
(không phá flow) — khi điền SMTP_* vào .env sẽ gửi thật.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger("tunix.email")


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not settings.smtp_host or not settings.smtp_user:
        logger.info(
            "[SMTP chua cau hinh - dev mode] To: %s | Subject: %s | Body:\n%s",
            to_email,
            subject,
            html_body,
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if settings.smtp_use_tls:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, [to_email], msg.as_string())
        server.quit()
        logger.info("Email sent to %s", to_email)
        return True
    except Exception as exc:  # noqa: BLE001 - không làm hỏng flow khi email lỗi
        logger.warning("Send email failed to %s: %s", to_email, exc)
        return False


def build_reset_email_html(reset_url: str) -> str:
    return f"""\
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e5e9f0;border-radius:16px;overflow:hidden;">
  <div style="background:#008080;padding:20px 24px;">
    <span style="color:#fff;font-size:22px;font-weight:800;">Tuni<span style="opacity:.7">X</span></span>
  </div>
  <div style="padding:24px;">
    <h2 style="color:#1a1f36;margin:0 0 12px;">Đặt lại mật khẩu</h2>
    <p style="color:#4a5a72;line-height:1.6;">Bạn nhận được email này vì chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản TuniX của bạn.</p>
    <p style="color:#4a5a72;line-height:1.6;">Nhấn nút bên dưới để tạo mật khẩu mới. Liên kết có hiệu lực trong 30 phút.</p>
    <p style="text-align:center;margin:24px 0;">
      <a href="{reset_url}" style="background:#008080;color:#fff;padding:12px 28px;border-radius:30px;text-decoration:none;font-weight:600;">Đặt lại mật khẩu</a>
    </p>
    <p style="color:#8a99b1;font-size:12px;">Nếu bạn không yêu cầu đặt lại mật khẩu, bạn có thể bỏ qua email này.</p>
  </div>
</div>"""
