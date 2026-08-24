from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TuniX API"
    app_env: str = "development"
    api_prefix: str = "/api"

    database_url: str = "sqlite:///./tunix.db"
    secret_key: str = "change_me_in_production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_minutes: int = 60 * 24 * 7

    vnstock_api_key: str = ""
    redis_url: str = ""
    frontend_dir: str = "frontend"

    # Auth / token
    access_token_expire_minutes: int = 60
    refresh_token_expire_minutes: int = 60 * 24 * 7
    reset_token_expire_minutes: int = 30
    # Frontend public URL (dùng cho redirect OAuth & link reset)
    frontend_url: str = "http://localhost:8000"

    # SMTP (gửi email reset password / xác thực)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@tunix.vn"
    smtp_use_tls: bool = True

    # OAuth Google
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # OAuth Facebook (Meta) — sắp ra mắt, để trống = vô hiệu hoá
    facebook_client_id: str = ""
    facebook_client_secret: str = ""
    facebook_redirect_uri: str = ""

    # Rate limit đăng nhập (chống brute-force) — in-memory
    login_rate_limit_minutes: int = 15
    login_rate_limit_max: int = 10

    # Xác thực email
    email_verification_expire_minutes: int = 60 * 24  # 24 giờ
    resend_verification_cooldown_seconds: int = 60  # cooldown gửi lại email xác thực
    # APP_URL — link xác thực/đặt lại mật khẩu; nếu trống sẽ dùng FRONTEND_URL
    app_url: str = ""

    # TuniAI (DeepSeek) — Phase 1 AI Chat
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    ai_chat_max_turns: int = 8
    ai_chat_max_tokens: int = 4096
    ai_rate_limit_minutes: int = 5
    ai_rate_limit_max: int = 30

    # Trung tâm cảnh báo (Alert Center) — Support/Resistance + phát hiện tín hiệu
    alert_near_support_threshold: float = 0.03    # % gần vùng hỗ trợ để cảnh báo (3%)
    alert_near_resistance_threshold: float = 0.03  # % gần vùng kháng cự để cảnh báo (3%)
    alert_cooldown_hours: int = 24                # chống spam: không gửi lặp cùng vùng trong N giờ
    alert_unusual_move_pct: float = 3.0           # biến động giá % coi là bất thường (asset events)
    alert_rsi_overbought: float = 70.0
    alert_rsi_oversold: float = 30.0
    alert_history_period: str = "1y"              # khung dữ liệu để tính vùng S/R
    asset_event_window_days: int = 183            # chỉ lấy sự kiện tài sản sắp tới / trong ~6 tháng gần

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


settings = Settings()
