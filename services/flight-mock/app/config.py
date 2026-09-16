"""Settings for the mock flight service, read from the environment."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

def _find_root_env() -> Path:
    """
    Walk up from this file looking for the repo-root .env.

    The repo keeps one .env at its root rather than one per service. Inside a
    container that file is absent and the directory tree is shallower, so this
    must degrade to "no env file" rather than indexing off the end of parents.
    Real environment variables win in either case, which is what Docker and
    Kubernetes supply.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return Path(".env")


ROOT_ENV = _find_root_env()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_ENV, ".env"), extra="ignore")

    database_url: str = "postgresql://voiceops:voiceops@localhost:5432/voiceops"
    flight_mock_port: int = 8002
    log_level: str = "info"

    # Chaos defaults applied at startup. Everything here can be changed at
    # runtime through /admin/chaos without restarting the service.
    chaos_enabled: bool = False
    chaos_error_rate: float = 0.0
    chaos_latency_ms: int = 0
    chaos_error_kind: str = "503"

    # How close to departure a booking stops being cancellable.
    cancellation_cutoff_hours: int = 2


settings = Settings()
