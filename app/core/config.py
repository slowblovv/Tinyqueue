"""Application configuration, sourced entirely from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://tinyqueue:tinyqueue@localhost:5432/tinyqueue"

    # Logging
    log_level: str = "INFO"

    # Worker
    worker_count: int = 4
    job_poll_interval: float = 0.5
    job_timeout: int = 30
    max_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_jitter: float = 0.5
    heartbeat_interval: int = 5
    heartbeat_timeout: int = 30
    claim_batch_size: int = 1

    # API / validation
    max_payload_bytes: int = 256 * 1024
    max_job_type_length: int = 100
    max_idempotency_key_length: int = 255
    default_page_limit: int = 20
    max_page_limit: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
