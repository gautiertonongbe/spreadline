"""Environment-based configuration.

Every tunable lives here. Nothing in the codebase reads os.environ directly, so
the full surface of deployment-dependent behaviour is inspectable in one place.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: List settings are written as plain comma-separated strings in .env files and
#: deployment consoles. NoDecode stops pydantic-settings from trying to JSON-parse
#: them first, so "mock,amazon" is accepted rather than demanding '["mock"]'.
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Spreadline"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False

    # --- Database ----------------------------------------------------------
    # Postgres in every real environment. SQLite is supported so the test suite
    # and a first-run developer do not need a database server.
    database_url: str = "postgresql+psycopg://spreadline:spreadline@localhost:5432/spreadline"
    database_echo: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # --- CORS --------------------------------------------------------------
    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Observability -----------------------------------------------------
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

    # --- Providers ---------------------------------------------------------
    # The default provider set is deliberately mock-only: Spreadline never
    # pretends to have a live integration it does not have credentials for.
    enabled_providers: CsvList = Field(default_factory=lambda: ["mock"])
    provider_timeout_seconds: float = 10.0
    provider_max_retries: int = 3
    provider_backoff_base_seconds: float = 0.25
    provider_backoff_max_seconds: float = 8.0
    provider_rate_limit_per_second: float = 5.0
    provider_circuit_failure_threshold: int = 5
    provider_circuit_reset_seconds: float = 60.0

    amazon_provider_credentials: str | None = None
    walmart_provider_credentials: str | None = None

    # Best Buy Products API. A free developer key from developer.bestbuy.com is
    # enough for development and testing; commercial use needs a partner
    # agreement with Best Buy, which is the operator's to arrange.
    bestbuy_api_key: str | None = None
    bestbuy_base_url: str = "https://api.bestbuy.com/v1"

    # eBay Browse API. A free developer account grants an application token via
    # the client-credentials flow; no seller account and no per-request cost.
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_marketplace_id: str = "EBAY_US"
    ebay_environment: Literal["production", "sandbox"] = "production"

    # --- Freshness (TTL, seconds) -----------------------------------------
    ttl_current_price_seconds: int = 900  # 15 min
    ttl_availability_seconds: int = 900
    ttl_competition_seconds: int = 3600  # 1 h
    ttl_demand_seconds: int = 21600  # 6 h
    ttl_price_history_seconds: int = 86400  # 1 d
    ttl_product_metadata_seconds: int = 604800  # 7 d

    # --- Scheduling --------------------------------------------------------
    scheduler_enabled: bool = False
    scheduler_backend: Literal["apscheduler", "noop"] = "apscheduler"

    # --- AI (optional intelligence layer, off by default) -----------------
    ai_enabled: bool = False
    ai_provider: Literal["null", "anthropic"] = "null"
    ai_model: str = "claude-sonnet-5"
    ai_api_key: str | None = None
    ai_max_match_confidence: float = 0.80  # AI may never assert a high-confidence match

    @field_validator("cors_origins", "enabled_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
