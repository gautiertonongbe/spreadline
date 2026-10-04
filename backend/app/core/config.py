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
    # --- Spreadline's own history -------------------------------------
    #: Whether provider answers are persisted as observations. On by default:
    #: the dataset only has value if it was accumulating from the first day, and
    #: a capture that has to be switched on is one that was off when it mattered.
    history_capture_enabled: bool = True
    #: Listings polled per scheduled run. Bounded so a scheduled job cannot grow
    #: into an unplanned provider bill.
    history_refresh_batch_size: int = 25
    #: How often the universe refresh runs. What each listing is actually due for
    #: is its own interval; this is only how often the question is asked.
    history_refresh_interval_seconds: int = 900
    #: Upper bound on the tracked universe. Spreadline observes what the operator
    #: works on; it does not crawl a marketplace.
    history_universe_limit: int = 500

    # --- Sessions ---------------------------------------------------------
    #: How long a session lives at the outside, and how long it may sit idle.
    #: The idle limit is the one that matters day to day: a browser left open on
    #: a machine somebody walked away from should stop being a way in.
    session_ttl_hours: int = 720  # 30 days
    session_idle_timeout_minutes: int = 10080  # 7 days
    #: Whether the session cookie is marked Secure. On everywhere but plain-HTTP
    #: local development, where marking it Secure means no cookie at all.
    secure_cookies: bool = True

    #: How long an execution instruction stays actionable. An authorisation to
    #: pay up to a price is only as good as the price that justified it, so it
    #: expires rather than waiting indefinitely for somebody to get to it.
    execution_instruction_ttl_hours: int = 48

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
