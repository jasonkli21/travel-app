from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    owner_id: str = Field(default="local", min_length=1, max_length=128)
    travel_auth_mode: Literal["local", "google_oidc"] = "local"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: AnyHttpUrl | None = None
    google_oauth_allowed_email: str = ""
    google_oidc_issuer: Literal["https://accounts.google.com", "accounts.google.com"] = (
        "https://accounts.google.com"
    )
    auth_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    auth_session_cookie_name: str = "__Host-travel_session"
    auth_csrf_cookie_name: str = "__Host-travel_csrf"
    auth_oauth_flow_cookie_name: str = "__Host-travel_oauth_flow"
    personal_ai_auth_mode: Literal["none", "google_user_id_token", "google_cloud_run_iam"] = "none"
    personal_ai_user_id_token_audience: str = ""
    personal_ai_service_iam_audience: str = ""
    personal_ai_service_account: str = ""
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
    def google_oauth_allowed_email_normalized(self) -> str:
        return self.google_oauth_allowed_email.strip().lower()

    @model_validator(mode="after")
    def validate_identity_configuration(self) -> "Settings":
        if self.travel_auth_mode == "google_oidc":
            if (
                not self.google_oauth_client_id.strip()
                or self.google_oauth_client_secret is None
                or not self.google_oauth_client_secret.get_secret_value().strip()
                or self.google_oauth_redirect_uri is None
                or not self.google_oauth_allowed_email_normalized
                or "@" not in self.google_oauth_allowed_email_normalized
            ):
                raise ValueError("Google OIDC mode requires complete server-side credentials.")
            redirect = self.google_oauth_redirect_uri
            if redirect.scheme != "https" and redirect.host not in {"localhost", "127.0.0.1"}:
                raise ValueError("Google OAuth redirect URI must use HTTPS outside loopback.")

        if self.personal_ai_auth_mode == "google_user_id_token":
            if self.travel_auth_mode != "google_oidc" or (
                self.personal_ai_user_id_token_audience != self.google_oauth_client_id
            ):
                raise ValueError(
                    "The AI user-token audience must match the configured Google OAuth client."
                )
        if self.personal_ai_auth_mode == "google_cloud_run_iam":
            if (
                not self.personal_ai_service_iam_audience.startswith("https://")
                or not self.personal_ai_service_account.strip()
            ):
                raise ValueError("Cloud Run AI transport requires an HTTPS audience and identity.")
        if (
            self.travel_auth_mode == "google_oidc"
            and (self.personal_ai_research_enabled or self.personal_ai_proposals_enabled)
            and self.personal_ai_auth_mode == "none"
        ):
            raise ValueError("Authenticated AI operations require an independent AI credential.")
        return self

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
