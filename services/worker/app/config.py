"""Worker settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_root_env() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".env").is_file():
            return parent / ".env"
    return Path(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(_find_root_env(), ".env"), extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://voiceops:voiceops@localhost:5432/voiceops"
    queue_namespace: str = "vo"

    flight_api_base_url: str = "http://localhost:8002"
    flight_api_timeout_sec: float = 10.0

    worker_concurrency: int = 4
    # How long a worker may hold a job before the reaper assumes it died. Must
    # exceed the slowest handler, or healthy work gets recovered and run twice.
    visibility_timeout_sec: float = 60.0
    max_attempts: int = 5
    backoff_base_sec: float = 2.0
    backoff_max_sec: float = 300.0

    # Prometheus scrapes this. The worker serves nothing else over HTTP.
    metrics_port: int = 9101

    # How often the scheduler promotes due jobs and reaps expired leases.
    scheduler_interval_sec: float = 1.0
    # Idle poll interval when no work is waiting.
    poll_interval_sec: float = 0.1


settings = Settings()
