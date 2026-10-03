from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    owner_id: str = "local"
    database_url: str = "postgresql+psycopg://travel:travel@localhost:5432/travel"
    personal_ai_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")
    personal_ai_timeout_seconds: float = Field(default=45.0, gt=0, le=60)
    personal_ai_research_enabled: bool = False
    cors_origins: str = "http://localhost:3000"
    geoapify_api_key: SecretStr | None = None
    geoapify_timeout_seconds: float = Field(default=8.0, gt=0, le=60)

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
