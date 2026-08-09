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

    # OAuth Facebook
    facebook_client_id: str = ""
    facebook_client_secret: str = ""
    facebook_redirect_uri: str = ""

    # Rate limit đăng nhập (chống brute-force) — in-memory
    login_rate_limit_minutes: int = 15
    login_rate_limit_max: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


settings = Settings()
