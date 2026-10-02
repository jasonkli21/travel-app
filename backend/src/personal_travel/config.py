from functools import lru_cache

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    owner_id: str = "local"
    database_url: str = "postgresql+psycopg://travel:travel@localhost:5432/travel"
    personal_ai_base_url: AnyHttpUrl = "http://localhost:8001"
    personal_ai_timeout_seconds: float = 15.0
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
