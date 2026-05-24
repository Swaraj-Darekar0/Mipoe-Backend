from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Mipoe API"
    api_prefix: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    frontend_url: str = Field(default="http://localhost:8080", alias="FRONTEND_URL")

    database_url: str = Field(alias="DATABASE_URL")
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    redis_url: str = Field(default="redis://localhost:6379/2", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/1")
    password_reset_token_ttl_seconds: int = Field(default=900, alias="PASSWORD_RESET_TOKEN_TTL_SECONDS")

    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_key: str | None = Field(default=None, alias="SUPABASE_KEY")
    supabase_anon_key: str | None = Field(default=None, alias="SUPABASE_ANON_KEY")
    resend_api_key: str | None = Field(default=None, alias="RESEND_API_KEY")
    resend_from_email: str | None = Field(default=None, alias="RESEND_FROM_EMAIL")
    resend_reply_to: str | None = Field(default=None, alias="RESEND_REPLY_TO")

    cashfree_app_id: str | None = Field(default=None, alias="CASHFREE_APP_ID")
    cashfree_secret_key: str | None = Field(default=None, alias="CASHFREE_SECRET_KEY")
    cashfree_api_url: str = Field(default="https://sandbox.cashfree.com/pg", alias="CASHFREE_API_URL")

    instagram_username: str | None = Field(default=None, alias="INSTAGRAM_USERNAME")
    instagram_password: str | None = Field(default=None, alias="INSTAGRAM_PASSWORD")

    token_crypt_key: str | None = Field(default=None, alias="TOKEN_CRYPT_KEY")
    secret_key: str | None = Field(default=None, alias="SECRET_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
