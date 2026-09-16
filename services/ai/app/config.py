"""Settings for the AI service."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_root_env() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return Path(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(_find_root_env(), ".env"), extra="ignore")

    database_url: str = "postgresql://voiceops:voiceops@localhost:5432/voiceops"
    ai_port: int = 8000
    log_level: str = "info"

    # ---------- Provider selection ----------
    stt_provider: str = "local"
    llm_provider: str = "ollama"
    tts_provider: str = "piper"

    # ---------- Local runtimes ----------
    # These run natively on the host, never in Docker: Docker Desktop on macOS
    # has no Metal passthrough. See ADR 0001.
    whisper_bin: str = "whisper-cli"
    whisper_model: str = "./models/whisper/ggml-small.en.bin"
    piper_bin: str = "piper"
    piper_voice: str = "./models/piper/en_US-lessac-medium.onnx"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"

    # ---------- Hosted providers (free tiers) ----------
    groq_api_key: str = ""
    groq_llm_model: str = "llama-3.3-70b-versatile"
    groq_stt_model: str = "whisper-large-v3"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ---------- Downstream ----------
    flight_api_base_url: str = "http://localhost:8002"
    flight_api_timeout_sec: float = 10.0

    # ---------- Orchestration ----------
    # Cap on tool-calling round trips within one turn. Without it, a model that
    # keeps requesting tools loops until something else times out.
    max_tool_iterations: int = 5
    llm_timeout_sec: float = 120.0


settings = Settings()


# Model paths in .env are written relative to the repo root (./models/...) so they
# read the same everywhere. Resolve them against the directory holding .env, and
# fall back to the process working directory when there is none (containers,
# where the paths are supplied absolute anyway).
_REPO_ROOT = _find_root_env().parent


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (_REPO_ROOT / path).resolve()
