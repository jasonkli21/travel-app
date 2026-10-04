from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    owner_id: str = Field(default="local", min_length=1, max_length=128)
    database_url: str = "postgresql+psycopg://travel:travel@localhost:5432/travel"
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=15000, ge=1000, le=60000)
    database_lock_timeout_ms: int = Field(default=5000, ge=1000, le=30000)
    personal_ai_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")
    personal_ai_timeout_seconds: float = Field(default=45.0, gt=0, le=50)
    personal_ai_research_enabled: bool = False
    personal_ai_proposals_enabled: bool = False
    personal_ai_proposal_timeout_seconds: float = Field(default=45.0, gt=0, le=49)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    allowed_hosts: str = "localhost,127.0.0.1,::1"
    geoapify_api_key: SecretStr | None = None
    geoapify_timeout_seconds: float = Field(default=8.0, gt=0, le=60)

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
