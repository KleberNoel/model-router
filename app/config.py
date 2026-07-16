from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MODEL_ROUTER_", env_file=".env", extra="ignore")

    app_name: str = "Model Router"
    env: str = "development"
    database_url: str = "sqlite:///./model_router.db"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    database_auto_create: bool = True
    redis_url: str = "redis://127.0.0.1:6379/0"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "model-router"
    jwt_audience: str = "model-router-clients"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    refresh_cookie_name: str = "mr_refresh"
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    request_timeout_seconds: int = 600
    upstream_verify_tls: bool = True

    managed_llama_command: str = "llama-server"
    managed_llama_host: str = "127.0.0.1"
    managed_llama_port: int = 8090
    managed_llama_health_path: str = "/health"
    managed_llama_startup_timeout_seconds: int = 180
    managed_llama_shutdown_timeout_seconds: int = 35
    managed_llama_idle_timeout_seconds: int = 900
    managed_llama_models_json: str | None = None

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_tenant_name: str | None = None
    bootstrap_tenant_slug: str | None = None
    default_model_name: str | None = None
    default_upstream_base_url: str | None = None
    default_upstream_model_name: str | None = None
    bootstrap_routes_json: str | None = None
    bootstrap_api_keys_json: str | None = None

    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_redirect_uri: str = "http://127.0.0.1:4000/api/v1/integrations/google/callback"
    google_oauth_encryption_key: str | None = None
    google_oauth_scopes: list[str] = Field(
        default_factory=lambda: [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ]
    )
    google_default_task_list: str = "@default"
    gmail_triage_enabled: bool = False
    gmail_triage_interval_seconds: int = 900
    gmail_triage_query: str = "is:unread is:important newer_than:7d"

    otel_enabled: bool = False
    otel_service_name: str = "model-router"
    otel_exporter_otlp_endpoint: str | None = None
    google_tasks_sync_interval_seconds: int = 300
    agent_runner_url: str = "http://127.0.0.1:4000"
    agent_runner_api_key: str | None = None
    gmail_triage_model_route: str = "local/qwen-reasoning"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
