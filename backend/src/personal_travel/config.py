from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
    google_oauth_client_id: str = Field(default="", max_length=255)
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: AnyHttpUrl | None = None
    google_oauth_allowed_email: str = Field(default="", max_length=320)
    google_oauth_allowed_hosted_domain: str = Field(default="", max_length=253)
    google_oidc_issuer: Literal["https://accounts.google.com", "accounts.google.com"] = (
        "https://accounts.google.com"
    )
    auth_session_ttl_seconds: int = Field(default=28800, ge=300, le=28800)
    auth_session_cookie_name: str = Field(default="__Host-travel_session", max_length=64)
    auth_csrf_cookie_name: str = Field(default="__Host-travel_csrf", max_length=64)
    auth_oauth_flow_cookie_name: str = Field(default="__Host-travel_oauth_flow", max_length=64)
    auth_ai_token_cookie_name: str = Field(default="__Host-travel_ai_token", max_length=64)
    personal_ai_auth_mode: Literal["none", "google_cloud_run_iam"] = "none"
    personal_ai_user_id_token_audience: str = Field(default="", max_length=500)
    personal_ai_service_iam_audience: str = Field(default="", max_length=500)
    personal_ai_allow_custom_service_audience: bool = False
    personal_ai_service_account: str = Field(default="", max_length=320)
    database_url: str = "postgresql+psycopg://travel:travel@localhost:5432/travel"
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=15000, ge=1000, le=60000)
    database_lock_timeout_ms: int = Field(default=5000, ge=1000, le=30000)
    personal_ai_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")
    personal_ai_timeout_seconds: float = Field(default=45.0, gt=0, le=50)
    personal_ai_research_enabled: bool = False
    personal_ai_comparisons_enabled: bool = False
    personal_ai_proposals_enabled: bool = False
    personal_ai_extractions_enabled: bool = False
    private_imports_enabled: bool = False
    private_attachments_enabled: bool = False
    private_source_dir: str = ""
    personal_ai_proposal_timeout_seconds: float = Field(default=45.0, gt=0, le=49)
    personal_ai_extraction_timeout_seconds: float = Field(default=35.0, gt=0, le=40)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    allowed_hosts: str = "localhost,127.0.0.1,::1"
    geoapify_api_key: SecretStr | None = None
    geoapify_timeout_seconds: float = Field(default=8.0, gt=0, le=60)

    @property
    def google_oauth_allowed_email_normalized(self) -> str:
        return self.google_oauth_allowed_email.strip().lower()

    @model_validator(mode="after")
    def validate_identity_configuration(self) -> "Settings":
        if self.private_imports_enabled or self.private_attachments_enabled:
            source_path = Path(self.private_source_dir).expanduser()
            if self.travel_auth_mode != "google_oidc" or not self.private_source_dir:
                raise ValueError(
                    "Private imports and attachments require Google mode "
                    "and a private source directory."
                )
            if not source_path.is_absolute():
                raise ValueError("Private source storage must use an absolute directory path.")
        if self.travel_auth_mode == "google_oidc":
            allowed_email = self.google_oauth_allowed_email_normalized
            if (
                not self.google_oauth_client_id.strip()
                or not self.google_oauth_client_id.isascii()
                or any(character.isspace() for character in self.google_oauth_client_id)
                or self.google_oauth_client_secret is None
                or not self.google_oauth_client_secret.get_secret_value().strip()
                or self.google_oauth_redirect_uri is None
                or not allowed_email
                or not allowed_email.isascii()
                or any(character.isspace() for character in allowed_email)
                or allowed_email.count("@") != 1
                or not all(allowed_email.split("@", 1))
                or "." not in allowed_email.rsplit("@", 1)[1]
            ):
                raise ValueError("Google OIDC mode requires complete server-side credentials.")
            cookie_names = (
                self.auth_session_cookie_name,
                self.auth_csrf_cookie_name,
                self.auth_oauth_flow_cookie_name,
                self.auth_ai_token_cookie_name,
            )
            if cookie_names != (
                "__Host-travel_session",
                "__Host-travel_csrf",
                "__Host-travel_oauth_flow",
                "__Host-travel_ai_token",
            ):
                raise ValueError("Authentication cookie names must match the web proxy contract.")
            hosted_domain = self.google_oauth_allowed_hosted_domain.strip().lower()
            if allowed_email.rsplit("@", 1)[1] not in {"gmail.com", "googlemail.com"} and (
                not hosted_domain
                or hosted_domain != allowed_email.rsplit("@", 1)[1]
                or not hosted_domain.isascii()
            ):
                raise ValueError("A non-Gmail owner requires a matching Workspace hosted domain.")
            cookie_name_characters = "!#$%&'*+-.^_`|~"
            if len(set(cookie_names)) != len(cookie_names) or any(
                not name.startswith("__Host-")
                or len(name) <= len("__Host-")
                or any(
                    not character.isascii()
                    or not (character.isalnum() or character in cookie_name_characters)
                    for character in name
                )
                for name in cookie_names
            ):
                raise ValueError("Authentication cookies must have unique __Host- names.")
            redirect = self.google_oauth_redirect_uri
            if (
                redirect.username is not None
                or redirect.password is not None
                or redirect.query
                or redirect.fragment
                or redirect.path != "/auth/google/callback"
            ):
                raise ValueError("Google OAuth must use the same-origin callback route.")
            if redirect.scheme != "https" and redirect.host not in {"localhost", "127.0.0.1"}:
                raise ValueError("Google OAuth redirect URI must use HTTPS outside loopback.")

        if self.personal_ai_auth_mode == "google_cloud_run_iam":
            service_audience = self.personal_ai_service_iam_audience.strip()
            parsed_service_audience = urlsplit(service_audience)
            service_account = self.personal_ai_service_account.strip().lower()
            service_account_name, separator, service_account_domain = service_account.partition("@")
            ai_url = urlsplit(str(self.personal_ai_base_url))
            if (
                self.travel_auth_mode != "google_oidc"
                or self.personal_ai_user_id_token_audience != self.google_oauth_client_id
                or not self.google_oauth_client_id.strip()
                or parsed_service_audience.scheme != "https"
                or not parsed_service_audience.hostname
                or parsed_service_audience.username is not None
                or parsed_service_audience.password is not None
                or parsed_service_audience.query
                or parsed_service_audience.fragment
                or not separator
                or not service_account_name
                or not service_account_domain.endswith(".iam.gserviceaccount.com")
                or any(character.isspace() for character in service_account)
                or ai_url.scheme != "https"
                or not ai_url.hostname
                or ai_url.username is not None
                or ai_url.password is not None
                or ai_url.query
                or ai_url.fragment
                or (
                    not self.personal_ai_allow_custom_service_audience
                    and (parsed_service_audience.scheme, parsed_service_audience.netloc)
                    != (ai_url.scheme, ai_url.netloc)
                )
            ):
                raise ValueError(
                    "Cloud Run AI transport requires a Google user-token audience matching the "
                    "OAuth client, plus an HTTPS service audience and identity."
                )
        if self.personal_ai_extractions_enabled and not self.private_imports_enabled:
            raise ValueError("Booking extraction requires private source intake to be enabled.")
        if (
            self.travel_auth_mode == "google_oidc"
            and (
                self.personal_ai_research_enabled
                or self.personal_ai_comparisons_enabled
                or self.personal_ai_proposals_enabled
                or self.personal_ai_extractions_enabled
            )
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
