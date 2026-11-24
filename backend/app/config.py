from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Complete Office Automation Suite"
    app_env: str = "production"

    database_url: str = (
        "postgresql+psycopg://office_user:change-this-password"
        "@localhost:5432/office_suite"
    )

    secret_key: str = "development-only-change-this-secret"
    access_token_expire_minutes: int = 60

    upload_dir: str = "/opt/office-suite/backend/uploads"
    max_upload_size_mb: int = 20
    email_imap_host: str | None = None
    email_imap_port: int = 993
    email_imap_username: str | None = None
    email_imap_password: str | None = None
    email_imap_ssl: bool = True
    email_imap_mailbox: str = "INBOX"

    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_smtp_ssl: bool = False
    email_smtp_starttls: bool = True
    email_from_address: str | None = None


    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file="/opt/office-suite/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
